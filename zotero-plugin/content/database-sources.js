/* Four verified research-track sources. Runs entirely inside Zotero/JavaScript. */
var Search4PaperDatabaseSources = (() => {
  const configs = Search4PaperCore.DATABASE_SOURCES;
  const text = node => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const html = raw => new DOMParser().parseFromString(raw, "text/html");
  const value = (content, key) => content[key]?.value ?? content[key] ?? "";
  const titleKey = title => encodeURIComponent(title.normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim());
  function configFor(conference, year) {
    const config = configs[conference]?.[year];
    if (!config) throw new Error(`${conference} ${year} 尚未核实官方 Research Track 来源；当前已核实年份：2025。`);
    return config;
  }
  function paper(conference, year, fields) {
    const config = configFor(conference, year);
    if (!fields.title?.trim() || !fields.authors?.length || fields.authors.some(a => !a.trim())) {
      throw new Error(`${conference} 研究论文缺少标题或作者，不能使用不完整名单。`);
    }
    return { source: conference.toLowerCase(), year, venueID: Search4PaperCore.venueID(conference, year),
      id: `${year}:${titleKey(fields.title)}`, number: null, sourceURL: config.sourceURL, sourceMetadata: config,
      abstract: "", keywords: "", tldr: "", doi: "", pdfURL: "", bibtex: "", detailURL: "",
      url: config.sourceURL, track: "Research Papers", ...fields };
  }
  function merge(papers, listField) {
    const seen = new Map();
    for (const p of papers) {
      const prior = seen.get(p.id);
      if (!prior) seen.set(p.id, p);
      else {
        if (prior.title !== p.title || prior.authors.join(";") !== p.authors.join(";")) {
          throw new Error("重复论文身份对应的标题或作者不一致，请核对官方名单。");
        }
        if (listField) prior[listField] = [...new Set([...prior[listField], ...p[listField]])];
      }
    }
    return [...seen.values()];
  }
  // Split before stripping affiliations: commas/semicolons inside parentheses are not authors.
  function affiliatedAuthors(raw) {
    const authors = []; let depth = 0, name = "";
    for (const char of raw) {
      if (char === "(") depth++;
      else if (char === ")") { if (!depth) throw new Error("作者单位括号不匹配。"); depth--; }
      else if (!depth && char === ";") { authors.push(name.replace(/\*/g, "").trim()); name = ""; }
      else if (!depth) name += char;
    }
    if (depth) throw new Error("作者单位括号不完整。");
    authors.push(name.replace(/\*/g, "").trim());
    if (authors.some(a => !a)) throw new Error("官方名单包含空作者。");
    return [...new Set(authors)];
  }
  function links(node, sourceURL) {
    let detailURL = "", doi = "", pdfURL = "";
    for (const a of node.querySelectorAll("a[href]")) {
      const url = new URL(a.getAttribute("href"), sourceURL);
      if (!["http:", "https:"].includes(url.protocol)) continue;
      const match = url.href.match(/(?:doi\.org\/|dl\.acm\.org\/doi\/(?:abs\/|full\/|pdf\/)?)?(10\.\d{4,9}\/[^?#\s]+)/i);
      if (match) { doi = decodeURIComponent(match[1]); detailURL = url.href; }
      else if (!detailURL) detailURL = url.href;
      if (/\.pdf(?:$|\?)/i.test(url.href) || /\/doi\/pdf\//.test(url.pathname)) pdfURL = url.href;
    }
    return { detailURL, doi, pdfURL, ...(detailURL ? { url: detailURL } : {}) };
  }
  function parseSIGMOD(raw, year) {
    const config = configFor("SIGMOD", year), doc = html(raw), papers = [];
    if (!text(doc.querySelector("title")).includes(String(year)) || !text(doc.body).includes("Accepted Papers for SIGMOD")) throw new Error("SIGMOD 页面会议/年份不符。");
    const rounds = [...doc.querySelectorAll('h3[id^="round"]')];
    if (rounds.length !== config.rounds.length || config.rounds.some(id => !rounds.some(h => h.id === id))) throw new Error("SIGMOD 录用轮次不完整。");
    for (const heading of rounds) {
      const list = heading.nextElementSibling;
      if (list?.localName !== "ul" || !list.children.length) throw new Error("SIGMOD 轮次缺少论文列表。");
      for (const li of list.children) {
        const title = li.querySelector("b"), br = li.querySelector("br");
        if (li.localName !== "li" || !title || !br) throw new Error("SIGMOD 论文结构无法识别。");
        let authorText = ""; for (let n = br.nextSibling; n; n = n.nextSibling) authorText += n.textContent;
        papers.push(paper("SIGMOD", year, { title: text(title), authors: affiliatedAuthors(authorText),
          rounds: [heading.id], ...links(title, config.sourceURL) }));
      }
    }
    return merge(papers, "rounds");
  }
  function parseSIGIR(raw, year) {
    const config = configFor("SIGIR", year), doc = html(raw), papers = [];
    if (!text(doc.querySelector("title")).includes(`SIGIR ${year}`)) throw new Error("SIGIR 页面年份不符。");
    for (const track of config.tracks) {
      const heading = doc.getElementById(track), list = heading?.nextElementSibling;
      if (heading?.localName !== "h2" || list?.localName !== "ul" || !list.children.length) throw new Error(`SIGIR 缺少 ${track} 研究论文列表。`);
      for (const li of list.children) {
        if (!li.matches("li.accepted-paper-item")) throw new Error("SIGIR 研究论文结构无法识别。");
        const title = li.querySelector(".accepted-paper-title");
        papers.push(paper("SIGIR", year, { title: text(title), authors: text(li.querySelector(".accepted-paper-author")).split(/\s*,\s*/),
          track: track === "full-papers" ? "Full Papers" : "Short Papers", ...links(title, config.sourceURL) }));
      }
    }
    return merge(papers);
  }
  function parseVLDB(raw, year) {
    const config = configFor("VLDB", year), doc = html(raw);
    const data = JSON.parse(doc.getElementById("__NEXT_DATA__")?.textContent || "null")?.props?.pageProps;
    if (Number(data?.volume) !== config.volume || !data?.groupedIssues) throw new Error("PVLDB 卷或官方结构无法识别。");
    const papers = [];
    for (const issue of config.issues) {
      const records = data.groupedIssues[issue];
      if (!Array.isArray(records) || !records.length) throw new Error(`PVLDB 缺少第 ${issue} 期，未返回部分名单。`);
      let count = 0;
      for (const record of records) {
        if (record.title === "Front Matter") continue;
        if (record.issue !== issue || !Number.isInteger(record.start_page) || !Number.isInteger(record.end_page)
          || record.end_page < record.start_page || !/^https:\/\/www\.vldb\.org\/pvldb\/vol18\/p\d+-[^/]+\.pdf$/.test(record.pdf || "")) {
          throw new Error("PVLDB 论文期号、页码或正式 PDF 链接异常。");
        }
        papers.push(paper("VLDB", year, { id: `${year}:${record.pdf.split("/").pop()}`, title: record.title,
          authors: record.authors.split(/\s*,\s*/), volume: config.volume, issue,
          track: "Research Track (PVLDB 18, issues 1–11)", publicationTitle: "Proceedings of the VLDB Endowment",
          pages: `${record.start_page}-${record.end_page}`, url: record.pdf, pdfURL: record.pdf,
          abstract: record.abstract || "", doi: record.doi || "", date: record.date || "" }));
        count++;
      }
      if (!count) throw new Error(`PVLDB 第 ${issue} 期没有研究论文。`);
    }
    return merge(papers);
  }
  function normalizeKDD(note, year, cycleIndex) {
    const config = configFor("KDD", year), cycle = config.cycles[cycleIndex], content = note.content || {};
    if (!note.id || !note.invitations?.includes(`${cycle}/-/Submission`)
        || value(content, "venue") !== config.acceptedVenues[cycleIndex]) throw new Error("OpenReview 返回了非 KDD 已录用 Research Track 论文。");
    const keywords = value(content, "keywords");
    const pdf = value(content, "pdf");
    return paper("KDD", year, { id: `${year}:${note.id}`, title: value(content, "title"), authors: value(content, "authors"),
      abstract: value(content, "abstract"), keywords: Array.isArray(keywords) ? keywords.join("; ") : keywords,
      tldr: value(content, "TLDR") || value(content, "TL;DR") || value(content, "tldr"), doi: value(content, "doi"),
      pdfURL: pdf ? new URL(pdf, "https://openreview.net").href : "", url: `https://openreview.net/forum?id=${encodeURIComponent(note.id)}`,
      cycles: [cycle], track: "Research Track", presentation: value(content, "presentation"),
      acceptedVenue: value(content, "venue"), openReviewVenueID: value(content, "venueid"), retrievalSource: "OpenReview" });
  }
  async function fetchKDDOpenReview(year, { request, signal, onProgress = () => {} }) {
    const config = configFor("KDD", year), papers = [];
    for (let cycleIndex = 0; cycleIndex < config.cycles.length; cycleIndex++) {
      const cycle = config.cycles[cycleIndex], seen = new Set(); let offset = 0, count;
      do {
        signal?.throwIfAborted();
        const params = new URLSearchParams({ invitation: `${cycle}/-/Submission`, "content.venue": config.acceptedVenues[cycleIndex],
          limit: "1000", offset: String(offset), sort: "tmdate:asc" });
        let payload;
        try { payload = await request(`https://api2.openreview.net/notes?${params}`, signal); }
        catch (error) { signal?.throwIfAborted(); error.openReviewUnavailable = true; throw error; }
        if (!Array.isArray(payload.notes) || !Number.isInteger(payload.count) || payload.count < 0) throw new Error("KDD OpenReview 响应格式无效。");
        if (payload.count === 0) { const error = new Error("OpenReview 未公开完整录用名单"); error.openReviewUnavailable = true; throw error; }
        if ((count !== undefined && payload.count !== count) || !payload.notes.length) throw new Error("KDD OpenReview 分页不完整。");
        count = payload.count;
        for (const note of payload.notes) {
          if (seen.has(note.id)) throw new Error("KDD OpenReview 同一周期分页重复。");
          seen.add(note.id); papers.push(normalizeKDD(note, year, cycleIndex));
        }
        offset += payload.notes.length;
        if (offset > count) throw new Error("KDD OpenReview 分页总数不符。");
        onProgress(papers.length, null, cycle);
      } while (offset < count);
    }
    return merge(papers, "cycles");
  }
  function parseKDDOfficial(raw, year) {
    const config = configFor("KDD", year), doc = html(raw), papers = [];
    if (!text(doc.querySelector("title")).includes(`KDD ${year}`) || !text(doc.querySelector("h2")).includes("Research Track Papers")) throw new Error("KDD 官方页面不是所选年份 Research Track。");
    const tabs = doc.querySelector(".plethoraplugins-tabs-container");
    const headings = [...(tabs?.querySelectorAll(".plethoraplugins-tabs a") || [])];
    const panels = [...(tabs?.querySelector(".plethoraplugins-tabs--content")?.children || [])];
    if (headings.length !== 2 || panels.length !== 2 || new Set(headings.map(h => text(h))).size !== 2) throw new Error("KDD 官方周期列表不完整。");
    for (let i = 0; i < panels.length; i++) {
      const name = text(headings[i]), cycleIndex = name === "August Cycle" ? 0 : name === "February Cycle" ? 1 : -1;
      if (cycleIndex < 0) throw new Error("未知 KDD Research Track 周期。");
      const rows = [...panels[i].querySelectorAll("table tr")];
      if (!rows.length || rows.length % 2) throw new Error("KDD 官方论文标题/作者行不完整。");
      for (let j = 0; j < rows.length; j += 2) {
        const title = text(rows[j].querySelector("strong"));
        const doi = text(rows[j]).match(/DOI:\s*(?:https:\/\/doi.org\/)?(10\.\d{4,9}\/[^\s]+)/)?.[1] || "";
        papers.push(paper("KDD", year, { title, authors: affiliatedAuthors(text(rows[j + 1])), doi,
          ...(doi ? { url: `https://doi.org/${doi}`, detailURL: `https://doi.org/${doi}` } : {}),
          cycles: [config.cycles[cycleIndex]], retrievalSource: "Official Research Track List" }));
      }
    }
    return merge(papers, "cycles");
  }
  async function fetchAccepted(conference, year, settings) {
    const config = configFor(conference, year), { request, signal } = settings;
    signal?.throwIfAborted();
    let warning = "";
    if (conference === "KDD") {
      try { return await fetchKDDOpenReview(year, settings); }
      catch (error) {
        if (!error.openReviewUnavailable) throw error;
        signal?.throwIfAborted();
        warning = "OpenReview 不可用；本次使用 KDD 官方 Research Track 两周期名单（非 OpenReview 完整元数据）。";
      }
    }
    const raw = await request(config.sourceURL, signal, "text");
    signal?.throwIfAborted();
    const papers = ({ SIGMOD: parseSIGMOD, SIGIR: parseSIGIR, VLDB: parseVLDB, KDD: parseKDDOfficial })[conference](raw, year);
    if (!papers.length) throw new Error(`${conference} 官方研究论文名单为空，未保存。`);
    if (warning) for (const p of papers) p.retrievalWarning = warning;
    return papers;
  }
  return { fetchAccepted, parseSIGMOD, parseSIGIR, parseVLDB, parseKDDOfficial, normalizeKDD, fetchKDDOpenReview };
})();
