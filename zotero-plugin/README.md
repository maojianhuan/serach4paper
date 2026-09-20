# search4paper for Zotero 10

完全独立的 XPI。用户安装后即可在 Zotero 内获取 ICML/OpenReview 的公开录用名单、筛选研究方向、预览候选论文并导入个人文献库的指定集合。不需要 Python、Node、外部服务或开启本地通信。

## 安装与使用

1. 在 Zotero 10 中打开 **工具 → 插件（Tools → Plugins）**，点击齿轮菜单的 **从文件安装插件（Install Plugin From File）**，选择 `search4paper-0.1.0.xpi`。
2. 打开 **工具 → search4paper：检索 ICML 论文…**。
3. 选择年份，输入研究方向词组和可选上下文词组，点击 **检索 ICML 论文**。例如主题 `anomaly detection`、上下文 `time series`。
4. 点击候选标题查看作者、摘要和逐字段命中依据；勾选需要导入的论文。结果每页 100 篇，跨页选择保留，“选中本页”只选当前页。
5. 选择“我的文献库”中的目标集合。填写“新建子集合”时，在该位置下创建或复用唯一的同名子集合；留空则直接导入所选集合。
6. 按需填写标签、勾选 **导入后让 Zotero 获取 PDF**，点击 **导入选中论文**。

修改词组或字段后，点击 **应用筛选**，无需重新下载名单；修改年份后需重新检索。集合选择默认使用 Zotero 中唯一选中的个人文献库集合，也可手动选择其他位置。

## 检索与导入规则

- 首版只支持 ICML 的公开 OpenReview 录用名单，按 `ICML.cc/{year}/Conference` 精确筛选；不包含投稿全集或研讨会。没有公开名单时明确提示不可用，不把它解释为没有相关论文。
- 仅在完整分页、数量一致、ID 不重复、会议标识一致后展示结果。获取失败或取消时不提供部分名单。
- 主题词组以分号分隔，匹配任意一个；上下文不为空时，所选字段中还必须命中至少一个上下文词组。匹配沿用 Python 版的大小写、词形、词干和连字符规则，不是语义模型检索，也不是布尔查询表达式。
- 可搜索标题、摘要、关键词和 TLDR。原始摘要和命中片段直接展示，不生成或补写摘要；缺失摘要数量显示在覆盖信息中。
- 按 DOI 或区分大小写的 OpenReview ID 去重，也识别已有条目的 OpenReview URL 和 Python 版写入的 `OpenReview ID:`。不按标题相似度合并。多个既有条目匹配时停止后续导入并明确报错。
- 已有条目只追加目标集合、标签及缺失的来源笔记，保留用户修改。来源笔记包含查询条件、命中依据和来源提供的 BibTeX。作者全名以单字段保留，不猜测姓/名拆分；正式引用前可在 Zotero 中调整。
- 新条目和来源笔记在同一 Zotero 事务中保存。导入期间取消或出错时，已经完成的论文保留，结果显示未处理数量。
- PDF 使用 Zotero 原生附件接口。有直接 PDF 地址时交给原生下载与存储功能，没有时调用原生可用文件查找。已有 PDF 会复用。访问限制、验证码或下载失败不影响已导入的文献条目。取消 PDF 操作会在当前论文处理结束后停止后续论文。
- 数据只在检索窗口存续期间保留，不创建额外的本地论文数据库。首版不支持群组文献库、PDF 全文内嵌预览或其他会议。

## 从源码构建

构建工具需要 Python 3，使用插件不需要 Python：

```bash
python zotero-plugin/build.py
```

产物为 `dist/search4paper-0.1.0.xpi`。XPI 只包含 manifest、bootstrap 和 HTML/CSS/JavaScript，不包含论文快照、Python 或测试代码。

Zotero 要求 manifest 提供更新清单地址；`updates.json` 当前为空，首版使用手动安装更新。只有将清单发布到仓库地址后，Zotero 才能读取它；本地构建不会发布任何文件。

## 验证

纯 JavaScript 检索测试使用 Node 20+ 自带的测试运行器，无需安装 npm 依赖：

```bash
node --test tests/test_zotero_plugin.cjs
```

真实 Zotero 测试脚本为 `tests/zotero_plugin_smoke.js`。仅在独立测试 profile/data 目录中运行，它会创建测试文献及集合、修改其中一个测试标题，并测试禁用/启用插件。先安装构建好的 XPI，然后在 Zotero 的 **Tools → Developer → Run JavaScript** 中加载：

```javascript
Services.scriptloader.loadSubScriptWithOptions(
  "file:///ABSOLUTE/REPO/tests/zotero_plugin_smoke.js", { target: window, ignoreCache: true }
);
return await runSearch4PaperSmoke({
  expectedDataDir: "/ABSOLUTE/ISOLATED/ZOTERO/DATA",
  reportPath: "/ABSOLUTE/TEST/REPORT.json"
});
```

脚本要求 `expectedDataDir` 与 Zotero 实际数据目录完全一致。可传入 `expectedIDs` 数组，与同一时期的 Python 查询结果逐篇比对；实时名单变化时应核查差异，不应直接修改预期以掩盖问题。

同一脚本还提供 `runSearch4PaperPDFSmoke({ expectedDataDir, pdfURL, fixturePath, reportPath })`，使用 HTTPS 测试 PDF 及本地对照文件核验原生下载、存储字节和附件去重；以及 `runSearch4PaperImportFailureSmoke({ expectedDataDir, reportPath })`，核验来源笔记写入失败时的事务回滚、重复标识冲突和批次取消。这些测试只能用于独立测试资料库。

验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：

- 15 项 JavaScript 自动化测试通过。
- 实际安装 XPI，联网完整获取 ICML 2026 的 6,341 篇论文，筛出 11 篇候选，其 OpenReview ID 与 Python 查询结果完全一致；仅搜索标题时为 6 篇。
- 在真实插件窗口中预览、选择和导入 11 篇论文，核对目标父子集合、书目字段、作者原文、标签和来源笔记；重复导入不增加父条目或来源笔记，保留手动修改的标题。
- 验证请求失败、取消请求、修改筛选条件后禁止导入旧选择、事务回滚、标识冲突停止后续导入、插件禁用/启用及 Zotero 重启后的加载。980×720 窗口下主要操作和状态信息可见。
- 通过 Zotero 原生接口下载 [W3C PDF 测试文件](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf)，核验存储的 13,264 字节与对照文件一致，重复导入复用附件。
- 实际 OpenReview 论文 PDF 在这台机器上仍返回 HTTP 403；插件显示具体论文和错误，保留已导入条目。没有把测试文件下载成功当作 OpenReview 全文抓取成功。Windows/macOS 尚未运行验收。

联网测试还发现 OpenReview 偏移量 5,000 的页面曾返回缓存中的空响应；同一请求使用 `Cache-Control: no-cache` 后返回正确页面。插件显式请求重新验证缓存，并继续保留分页完整性检查，不自动重试或用部分名单替代完整名单。

开发接口依据：[Zotero 10 开发说明](https://www.zotero.org/support/dev/zotero_10_for_developers)、[Zotero 10.0.3 附件实现](https://github.com/zotero/zotero/blob/10.0.3/chrome/content/zotero/xpcom/attachments.js)、[OpenReview API v2](https://docs.openreview.net/reference/api-v2/openapi-definition)。
