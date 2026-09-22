/* Strict arXiv fallback matching. Never select a result from title alone. */
var Search4PaperArxiv = (() => {
  const text = node => (node?.textContent || "").replace(/\s+/g, " ").trim();
  const normalized = value => String(value || "").normalize("NFKD").toLowerCase()
    .replace(/\p{M}/gu, "").replace(/[^a-z0-9]+/g, " ").trim();
  const surname = value => normalized(value).split(" ").filter(Boolean).pop() || "";
  const latestID = value => String(value || "").replace(/^https?:\/\/arxiv\.org\/abs\//, "").replace(/v\d+$/i, "");
  let lastRequestAt = 0;

  function explicitPDF(value) {
    if (!value || !URL.canParse(value)) return "";
    const url = new URL(value);
    if (url.protocol !== "https:" || !["arxiv.org", "www.arxiv.org"].includes(url.hostname)) return "";
    const match = url.pathname.match(/^\/(?:abs|pdf)\/([^/]+?)(?:\.pdf)?$/);
    const id = latestID(match?.[1]);
    return id && /^(?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7}|\d{4}\.\d{4,5})$/i.test(id)
      ? `https://arxiv.org/pdf/${id}` : "";
  }

  async function resolve(item, paper, { request, signal, wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    const known = explicitPDF(paper.pdfURL) || explicitPDF(paper.url) || explicitPDF(item.getField("url"));
    if (known) return { pdfURL: known, arxivID: known.split("/").pop(), matchedBy: "explicit" };
    const title = item.getField("title").trim();
    const creators = item.getCreators?.() || [];
    const authors = creators.map(author => author.lastName || author.name || "").filter(Boolean);
    if (!title || !authors.length) return null;
    const yearText = String(item.getField("date") || paper.year || "");
    const year = Number(yearText.match(/\b(19|20)\d{2}\b/)?.[0] || 0);
    const query = `ti:\"${title.replace(/[\"\\]/g, " ")}\"`;
    const url = "https://export.arxiv.org/api/query?" + new URLSearchParams({
      search_query: query, start: "0", max_results: "10",
      sortBy: "lastUpdatedDate", sortOrder: "descending"
    });
    const delay = Math.max(0, 3000 - (Date.now() - lastRequestAt));
    if (delay) await wait(delay);
    signal?.throwIfAborted();
    lastRequestAt = Date.now();
    const doc = new DOMParser().parseFromString(await request(url, signal, "text"), "application/xml");
    if (doc.querySelector("parsererror")) throw new Error("arXiv 返回了无效的 XML。");
    const wantedAuthors = new Set(authors.map(surname).filter(Boolean));
    const required = Math.min(2, wantedAuthors.size);
    const matches = [...doc.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "entry")].filter(entry => {
      if (normalized(text(entry.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "title")[0])) !== normalized(title)) return false;
      const found = new Set([...entry.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "author")]
        .map(author => surname(text(author.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "name")[0]))).filter(Boolean));
      if ([...wantedAuthors].filter(name => found.has(name)).length < required) return false;
      const published = Number(text(entry.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "published")[0]).slice(0, 4));
      return !year || !published || Math.abs(year - published) <= 1;
    });
    const candidates = new Map(matches.map(entry => {
      const id = latestID(text(entry.getElementsByTagNameNS("http://www.w3.org/2005/Atom", "id")[0]));
      return [id, entry];
    }).filter(([id]) => id));
    if (candidates.size !== 1) return null;
    const arxivID = [...candidates.keys()][0];
    return { arxivID, pdfURL: `https://arxiv.org/pdf/${arxivID}`, matchedBy: "title-authors-year" };
  }

  return { explicitPDF, resolve };
})();
if (typeof module !== "undefined") module.exports = Search4PaperArxiv;
