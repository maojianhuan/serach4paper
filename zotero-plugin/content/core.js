/* Pure retrieval/matching logic; also exercised by Node's built-in test runner. */
var Search4PaperCore = (() => {
  const FIELDS = ["title", "abstract", "keywords", "tldr"];
  const CONFERENCES = ["ICML", "NeurIPS", "ICLR", "AAAI", "ACL", "CVPR", "ICCV", "EMNLP", "ECCV", "CRYPTO", "EUROCRYPT", "ASIACRYPT"];
  const OPENREVIEW_CONFERENCES = ["ICML", "NeurIPS", "ICLR"];
  const CONFERENCE_NAMES = {
    ICML: "International Conference on Machine Learning",
    NeurIPS: "Advances in Neural Information Processing Systems",
    ICLR: "International Conference on Learning Representations",
    AAAI: "AAAI Conference on Artificial Intelligence",
    ACL: "Annual Meeting of the Association for Computational Linguistics",
    CVPR: "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
    ICCV: "IEEE/CVF International Conference on Computer Vision",
    CRYPTO: "International Cryptology Conference",
    EUROCRYPT: "International Conference on the Theory and Applications of Cryptographic Techniques",
    ASIACRYPT: "International Conference on the Theory and Application of Cryptology and Information Security",
    ECCV: "European Conference on Computer Vision",
    EMNLP: "Conference on Empirical Methods in Natural Language Processing"
  };
  function sourceName(conference) {
    if (OPENREVIEW_CONFERENCES.includes(conference)) return "OpenReview";
    if (["CRYPTO", "EUROCRYPT", "ASIACRYPT"].includes(conference)) return "IACR CryptoDB";
    if (conference === "ECCV") return "ECVA Open Access";
    if (conference === "AAAI") return "AAAI Proceedings";
    if (["ACL", "EMNLP"].includes(conference)) return "ACL Anthology";
    if (["CVPR", "ICCV"].includes(conference)) return "CVF Open Access";
    throw new Error("请选择受支持的会议。");
  }

  function venueID(conference, year) {
    if (!CONFERENCES.includes(conference)) throw new Error("请选择受支持的会议。");
    validateYear(year);
    return OPENREVIEW_CONFERENCES.includes(conference)
      ? `${conference}.cc/${year}/Conference` : `${conference}/${year}/Conference`;
  }
  const value = (content, key) => {
    const field = content[key];
    return (field && typeof field === "object" && "value" in field ? field.value : field) ?? "";
  };
  const words = text => (String(text).normalize("NFKD").toLowerCase()
    .replace(/ß/g, "ss").replace(/\p{M}/gu, "").match(/[a-z0-9]+/g) || []);

  // Keep the existing Python topic search's prefix/inflection semantics.
  function tokenMatches(query, actual) {
    if (actual.startsWith(query)) return true;
    if (actual.length >= 5 && query.startsWith(actual)) return true;
    const length = Math.min(5, query.length, actual.length);
    return length >= 5 && query.slice(0, length) === actual.slice(0, length);
  }

  function phraseMatches(text, term) {
    const tokens = words(text), wanted = words(term);
    return wanted.length > 0 && tokens.some((_, index) =>
      index + wanted.length <= tokens.length &&
      wanted.every((word, offset) => tokenMatches(word, tokens[index + offset])));
  }

  function splitTerms(text) {
    return [...new Set(text.split(/[;；]/).map(s => s.trim()).filter(Boolean))];
  }

  function validateQuery(query) {
    if (!query.terms.length || query.terms.some(term => !words(term).length)) {
      throw new Error("请输入至少一个英文关键词；多个关键词用分号分隔。");
    }
    if (!query.fields.length || query.fields.some(field => !FIELDS.includes(field))) {
      throw new Error("请至少勾选一个查找位置。");
    }
    if (!["AND", "OR"].includes(query.operator)) {
      throw new Error("请选择“全部满足（AND）”或“任意满足（OR）”。");
    }
  }

  function snippet(text, term) {
    text = String(text).replace(/\s+/g, " ").trim();
    if (text.length <= 260) return text;
    const parts = [...text.matchAll(/\S+/g)];
    const startWord = parts.findIndex((part, i) =>
      phraseMatches(parts.slice(i, i + words(term).length + 2).map(p => p[0]).join(" "), term));
    const start = Math.max(0, (parts[Math.max(0, startWord)]?.index || 0) - 85);
    return (start ? "…" : "") + text.slice(start, start + 260) + (start + 260 < text.length ? "…" : "");
  }

  function matchPaper(paper, query) {
    const evidence = [];
    const matchedTerms = new Set();
    for (const field of query.fields) {
      for (const term of query.terms) {
        if (phraseMatches(paper[field] || "", term)) {
          matchedTerms.add(term);
          evidence.push({ field, term, text: snippet(paper[field], term) });
        }
      }
    }
    if (!evidence.length) return null;
    if (query.operator === "AND" && !query.terms.every(term => matchedTerms.has(term))) return null;
    return { ...paper, evidence };
  }

  function normalizeNote(note, year, conference = "ICML") {
    const content = note.content || {};
    const venue = venueID(conference, year);
    if (value(content, "venueid") !== venue) throw new Error(`OpenReview 返回了其他会议或未录用记录：${note.id}`);
    if (!note.id || !String(value(content, "title")).trim()) throw new Error("OpenReview 记录缺少 ID 或标题。");
    const authors = value(content, "authors");
    if (!Array.isArray(authors) || authors.some(author => typeof author !== "string")) {
      throw new Error(`OpenReview 作者格式异常：${note.id}`);
    }
    const keywords = value(content, "keywords");
    const pdf = value(content, "pdf");
    const pdfURL = pdf ? new URL(pdf, "https://openreview.net") : null;
    if (pdfURL && !["http:", "https:"].includes(pdfURL.protocol)) throw new Error(`不支持的 PDF 地址：${note.id}`);
    return {
      id: note.id, year, venueID: venue, number: note.number ?? null,
      title: String(value(content, "title")), authors,
      abstract: String(value(content, "abstract")),
      keywords: Array.isArray(keywords) ? keywords.join("; ") : String(keywords),
      tldr: String(value(content, "TLDR") || value(content, "TL;DR") || value(content, "tldr")),
      doi: String(value(content, "doi")),
      url: `https://openreview.net/forum?id=${encodeURIComponent(note.id)}`,
      pdfURL: pdfURL?.href || "",
      bibtex: String(value(content, "_bibtex"))
    };
  }

  function validateYear(year) {
    if (!Number.isInteger(year) || year < 2000 || year > 2100) throw new Error("请输入有效年份（2000–2100）。");
  }

  // Local files are an input boundary: reject incomplete or mismatched lists.
  function validateMetadata(metadata, year, conference = "ICML") {
    const venue = venueID(conference, year);
    if (metadata?.schemaVersion !== 1) throw new Error("不支持的本地元数据格式版本。");
    if (metadata.year !== year || metadata.venueID !== venue) throw new Error("本地元数据的会议或年份不一致。");
    if (typeof metadata.fetchedAt !== "string" || !Number.isFinite(Date.parse(metadata.fetchedAt))) {
      throw new Error("本地元数据缺少有效的获取时间。");
    }
    if (!Array.isArray(metadata.papers) || !Number.isInteger(metadata.paperCount)
      || metadata.paperCount < 1 || metadata.papers.length !== metadata.paperCount) {
      throw new Error("本地元数据的论文数量不完整。");
    }
    const ids = new Set();
    const strings = ["id", "title", "abstract", "keywords", "tldr", "doi", "url", "pdfURL", "bibtex"];
    for (const paper of metadata.papers) {
      if (!paper || strings.some(field => typeof paper[field] !== "string")
        || !paper.id.trim() || !paper.title.trim() || paper.year !== year || paper.venueID !== venue
        || !Array.isArray(paper.authors) || paper.authors.some(author => typeof author !== "string")) {
        throw new Error("本地元数据包含格式异常或会议、年份不一致的论文。");
      }
      if (ids.has(paper.id)) throw new Error("本地元数据包含重复的论文 ID。");
      ids.add(paper.id);
      const source = OPENREVIEW_CONFERENCES.includes(conference) ? "openreview"
        : conference === "AAAI" ? "aaai" : ["ACL", "EMNLP"].includes(conference) ? "acl" : conference === "ECCV" ? "ecva" : ["CRYPTO", "EUROCRYPT", "ASIACRYPT"].includes(conference) ? "iacr" : "cvf";
      const sourceURL = source === "openreview" ? `https://openreview.net/forum?id=${encodeURIComponent(paper.id)}`
        : source === "aaai" ? `https://ojs.aaai.org/index.php/AAAI/article/view/${paper.id}`
        : source === "iacr" ? `https://www.iacr.org/cryptodb/data/paper.php?pubkey=${paper.id}`
        : source === "acl" ? `https://aclanthology.org/${paper.id}/` : source === "ecva" ? `https://www.ecva.net${paper.id}` : `https://openaccess.thecvf.com${paper.id}`;
      if ((source !== "openreview" && paper.source !== source)
        || (paper.source !== undefined && paper.source !== source) || paper.url !== sourceURL
        || (["aaai", "iacr"].includes(source) && !/^\d+$/.test(paper.id))
        || (source === "acl" && !(year < 2020 ? new RegExp(`^${conference === "ACL" ? "P" : "D"}${String(year).slice(-2)}-1\\d{3}$`).test(paper.id) : paper.id.startsWith(`${year}.${conference.toLowerCase()}-`)))
        || (source === "cvf" && !new RegExp(`^/(?:content/${conference}${year}|content_${conference.toLowerCase()}_${year}|content_${conference}_${year})/html/[^/]+_paper\\.html$`).test(paper.id))
        || (source === "ecva" && !new RegExp(`^/papers/eccv_${year}/papers_ECCV/html/[^/]+_paper\\.php$`).test(paper.id))
        || (paper.pdfURL && (!URL.canParse(paper.pdfURL) || !["http:", "https:"].includes(new URL(paper.pdfURL).protocol)))) {
        throw new Error("本地元数据包含无效的论文链接。");
      }
    }
    return metadata;
  }

  async function fetchAccepted(year, { conference = "ICML", request, signal, onProgress = () => {} }) {
    const venue = venueID(conference, year);
    if (!OPENREVIEW_CONFERENCES.includes(conference)) throw new Error("此会议需使用对应的官方论文库。");
    const papers = [], ids = new Set();
    let count;
    do {
      signal?.throwIfAborted();
      const params = new URLSearchParams({ term: venue, content: "venueid", type: "exact",
        venueid: venue, limit: "1000", offset: String(papers.length), count: "true", sort: "tmdate:asc" });
      const payload = await request(`https://api2.openreview.net/notes/search?${params}`, signal);
      signal?.throwIfAborted();
      if (!Array.isArray(payload?.notes) || !Number.isInteger(payload.count) || payload.count < 0) {
        throw new Error("OpenReview 返回格式异常，未得到完整论文列表。");
      }
      if (count !== undefined && count !== payload.count) throw new Error("分页期间录用名单数量发生变化，请重新检索。");
      count = payload.count;
      if (!count) throw new Error(`尚未获得 ${conference} ${year} 的公开录用名单；这不表示没有相关论文。`);
      if (!payload.notes.length) throw new Error("OpenReview 分页提前结束，未得到完整论文列表。");
      for (const note of payload.notes) {
        const paper = normalizeNote(note, year, conference);
        if (ids.has(paper.id)) throw new Error("OpenReview 分页出现重复论文，请重新检索。");
        ids.add(paper.id);
        papers.push(paper);
      }
      if (papers.length > count) throw new Error("OpenReview 分页总数不一致，请重新检索。");
      onProgress(papers.length, count);
    } while (papers.length < count);
    papers.sort((a, b) => (a.number ?? Infinity) - (b.number ?? Infinity) || a.id.localeCompare(b.id));
    return papers;
  }

  function identities(data) {
    const keys = new Set();
    const addDOI = doi => {
      doi = String(doi || "").trim().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "").toLowerCase();
      if (doi) keys.add("doi:" + doi);
    };
    addDOI(data.doi);
    if (data.id) keys.add(data.source && data.source !== "openreview"
      ? `source:${data.source}:${data.id}` : "openreview:" + data.id);
    if (data.url && URL.canParse(data.url)) {
      const url = new URL(data.url);
      if (["openreview.net", "www.openreview.net"].includes(url.hostname) && ["/forum", "/pdf"].includes(url.pathname)) {
        const id = url.searchParams.get("id");
        if (id) keys.add("openreview:" + id);
      }
      const aaai = url.hostname === "ojs.aaai.org" && url.pathname.match(/^\/index\.php\/AAAI\/article\/(?:view|download)\/(\d+)(?:\/\d+)?\/?$/);
      const acl = url.hostname === "aclanthology.org" && url.pathname.match(/^\/(\d{4}\.(?:acl|emnlp)-(?:long|main)\.\d+|[PD]\d{2}-1\d{3})(?:\.pdf|\/)?$/);
      const cvf = url.hostname === "openaccess.thecvf.com" && url.pathname.match(/^\/(?:content\/(?:CVPR|ICCV)\d{4}|content_(?:cvpr|iccv)_\d{4}|content_(?:CVPR|ICCV)_\d{4})\/(?:html|papers)\/[^/]+\.(?:html|pdf)$/);
      if (["www.ecva.net", "ecva.net"].includes(url.hostname) && /^\/papers\/eccv_\d{4}\/papers_ECCV\/html\/[^/]+_paper\.php$/.test(url.pathname)) keys.add(`source:ecva:${url.pathname}`);
      if (["www.iacr.org", "iacr.org"].includes(url.hostname) && url.pathname === "/cryptodb/data/paper.php"
        && /^\d+$/.test(url.searchParams.get("pubkey"))) keys.add(`source:iacr:${url.searchParams.get("pubkey")}`);
      if (aaai) keys.add(`source:aaai:${aaai[1]}`);
      if (acl) keys.add(`source:acl:${acl[1]}`);
      if (cvf) keys.add(`source:cvf:${url.pathname.replace("/papers/", "/html/").replace(/\.pdf$/, ".html")}`);
    }
    for (const line of (data.extra || "").split("\n")) {
      if (line.startsWith("OpenReview ID: ")) keys.add("openreview:" + line.slice(15).trim());
      if (line.startsWith("DOI: ")) addDOI(line.slice(5));
      if (/^Source ID: (aaai|acl|cvf|ecva|iacr):/.test(line)) keys.add("source:" + line.slice(11).trim());
    }
    return keys;
  }

  const escapeHTML = text => String(text).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function evidenceNote(paper, query) {
    const lines = ["Imported by search4paper", `Source: ${paper.url}`, `Venue: ${paper.venueID}`,
      `Keywords: ${query.terms.join("; ")}`, `Match: ${query.operator}`,
      `Fields: ${query.fields.join(", ")}`,
      ...(paper.evidence || []).map(hit => `[${hit.field}] ${hit.term}: ${hit.text}`)];
    if (paper.bibtex) lines.push("Source BibTeX:", paper.bibtex);
    return lines.map(line => `<p>${escapeHTML(line)}</p>`).join("");
  }

  return { FIELDS, CONFERENCES, OPENREVIEW_CONFERENCES, CONFERENCE_NAMES, sourceName, venueID, words, phraseMatches, splitTerms, validateQuery, matchPaper, normalizeNote,
    validateYear, validateMetadata, fetchAccepted, identities, evidenceNote };
})();
if (typeof module !== "undefined") module.exports = Search4PaperCore;
