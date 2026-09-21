/* Runs only in the privileged plugin window; no localhost service is used. */
var Search4PaperUI = {
  Zotero: window.arguments[0].Zotero,
  ieee: window.arguments[0].ieeeSession,
  activePage: "search", operationPage: null,
  papers: [], candidates: [], selected: new Set(), active: null,
  page: 0, pageSize: 100, busy: false, controller: null, query: null, loadedYear: null, loadedConference: null, filtersDirty: true,
  labels: { title: "标题", abstract: "摘要", keywords: "关键词", tldr: "TLDR" },
  $(id) { return document.getElementById(id); },
  switchPage(page) {
    this.activePage = page;
    for (const name of ["search", "fulltext"]) {
      this.$("page-" + name).hidden = name !== page;
      this.$("tab-" + name).setAttribute("aria-selected", String(name === page));
      this.$("tab-" + name).tabIndex = name === page ? 0 : -1;
    }
    this.$("page-title").textContent = page === "search" ? "论文检索" : "全文获取";
  },
  status(text, error = false) {
    const id = (this.operationPage || this.activePage) === "fulltext" ? "fulltext-status" : "status";
    this.$(id).textContent = text;
    this.$(id).classList.toggle("error", error);
  },
  controls() {
    for (const element of document.querySelectorAll("input, select, button")) element.disabled = this.busy;
    this.$("tab-search").disabled = this.$("tab-fulltext").disabled = false;
    this.$("cancel").disabled = !this.busy || this.operationPage !== "search";
    this.$("fulltext-cancel").disabled = !this.busy || this.operationPage !== "fulltext";
    if (this.busy) return;
    const noConference = !this.$("conference").value;
    this.$("conference").disabled = noConference;
    this.$("fetch").disabled = this.$("refresh-papers").disabled = noConference;
    this.$("filter").disabled = noConference || !this.papers.length;
    this.$("import").disabled = !this.selected.size || this.filtersDirty;
    this.$("import").title = this.filtersDirty ? "检索条件已修改，请先搜索论文或应用筛选。"
      : !this.selected.size ? "请勾选论文左侧的复选框，或点击“选中本页”。" : "导入到所选文献库位置。";
    this.$("select-page").disabled = !this.candidates.length;
    this.$("clear").disabled = !this.selected.size;
    this.$("previous").disabled = this.page === 0;
    this.$("next").disabled = (this.page + 1) * this.pageSize >= this.candidates.length;
    this.$("source").disabled = !this.active;
  },
  updateConferenceOptions() {
    const select = this.$("conference"), previous = select.value;
    const names = Search4PaperCore.filterConferences({ field: this.$("venue-field").value,
      type: this.$("venue-type").value, rank: this.$("venue-rank").value });
    select.replaceChildren();
    for (const name of names) select.add(new Option(name, name));
    select.value = names.includes(previous) ? previous : names[0] || "";
    this.$("venue-summary").textContent = names.length
      ? `CCF 2026 · 当前可选 ${names.length} / ${Search4PaperCore.CONFERENCES.length} 个会议。三个目录条件同时满足；仅显示已支持来源。`
      : "当前组合暂无已支持来源。期刊和其他会议尚未接入，请调整目录筛选。";
    if (previous !== select.value && this.loadedConference) {
      this.filtersDirty = true;
      this.status("会议选择已改变；已有结果保留，更换会议后请重新搜索。");
    }
    this.controls();
  },
  async operation(action) {
    if (this.busy) return;
    this.busy = true;
    this.operationPage = this.activePage;
    this.controller = new AbortController();
    this.controls();
    try { await action(this.controller.signal); }
    catch (error) {
      if (!window.closed) this.status(this.controller.signal.aborted ? "操作已取消。" : error.message, !this.controller.signal.aborted);
      if (!this.controller.signal.aborted) this.Zotero.logError(error);
    }
    finally {
      this.busy = false;
      this.operationPage = null;
      this.controller = null;
      if (!window.closed) this.controls();
    }
  },
  readQuery() {
    const query = { terms: Search4PaperCore.splitTerms(this.$("terms").value),
      operator: this.$("operator").value,
      fields: Search4PaperCore.FIELDS.filter(field => this.$("field-" + field).checked) };
    Search4PaperCore.validateQuery(query);
    return query;
  },
  updateQuerySummary() {
    const terms = Search4PaperCore.splitTerms(this.$("terms").value);
    const operator = this.$("operator").value;
    this.$("query-expression").textContent = terms.length
      ? terms.map(term => `“${term}”`).join(` ${operator} `)
      : "请填写至少一个关键词。";
    const fields = Search4PaperCore.FIELDS.filter(field => this.$("field-" + field).checked);
    this.$("field-rule").textContent = fields.length
      ? `查找位置：${fields.map(field => this.labels[field]).join(" / ")}。关键词可在任一勾选字段中命中，不同关键词可出现在不同字段。`
      : "请至少勾选一个查找位置。";
  },
  async request(url, signal, responseType = "json") {
    signal.throwIfAborted();
    let cancel;
    const abort = () => cancel?.();
    signal.addEventListener("abort", abort, { once: true });
    try {
      const response = await this.Zotero.HTTP.request("GET", url, {
        // Official proceedings XML/program files can exceed 10 MB on a slow connection.
        responseType, timeout: url.startsWith("https://api2.openreview.net/") ? 60000 : 300000,
        errorDelayMax: 0, noRetryOnThrottle: true,
        // A live ICML page at offset 5000 returned a cached empty response;
        // Cache-Control: no-cache returned the complete page and correct total.
        noCache: true, headers: { "Cache-Control": "no-cache" },
        cancellerReceiver(fn) { cancel = fn; if (signal.aborted) fn(); }
      });
      return response.response;
    }
    catch (error) {
      signal.throwIfAborted();
      throw new Error(`论文来源请求失败${error.status ? `（HTTP ${error.status}）` : ""}：${url}\n${error.status === 404 ? "该会议年份的官方数据尚不可用。" : error.message}`);
    }
    finally { signal.removeEventListener("abort", abort); }
  },
  async fetchPapers(signal, refresh = false) {
    const query = this.readQuery();
    const conference = this.$("conference").value;
    const year = Number(this.$("year").value);
    const venueID = Search4PaperCore.venueID(conference, year);
    const source = Search4PaperCore.sourceName(conference);
    const path = PathUtils.join(this.Zotero.DataDirectory.dir, "search4paper", conference, `${year}.json`);
    this.papers = []; this.loadedYear = null; this.loadedConference = null; this.query = null;
    this.candidates = []; this.selected.clear(); this.active = null; this.page = 0;
    this.render(); this.preview(null);
    this.$("coverage").textContent = `正在读取 ${conference} ${year} 的公开录用名单…`;
    this.status("正在读取本地元数据…");
    let metadata, local = false;
    try {
      if (!refresh && await IOUtils.exists(path)) {
        local = true;
        try { metadata = Search4PaperCore.validateMetadata(await IOUtils.readJSON(path), year, conference); }
        catch (error) { throw new Error(`读取本地元数据失败：${error.message}\n${path}\n请点击“刷新名单”重新获取。`); }
      }
      else {
        this.status(`正在连接 ${source}…`);
        const papers = await Search4PaperSources.fetchAccepted(year, {
          conference, signal, request: (url, requestSignal, type) => this.request(url, requestSignal, type),
          onProgress: (done, total, detail = "") => this.status(`获取 ${conference} ${year}：${done}${total == null ? "" : ` / ${total}`} 篇${detail ? ` · ${detail}` : ""}`)
        });
        metadata = { schemaVersion: 1, venueID, year,
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
      this.$("coverage").textContent = "本次未加载完整名单；可点击“搜索论文”读取已有本地数据。";
      throw error;
    }
    this.papers = metadata.papers;
    this.loadedYear = year;
    this.loadedConference = conference;
    const scope = (Search4PaperCore.DATABASE_SOURCES[conference] || Search4PaperCore.SYSTEMS_SOURCES[conference])
      ? [...new Set(this.papers.map(p => p.track))].join(" / ") : conference === "ACL" ? (year < 2020 ? "主会第 1 卷" : "主会 Long Papers") : conference === "EMNLP" ? (year < 2020 ? "主会第 1 卷" : "主会 main 卷")
      : conference === "ICDE" ? "Research Papers" : conference === "AAAI" ? "Technical Tracks" : "主会论文";
    this.$("coverage").textContent = `${conference} ${year} · ${source} · ${scope} ${this.papers.length} 篇 · 缺少摘要 ${this.papers.filter(p => !p.abstract.trim()).length} 篇 · ${local ? "本地名单" : "已保存到本地"} · 获取时间 ${new Date(metadata.fetchedAt).toLocaleString()}`;
    if (this.papers[0]?.retrievalWarning) this.$("coverage").textContent += ` · ${this.papers[0].retrievalWarning}`;
    await this.filterPapers(signal, query);
  },
  async filterPapers(signal, query = this.readQuery()) {
    if (this.$("conference").value !== this.loadedConference || Number(this.$("year").value) !== this.loadedYear) {
      throw new Error("会议或年份已更改，请点击“搜索论文”获取对应名单。");
    }
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
    this.status(`找到 ${candidates.length} 篇候选论文。请查看摘要与命中依据。`);
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
    if (signal.aborted) summary += " 已取消后续处理。";
    this.status(summary, Boolean(failed));
  },
  async getFullText(signal) {
    const items = [...(this.Zotero.getActiveZoteroPane()?.getSelectedItems() || [])];
    if (!items.length) throw new Error("请先在 Zotero 主窗口选择需要全文的文献条目。");
    const outcomes = [];
    for (const item of items) {
      if (signal.aborted) break;
      const title = item.getField("title");
      this.status(`获取全文 ${outcomes.length + 1} / ${items.length}：${title}`);
      try {
        if (!item.isRegularItem() || item.deleted || !this.Zotero.Libraries.get(item.libraryID).filesEditable) {
          throw new Error("请选择可添加附件的文献条目。");
        }
        // Use the existing institutional path only after the user explicitly starts login.
        let institutional = this.ieee.context && this.ieee.hasPaperURL(item.getField("url"));
        let result;
        const icde = /^Source ID: icde:/m.test(item.getField("extra"));
        if (!institutional || icde) {
          // ICDE is not assumed paywalled: try the existing direct/Zotero path first.
          const paper = this.lastImport?.results.find(r => r.item?.id === item.id)?.paper || { title };
          [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper }], { signal });
        }
        let resolutionError = "";
        if (!result?.hasPDF && !signal.aborted && !this.ieee.hasPaperURL(item.getField("url")) && this.ieee.canResolve(item)) {
          this.status(`正在查找 IEEE 论文地址：${title}`);
          try {
            await this.ieee.resolvePaperURL(item, { signal });
            institutional = this.ieee.context && this.ieee.hasPaperURL(item.getField("url"));
            if (!institutional && !signal.aborted) {
              // The new URL/DOI can also help Zotero find an accessible copy without login.
              [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper: { title } }], { signal });
            }
          }
          catch (error) { resolutionError = error.message; }
        }
        if (institutional && !result?.hasPDF && !signal.aborted) {
          [result] = await this.ieee.download([item], { signal, onProgress: progress => {
            this.$("ieee-status").textContent = this.ieee.status;
            this.status(`获取全文 ${outcomes.length + 1} / ${items.length}：${title} · ${progress.phase === "waiting" ? "等待下载间隔" : progress.phase === "downloading" ? "开始下载 " + progress.startedAt : progress.hasPDF ? "附件已取得" : progress.error}`);
          } });
        }
        if (result && !result.hasPDF && !institutional && !signal.aborted && (icde || resolutionError || this.ieee.hasPaperURL(item.getField("url")))) {
          const reason = resolutionError || (this.ieee.hasPaperURL(item.getField("url"))
            ? "尚未启动 IEEE 机构登录；如需订阅访问，请先完成北航登录后重试。"
            : "未取得 IEEE 单篇论文地址；请核实条目元数据后重试。");
          result.error = [result.error, reason].filter(Boolean).join("；");
        }
        if (result) outcomes.push(result);
      }
      catch (error) { outcomes.push({ itemID: item.id, title, hasPDF: false, error: error.message }); }
    }
    this.lastPDFs = outcomes;
    this.$("ieee-status").textContent = this.ieee.status;
    this.status(`全文获取：成功 ${outcomes.filter(r => r.hasPDF).length}，失败 ${outcomes.filter(r => !r.hasPDF).length}，未处理 ${items.length - outcomes.length}。文献条目已保留。${signal.aborted ? " 已取消后续处理。" : ""}\n`
      + outcomes.map(r => `${r.title}：${r.reused ? "复用已有 PDF" : r.hasPDF ? "附件已取得" : r.error || "未取得可用全文"}${r.startedAt ? `（请求开始 ${r.startedAt}）` : ""}`).join("\n"));
  },
  init() {
    for (const page of ["search", "fulltext"]) {
      const tab = this.$("tab-" + page);
      tab.addEventListener("click", () => this.switchPage(page));
      tab.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "search" : event.key === "End" ? "fulltext" : page === "search" ? "fulltext" : "search";
        this.switchPage(next); this.$("tab-" + next).focus();
      });
    }
    this.$("ieee-status").textContent = this.ieee.status;
    this.$("ieee-login").addEventListener("click", () => this.operation(async () => {
      this.ieee.login();
      this.$("ieee-status").textContent = this.ieee.status;
    }));
    this.$("get-fulltext").addEventListener("click", () => this.operation(signal => this.getFullText(signal)));
    this.$("fulltext-cancel").addEventListener("click", () => {
      if (this.operationPage === "fulltext") this.controller?.abort();
    });
    for (const field of new Set(Object.values(Search4PaperCore.CONFERENCE_CATEGORIES).map(venue => venue.field))) {
      this.$("venue-field").add(new Option(field, field));
    }
    for (const id of ["venue-field", "venue-type", "venue-rank"]) {
      this.$(id).addEventListener("change", () => this.updateConferenceOptions());
    }
    this.updateConferenceOptions();
    for (const input of document.querySelectorAll(".query input, .query select, .filter input")) {
      input.addEventListener("input", () => {
        this.updateQuerySummary();
        this.filtersDirty = true; this.controls();
        this.status("检索条件已修改，请应用筛选；更换会议或年份后请重新搜索。");
      });
    }
    this.$("fetch").addEventListener("click", () => this.operation(signal => this.fetchPapers(signal)));
    this.$("refresh-papers").addEventListener("click", () => this.operation(signal => this.fetchPapers(signal, true)));
    this.$("filter").addEventListener("click", () => this.operation(signal => this.filterPapers(signal)));
    this.$("import").addEventListener("click", () => this.operation(signal => this.importPapers(signal)));
    this.$("cancel").addEventListener("click", () => {
      if (this.operationPage === "search") this.controller?.abort();
    });
    this.$("refresh").addEventListener("click", () => this.refreshCollections());
    this.$("select-page").addEventListener("click", () => {
      for (const paper of this.candidates.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)) this.selected.add(paper.id);
      this.render();
    });
    this.$("clear").addEventListener("click", () => { this.selected.clear(); this.render(); });
    this.$("previous").addEventListener("click", () => { this.page--; this.render(); });
    this.$("next").addEventListener("click", () => { this.page++; this.render(); });
    this.$("source").addEventListener("click", () => this.Zotero.launchURL(this.active.url));
    window.addEventListener("unload", () => this.controller?.abort());
    this.refreshCollections();
    const selected = this.Zotero.getActiveZoteroPane()?.getSelectedCollections() || [];
    if (selected.length === 1 && selected[0].libraryID === this.Zotero.Libraries.userLibraryID) this.$("collection").value = String(selected[0].id);
    this.updateQuerySummary();
    this.controls();
  }
};
window.addEventListener("DOMContentLoaded", () => Search4PaperUI.init(), { once: true });
