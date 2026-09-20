/* Pure retrieval/matching logic; also exercised by Node's built-in test runner. */
var Search4PaperCore = (() => {
  const FIELDS = ["title", "abstract", "keywords", "tldr"];
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
      throw new Error("请输入英文主题词组；多个词组用分号分隔。");
    }
    if (!query.fields.length || query.fields.some(field => !FIELDS.includes(field))) {
      throw new Error("请至少选择一个搜索字段。");
    }
    if (query.context.some(term => !words(term).length)) {
      throw new Error("上下文词组必须包含英文字母或数字。");
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
    const combined = query.fields.map(field => paper[field] || "").join(" ");
    if (query.context.length && !query.context.some(term => phraseMatches(combined, term))) return null;
    const evidence = [];
    for (const field of query.fields) {
      for (const term of query.terms) {
        if (phraseMatches(paper[field] || "", term)) {
          evidence.push({ field, term, text: snippet(paper[field], term) });
        }
      }
    }
    return evidence.length ? { ...paper, evidence } : null;
  }

  function normalizeNote(note, year) {
    const content = note.content || {};
    const venueID = `ICML.cc/${year}/Conference`;
    if (value(content, "venueid") !== venueID) throw new Error(`OpenReview 返回了其他会议或未录用记录：${note.id}`);
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
      id: note.id, year, venueID, number: note.number ?? null,
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

  async function fetchAccepted(year, { request, signal, onProgress = () => {} }) {
    if (!Number.isInteger(year) || year < 2000 || year > 2100) throw new Error("请输入有效年份（2000–2100）。");
    const venueID = `ICML.cc/${year}/Conference`;
    const papers = [], ids = new Set();
    let count;
    do {
      signal?.throwIfAborted();
      const params = new URLSearchParams({ term: venueID, content: "venueid", type: "exact",
        venueid: venueID, limit: "1000", offset: String(papers.length), count: "true", sort: "tmdate:asc" });
      const payload = await request(`https://api2.openreview.net/notes/search?${params}`, signal);
      signal?.throwIfAborted();
      if (!Array.isArray(payload?.notes) || !Number.isInteger(payload.count) || payload.count < 0) {
        throw new Error("OpenReview 返回格式异常，未得到完整论文列表。");
      }
      if (count !== undefined && count !== payload.count) throw new Error("分页期间录用名单数量发生变化，请重新检索。");
      count = payload.count;
      if (!count) throw new Error(`尚未获得 ICML ${year} 的公开录用名单；这不表示没有相关论文。`);
      if (!payload.notes.length) throw new Error("OpenReview 分页提前结束，未得到完整论文列表。");
      for (const note of payload.notes) {
        const paper = normalizeNote(note, year);
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
    if (data.id) keys.add("openreview:" + data.id);
    if (data.url && URL.canParse(data.url)) {
      const url = new URL(data.url);
      if (["openreview.net", "www.openreview.net"].includes(url.hostname) && ["/forum", "/pdf"].includes(url.pathname)) {
        const id = url.searchParams.get("id");
        if (id) keys.add("openreview:" + id);
      }
    }
    for (const line of (data.extra || "").split("\n")) {
      if (line.startsWith("OpenReview ID: ")) keys.add("openreview:" + line.slice(15).trim());
      if (line.startsWith("DOI: ")) addDOI(line.slice(5));
    }
    return keys;
  }

  const escapeHTML = text => String(text).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function evidenceNote(paper, query) {
    const lines = ["Imported by search4paper", `Source: ${paper.url}`, `Venue: ${paper.venueID}`,
      `Topic: ${query.terms.join("; ")}`, `Context: ${query.context.join("; ")}`,
      `Fields: ${query.fields.join(", ")}`,
      ...(paper.evidence || []).map(hit => `[${hit.field}] ${hit.term}: ${hit.text}`)];
    if (paper.bibtex) lines.push("Source BibTeX:", paper.bibtex);
    return lines.map(line => `<p>${escapeHTML(line)}</p>`).join("");
  }

  return { FIELDS, words, phraseMatches, splitTerms, validateQuery, matchPaper, normalizeNote,
    fetchAccepted, identities, evidenceNote };
})();
if (typeof module !== "undefined") module.exports = Search4PaperCore;
