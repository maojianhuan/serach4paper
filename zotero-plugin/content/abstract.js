/* Strict abstract enrichment. Existing abstracts are never replaced. */
var Search4PaperAbstract = {
  normalize(value) {
    return String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  },

  surname(value) {
    const words = String(value || "").normalize("NFKC").toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
    return words.at(-1) || "";
  },

  authorsMatch(wanted, actual) {
    const left = new Set((wanted || []).map(value => this.surname(value)).filter(Boolean));
    const right = new Set((actual || []).map(value => this.surname(value)).filter(Boolean));
    if (!left.size) return false;
    const matches = [...left].filter(value => right.has(value)).length;
    return matches >= Math.min(2, left.size);
  },

  contentValue(content, key) {
    const field = content?.[key];
    return field && typeof field === "object" && "value" in field ? field.value : field;
  },

  async openReview(paper, request, signal) {
    if (!/^https:\/\/openreview\.net\/forum\?/.test(paper.url || "")) return null;
    const id = new URL(paper.url).searchParams.get("id");
    if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) return null;
    const endpoint = "https://api2.openreview.net/notes/search?"
      + `term=${encodeURIComponent(paper.title)}&type=exact&content=title&source=forum&limit=10`;
    const response = await request(endpoint, signal, "json");
    const matches = (response?.notes || []).filter(note => note.id === id
      && this.normalize(this.contentValue(note.content, "title")) === this.normalize(paper.title));
    if (matches.length !== 1) return null;
    const abstract = String(this.contentValue(matches[0].content, "abstract") || "").trim();
    return abstract ? { abstract, source: "OpenReview", sourceURL: `https://openreview.net/forum?id=${encodeURIComponent(id)}` } : null;
  },

  async semanticScholar(paper, request, signal) {
    const endpoint = "https://api.semanticscholar.org/graph/v1/paper/search?"
      + `query=${encodeURIComponent(paper.title)}&limit=10&fields=title,abstract,authors,year,externalIds,url`;
    const response = await request(endpoint, signal, "json");
    const matches = (response?.data || []).filter(candidate => {
      if (!candidate?.abstract || this.normalize(candidate.title) !== this.normalize(paper.title)) return false;
      if (paper.year && candidate.year && Number(candidate.year) !== Number(paper.year)) return false;
      return this.authorsMatch(paper.authors, (candidate.authors || []).map(author => author.name));
    });
    if (matches.length !== 1) return null;
    return { abstract: matches[0].abstract.trim(), source: "Semantic Scholar", sourceURL: matches[0].url || endpoint };
  },

  async resolve(paper, { request, signal } = {}) {
    if (String(paper.abstract || "").trim()) return { abstract: paper.abstract, source: "原始来源", sourceURL: paper.url || "" };
    const errors = [];
    let openReview;
    try { openReview = await this.openReview(paper, request, signal); }
    catch (error) { errors.push(`OpenReview：${error.message}`); }
    if (openReview) return openReview;
    try {
      const semanticScholar = await this.semanticScholar(paper, request, signal);
      if (semanticScholar) return semanticScholar;
    }
    catch (error) { errors.push(`Semantic Scholar：${error.message}`); }
    if (errors.length) throw new Error(errors.join("；"));
    return null;
  }
};
