/* Official proceedings adapters. DOMParser and requests run inside Zotero. */
var Search4PaperSources = (() => {
  const OAI = "http://www.openarchives.org/OAI/2.0/";
  const DC = "http://purl.org/dc/elements/1.1/";
  const aaaiOAI = "https://ojs.aaai.org/index.php/AAAI/oai";
  const text = node => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const elements = (node, namespace, name) => [...node.getElementsByTagNameNS(namespace, name)];
  const xml = raw => {
    const doc = new DOMParser().parseFromString(raw, "application/xml");
    if (doc.querySelector("parsererror")) throw new Error("官方论文库返回了不完整或无效的 XML。");
    return doc;
  };
  const html = raw => new DOMParser().parseFromString(raw, "text/html");
  const plainHTML = raw => text(html(raw).body);
  function paper(conference, year, source, fields) {
    if (!fields.id || !fields.title?.trim() || !fields.authors?.length
      || fields.authors.some(author => typeof author !== "string" || !author.trim())) {
      throw new Error(`${conference} ${year} 论文缺少有效的 ID、标题或作者。`);
    }
    return { year, venueID: Search4PaperCore.venueID(conference, year), source, number: null,
      abstract: "", keywords: "", tldr: "", doi: "", pdfURL: "", bibtex: "", ...fields };
  }
  const unavailable = (conference, year) => new Error(`尚未获得 ${conference} ${year} 的官方主会论文名单；这不表示没有相关论文。`);

  function parseAnthology(raw, conference, year) {
    const legacy = year < 2020;
    const collectionID = legacy ? `${conference === "ACL" ? "P" : "D"}${String(year).slice(-2)}` : `${year}.${conference.toLowerCase()}`;
    const doc = xml(raw);
    if (doc.documentElement.getAttribute("id") !== collectionID) throw new Error("ACL Anthology 返回了其他会议或年份。");
    const track = legacy ? "1" : conference === "ACL" ? "long" : "main";
    const volume = doc.querySelector(`volume[id="${track}"]`);
    if (!volume) throw unavailable(conference, year);
    const meta = volume.querySelector("meta");
    if (text(meta?.querySelector("year")) !== String(year)
      || ![...(meta?.querySelectorAll("venue") || [])].some(node => text(node) === conference.toLowerCase())) throw new Error("ACL Anthology 卷的会议或年份不一致。");
    return [...volume.children].filter(node => node.localName === "paper").map(node => {
      const number = node.getAttribute("id");
      if (!/^\d+$/.test(number)) throw new Error("ACL Anthology 论文 ID 格式异常。");
      const id = legacy ? `${collectionID}-${track}${number.padStart(3, "0")}` : `${collectionID}-${track}.${number}`;
      const pdf = node.querySelector("pdf");
      return paper(conference, year, "acl", {
        id, title: text(node.querySelector("title")),
        authors: [...node.querySelectorAll(":scope > author")].map(author =>
          [text(author.querySelector("first")), text(author.querySelector("last"))].filter(Boolean).join(" ")),
        abstract: text(node.querySelector("abstract")), doi: text(node.querySelector("doi")),
        url: `https://aclanthology.org/${id}/`,
        pdfURL: pdf ? new URL(text(pdf) || `${id}.pdf`, "https://aclanthology.org/").href : ""
      });
    });
  }

  // Follow OAI continuation tokens, including the final page's declared count.
  async function oaiPages(verb, params, { request, signal }, consume) {
    let url = `${aaaiOAI}?${new URLSearchParams({ verb, ...params })}`;
    let received = 0, total;
    const tokens = new Set();
    while (url) {
      signal?.throwIfAborted();
      const doc = xml(await request(url, signal, "text"));
      signal?.throwIfAborted();
      const error = elements(doc, OAI, "error")[0];
      // The official legacy set catalogue includes empty sets (e.g. AAAI:2023-11).
      if (error?.getAttribute("code") === "noRecordsMatch" && verb === "ListRecords" && received === 0) return;
      if (error) throw new Error(`AAAI OAI：${text(error)}`);
      const list = elements(doc, OAI, verb)[0];
      if (!list) throw new Error("AAAI OAI 缺少分页数据。");
      const rows = elements(list, OAI, verb === "ListSets" ? "set" : "record");
      const token = elements(list, OAI, "resumptionToken")[0];
      if (token?.hasAttribute("completeListSize")) {
        const size = Number(token.getAttribute("completeListSize"));
        if (!Number.isInteger(size) || size < 0 || (total !== undefined && total !== size)) {
          throw new Error("AAAI OAI 分页总数发生变化。");
        }
        total = size;
      }
      if (token?.hasAttribute("cursor") && Number(token.getAttribute("cursor")) !== received) {
        throw new Error("AAAI OAI 分页位置不连续。");
      }
      received += rows.length;
      consume(rows);
      const next = text(token);
      if (next && (!rows.length || tokens.has(next))) throw new Error("AAAI OAI 分页未前进。");
      tokens.add(next);
      url = next ? `${aaaiOAI}?${new URLSearchParams({ verb, resumptionToken: next })}` : "";
    }
    if (total !== undefined && received !== total) throw new Error("AAAI OAI 分页提前结束，未得到完整名单。");
  }

  function aaaiRecordYear(record) {
    const sources = elements(record, DC, "source").map(text);
    const proceedings = sources.find(value => /Proceedings of the AAAI Conference/.test(value));
    const match = proceedings?.match(/\bAAAI-(\d{2})\b/) || proceedings?.match(/\bVol\. \d+ No\. \d+ \((\d{4})\)/);
    if (!match) throw new Error("AAAI OAI 缺少可确认会议年份的论文集信息。");
    return match[1].length === 2 ? 2000 + Number(match[1]) : Number(match[1]);
  }

  function parseAAAIRecord(record, year) {
    const header = elements(record, OAI, "header")[0];
    if (header?.getAttribute("status") === "deleted") return null;
    const values = name => elements(record, DC, name).map(text);
    const url = values("identifier").find(value => /^https:\/\/ojs\.aaai\.org\/index\.php\/AAAI\/article\/view\/\d+$/.test(value));
    const id = url?.split("/").pop();
    const doi = values("identifier").find(value => /^10\./.test(value)) || "";
    // Older OAI sets contain multiple conference years; the proceedings determine the year.
    if (aaaiRecordYear(record) !== year) throw new Error("AAAI OAI 返回了其他年份的论文。");
    const pdf = values("relation").find(value => value.startsWith(`${url}/`) && /\/\d+$/.test(value));
    return paper("AAAI", year, "aaai", {
      id, url, doi, title: values("title")[0], authors: values("creator"),
      abstract: plainHTML(values("description")[0] || ""), keywords: values("subject").join("; "),
      pdfURL: pdf ? pdf.replace("/article/view/", "/article/download/") : ""
    });
  }

  async function fetchLegacyAAAI(year, groupNames, { request, signal, onProgress }) {
    let url = "https://ojs.aaai.org/index.php/AAAI/issue/archive";
    const issues = new Set(), pages = new Set();
    while (url) {
      signal?.throwIfAborted();
      if (pages.has(url)) throw new Error("AAAI 论文集目录分页未前进。");
      pages.add(url);
      const doc = html(await request(url, signal, "text"));
      const headings = [...doc.querySelectorAll(".obj_issue_summary h2")];
      if (!headings.length) throw new Error("AAAI 官方论文集目录格式异常。");
      for (const heading of headings) {
        const label = text(heading), volume = label.match(/\bVol\. (\d+) No\./);
        if (!volume) throw new Error("AAAI 论文集目录缺少卷号。");
        // Official OJS volumes 24 (2010) through 37 (2023); some special-program titles omit the year.
        // Every fetched record is independently checked against its explicit proceedings year.
        const issueYear = Number(volume[1]) + 1986;
        if (issueYear === year) {
          const href = heading.querySelector("a.title")?.getAttribute("href");
          if (!/^https:\/\/ojs\.aaai\.org\/index\.php\/AAAI\/issue\/view\/\d+$/.test(href)) throw new Error("AAAI 论文集链接异常。");
          issues.add(href);
        }
      }
      const next = doc.querySelector("a.next")?.getAttribute("href");
      if (next && !/^https:\/\/ojs\.aaai\.org\/index\.php\/AAAI\/issue\/archive\/\d+$/.test(next)) throw new Error("AAAI 目录分页链接异常。");
      url = next || "";
      onProgress(0, null, `读取 AAAI 历史论文集目录：第 ${pages.size} 页…`);
    }
    const ids = new Set();
    for (const issue of issues) {
      signal?.throwIfAborted();
      const doc = html(await request(issue, signal, "text"));
      const sections = [...doc.querySelectorAll(".sections > .section")];
      if (!sections.length) throw new Error(`AAAI 论文集缺少分组：${issue}`);
      for (const section of sections) {
        if (!groupNames.has(text(section.querySelector("h2")))) continue;
        for (const link of section.querySelectorAll("h3.title > a")) {
          const match = link.getAttribute("href")?.match(/^https:\/\/ojs\.aaai\.org\/index\.php\/AAAI\/article\/view\/(\d+)$/);
          if (!match) throw new Error("AAAI 历史论文链接异常。");
          if (ids.has(match[1])) throw new Error("AAAI 历史论文集包含重复条目。");
          ids.add(match[1]);
        }
      }
    }
    const papers = [];
    for (const id of ids) {
      signal?.throwIfAborted();
      const url = `${aaaiOAI}?${new URLSearchParams({ verb: "GetRecord", metadataPrefix: "oai_dc", identifier: `oai:ojs.aaai.org:article/${id}` })}`;
      const doc = xml(await request(url, signal, "text"));
      const record = elements(doc, OAI, "record")[0];
      if (!record) throw new Error(`AAAI OAI 缺少论文记录：${id}`);
      const item = parseAAAIRecord(record, year);
      if (!item || item.id !== id) throw new Error(`AAAI OAI 论文身份不一致：${id}`);
      papers.push(item);
      onProgress(papers.length, ids.size, "逐篇读取 AAAI 历史论文元数据…");
    }
    return papers;
  }

  async function fetchAAAI(year, options) {
    if (year < 2010) throw new Error("AAAI 当前适配 OJS 官方论文库 2010 年起的论文；更早来源尚未适配。");
    const groups = new Map(), prefix = `AAAI:AI${String(year).slice(-2)}-`;
    await oaiPages("ListSets", {}, options, rows => {
      for (const row of rows) {
        const id = text(elements(row, OAI, "setSpec")[0]), name = text(elements(row, OAI, "setName")[0]);
        const technical = /^(?:AAAI (?:Special )?Technical Track\b|AAAI Special Track on AI for Social Impact$|Technical Papers:|Main Track(?::|$)|Main Technical Papers$)/.test(name);
        const oldMain = ["CMCS", "HAAI", "MLM", "STCOGS", "STCOMPS", "STIS", "SRAI", "ST-COGS", "ST-CS", "ST-IAC", "IS", "COG", "CSAI", "CONSS", "RST", "KBIS", "MULTI", "WEB", "COMP", "INTIN", "CON", "KBI", "KR", "MDT", "RPPA", "RUU", "AIB", "CAI", "II", "PGAI"];
        if (year >= 2024 ? id.startsWith(prefix) && technical
          : id.startsWith("AAAI:") && !/^AAAI:AI\d{2}-/.test(id) && (technical || oldMain.includes(id.slice(5)))) groups.set(id, name);
      }
      options.onProgress(0, null, "读取 AAAI 官方论文分组…");
    });
    if (!groups.size) throw unavailable("AAAI", year);
    if (year < 2024) return fetchLegacyAAAI(year, new Set(groups.values()), options);
    const papers = [];
    let done = 0;
    for (const [group, name] of groups) {
      await oaiPages("ListRecords", { metadataPrefix: "oai_dc", set: group }, options, rows => {
        for (const row of rows) {
          const normalized = parseAAAIRecord(row, year);
          if (normalized) papers.push(normalized);
        }
        options.onProgress(papers.length, null, `${name}；分组 ${done + 1} / ${groups.size}`);
      });
      done++;
    }
    return papers;
  }

  function parseCVFIndex(raw, conference, year) {
    const doc = html(raw);
    const prefixes = [`/content/${conference}${year}/html/`, `/content_${conference.toLowerCase()}_${year}/html/`, `/content_${conference}_${year}/html/`];
    return [...doc.querySelectorAll("dt.ptitle > a")].map(link => {
      const url = new URL(link.getAttribute("href"), "https://openaccess.thecvf.com");
      if (url.origin !== "https://openaccess.thecvf.com" || !prefixes.some(prefix => url.pathname.startsWith(prefix))
        || !url.pathname.endsWith("_paper.html")) throw new Error("CVF 返回了其他会议、年份或研讨会论文。");
      const authors = link.parentElement.nextElementSibling;
      const links = authors?.nextElementSibling;
      const pdf = links?.querySelector('a[href$="_paper.pdf"]');
      return paper(conference, year, "cvf", {
        id: url.pathname, url: url.href, title: text(link),
        authors: [...(authors?.querySelectorAll('input[name="query_author"], input[name="query"]') || [])].map(input => input.value),
        pdfURL: pdf ? new URL(pdf.getAttribute("href"), "https://openaccess.thecvf.com/").href : "",
        bibtex: links?.querySelector(".bibref")?.textContent.trim() || ""
      });
    });
  }

  async function fetchCVF(conference, year, options) {
    const firstYear = 2013;
    if (year < firstYear) throw new Error(`${conference} 当前支持 ${firstYear} 年及以后的官方论文库。`);
    if (conference === "ICCV" && year % 2 === 0) throw new Error(`ICCV 在奇数年举办，请选择有效会议年份（例如 ${year - 1}）。`);
    const { request, signal, onProgress } = options;
    const indexURL = `https://openaccess.thecvf.com/${conference}${year}`;
    const raw = await request(indexURL + (year >= 2020 ? "?day=all" : ""), signal, "text");
    const papers = parseCVFIndex(raw, conference, year);
    // Older indexes link separate dates instead of accepting day=all.
    if (!papers.length) {
      const days = [...new Set([...html(raw).querySelectorAll("a[href]")].map(a => new URL(a.getAttribute("href"), indexURL).href)
        .filter(url => { const u = new URL(url); return u.origin === "https://openaccess.thecvf.com"
          && u.pathname === `/${conference}${year}.py` && /^\d{4}-\d{2}-\d{2}$/.test(u.searchParams.get("day")); }))];
      for (const day of days) {
        signal?.throwIfAborted();
        const daily = parseCVFIndex(await request(day, signal, "text"), conference, year);
        if (!daily.length) throw new Error(`CVF 日期索引没有返回论文，名单不完整：${day}`);
        papers.push(...daily);
      }
    }
    if (!papers.length) throw unavailable(conference, year);
    signal?.throwIfAborted();
    onProgress(0, papers.length, year >= (conference === "CVPR" ? 2023 : 2025) ? "读取官方会议日程中的批量摘要…" : "逐篇读取 CVF 官方论文详情中的摘要…");
    // The proceedings index has no abstracts; official oral/poster data supplies them in one request.
    // ICCV repeats oral presentations and often omits paper URLs, so match exact normalized titles.
    const useProgram = conference === "CVPR" ? year >= 2023 : year >= 2025;
    const program = useProgram ? await request(`https://${conference.toLowerCase()}.thecvf.com/static/virtual/data/${conference.toLowerCase()}-${year}-orals-posters.json`, signal) : { results: [], count: 0 };
    if (!Array.isArray(program?.results) || program.count !== program.results.length || program.next) {
      throw new Error("CVF 官方会议日程的论文数据不完整。");
    }
    const key = title => title.normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase();
    const abstracts = new Map();
    for (const entry of program.results) {
      if (typeof entry.name !== "string" || typeof entry.abstract !== "string") throw new Error("CVF 官方摘要格式异常。");
      const title = key(entry.name);
      if (!abstracts.has(title)) abstracts.set(title, new Set());
      abstracts.get(title).add(entry.abstract.trim());
    }
    let done = 0;
    for (const item of papers) {
      signal?.throwIfAborted();
      const matches = abstracts.get(key(item.title));
      if (matches?.size === 1 && [...matches][0]) item.abstract = [...matches][0];
      else {
        // Actual proceedings/program title differences require reading the authoritative paper page.
        const doc = html(await request(item.url, signal, "text"));
        // ICCV 2025's index says "RoboTrom-Nav", while that same paper page says "RoboTron-Nav".
        // A matching canonical PDF also establishes identity when the published titles differ.
        const samePDF = item.pdfURL && doc.querySelector('meta[name="citation_pdf_url"]')?.getAttribute("content") === item.pdfURL;
        if ((!samePDF && key(text(doc.querySelector("#papertitle"))) !== key(item.title)) || !doc.querySelector("#abstract")) {
          throw new Error(`CVF 论文详情不完整或标题不一致：${item.url}`);
        }
        item.abstract = text(doc.querySelector("#abstract"));
      }
      onProgress(++done, papers.length);
    }
    return papers;
  }

  function parseECVAIndex(raw, year) {
    const doc = html(raw), prefix = `/papers/eccv_${year}/papers_ECCV/html/`;
    return [...doc.querySelectorAll("dt.ptitle > a")].filter(link =>
      new URL(link.getAttribute("href"), "https://www.ecva.net/").pathname.startsWith(prefix)).map(link => {
      const url = new URL(link.getAttribute("href"), "https://www.ecva.net/");
      if (url.origin !== "https://www.ecva.net" || !url.pathname.endsWith("_paper.php")) throw new Error("ECCV 论文来源链接异常。");
      const authors = link.parentElement.nextElementSibling, links = authors?.nextElementSibling;
      const pdf = [...(links?.querySelectorAll("a[href]") || [])].find(a => text(a).toLowerCase() === "pdf");
      const doi = links?.querySelector('a[href^="https://link.springer.com/chapter/"]');
      return paper("ECCV", year, "ecva", { id: url.pathname, url: url.href, title: text(link),
        // 2018 uses BibTeX-style "Family, Given and Family, Given"; later years use comma-separated full names.
        // Some 2020 entries contain doubled commas; these separators do not represent authors.
        authors: text(authors).split(year === 2018 ? /\s+and\s+/ : /,+/).map(name => name.replace(/\*+$/g, "").trim()).filter(Boolean),
        doi: doi ? doi.getAttribute("href").split("/chapter/")[1] : "",
        pdfURL: pdf ? new URL(pdf.getAttribute("href"), "https://www.ecva.net/").href : "" });
    });
  }

  async function fetchECCV(year, { request, signal, onProgress }) {
    if (year % 2) throw new Error("ECCV 在偶数年举办，请选择有效会议年份。");
    if (year < 2018) throw new Error("ECVA 官方开放论文库从 ECCV 2018 年起；更早年份尚未适配。");
    const papers = parseECVAIndex(await request("https://www.ecva.net/papers.php", signal, "text"), year);
    let done = 0;
    for (const item of papers) {
      signal?.throwIfAborted();
      const doc = html(await request(item.url, signal, "text"));
      if (text(doc.querySelector("#papertitle")) !== item.title || !doc.querySelector("#abstract")) {
        throw new Error(`ECCV 论文详情不完整或标题不一致：${item.url}`);
      }
      item.abstract = text(doc.querySelector("#abstract"));
      onProgress(++done, papers.length, "逐篇读取 ECVA 官方论文摘要…");
    }
    return papers;
  }

  function parseIACRIndex(raw, conference, year) {
    const doc = html(raw);
    if (![...doc.querySelectorAll("h3")].some(node => text(node).toUpperCase() === `PAPERS FROM ${conference} ${year}`)) {
      throw new Error("IACR CryptoDB 返回了其他会议、年份或无效的论文目录。");
    }
    const papers = [];
    for (const link of doc.querySelectorAll(".pub-title > a")) {
      const row = link.closest(".row"), cells = row?.children;
      if (!cells || text(cells[0]) !== String(year) || text(cells[1]).toUpperCase() !== conference) {
        throw new Error("IACR CryptoDB 目录混入其他会议或年份。");
      }
      if (row.querySelector('[title="Invited talk/paper"]')) continue;
      const url = new URL(link.getAttribute("href"), "https://www.iacr.org/cryptodb/data/");
      const id = url.searchParams.get("pubkey");
      if (url.origin !== "https://www.iacr.org" || url.pathname !== "/cryptodb/data/paper.php" || !/^\d+$/.test(id)) {
        throw new Error("IACR CryptoDB 论文链接异常。");
      }
      papers.push(paper(conference, year, "iacr", { id, url: `https://www.iacr.org/cryptodb/data/paper.php?pubkey=${id}`,
        title: text(link), authors: [...row.querySelectorAll(".authors .author")].map(text),
        abstract: text(row.querySelector(".abstract")) }));
    }
    return papers;
  }

  function completeIACRPaper(raw, item, conference, year) {
    const doc = html(raw), rows = [...doc.querySelectorAll("table tr")];
    const field = label => rows.find(row => text(row.querySelector("th")) === label)?.querySelector("td");
    const venue = field("Conference:")?.querySelector("a[href]");
    const venueURL = venue && new URL(venue.getAttribute("href"), item.url);
    if (!venueURL || !["iacr.org", "www.iacr.org"].includes(venueURL.hostname)
      || venueURL.pathname !== "/cryptodb/data/conf.php" || venueURL.searchParams.get("year") !== String(year)
      || venueURL.searchParams.get("venue")?.toUpperCase() !== conference
      || ![...doc.querySelectorAll("h3")].some(node => text(node) === item.title)) {
      throw new Error(`IACR CryptoDB 论文详情身份不一致：${item.url}`);
    }
    const download = field("Download:");
    const links = [...(download?.querySelectorAll("a[href]") || [])].map(a => new URL(a.getAttribute("href"), item.url));
    const doi = links.find(url => ["doi.org", "dx.doi.org"].includes(url.hostname));
    // Only direct paper links in Download, never Presentation slides or search-engine links.
    const pdf = links.find(url => ["https:", "http:"].includes(url.protocol) && /\.pdf$/i.test(url.pathname));
    return { ...item, abstract: text(field("Abstract:")) || item.abstract,
      doi: doi ? decodeURIComponent(doi.pathname.slice(1)) : "", pdfURL: pdf?.href || "",
      bibtex: doc.querySelector(".bibtex pre")?.textContent.trim() || "" };
  }

  async function fetchIACR(conference, year, { request, signal, onProgress }) {
    const url = `https://www.iacr.org/cryptodb/data/conf.php?${new URLSearchParams({ year, venue: conference.toLowerCase() })}`;
    const papers = parseIACRIndex(await request(url, signal, "text"), conference, year);
    for (let i = 0; i < papers.length; i++) {
      signal?.throwIfAborted();
      papers[i] = completeIACRPaper(await request(papers[i].url, signal, "text"), papers[i], conference, year);
      onProgress(i + 1, papers.length, "读取 IACR 官方论文元数据…");
    }
    return papers;
  }

  async function fetchAccepted(year, options) {
    const { conference, signal, request, onProgress = () => {} } = options;
    Search4PaperCore.venueID(conference, year);
    if (Search4PaperCore.OPENREVIEW_CONFERENCES.includes(conference)) return Search4PaperCore.fetchAccepted(year, options);
    const settings = { ...options, onProgress };
    signal?.throwIfAborted();
    let papers;
    if (["ACL", "EMNLP"].includes(conference)) {
      if (conference === "EMNLP" && year < 2007) throw new Error("EMNLP 的 D 系列目录从 2007 年起；更早的 W 系列尚未适配。");
      const collection = year < 2020 ? `${conference === "ACL" ? "P" : "D"}${String(year).slice(-2)}` : `${year}.${conference.toLowerCase()}`;
      const raw = await request(`https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/${collection}.xml`, signal, "text");
      signal?.throwIfAborted();
      papers = parseAnthology(raw, conference, year);
    }
    else if (["CRYPTO", "EUROCRYPT", "ASIACRYPT"].includes(conference)) papers = await fetchIACR(conference, year, settings);
    else if (conference === "ECCV") papers = await fetchECCV(year, settings);
    else if (conference === "AAAI") papers = await fetchAAAI(year, settings);
    else papers = await fetchCVF(conference, year, settings);
    signal?.throwIfAborted();
    if (!papers.length) throw unavailable(conference, year);
    Search4PaperCore.validateMetadata({ schemaVersion: 1, year,
      venueID: Search4PaperCore.venueID(conference, year), fetchedAt: new Date().toISOString(),
      paperCount: papers.length, papers }, year, conference);
    onProgress(papers.length, papers.length);
    return papers;
  }
  return { fetchAccepted, parseAnthology, parseAAAIRecord, parseCVFIndex, parseECVAIndex, parseIACRIndex, completeIACRPaper };
})();
