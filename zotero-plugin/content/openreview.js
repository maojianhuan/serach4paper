/* Resolve OpenReview's immutable PDF object before using the note-id endpoint. */
var Search4PaperOpenReview = {
  id(item) {
    const value = item.getField("url");
    if (URL.canParse(value)) {
      const url = new URL(value);
      if (url.protocol === "https:" && url.hostname === "openreview.net" && !url.port && !url.username && !url.password
          && ["/forum", "/pdf"].includes(url.pathname)) {
        const id = url.searchParams.get("id");
        if (/^[A-Za-z0-9_-]+$/.test(id || "")) return id;
      }
    }
    return item.getField("extra").match(/^OpenReview ID: ([A-Za-z0-9_-]+)\s*$/m)?.[1] || "";
  },

  async URLs(item, { request, signal } = {}) {
    const id = this.id(item);
    if (!id) return [];
    const fallback = `https://openreview.net/pdf?id=${encodeURIComponent(id)}`;
    const title = item.getField("title");
    if (!title) return [fallback];
    const endpoint = "https://api2.openreview.net/notes/search?"
      + `term=${encodeURIComponent(title)}&type=exact&content=title&source=forum&limit=10`;
    const response = await request(endpoint, signal, "json");
    const note = response?.notes?.find(candidate => candidate.id === id);
    const value = note?.content?.pdf?.value;
    const stable = /^\/pdf\/[a-f0-9]{40}\.pdf$/i.test(value || "")
      ? new URL(value, "https://openreview.net").href : "";
    return stable ? [stable, fallback] : [fallback];
  }
};
