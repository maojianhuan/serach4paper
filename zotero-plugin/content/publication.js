/* Strict publisher metadata enrichment for records whose source omitted a DOI. */
var Search4PaperPublication = {
  normalize(value) {
    return String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  },

  isWWW(item, paper = {}) {
    return /^ACM\.org\/TheWebConf\/\d{4}\/Conference$/i.test(paper.venueID || "")
      || /(?:^|\b)(?:the web conference|www)(?:\b|$)/i.test([
        item.getField("conferenceName"), item.getField("proceedingsTitle")
      ].join(" "));
  },

  recordYear(record) {
    for (const value of [record.published, record["published-print"], record["published-online"], record.issued]) {
      const year = value?.["date-parts"]?.[0]?.[0];
      if (year) return String(year);
    }
    return "";
  },

  matches(record, { title, year, authors }) {
    const doi = String(record.DOI || "");
    if (!/^10\.1145\/\d+(?:\.\d+)+$/i.test(doi)) return false;
    if (!(record.title || []).some(value => this.normalize(value) === this.normalize(title))) return false;
    if (this.recordYear(record) !== String(year)) return false;
    const venue = [...(record["container-title"] || []), record.event?.name || ""].join(" ");
    if (!/(?:(?:the|acm on) web conference|\bwww\b)/i.test(venue)) return false;
    const candidates = (record.author || []).map(author => ({
      full: this.normalize([author.given, author.family].filter(Boolean).join(" ")),
      family: this.normalize(author.family)
    }));
    const matched = authors.filter(author => {
      const value = this.normalize(author);
      return candidates.some(candidate => value === candidate.full
        || (candidate.family.length >= 2 && value.endsWith(candidate.family)));
    });
    return matched.length >= Math.min(2, authors.length);
  },

  async resolveWWWDOI(item, paper = {}, { request, signal } = {}) {
    if (item.getField("DOI") || !this.isWWW(item, paper)) return "";
    const title = item.getField("title") || paper.title;
    const year = String(item.getField("date") || paper.year || "").match(/\b(?:19|20)\d{2}\b/)?.[0];
    const authors = item.getCreators().filter(creator => !creator.creatorType || creator.creatorType === "author")
      .map(creator => [creator.firstName, creator.lastName].filter(Boolean).join(" "));
    if (!title || !year || !authors.length) throw new Error("WWW DOI 补全：缺少标题、年份或作者，未修改条目。");
    const filter = `prefix:10.1145,type:proceedings-article,from-pub-date:${year}-01-01,until-pub-date:${year}-12-31`;
    const url = `https://api.crossref.org/works?filter=${encodeURIComponent(filter)}&rows=20&query.bibliographic=${encodeURIComponent(title)}`;
    const response = await request(url, signal, "json");
    const records = response?.message?.items;
    if (!Array.isArray(records)) throw new Error("WWW DOI 补全：Crossref 返回的元数据格式无效。");
    const matches = records.filter(record => this.matches(record, { title, year, authors }));
    const unique = [...new Set(matches.map(record => record.DOI.toLowerCase()))];
    if (!unique.length) throw new Error("WWW DOI 补全：未找到标题、作者、年份和会议均一致的 Crossref 记录；未修改条目。");
    if (unique.length > 1) throw new Error("WWW DOI 补全：存在多个严格匹配记录；未修改条目。");
    if (!item.isEditable()) throw new Error("WWW DOI 补全：文献条目不可编辑。");
    signal?.throwIfAborted();
    item.setField("DOI", matches[0].DOI);
    await item.saveTx();
    return matches[0].DOI;
  }
};
