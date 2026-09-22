/* IEEE Xplore / Beihang only. Authentication stays in Zotero's cookie context. */
var Search4PaperIEEE = class {
  constructor(Zotero, { now = () => performance.now(), sleep = ms => Zotero.Promise.delay(ms) } = {}) {
    this.Zotero = Zotero;
    this.now = now;
    this.sleep = sleep;
    this.context = null;
    this.viewer = null;
    this.pending = null;
    this.controller = null;
    this.lastStart = null;
    this.closed = false;
    this.status = "未开始 IEEE 北航机构登录";
  }

  login() {
    if (this.closed) throw new Error("IEEE 会话已关闭，请重新启用插件。");
    if (!this.context) this.context = this.Zotero.HTTP.newCookieContext();
    if (this.viewer && !this.viewer.closed) this.viewer.focus();
    else this.viewer = this.Zotero.getMainWindow().openDialog(
      "chrome://search4paper/content/ieee-login.xhtml", "search4paper-ieee-login",
      "chrome,centerscreen,resizable,width=1000,height=760", { userContextId: this.context.id }
    );
    this.status = "请在 IEEE 页面选择 Institutional Sign In → Beihang University，手动完成登录；PDF 访问尚未验证。";
  }

  pdfURL(value) {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "ieeexplore.ieee.org" || url.port || url.username || url.password) {
      throw new Error("条目的 URL 必须是 HTTPS IEEE Xplore 论文或 PDF 地址。");
    }
    const document = url.pathname.match(/^\/(?:abstract\/)?document\/(\d+)\/?$/);
    const stamp = ["/stamp/stamp.jsp", "/stampPDF/getPDF.jsp"].includes(url.pathname);
    const id = document?.[1] || (stamp && url.searchParams.get("arnumber"));
    if (!id || !/^\d+$/.test(id)) throw new Error("请在 Zotero 条目的 URL 栏填写 IEEE /document/论文编号 或带 arnumber 的 PDF 链接。");
    return `https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=${id}`;
  }

  hasPaperURL(value) {
    try { this.pdfURL(value); return true; }
    catch { return false; }
  }

  canResolve(item) {
    return /^Source ID: icde:/m.test(item.getField("extra"))
      || /^10\.1109\//i.test(item.getField("DOI"))
      || /\b(?:IEEE|ICDE)\b/i.test(item.getField("conferenceName"))
      || /^https:\/\/(?:ieeexplore\.ieee\.org|ieee-icde\.org)(?:\/|$)/i.test(item.getField("url"));
  }

  async resolvePaperURL(item, { signal } = {}) {
    if (this.hasPaperURL(item.getField("url"))) return false;
    const normalize = value => String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
    const doi = item.getField("DOI").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
    const title = normalize(item.getField("title"));
    const year = item.getField("date").match(/\b(?:19|20)\d{2}\b/)?.[0];
    const authors = item.getCreators().filter(c => c.creatorTypeID === this.Zotero.CreatorTypes.getID("author"))
      .map(c => normalize([c.firstName, c.lastName].filter(Boolean).join(" ")));
    if (!title || (!doi && (!year || !authors.length))) {
      throw new Error("IEEE 地址补全：缺少标题、年份或作者，无法可靠核对；请手动核实论文地址。");
    }
    const endpoint = doi ? `https://api.crossref.org/works/${encodeURIComponent(doi)}`
      : `https://api.crossref.org/works?filter=prefix:10.1109&rows=20&query.bibliographic=${encodeURIComponent(item.getField("title"))}`;
    let cancel;
    const abort = () => cancel?.();
    signal?.throwIfAborted();
    signal?.addEventListener("abort", abort, { once: true });
    let response;
    try {
      // Public bibliographic metadata only; never send the institutional cookie context.
      response = await this.Zotero.HTTP.request("GET", endpoint, {
        responseType: "json", timeout: 30000, errorDelayMax: 0, noRetryOnThrottle: true,
        cancellerReceiver(fn) { cancel = fn; if (signal?.aborted) fn(); }
      });
    }
    catch (error) {
      signal?.throwIfAborted();
      throw new Error(`IEEE 地址补全：Crossref 查询失败${error.status ? `（HTTP ${error.status}）` : ""}；未修改条目。`);
    }
    finally { signal?.removeEventListener("abort", abort); }
    signal?.throwIfAborted();
    const message = response.response?.message;
    const records = doi ? (message?.DOI ? [message] : null) : message?.items;
    if (!Array.isArray(records)) throw new Error("IEEE 地址补全：Crossref 返回的元数据格式无效。");
    const matches = records.filter(record => {
      if (!/^10\.1109\//i.test(record.DOI || "") || !record.title?.some(t => normalize(t) === title)) return false;
      if (doi && record.DOI.toLowerCase() !== doi.toLowerCase()) return false;
      const years = [record.event?.start, record.published, record["published-print"], record["published-online"]]
        .map(date => String(date?.["date-parts"]?.[0]?.[0] || ""));
      if (year && !years.includes(year)) return false;
      const names = (record.author || []).map(a => normalize([a.given, a.family].filter(Boolean).join(" ")));
      return !authors.length || authors.some(a => names.includes(a));
    });
    const unique = [...new Map(matches.map(record => [record.DOI.toLowerCase(), record])).values()];
    if (unique.length !== 1) throw new Error(unique.length
      ? "IEEE 地址补全：存在多个匹配，未自动修改；请核实论文地址后重试。"
      : "IEEE 地址补全：未找到标题、作者和年份一致的记录；可能尚未收录，请稍后重试或手动填写地址。");
    const record = unique[0];
    const url = record.resource?.primary?.URL;
    if (!this.hasPaperURL(url)) throw new Error("IEEE 地址补全：匹配记录未提供有效 IEEE 单篇论文地址；未修改条目。");
    if (!this.Zotero.Libraries.get(item.libraryID).editable) throw new Error("IEEE 地址补全：文献库不可编辑。");
    signal?.throwIfAborted();
    const old = { url: item.getField("url"), DOI: item.getField("DOI"), extra: item.getField("extra") };
    try {
      item.setField("url", url);
      if (!old.DOI) item.setField("DOI", record.DOI);
      // Preserve the previous source link when replacing a conference-list URL.
      const source = old.url ? `Original URL: ${old.url}` : "";
      if (source && !old.extra.split("\n").includes(source)) item.setField("extra", [old.extra, source].filter(Boolean).join("\n"));
      await item.saveTx();
    }
    catch (error) {
      for (const [field, value] of Object.entries(old)) item.setField(field, value);
      throw error;
    }
    return true;
  }

  download(items, { signal, onProgress = () => {} } = {}) {
    if (this.closed || !this.context) throw new Error("请先启动 IEEE 北航机构登录，并在打开的页面中完成登录。");
    if (this.pending) throw new Error("IEEE 机构下载正在进行，请等待当前批次完成。");
    if (!items.length) throw new Error("请先在 Zotero 主窗口选择需要附件的 IEEE 文献条目。");
    this.controller = new AbortController();
    const abort = () => this.controller?.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    this.pending = this.downloadBatch([...items], this.controller.signal, onProgress).finally(() => {
      signal?.removeEventListener("abort", abort);
      this.controller = null;
      this.pending = null;
    });
    return this.pending;
  }

  async downloadBatch(items, signal, onProgress) {
    const outcomes = [];
    for (const item of items) {
      if (signal.aborted) break;
      const result = { itemID: item.id, title: item.getField("title"), hasPDF: false, startedAt: null, error: "" };
      let directory;
      try {
        if (!item.isRegularItem() || item.deleted || !this.Zotero.Libraries.get(item.libraryID).filesEditable) {
          throw new Error("请选择可添加附件的文献条目。");
        }
        const url = this.pdfURL(item.getField("url"));
        const attachments = await this.Zotero.Items.getAsync(item.getAttachments());
        for (const attachment of attachments) {
          if (attachment.attachmentContentType === "application/pdf" && attachment.isFileAttachment() && await attachment.fileExists()) {
            result.hasPDF = true;
            result.reused = true;
            result.source = "已有本地 PDF";
            break;
          }
        }
        if (!result.hasPDF) {
          onProgress({ ...result, phase: "waiting", index: outcomes.length + 1, total: items.length });
          // A session-wide monotonic clock covers failures and separate batches too.
          while (this.lastStart !== null && this.now() - this.lastStart < 10000) {
            signal.throwIfAborted();
            await this.sleep(Math.min(250, 10000 - (this.now() - this.lastStart)));
          }
          signal.throwIfAborted();
          this.lastStart = this.now();
          result.startedAt = new Date().toISOString();
          let cancel;
          const abort = () => cancel?.();
          signal.addEventListener("abort", abort, { once: true });
          let response;
          try {
            let request;
            try { request = this.Zotero.HTTP.request("GET", url, {
              userContextId: this.context.id, responseType: "arraybuffer", timeout: 120000,
              errorDelayMax: 0, noRetryOnThrottle: true,
              cancellerReceiver(fn) { cancel = fn; if (signal.aborted) fn(); }
            }); }
            finally { this.lastStart = this.now(); }
            onProgress({ ...result, phase: "downloading", index: outcomes.length + 1, total: items.length });
            response = await request;
          }
          catch (error) {
            signal.throwIfAborted();
            // Do not copy login response bodies, redirect URLs, or SAML data into plugin messages.
            throw new Error(`IEEE 下载失败${error.status ? `（HTTP ${error.status}）` : "（网络请求失败）"}；请在登录窗口检查北航登录状态和论文访问权限。`);
          }
          finally { signal.removeEventListener("abort", abort); }
          signal.throwIfAborted();
          const bytes = new Uint8Array(response.response);
          const header = String.fromCharCode(...bytes.subarray(0, 8));
          const tail = String.fromCharCode(...bytes.subarray(Math.max(0, bytes.length - 2048)));
          if (!/^%PDF-\d\.\d/.test(header) || !tail.includes("%%EOF")) {
            throw new Error("IEEE 返回的不是完整 PDF（可能是登录页、访问限制或截断响应）；请完成北航登录后重试。");
          }
          this.status = "IEEE PDF 访问已验证；本次插件会话可继续使用（未自动核实机构身份）。";
          directory = (await this.Zotero.Attachments.createTemporaryStorageDirectory()).path;
          const path = PathUtils.join(directory, "ieee.pdf");
          await IOUtils.write(path, bytes);
          signal.throwIfAborted();
          await this.Zotero.Attachments.importFromFile({ file: path, parentItemID: item.id, title: "IEEE Full Text" });
          result.hasPDF = true;
          result.source = "IEEE 出版商 PDF";
        }
      }
      catch (error) {
        result.error = signal.aborted ? "已取消 IEEE 下载。" : error.message;
        if (!signal.aborted) this.status = "最近一次 IEEE 下载失败；请检查登录状态、访问权限或附件错误。";
      }
      finally {
        if (directory) await IOUtils.remove(directory, { recursive: true });
      }
      outcomes.push(result);
      onProgress({ ...result, phase: "finished", index: outcomes.length, total: items.length });
    }
    return outcomes;
  }

  async dispose() {
    this.closed = true;
    this.controller?.abort();
    if (this.viewer && !this.viewer.closed) this.viewer.close();
    try { await this.pending; }
    finally {
      this.context?.dispose();
      this.context = null;
      this.viewer = null;
      this.status = "IEEE 会话已关闭";
    }
  }
};
