/* Verified official systems/security/software-engineering paper lists. No PDF requests. */
var Search4PaperSystemsSources = (() => {
  const configs = Search4PaperCore.SYSTEMS_SOURCES;
  const text = node => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const html = raw => new DOMParser().parseFromString(raw, "text/html");
  const key = value => value.normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
  function configFor(conference, year) {
    const config = configs[conference]?.[year];
    if (!config) throw new Error(`${conference} ${year} 官方论文来源尚未核实；当前支持 2025。`);
    return config;
  }
  function paper(conference, year, fields, provenance) {
    const config = configFor(conference, year);
    if (!fields.title || !fields.authors?.length || fields.authors.some(a => !a?.trim())) throw new Error(`${conference} 论文标题或作者不完整。`);
    return { source: conference.toLowerCase().replace(/ /g, "-"), year,
      venueID: Search4PaperCore.venueID(conference, year), id: `${year}:${encodeURIComponent(key(fields.title))}`,
      number: null, abstract: "", keywords: "", tldr: "", doi: "", pdfURL: "", bibtex: "", detailURL: "",
      url: config.sourceURL, sourceURL: config.sourceURL, sourceMetadata: provenance,
      track: "Research Papers", ...fields };
  }
  function merge(papers) {
    const seen = new Map();
    for (const p of papers) {
      const prior = seen.get(p.id);
      if (!prior) seen.set(p.id, p);
      else {
        if (key(prior.title) !== key(p.title) || prior.authors.join(";") !== p.authors.join(";")) throw new Error("重复论文的标题/作者不一致。");
        if (p.cycles) prior.cycles = [...new Set([...(prior.cycles || []), ...p.cycles])];
      }
    }
    if (!seen.size) throw new Error("官方研究论文名单为空或结构无法识别，未保存。");
    return [...seen.values()];
  }
  function authors(raw) {
    let depth = 0, clean = "";
    for (const c of raw) {
      if (c === "(") depth++;
      else if (c === ")") { if (!depth) throw new Error("作者单位括号不匹配。"); depth--; }
      else if (!depth) clean += c;
    }
    if (depth) throw new Error("作者单位括号不完整。");
    return clean.split(/\s*(?:,|;|\band\b)\s*/).map(a => a.trim()).filter(Boolean);
  }
  function exposedLinks(node, base) {
    let doi = "", detailURL = "", pdfURL = "";
    for (const a of node.querySelectorAll("a[href]")) {
      const url = new URL(a.getAttribute("href").trim(), base);
      if (!["https:", "http:"].includes(url.protocol)) continue;
      const match = url.href.match(/(?:doi\.org\/|dl\.acm\.org\/doi\/(?:abs\/|full\/|pdf\/)?)(10\.\d{4,9}\/[^?#\s]+)/);
      if (match) { doi = decodeURIComponent(match[1]); detailURL = url.href; }
      if (/\.pdf(?:$|\?)|\/doi\/pdf\/|arxiv\.org\/pdf\//.test(url.href)) pdfURL = url.href;
    }
    return { doi, detailURL, pdfURL, ...(detailURL ? { url: detailURL } : {}) };
  }
  function parseUSENIX(raw, conference, year, sourceURL, provenance) {
    const config = configFor(conference, year), doc = html(raw), base = config.sourceURL.replace(/technical-sessions$/, "");
    if (!text(doc.querySelector("title")).includes(`'${String(year).slice(-2)}`)) throw new Error("USENIX 页面年份无法核实。");
    const papers = [];
    for (const article of doc.querySelectorAll("article.node-paper")) {
      // Invited/keynote/award presentations use presented-by, not paper-people-text.
      const people = article.querySelector(".field-name-field-paper-people-text");
      if (!people) continue;
      const a = article.querySelector("h2 a[href]"), title = text(a);
      const url = a && new URL(a.getAttribute("href"), sourceURL).href;
      if (!url?.startsWith(base + "presentation/")) throw new Error("USENIX 论文详情不属于所选会议年份。");
      const names = (people.querySelector("p") || people).cloneNode(true);
      for (const em of names.querySelectorAll("em")) em.remove();
      papers.push(paper(conference, year, { title, authors: authors(text(names)), id: `${year}:${new URL(url).pathname}`,
        url, detailURL: url, abstract: text(article.querySelector('.field-name-field-paper-description-long, .field-name-field-paper-description')),
        ...(provenance.mode === "accepted-cycles" ? { cycles: [sourceURL], retrievalWarning: "仅官方已公布的周期录用名单，非最终完整 proceedings。" } : {}) }, provenance));
    }
    return merge(papers);
  }
  function completeUSENIX(p, raw) {
    const doc = html(raw), metas = name => [...doc.querySelectorAll(`meta[name="${name}"]`)].map(m => m.getAttribute("content").trim());
    const title = metas("citation_title")[0] || text(doc.querySelector("h1#page-title"));
    const authorList = metas("citation_author");
    if (!title || key(title.replace(/[{}]/g, "")) !== key(p.title)) throw new Error(`USENIX 论文详情元数据不符：${p.title}`);
    const date = metas("citation_publication_date")[0] || "";
    if (date && !date.startsWith(String(p.year))) throw new Error("USENIX 论文出版年份不符。");
    const first = metas("citation_firstpage")[0], last = metas("citation_lastpage")[0];
    const paperPDF = [...doc.querySelectorAll('.field-name-field-final-paper-pdf a[href]')].find(a => /\.pdf(?:$|\?)/i.test(a.href || a.getAttribute("href")) && !/appendix|slides|prepublication/i.test(text(a)));
    return { ...p, authors: authorList.length ? authorList : p.authors, date, publicationTitle: metas("citation_conference_title")[0] || "",
      doi: metas("citation_doi")[0] || "", pdfURL: metas("citation_pdf_url")[0] || (paperPDF ? new URL(paperPDF.getAttribute("href"), p.detailURL).href : ""),
      pages: first ? (last ? `${first}-${last}` : first) : "",
      abstract: p.abstract || text(doc.querySelector('.field-name-field-paper-description-long, .field-name-field-paper-description')) };
  }
  function securityCycleURLs(raw, year) {
    const base = configFor("USENIX Security", year).sourceURL.replace(/technical-sessions$/, "");
    const urls = [...new Set([...html(raw).querySelectorAll("a[href]")].map(a => new URL(a.getAttribute("href"), base).href)
      .filter(url => url.startsWith(base) && /^(cycle\d+|summer|fall|winter)-accepted-papers$/.test(url.slice(base.length))))];
    if (!urls.length) throw new Error("USENIX Security 尚无可核实的官方周期录用名单。");
    return urls;
  }
  async function fetchUSENIX(conference, year, settings) {
    const { request, signal, onProgress = () => {} } = settings, config = configFor(conference, year);
    let papers;
    let raw;
    try { raw = await request(config.sourceURL, signal, "text"); }
    catch (error) {
      signal?.throwIfAborted();
      // Only an unpublished (404) final list permits the explicit cycle-list path.
      if (conference !== "USENIX Security" || (error.status ?? error.xmlhttp?.status) !== 404) throw error;
    }
    if (raw !== undefined) papers = parseUSENIX(raw, conference, year, config.sourceURL, { mode: "final-program", urls: [config.sourceURL] });
    else {
      const home = config.sourceURL.replace(/technical-sessions$/, "");
      const urls = securityCycleURLs(await request(home, signal, "text"), year);
      const provenance = { mode: "accepted-cycles", urls };
      const lists = [];
      for (const url of urls) {
        signal?.throwIfAborted();
        lists.push(...parseUSENIX(await request(url, signal, "text"), conference, year, url, provenance));
      }
      papers = merge(lists);
    }
    // Metadata pages only: preserve official citation authors/PDF URLs without fetching PDFs.
    for (let i = 0; i < papers.length; i++) {
      signal?.throwIfAborted();
      papers[i] = completeUSENIX(papers[i], await request(papers[i].detailURL, signal, "text"));
      onProgress(i + 1, papers.length, `${conference} 论文元数据`);
    }
    return papers;
  }
  function parseCCS(data, year) {
    const config = configFor("CCS", year), papers = [];
    if (typeof data === "string") data = JSON.parse(data);
    const provenance = { mode: "accepted-cycles", urls: [config.sourceURL, config.dataURL], cycles: config.cycles };
    for (const cycle of config.cycles) {
      if (!Array.isArray(data?.[cycle]) || !data[cycle].length) throw new Error(`CCS 缺少 ${cycle} 研究论文。`);
      for (const row of data[cycle]) {
        const title = typeof row.title === "string" ? row.title.replace(/^\(#\d+\)\s*/, "").trim() : "";
        const node = html("<div></div>").body;
        if (row.url) { const a = node.ownerDocument.createElement("a"); a.setAttribute("href", row.url); node.appendChild(a); }
        papers.push(paper("CCS", year, { title, authors: row.authors?.map(a => a.name?.trim()), cycles: [cycle],
          ...exposedLinks(node, config.sourceURL) }, provenance));
      }
    }
    return merge(papers);
  }
  function parseNDSS(raw, year) {
    const config = configFor("NDSS", year), doc = html(raw), papers = [];
    if (!text(doc.querySelector("title")).includes(String(year))) throw new Error("NDSS 页面年份不符。");
    const options = [...doc.querySelectorAll('select[name="tx_post_tag"] option[value]')].filter(o => o.value);
    if (options.length !== config.cycles.length || config.cycles.some(c => !options.some(o => o.value === c))) throw new Error("NDSS 录用周期无法核实。");
    const counts = options.map(o => Number(text(o).match(/\((\d+)\)$/)?.[1]));
    const provenance = { mode: "accepted-cycles", urls: [config.sourceURL], cycles: config.cycles };
    const list = doc.querySelector('select[name="tx_post_tag"]')?.closest('.pt-cv-wrapper');
    if (!list) throw new Error("NDSS 录用名单容器无法识别。");
    for (const row of list.querySelectorAll('.pt-cv-content-item')) {
      const a = row.querySelector('.pt-cv-title a[href]'), url = a && new URL(a.getAttribute("href"), config.sourceURL);
      if (!url || url.hostname !== "www.ndss-symposium.org" || !url.pathname.startsWith('/ndss-paper/')) throw new Error("NDSS 研究论文链接无法识别。");
      papers.push(paper("NDSS", year, { title: text(a), authors: authors(text(row.querySelector('.pt-cv-ctf-display_authors'))),
        url: url.href, detailURL: url.href }, provenance));
    }
    const result = merge(papers);
    if (counts.every(Number.isFinite) && result.length !== counts.reduce((a,b) => a+b, 0)) throw new Error("NDSS 论文数量与官方周期计数不符，名单可能分页或不完整。");
    return result;
  }
  function parseSIGCOMM(raw, year) {
    const config = configFor("SIGCOMM", year), doc = html(raw);
    if (!text(doc.querySelector("h1")).includes(`SIGCOMM ${year}`)) throw new Error("SIGCOMM 页面年份不符。");
    const heading = [...doc.querySelectorAll("h2")].find(h => text(h) === "Full papers");
    const list = heading?.parentElement.nextElementSibling;
    if (list?.localName !== "ul") throw new Error("SIGCOMM Full papers 列表无法识别。");
    return merge([...list.children].map(row => paper("SIGCOMM", year, {
      title: text(row.querySelector('.text-color-primary')), authors: authors(text(row.querySelector('.style_italic'))),
      track: "Full papers", ...exposedLinks(row, config.sourceURL)
    }, { mode: "accepted-papers", urls: [config.sourceURL] })));
  }
  function parseFSE(raw, year) {
    const config = configFor("FSE", year), doc = html(raw);
    if (!text(doc.querySelector("title")).includes(`FSE ${year}`) || !text(doc.querySelector("title")).includes("Research Papers")) throw new Error("FSE 页面不是所选年份 Research Papers。");
    const rows = [...doc.querySelectorAll('#event-overview table tr')].filter(row => row.querySelector('td'));
    const papers = [];
    for (const row of rows) {
      if (text(row.querySelector('.prog-track')) !== 'Research Papers') continue;
      const a = row.querySelector('a[data-event-modal]');
      if (!a?.getAttribute('data-event-modal')) throw new Error("FSE 研究论文条目无法识别。");
      papers.push(paper("FSE", year, { title: text(a), id: `${year}:${a.getAttribute('data-event-modal')}`,
        authors: [...row.querySelectorAll('.performers a')].map(text), ...exposedLinks(row, config.sourceURL)
      }, { mode: "accepted-papers", urls: [config.sourceURL] }));
    }
    return merge(papers);
  }
  async function fetchAccepted(conference, year, settings) {
    const config = configFor(conference, year), { request, signal } = settings;
    signal?.throwIfAborted();
    if (config.usenix) return fetchUSENIX(conference, year, settings);
    const raw = await request(config.dataURL || config.sourceURL, signal, "text");
    signal?.throwIfAborted();
    return ({ CCS: parseCCS, NDSS: parseNDSS, SIGCOMM: parseSIGCOMM, FSE: parseFSE })[conference](raw, year);
  }
  return { fetchAccepted, parseUSENIX, completeUSENIX, securityCycleURLs, parseCCS, parseNDSS, parseSIGCOMM, parseFSE, merge };
})();
