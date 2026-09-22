var Search4PaperImport = {
  collection(Zotero, parentID, name) {
    const libraryID = Zotero.Libraries.userLibraryID;
    const parent = parentID ? Zotero.Collections.get(parentID) : null;
    if (parentID && (!parent || parent.deleted || parent.libraryID !== libraryID)) {
      throw new Error("目标集合已不存在，请刷新集合列表。");
    }
    if (!name) return parent;
    const siblings = parentID ? Zotero.Collections.getByParent(parentID) : Zotero.Collections.getByLibrary(libraryID);
    const matches = siblings.filter(collection => collection.name === name);
    if (matches.length > 1) throw new Error("该位置有多个同名子集合，请直接选择目标集合。");
    if (matches.length) return matches[0];
    const collection = new Zotero.Collection();
    collection.libraryID = libraryID;
    collection.name = name;
    if (parentID) collection.parentID = parentID;
    return collection;
  },

  async papers(Zotero, papers, { parentID = null, name = "", tags = [], query, signal, onProgress = () => {} }) {
    const results = [];
    const libraryID = Zotero.Libraries.userLibraryID;
    const index = new Map();
    const addIndex = (keys, item) => {
      for (const key of keys) {
        if (!index.has(key)) index.set(key, new Map());
        index.get(key).set(item.id, item);
      }
    };
    const existing = await Zotero.Items.getAll(libraryID, true, false);
    for (const item of existing) {
      if (!item.isRegularItem()) continue;
      addIndex(Search4PaperCore.identities({ doi: item.getField("DOI"), url: item.getField("url"), extra: item.getField("extra") }), item);
    }
    signal?.throwIfAborted();
    const collection = this.collection(Zotero, parentID, name.trim());
    for (const paper of papers) {
      if (signal?.aborted) break;
      onProgress(results.length, papers.length, paper.title);
      try {
        const keys = Search4PaperCore.identities(paper);
        if (!paper.title || !keys.size) throw new Error("论文缺少标题或来源标识。");
        const matches = new Map();
        for (const key of keys) for (const [id, item] of index.get(key) || []) matches.set(id, item);
        if (matches.size > 1) throw new Error("文献库中有多个相同 DOI/来源 ID 的条目，请先处理重复项。");
        const created = !matches.size;
        const item = created ? new Zotero.Item("conferencePaper") : [...matches.values()][0];
        await Zotero.DB.executeTransaction(async () => {
          if (collection && !collection.id) await collection.save();
          if (created) {
            const conference = Search4PaperCore.CONFERENCES.find(name => Search4PaperCore.venueID(name, paper.year) === paper.venueID);
            const conferenceName = Search4PaperCore.CONFERENCE_NAMES[conference];
            if (!conferenceName) throw new Error(`不支持导入此会议：${paper.venueID}`);
            item.libraryID = libraryID;
            item.setField("title", paper.title);
            item.setField("abstractNote", paper.abstract);
            item.setField("date", String(paper.year));
            item.setField("url", paper.url);
            item.setField("DOI", paper.doi);
            if (conference === "ICDE" || (Search4PaperCore.DATABASE_SOURCES[conference] || Search4PaperCore.SYSTEMS_SOURCES[conference])) item.setField("conferenceName", conferenceName);
            item.setField("proceedingsTitle", `${conferenceName} (${conference} ${paper.year})`);
            if ((Search4PaperCore.DATABASE_SOURCES[conference] || Search4PaperCore.SYSTEMS_SOURCES[conference])) {
              if (paper.publicationTitle) item.setField("proceedingsTitle", paper.publicationTitle);
              if (paper.date) item.setField("date", paper.date);
              if (paper.pages) item.setField("pages", paper.pages);
            }
            const identity = paper.source && paper.source !== "openreview"
              ? `Source ID: ${paper.source}:${paper.id}` : `OpenReview ID: ${paper.id}`;
            item.setField("extra", [identity, paper.pdfURL ? `Full Text URL: ${paper.pdfURL}` : ""].filter(Boolean).join("\n"));
            // Preserve source author names without guessing surnames.
            item.setCreators(paper.authors.map(name => ({ lastName: name, fieldMode: 1, creatorType: "author" })));
          }
          if (paper.source === "icde") {
            const extra = item.getField("extra");
            const sourceLine = `Official URL: ${paper.sourceURL}`;
            if (!extra.split("\n").includes(sourceLine)) item.setField("extra", [extra, sourceLine].filter(Boolean).join("\n"));
          }
          if (collection) item.addToCollection(collection.id);
          for (const tag of tags) item.addTag(tag);
          await item.save();
          const notes = created ? [] : await Zotero.Items.getAsync(item.getNotes());
          if (!notes.some(note => note.getNote().includes("<p>Imported by search4paper</p>"))) {
            const note = new Zotero.Item("note");
            note.libraryID = libraryID;
            note.parentID = item.id;
            note.setNote(Search4PaperCore.evidenceNote(paper, query));
            await note.save();
          }
        });
        addIndex(keys, item);
        results.push({ paper, item, status: created ? "created" : "existing" });
      }
      catch (error) {
        results.push({ paper, status: "failed", error: error.message });
        break;
      }
    }
    return { results, collectionID: collection?.id || null, unprocessed: papers.length - results.length };
  },

  openReviewPDFURL(item) {
    const value = item.getField("url");
    if (URL.canParse(value)) {
      const url = new URL(value);
      if (url.protocol === "https:" && url.hostname === "openreview.net" && !url.port && !url.username && !url.password) {
        if (url.pathname === "/forum" || url.pathname === "/pdf") {
          const id = url.searchParams.get("id");
          if (id && /^[A-Za-z0-9_-]+$/.test(id)) return `https://openreview.net/pdf?id=${encodeURIComponent(id)}`;
        }
        if (/^\/pdf\/[a-f0-9]+\.pdf$/i.test(url.pathname)) return url.href;
      }
    }
    const id = item.getField("extra").match(/^OpenReview ID: ([A-Za-z0-9_-]+)\s*$/m)?.[1];
    return id ? `https://openreview.net/pdf?id=${encodeURIComponent(id)}` : "";
  },

  async findPDFs(Zotero, results, { signal, onProgress = () => {}, direct = "auto",
      directLabel = "直接下载", native = true } = {}) {
    const outcomes = [];
    for (const { item, paper } of results.filter(result => result.item)) {
      if (signal?.aborted) break;
      onProgress(outcomes.length + 1, results.length, paper.title);
      let error = "";
      try {
        // Recover the stable note-based URL from the saved item, even after reopening
        // the window or selecting papers from an older import batch.
        const pdfURL = direct === false ? "" : typeof direct === "string" && direct !== "auto"
          ? direct : this.openReviewPDFURL(item) || paper.pdfURL;
        const existing = await Zotero.Items.getAsync(item.getAttachments());
        let hasExistingPDF = false;
        for (const attachment of existing) {
          if (attachment.attachmentContentType === "application/pdf" && attachment.isFileAttachment()
              && await attachment.fileExists()) hasExistingPDF = true;
        }
        if (!hasExistingPDF && (Zotero.Attachments.canFindFileForItem(item)
            || (pdfURL && item.isRegularItem() && !item.isFeedItem))) {
          const failures = [];
          for (const isDirect of [...(pdfURL ? [true] : []), ...(native ? [false] : [])]) {
            signal?.throwIfAborted();
            let attemptError = "", attachment;
            const stage = isDirect ? directLabel : "Zotero 原生全文查找";
            try {
              const resolvers = isDirect ? [{ url: pdfURL }]
                : Zotero.Attachments.getFileResolvers(item);
              attachment = await Zotero.Attachments.addFileFromURLs(item, resolvers, {
                onBeforeRequest() { signal?.throwIfAborted(); },
                onRequestError(exception) {
                  attemptError = `${exception.status ? `HTTP ${exception.status}: ` : ""}${exception.message || exception}`;
                  return false;
                }
              });
            }
            catch (exception) { attemptError = exception.message || String(exception); }
            if (attachment) break;
            failures.push(`${stage}：${attemptError || "未找到可用全文"}`);
            error = failures.join("；");
          }
        }
      }
      catch (exception) { error = exception.message; }
      const attachments = await Zotero.Items.getAsync(item.getAttachments());
      let hasPDF = false;
      for (const attachment of attachments) {
        if (attachment.attachmentContentType === "application/pdf" && attachment.isFileAttachment()
            && await attachment.fileExists()) hasPDF = true;
      }
      outcomes.push({ itemID: item.id, title: paper.title, hasPDF, error: hasPDF ? "" : error });
    }
    return outcomes;
  }
};
