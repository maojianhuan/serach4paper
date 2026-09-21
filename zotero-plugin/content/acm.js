/* ACM public PDFs. Browser verification and requests share one in-memory context. */
var Search4PaperACM = class {
  constructor(Zotero) {
    this.Zotero = Zotero;
    this.context = null;
    this.viewer = null;
    this.pending = null;
    this.controller = null;
    this.closed = false;
    this.verificationURL = "";
    this.status = "";
  }

  canHandle(item) {
    return /^\s*(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)?10\.1145\//i.test(item.getField("DOI"))
      || /^https:\/\/dl\.acm\.org\//i.test(item.getField("url"));
  }

  paperURL(item) {
    const doi = item.getField("DOI").trim().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "");
    let linkedDOI = "";
    const raw = item.getField("url");
    if (raw && URL.canParse(raw)) {
      const url = new URL(raw);
      if (url.hostname === "dl.acm.org") {
        if (url.protocol !== "https:" || url.port || url.username || url.password) throw new Error("ACM 论文地址必须是 HTTPS 官方链接。");
        linkedDOI = decodeURIComponent(url.pathname).match(/^\/doi\/(?:abs\/|full\/|pdf\/|epdf\/)?(10\.1145\/\d+(?:\.\d+)*)\/?$/i)?.[1] || "";
        if (!linkedDOI) throw new Error("ACM URL 不是受支持的单篇论文地址。");
      }
    }
    if (doi && linkedDOI && doi.toLowerCase() !== linkedDOI.toLowerCase()) throw new Error("条目的 ACM DOI 与论文 URL 不一致，请核实后重试。");
    const id = doi || linkedDOI;
    if (!/^10\.1145\/\d+(?:\.\d+)*$/i.test(id)) throw new Error("缺少有效 ACM DOI（10.1145/...），无法获取公开 PDF。");
    return `https://dl.acm.org/doi/${id}`;
  }

  ensureContext() {
    if (this.closed) throw new Error("ACM 会话已关闭，请重新启用插件。");
    if (!this.context) this.context = this.Zotero.HTTP.newCookieContext();
  }

  verify() {
    this.ensureContext();
    if (!this.verificationURL) throw new Error("当前没有需要验证的 ACM 论文。");
    if (this.viewer && !this.viewer.closed) this.viewer.focus();
    else this.viewer = this.Zotero.getMainWindow().openDialog(
      "chrome://search4paper/content/ieee-login.xhtml", "search4paper-acm-access",
      "chrome,centerscreen,resizable,width=1000,height=760",
      { userContextId: this.context.id, publisher: "ACM", initialURL: this.verificationURL }
    );
    this.status = "请在窗口中完成 ACM 访问验证；完成后关闭窗口，再点击“获取全文”。尚未验证下载是否可用。";
  }

  download(item, { signal } = {}) {
    if (this.closed) throw new Error("ACM 会话已关闭，请重新启用插件。");
    if (this.pending) throw new Error("ACM 下载正在进行。");
    this.controller = new AbortController();
    const abort = () => this.controller?.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    this.pending = this.downloadOne(item, this.controller.signal).finally(() => {
      signal?.removeEventListener("abort", abort);
      this.controller = null;
      this.pending = null;
    });
    return this.pending;
  }

  async downloadOne(item, signal) {
    const result = { itemID: item.id, title: item.getField("title"), hasPDF: false, error: "" };
    let directory;
    try {
      signal.throwIfAborted();
      if (!item.isRegularItem() || item.deleted || !this.Zotero.Libraries.get(item.libraryID).filesEditable) throw new Error("请选择可添加附件的文献条目。");
      const pageURL = this.paperURL(item);
      for (const attachment of await this.Zotero.Items.getAsync(item.getAttachments())) {
        if (attachment.attachmentContentType === "application/pdf" && attachment.isFileAttachment() && await attachment.fileExists()) {
          return { ...result, hasPDF: true, reused: true };
        }
      }
      this.ensureContext();
      // Public endpoint also used by Zotero's ACM translator; no citation API/login required.
      const pdfURL = pageURL.replace('/doi/', '/doi/pdf/') + '?download=true';
      let cancel, response;
      const abort = () => cancel?.();
      signal.addEventListener("abort", abort, { once: true });
      try {
        signal.throwIfAborted();
        response = await this.Zotero.HTTP.request("GET", pdfURL, {
          userContextId: this.context.id, responseType: "arraybuffer", timeout: 120000,
          errorDelayMax: 0, noRetryOnThrottle: true,
          cancellerReceiver(fn) { cancel = fn; if (signal.aborted) fn(); }
        });
      }
      catch (error) {
        signal.throwIfAborted();
        if ((error.status ?? error.xmlhttp?.status) === 403) {
          // Never infer authentication from visiting a page, and never echo response bodies.
          if (!this.verificationURL) this.verificationURL = pageURL;
          result.verificationRequired = true;
          this.status = "ACM 返回 HTTP 403。请点击“ACM 访问验证”，完成后重新获取全文。";
          throw new Error(this.status);
        }
        throw new Error(`ACM 公开 PDF 请求失败${error.status ? `（HTTP ${error.status}）` : "（网络错误）"}。`);
      }
      finally { signal.removeEventListener("abort", abort); }
      signal.throwIfAborted();
      const bytes = new Uint8Array(response.response);
      const header = String.fromCharCode(...bytes.subarray(0, 8));
      const tail = String.fromCharCode(...bytes.subarray(Math.max(0, bytes.length - 2048)));
      if (!/^%PDF-\d\.\d/.test(header) || !tail.includes("%%EOF")) throw new Error("ACM 返回的不是完整 PDF，未添加附件。");
      directory = (await this.Zotero.Attachments.createTemporaryStorageDirectory()).path;
      const path = PathUtils.join(directory, "acm.pdf");
      await IOUtils.write(path, bytes);
      signal.throwIfAborted();
      await this.Zotero.Attachments.importFromFile({ file: path, parentItemID: item.id, title: "ACM Full Text" });
      result.hasPDF = true;
      if (this.verificationURL === pageURL) this.verificationURL = "";
      if (!this.verificationURL) this.status = "ACM PDF 获取成功；当前会话可继续使用。";
    }
    catch (error) { result.error = signal.aborted ? "已取消 ACM 下载。" : error.message; }
    finally { if (directory) await IOUtils.remove(directory, { recursive: true }); }
    return result;
  }

  async dispose() {
    this.closed = true;
    this.controller?.abort();
    if (this.viewer && !this.viewer.closed) this.viewer.close();
    try { await this.pending; }
    finally { this.context?.dispose(); this.context = null; this.viewer = null; this.verificationURL = ""; }
  }
};
