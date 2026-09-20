/* Runs only in the privileged plugin window; no localhost service is used. */
var Search4PaperUI = {
  Zotero: window.arguments[0].Zotero,
  papers: [], candidates: [], selected: new Set(), active: null,
  page: 0, pageSize: 100, busy: false, controller: null, query: null, loadedYear: null, filtersDirty: true,
  labels: { title: "标题", abstract: "摘要", keywords: "关键词", tldr: "TLDR" },
  $(id) { return document.getElementById(id); },
  status(text, error = false) {
    this.$("status").textContent = text;
    this.$("status").classList.toggle("error", error);
  },
  controls() {
    for (const element of document.querySelectorAll("input, select, button")) element.disabled = this.busy;
    this.$("cancel").disabled = !this.busy;
    if (this.busy) return;
    this.$("filter").disabled = !this.papers.length;
    this.$("import").disabled = !this.selected.size || this.filtersDirty;
    this.$("select-page").disabled = !this.candidates.length;
    this.$("clear").disabled = !this.selected.size;
    this.$("previous").disabled = this.page === 0;
    this.$("next").disabled = (this.page + 1) * this.pageSize >= this.candidates.length;
    this.$("source").disabled = !this.active;
    this.$("pdf-link").disabled = !this.active?.pdfURL;
  },
  async operation(action) {
    if (this.busy) return;
    this.busy = true;
    this.controller = new AbortController();
    this.controls();
    try { await action(this.controller.signal); }
    catch (error) {
      if (!window.closed) this.status(this.controller.signal.aborted ? "操作已取消。" : error.message, !this.controller.signal.aborted);
      if (!this.controller.signal.aborted) this.Zotero.logError(error);
    }
    finally {
      this.busy = false;
      this.controller = null;
      if (!window.closed) this.controls();
    }
  },
  readQuery() {
    const query = { terms: Search4PaperCore.splitTerms(this.$("terms").value),
      context: Search4PaperCore.splitTerms(this.$("context").value),
      fields: Search4PaperCore.FIELDS.filter(field => this.$("field-" + field).checked) };
    Search4PaperCore.validateQuery(query);
    return query;
  },
  async request(url, signal) {
    signal.throwIfAborted();
    let cancel;
    const abort = () => cancel?.();
    signal.addEventListener("abort", abort, { once: true });
    try {
      const response = await this.Zotero.HTTP.request("GET", url, {
        responseType: "json", timeout: 60000, errorDelayMax: 0, noRetryOnThrottle: true,
        // A live ICML page at offset 5000 returned a cached empty response;
        // Cache-Control: no-cache returned the complete page and correct total.
        noCache: true, headers: { "Cache-Control": "no-cache" },
        cancellerReceiver(fn) { cancel = fn; if (signal.aborted) fn(); }
      });
      return response.response;
    }
    catch (error) {
      signal.throwIfAborted();
      throw new Error(`OpenReview 请求失败${error.status ? `（HTTP ${error.status}）` : ""}：${error.message}`);
    }
    finally { signal.removeEventListener("abort", abort); }
  },
  async fetchPapers(signal, refresh = false) {
    const query = this.readQuery();
    const year = Number(this.$("year").value);
    Search4PaperCore.validateYear(year);
    const path = PathUtils.join(this.Zotero.DataDirectory.dir, "search4paper", "ICML", `${year}.json`);
    this.papers = []; this.loadedYear = null; this.query = null;
    this.candidates = []; this.selected.clear(); this.active = null; this.page = 0;
    this.render(); this.preview(null);
    this.$("coverage").textContent = `正在读取 ICML ${year} 的公开录用名单…`;
    this.status("正在读取本地元数据…");
    let metadata, local = false;
    try {
      if (!refresh && await IOUtils.exists(path)) {
        local = true;
        try { metadata = Search4PaperCore.validateMetadata(await IOUtils.readJSON(path), year); }
        catch (error) { throw new Error(`读取本地元数据失败：${error.message}\n${path}\n请点击“刷新名单”重新获取。`); }
      }
      else {
        this.status("正在连接 OpenReview…");
        const papers = await Search4PaperCore.fetchAccepted(year, {
          signal, request: (url, requestSignal) => this.request(url, requestSignal),
          onProgress: (done, total) => this.status(`获取 ICML ${year}：${done} / ${total} 篇`)
        });
        metadata = { schemaVersion: 1, venueID: `ICML.cc/${year}/Conference`, year,
          fetchedAt: new Date().toISOString(), paperCount: papers.length, papers };
        signal.throwIfAborted();
        this.status(`正在保存 ${papers.length} 篇论文的本地元数据…`);
        try {
          await IOUtils.makeDirectory(PathUtils.parent(path), { createAncestors: true });
          signal.throwIfAborted();
          // Replace the previous complete list only after the new JSON is written.
          await IOUtils.writeJSON(path, metadata, { tmpPath: `${path}.tmp` });
        }
        catch (error) {
          signal.throwIfAborted();
          throw new Error(`保存本地元数据失败：${error.message}\n${path}`);
        }
      }
      signal.throwIfAborted();
    }
    catch (error) {
      this.$("coverage").textContent = "本次未加载完整名单；可点击“检索 ICML 论文”读取已有本地数据。";
      throw error;
    }
    this.papers = metadata.papers;
    this.loadedYear = year;
    this.$("coverage").textContent = `ICML ${year} · OpenReview 公开录用论文 ${this.papers.length} 篇 · 缺少摘要 ${this.papers.filter(p => !p.abstract.trim()).length} 篇 · ${local ? "本地名单" : "已保存到本地"} · 获取时间 ${new Date(metadata.fetchedAt).toLocaleString()}`;
    await this.filterPapers(signal, query);
  },
  async filterPapers(signal, query = this.readQuery()) {
    if (Number(this.$("year").value) !== this.loadedYear) throw new Error("年份已更改，请点击“检索 ICML 论文”获取该年的名单。");
    const candidates = [];
    for (let i = 0; i < this.papers.length; i++) {
      if (i % 100 === 0) {
        this.status(`筛选论文：${i} / ${this.papers.length}`);
        await new Promise(resolve => window.setTimeout(resolve, 0));
        signal.throwIfAborted();
      }
      const matched = Search4PaperCore.matchPaper(this.papers[i], query);
      if (matched) candidates.push(matched);
    }
    this.query = query; this.candidates = candidates; this.page = 0; this.selected.clear(); this.filtersDirty = false;
    this.render(); this.preview(candidates[0] || null);
    this.status(`找到 ${candidates.length} 篇候选论文。请查看摘要与命中依据，再勾选导入。`);
  },
  render() {
    const tbody = this.$("results"); tbody.replaceChildren();
    for (const paper of this.candidates.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)) {
      const tr = document.createElementNS("http://www.w3.org/1999/xhtml", "tr"); tr.dataset.id = paper.id;
      tr.classList.toggle("active", this.active?.id === paper.id);
      const choice = document.createElementNS("http://www.w3.org/1999/xhtml", "td"), text = document.createElementNS("http://www.w3.org/1999/xhtml", "td");
      const checkbox = document.createElementNS("http://www.w3.org/1999/xhtml", "input"); checkbox.type = "checkbox";
      checkbox.checked = this.selected.has(paper.id); checkbox.setAttribute("aria-label", `选择 ${paper.title}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) this.selected.add(paper.id); else this.selected.delete(paper.id);
        this.selection();
      });
      const title = document.createElementNS("http://www.w3.org/1999/xhtml", "button"); title.className = "paper-button"; title.textContent = paper.title;
      title.addEventListener("click", () => this.preview(paper));
      const fields = document.createElementNS("http://www.w3.org/1999/xhtml", "span"); fields.className = "match-fields";
      fields.textContent = [...new Set(paper.evidence.map(hit => this.labels[hit.field]))].join(" · ");
      choice.append(checkbox); text.append(title, fields); tr.append(choice, text); tbody.append(tr);
    }
    this.$("count").textContent = `候选论文 ${this.candidates.length} 篇`;
    this.$("empty").hidden = this.candidates.length > 0;
    this.$("empty").textContent = this.papers.length ? "没有匹配的论文。可以调整词组或扩大搜索字段。" : "检索后在这里预览候选论文；点击标题查看摘要与命中依据。";
    this.$("page").textContent = `${this.candidates.length ? this.page + 1 : 0} / ${Math.ceil(this.candidates.length / this.pageSize)}`;
    this.selection();
  },
  selection() {
    this.$("selection").textContent = `已选 ${this.selected.size} 篇`;
    this.controls();
  },
  preview(paper) {
    this.active = paper;
    this.$("paper-title").textContent = paper?.title || "论文预览";
    this.$("authors").textContent = paper ? paper.authors.join("; ") : "";
    this.$("abstract").textContent = paper ? paper.abstract || "来源未提供摘要。" : "选择一篇候选论文。";
    this.$("evidence").replaceChildren();
    for (const hit of paper?.evidence || []) {
      const li = document.createElementNS("http://www.w3.org/1999/xhtml", "li"), label = document.createElementNS("http://www.w3.org/1999/xhtml", "strong");
      label.textContent = `${this.labels[hit.field]} · ${hit.term}：`;
      li.append(label, document.createTextNode(hit.text)); this.$("evidence").append(li);
    }
    for (const row of this.$("results").children) row.classList.toggle("active", row.dataset.id === paper?.id);
    this.controls();
  },
  refreshCollections() {
    const select = this.$("collection"), current = select.value;
    select.replaceChildren(new Option("我的文献库（根目录）", ""));
    for (const collection of this.Zotero.Collections.getByLibrary(this.Zotero.Libraries.userLibraryID, true)) {
      const path = [collection.name];
      let parent = collection.parentID;
      while (parent) { const c = this.Zotero.Collections.get(parent); path.unshift(c.name); parent = c.parentID; }
      select.add(new Option(path.join(" / "), String(collection.id)));
    }
    select.value = current;
    if (select.selectedIndex < 0) select.value = "";
  },
  async importPapers(signal) {
    const papers = this.candidates.filter(p => this.selected.has(p.id));
    if (!papers.length) throw new Error("请先选择论文。");
    if (this.filtersDirty) throw new Error("检索条件已修改，请先应用筛选。");
    this.status("正在检查文献库中的已有条目…");
    const outcome = await Search4PaperImport.papers(this.Zotero, papers, {
      parentID: Number(this.$("collection").value) || null, name: this.$("new-collection").value,
      tags: Search4PaperCore.splitTerms(this.$("tags").value), query: this.query, signal,
      onProgress: (done, total, title) => this.status(`正在导入 ${done + 1} / ${total}：${title}`)
    });
    this.lastImport = outcome;
    const count = status => outcome.results.filter(result => result.status === status).length;
    let summary = `导入结果：新增 ${count("created")}，已有 ${count("existing")}，失败 ${count("failed")}，未处理 ${outcome.unprocessed}。`;
    this.refreshCollections();
    if (outcome.collectionID) this.$("collection").value = String(outcome.collectionID);
    this.$("new-collection").value = "";
    const failed = outcome.results.find(result => result.error);
    if (failed) summary += `\n${failed.paper.title}：${failed.error}`;
    if (this.$("include-pdf").checked && !signal.aborted && !failed) {
      this.status(summary + " 正在调用 Zotero 获取 PDF…");
      const pdfs = await Search4PaperImport.findPDFs(this.Zotero, outcome.results, { signal,
        onProgress: (done, total, title) => this.status(summary + `\nZotero 获取 PDF ${done} / ${total}：${title}。取消会停止后续论文。`) });
      this.lastPDFs = pdfs;
      summary += ` PDF 附件 ${pdfs.filter(pdf => pdf.hasPDF).length} / ${outcome.results.length}；未取得全文的条目已保留。`;
      for (const pdf of pdfs.filter(pdf => !pdf.hasPDF)) summary += `\n${pdf.title}：${pdf.error || "Zotero 未取得可用 PDF"}`;
    }
    if (signal.aborted) summary += " 已取消后续处理。";
    this.status(summary, Boolean(failed));
  },
  init() {
    for (const input of document.querySelectorAll(".query input, .filter input")) {
      input.addEventListener("input", () => {
        this.filtersDirty = true; this.controls();
        this.status("检索条件已修改，请应用筛选；更换年份后请重新检索。");
      });
    }
    this.$("fetch").addEventListener("click", () => this.operation(signal => this.fetchPapers(signal)));
    this.$("refresh-papers").addEventListener("click", () => this.operation(signal => this.fetchPapers(signal, true)));
    this.$("filter").addEventListener("click", () => this.operation(signal => this.filterPapers(signal)));
    this.$("import").addEventListener("click", () => this.operation(signal => this.importPapers(signal)));
    this.$("cancel").addEventListener("click", () => this.controller?.abort());
    this.$("refresh").addEventListener("click", () => this.refreshCollections());
    this.$("select-page").addEventListener("click", () => {
      for (const paper of this.candidates.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)) this.selected.add(paper.id);
      this.render();
    });
    this.$("clear").addEventListener("click", () => { this.selected.clear(); this.render(); });
    this.$("previous").addEventListener("click", () => { this.page--; this.render(); });
    this.$("next").addEventListener("click", () => { this.page++; this.render(); });
    this.$("source").addEventListener("click", () => this.Zotero.launchURL(this.active.url));
    this.$("pdf-link").addEventListener("click", () => this.Zotero.launchURL(this.active.pdfURL));
    window.addEventListener("unload", () => this.controller?.abort());
    this.refreshCollections();
    const selected = this.Zotero.getActiveZoteroPane()?.getSelectedCollections() || [];
    if (selected.length === 1 && selected[0].libraryID === this.Zotero.Libraries.userLibraryID) this.$("collection").value = String(selected[0].id);
    this.controls();
  }
};
window.addEventListener("DOMContentLoaded", () => Search4PaperUI.init(), { once: true });
