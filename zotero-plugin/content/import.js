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
            item.setField("proceedingsTitle", `${conferenceName} (${conference} ${paper.year})`);
            item.setField("extra", paper.source && paper.source !== "openreview"
              ? `Source ID: ${paper.source}:${paper.id}` : `OpenReview ID: ${paper.id}`);
            // Preserve source author names without guessing surnames.
            item.setCreators(paper.authors.map(name => ({ lastName: name, fieldMode: 1, creatorType: "author" })));
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

  async findPDFs(Zotero, results, { signal, onProgress = () => {} } = {}) {
    const outcomes = [];
    for (const { item, paper } of results.filter(result => result.item)) {
      if (signal?.aborted) break;
      onProgress(outcomes.length + 1, results.length, paper.title);
      let error = "";
      try {
        if (Zotero.Attachments.canFindFileForItem(item)) {
          const failures = [];
          for (const direct of (paper.pdfURL ? [true, false] : [false])) {
            signal?.throwIfAborted();
            let attemptError = "", attachment;
            const stage = direct ? "直接下载" : "Zotero 原生全文查找";
            try {
              const resolvers = direct ? [{ url: paper.pdfURL }]
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
