/* Runs only in the privileged plugin window; no localhost service is used. */
var Search4PaperUI = {
  Zotero: window.arguments[0].Zotero,
  ieee: window.arguments[0].ieeeSession,
  acm: window.arguments[0].acmSession,
  activePage: "search", operationPage: null,
  papers: [], candidates: [], selected: new Set(), active: null,
  page: 0, pageSize: 100, busy: false, controller: null, query: null, loadedTargets: [], coverageRows: [], filtersDirty: true,
  labels: { title: "标题", abstract: "摘要", keywords: "关键词", tldr: "TLDR" },
  $(id) { return document.getElementById(id); },
  selectedConferences() {
    const select = this.$("conference");
    const selected = [...(select.selectedOptions || [])].map(option => option.value).filter(Boolean);
    return selected.length ? selected : select.value ? [select.value] : [];
  },
  selectedYears() {
    const years = new Set();
    for (const part of String(this.$("year").value || "").split(/[;；,，\s]+/).filter(Boolean)) {
      const range = part.match(/^((?:19|20)\d{2})-((?:19|20)\d{2})$/);
      if (range) {
        const first = Number(range[1]), last = Number(range[2]);
        if (first > last || last - first > 10) throw new Error("年份范围必须递增且最多跨 10 年。");
        for (let year = first; year <= last; year++) years.add(year);
      }
      else {
        const year = Number(part); Search4PaperCore.validateYear(year); years.add(year);
      }
    }
    if (!years.size) throw new Error("请至少输入一个年份。");
    return [...years].sort((a, b) => a - b);
  },
  selectedTargets() {
    const conferences = this.selectedConferences();
    if (!conferences.length) throw new Error("请至少选择一个会议。");
    return conferences.flatMap(conference => this.selectedYears().map(year => ({ conference, year })));
  },
  targetKey(target) { return `${target.conference}:${target.year}`; },
  paperKey(paper) { return `${paper.venueID}:${paper.id}`; },
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
    const noConference = !this.selectedConferences().length;
    this.$("conference").disabled = noConference;
    this.$("fetch").disabled = this.$("refresh-papers").disabled = noConference;
    this.$("filter").disabled = noConference || !this.papers.length;
    this.$("import").disabled = !this.selected.size || this.filtersDirty;
    this.$("import").title = this.filtersDirty ? "检索条件已修改，请先搜索论文或应用筛选。"
      : !this.selected.size ? "请勾选论文左侧的复选框，或点击“选中本页”。" : "导入到所选文献库位置。";
    this.$("select-page").disabled = !this.candidates.length;
    this.$("clear").disabled = !this.selected.size;
    this.$("enrich-abstracts").disabled = !this.selected.size;
    this.$("previous").disabled = this.page === 0;
    this.$("next").disabled = (this.page + 1) * this.pageSize >= this.candidates.length;
    this.$("source").disabled = !this.active;
  },
  updateConferenceOptions() {
    const select = this.$("conference"), previous = new Set(this.selectedConferences());
    const names = Search4PaperCore.filterConferences({ field: this.$("venue-field").value,
      type: this.$("venue-type").value, rank: this.$("venue-rank").value });
    select.replaceChildren();
    for (const name of names) {
      const option = new Option(name, name); option.selected = previous.has(name); select.add(option);
    }
    if (!names.some(name => previous.has(name)) && names.length) {
      select.value = names[0]; select.options[0].selected = true;
    }
    if (!names.length) select.value = "";
    this.$("venue-summary").textContent = names.length
      ? `CCF 2026 · 当前可选 ${names.length} / ${Search4PaperCore.CONFERENCES.length} 个会议。三个目录条件同时满足；仅显示已支持来源。`
      : "当前组合暂无已支持来源。期刊和其他会议尚未接入，请调整目录筛选。";
    const changed = [...previous].sort().join("\n") !== this.selectedConferences().sort().join("\n");
    if (changed && this.loadedTargets.length) {
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
    const targets = this.selectedTargets();
    this.papers = []; this.loadedTargets = []; this.coverageRows = []; this.query = null;
    this.candidates = []; this.selected.clear(); this.active = null; this.page = 0;
    this.render(); this.preview(null);
    this.$("coverage").textContent = `正在读取 ${targets.length} 个会议/年份范围的公开录用名单…`;
    this.status("正在读取本地元数据…");
    const loaded = [];
    try {
      for (let index = 0; index < targets.length; index++) {
        const { conference, year } = targets[index], venueID = Search4PaperCore.venueID(conference, year);
        const source = Search4PaperCore.sourceName(conference);
        const path = PathUtils.join(this.Zotero.DataDirectory.dir, "search4paper", conference, `${year}.json`);
        let metadata, local = false;
        if (!refresh && await IOUtils.exists(path)) {
          local = true;
          try { metadata = Search4PaperCore.validateMetadata(await IOUtils.readJSON(path), year, conference); }
          catch (error) { throw new Error(`读取本地元数据失败：${error.message}\n${path}\n请点击“刷新名单”重新获取。`); }
        }
        else {
          this.status(`正在连接 ${source}（${index + 1} / ${targets.length}）…`);
          const papers = await Search4PaperSources.fetchAccepted(year, { conference, signal,
            request: (url, requestSignal, type) => this.request(url, requestSignal, type),
            onProgress: (done, total, detail = "") => this.status(`获取 ${conference} ${year}（${index + 1} / ${targets.length}）：${done}${total == null ? "" : ` / ${total}`} 篇${detail ? ` · ${detail}` : ""}`) });
          metadata = { schemaVersion: 1, venueID, year, fetchedAt: new Date().toISOString(), paperCount: papers.length, papers };
          signal.throwIfAborted();
          await IOUtils.makeDirectory(PathUtils.parent(path), { createAncestors: true });
          await IOUtils.writeJSON(path, metadata, { tmpPath: `${path}.tmp` });
        }
        loaded.push({ target: { conference, year }, metadata, local, source, path });
        signal.throwIfAborted();
      }
    }
    catch (error) {
      this.$("coverage").textContent = `本次未加载完整范围（已完成 ${loaded.length} / ${targets.length}）；未发布部分结果。`;
      throw error;
    }
    this.papers = loaded.flatMap(entry => entry.metadata.papers);
    this.loadedTargets = targets;
    this.coverageRows = loaded.map(entry => ({ ...entry.target, source: entry.source,
      paperCount: entry.metadata.paperCount,
      missingAbstracts: entry.metadata.papers.filter(paper => !paper.abstract.trim()).length,
      local: entry.local, fetchedAt: entry.metadata.fetchedAt,
      warning: entry.metadata.papers[0]?.retrievalWarning || "" }));
    this.$("coverage").textContent = `已加载 ${loaded.length} 个会议/年份范围、${this.papers.length} 篇论文；缺少摘要 ${this.coverageRows.reduce((sum, row) => sum + row.missingAbstracts, 0)} 篇。\n`
      + this.coverageRows.map(row => `${row.conference} ${row.year} · ${row.source} · ${row.paperCount} 篇 · 缺摘要 ${row.missingAbstracts} · ${row.local ? "本地名单" : "已保存到本地"}${row.warning ? ` · ${row.warning}` : ""}`).join("\n");
    await this.filterPapers(signal, query);
  },
  async filterPapers(signal, query = this.readQuery()) {
    const selected = this.selectedTargets().map(target => this.targetKey(target)).sort();
    const loaded = this.loadedTargets.map(target => this.targetKey(target)).sort();
    if (selected.join("\n") !== loaded.join("\n")) throw new Error("会议或年份已更改，请点击“搜索论文”获取对应名单。");
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
      const key = this.paperKey(paper);
      const tr = document.createElementNS("http://www.w3.org/1999/xhtml", "tr"); tr.dataset.id = key;
      tr.classList.toggle("active", this.active && this.paperKey(this.active) === key);
      const choice = document.createElementNS("http://www.w3.org/1999/xhtml", "td"), text = document.createElementNS("http://www.w3.org/1999/xhtml", "td");
      const checkbox = document.createElementNS("http://www.w3.org/1999/xhtml", "input"); checkbox.type = "checkbox";
      checkbox.checked = this.selected.has(key); checkbox.setAttribute("aria-label", `选择 ${paper.title}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) this.selected.add(key); else this.selected.delete(key);
        this.selection();
      });
      const title = document.createElementNS("http://www.w3.org/1999/xhtml", "button"); title.className = "paper-button"; title.textContent = paper.title;
      title.addEventListener("click", () => this.preview(paper));
      const fields = document.createElementNS("http://www.w3.org/1999/xhtml", "span"); fields.className = "match-fields";
      fields.textContent = `${paper.venueID.split("/")[0]} ${paper.year} · ` + [...new Set(paper.evidence.map(hit => this.labels[hit.field]))].join(" · ");
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
    for (const row of this.$("results").children) row.classList.toggle("active", paper && row.dataset.id === this.paperKey(paper));
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
    const papers = this.candidates.filter(p => this.selected.has(this.paperKey(p)));
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
  async enrichAbstracts(signal) {
    const papers = this.candidates.filter(paper => this.selected.has(this.paperKey(paper)) && !paper.abstract.trim());
    if (!papers.length) throw new Error("选中的论文均已有摘要。");
    const items = (await this.Zotero.Items.getAll(this.Zotero.Libraries.userLibraryID, true, false))
      .filter(item => item.isRegularItem() && !item.deleted);
    const results = [], changedTargets = new Set();
    for (const paper of papers) {
      if (signal.aborted) break;
      this.status(`补全摘要 ${results.length + 1} / ${papers.length}：${paper.title}`);
      try {
        const resolved = await Search4PaperAbstract.resolve(paper, {
          signal, request: (url, requestSignal, type) => this.request(url, requestSignal, type)
        });
        if (!resolved) throw new Error("未找到标题、作者和年份均严格匹配的摘要。");
        const sourcePaper = this.papers.find(candidate => this.paperKey(candidate) === this.paperKey(paper));
        if (!sourcePaper) throw new Error("当前候选已不属于已加载的会议/年份范围。");
        for (const target of [sourcePaper, paper]) {
          target.abstract = resolved.abstract;
          target.abstractSource = resolved.source;
          target.abstractSourceURL = resolved.sourceURL;
        }
        changedTargets.add(this.loadedTargets.find(target => Search4PaperCore.venueID(target.conference, target.year) === paper.venueID));
        const identities = Search4PaperCore.identities(paper), matches = items.filter(item => {
          const existing = Search4PaperCore.identities({ doi: item.getField("DOI"), url: item.getField("url"), extra: item.getField("extra") });
          return [...identities].some(identity => existing.has(identity));
        });
        if (matches.length > 1) throw new Error("Zotero 中存在多个相同来源标识的条目，摘要未写回条目。");
        let written = false;
        if (matches.length === 1 && !matches[0].getField("abstractNote").trim()) {
          matches[0].setField("abstractNote", paper.abstract); await matches[0].saveTx(); written = true;
        }
        results.push({ paper, source: resolved.source, written });
      }
      catch (error) { results.push({ paper, error: error.message }); }
    }
    for (const target of [...changedTargets].filter(Boolean)) {
      const venueID = Search4PaperCore.venueID(target.conference, target.year);
      const rows = this.papers.filter(paper => paper.venueID === venueID);
      const coverage = this.coverageRows.find(row => row.conference === target.conference && row.year === target.year);
      const path = PathUtils.join(this.Zotero.DataDirectory.dir, "search4paper", target.conference, `${target.year}.json`);
      await IOUtils.writeJSON(path, { schemaVersion: 1, venueID, year: target.year,
        fetchedAt: coverage.fetchedAt, paperCount: rows.length, papers: rows }, { tmpPath: `${path}.tmp` });
      coverage.missingAbstracts = rows.filter(paper => !paper.abstract.trim()).length;
    }
    await this.filterPapers(signal, this.query);
    const success = results.filter(result => !result.error).length;
    const written = results.filter(result => result.written).length;
    const failures = results.filter(result => result.error);
    this.status(`摘要补全：成功 ${success}，失败 ${failures.length}，写回已有 Zotero 条目 ${written}。${failures.length ? "\n" + failures.map(result => `${result.paper.title}：${result.error}`).join("\n") : ""}`, Boolean(failures.length));
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
        // Each stage is explicit so a publisher access failure cannot hide an
        // available author version, and Zotero's broad resolver remains last.
        let result, institutional = this.ieee.context && this.ieee.hasPaperURL(item.getField("url"));
        const errors = [];
        const remember = value => { if (value?.error) errors.push(value.error); return value; };
        const savedFullText = item.getField("extra").match(/^Full Text URL: (https?:\/\/\S+)\s*$/m)?.[1] || "";
        const paper = this.lastImport?.results.find(r => r.item?.id === item.id)?.paper || { title, pdfURL: savedFullText };
        const isHost = (value, hosts) => value && URL.canParse(value)
          && hosts.includes(new URL(value).hostname);
        const explicitOA = paper.pdfURL && !isHost(paper.pdfURL, ["openreview.net", "www.openreview.net",
          "arxiv.org", "www.arxiv.org", "dl.acm.org", "ieeexplore.ieee.org"]);
        if (explicitOA) {
          [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper }], {
            signal, direct: paper.pdfURL, directLabel: "开放出版商 PDF", native: false
          });
          remember(result);
        }

        if (!item.getField("DOI") && Search4PaperPublication.isWWW(item, paper) && !signal.aborted) {
          try {
            await Search4PaperPublication.resolveWWWDOI(item, paper, {
              signal, request: (url, requestSignal, type) => this.request(url, requestSignal, type)
            });
          }
          catch (error) { errors.push(error.message); }
        }

        if (!result?.hasPDF && !signal.aborted) {
          try {
            const arxiv = await Search4PaperArxiv.resolve(item, paper, {
              signal, request: (url, requestSignal, type) => this.request(url, requestSignal, type),
              wait: milliseconds => this.Zotero.Promise.delay(milliseconds)
            });
            if (arxiv) {
              [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper }], {
                signal, direct: arxiv.pdfURL, directLabel: `arXiv 最新版本（${arxiv.arxivID}）`, native: false
              });
              remember(result);
            }
          }
          catch (error) { errors.push(`arXiv 查询：${error.message}`); }
        }

        let openReviewURLs = [];
        const openReviewFallback = Search4PaperImport.openReviewPDFURL?.(item)
          || (isHost(paper.pdfURL, ["openreview.net", "www.openreview.net"]) ? paper.pdfURL : "");
        if (!result?.hasPDF && openReviewFallback && !signal.aborted) {
          try {
            openReviewURLs = await Search4PaperOpenReview.URLs(item, {
              signal, request: (url, requestSignal, type) => this.request(url, requestSignal, type)
            });
          }
          catch (error) { errors.push(`OpenReview 稳定地址解析：${error.message}`); }
          if (!openReviewURLs.length) openReviewURLs = [openReviewFallback];
          for (const url of [...new Set(openReviewURLs)]) {
            if (result?.hasPDF || signal.aborted) break;
            const stable = /^https:\/\/openreview\.net\/pdf\/[a-f0-9]{40}\.pdf$/i.test(url);
            [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper }], {
              signal, direct: url, directLabel: stable ? "OpenReview 稳定 PDF" : "OpenReview PDF", native: false
            });
            remember(result);
          }
        }

        const acm = this.acm.canHandle(item);
        if (acm && !result?.hasPDF && !signal.aborted) {
          result = await this.acm.download(item, { signal });
          remember(result);
          this.updateACMStatus();
        }

        const icde = /^Source ID: icde:/m.test(item.getField("extra"));
        let resolutionError = "";
        if (!result?.hasPDF && !signal.aborted && !this.ieee.hasPaperURL(item.getField("url")) && this.ieee.canResolve(item)) {
          this.status(`正在查找 IEEE 论文地址：${title}`);
          try {
            await this.ieee.resolvePaperURL(item, { signal });
            institutional = this.ieee.context && this.ieee.hasPaperURL(item.getField("url"));
          }
          catch (error) { resolutionError = error.message; }
        }
        if (institutional && !result?.hasPDF && !signal.aborted) {
          [result] = await this.ieee.download([item], { signal, onProgress: progress => {
            this.$("ieee-status").textContent = this.ieee.status;
            this.status(`获取全文 ${outcomes.length + 1} / ${items.length}：${title} · ${progress.phase === "waiting" ? "等待下载间隔" : progress.phase === "downloading" ? "开始下载 " + progress.startedAt : progress.hasPDF ? "附件已取得" : progress.error}`);
          } });
          remember(result);
        }
        if (!result?.hasPDF && !institutional && !signal.aborted && (icde || resolutionError || this.ieee.hasPaperURL(item.getField("url")))) {
          const reason = resolutionError || (this.ieee.hasPaperURL(item.getField("url"))
            ? "尚未启动 IEEE 机构登录；如需订阅访问，请先完成北航登录后重试。"
            : "未取得 IEEE 单篇论文地址；请核实条目元数据后重试。");
          errors.push(reason);
        }

        if (!result?.hasPDF && !signal.aborted) {
          [result] = await Search4PaperImport.findPDFs(this.Zotero, [{ item, paper: { title } }], {
            signal, direct: false, native: true
          });
          remember(result);
        }
        if (result && !result.hasPDF) result.error = [...new Set(errors)].join("；");
        if (result) outcomes.push(result);
      }
      catch (error) { outcomes.push({ itemID: item.id, title, hasPDF: false, error: error.message }); }
    }
    this.lastPDFs = outcomes;
    this.$("ieee-status").textContent = this.ieee.status;
    this.status(`全文获取：成功 ${outcomes.filter(r => r.hasPDF).length}，失败 ${outcomes.filter(r => !r.hasPDF).length}，未处理 ${items.length - outcomes.length}。文献条目已保留。${signal.aborted ? " 已取消后续处理。" : ""}\n`
      + outcomes.map(r => `${r.title}：${r.reused ? "复用已有 PDF" : r.hasPDF ? `附件已取得（${r.source || "来源未标记"}）` : r.error || "未取得可用全文"}${r.startedAt ? `（请求开始 ${r.startedAt}）` : ""}`).join("\n"));
  },
  updateACMStatus() {
    this.$("acm-access").hidden = !this.acm.verificationURL;
    this.$("acm-status").textContent = this.acm.status;
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
    this.updateACMStatus();
    this.$("acm-verify").addEventListener("click", () => this.operation(async () => {
      this.acm.verify();
      this.updateACMStatus();
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
    this.$("enrich-abstracts").addEventListener("click", () => this.operation(signal => this.enrichAbstracts(signal)));
    this.$("cancel").addEventListener("click", () => {
      if (this.operationPage === "search") this.controller?.abort();
    });
    this.$("refresh").addEventListener("click", () => this.refreshCollections());
    this.$("select-page").addEventListener("click", () => {
      for (const paper of this.candidates.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)) this.selected.add(this.paperKey(paper));
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
