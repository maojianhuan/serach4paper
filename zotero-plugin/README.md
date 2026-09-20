# search4paper for Zotero 10

完全独立的 XPI。用户安装后即可在 Zotero 内获取 ICML/OpenReview 的公开录用名单、筛选研究方向、预览候选论文并导入个人文献库的指定集合。不需要 Python、Node、外部服务或开启本地通信。

## 安装与使用

1. 在 Zotero 10 中打开 **工具 → 插件（Tools → Plugins）**，点击齿轮菜单的 **从文件安装插件（Install Plugin From File）**，选择 `search4paper-0.1.1.xpi`。
2. 打开 **工具 → search4paper：检索 ICML 论文…**。
3. 选择年份，输入研究方向词组和可选上下文词组，点击 **检索 ICML 论文**。例如主题 `anomaly detection`、上下文 `time series`。
4. 点击候选标题查看作者、摘要和逐字段命中依据；勾选需要导入的论文。结果每页 100 篇，跨页选择保留，“选中本页”只选当前页。
5. 选择“我的文献库”中的目标集合。填写“新建子集合”时，在该位置下创建或复用唯一的同名子集合；留空则直接导入所选集合。
6. 按需填写标签、勾选 **导入后让 Zotero 获取 PDF**，点击 **导入选中论文**。

修改词组或字段后，点击 **应用筛选**，无需重新下载名单；修改年份后需重新检索。检索优先使用该年的本地名单，需要获取最新数据时点击 **刷新名单**。集合选择默认使用 Zotero 中唯一选中的个人文献库集合，也可手动选择其他位置。

## 本地元数据

首次检索某一年时，从 OpenReview 获取完整录用名单并保存为 JSON：

```text
<Zotero 数据目录>/search4paper/ICML/<年份>.json
```

文件包含格式版本、会议标识、年份、获取时间、论文总数，以及标题、作者、摘要、关键词、TLDR、DOI、来源/PDF 链接和来源 BibTeX。不保存 OpenReview 原始响应或 PDF 内容，也不会把完整名单自动导入文献库；只有选中的论文才会成为 Zotero 条目。

- 重开窗口或重启 Zotero 后，点击 **检索 ICML 论文** 直接读取本地文件，可以离线筛选、预览和导入元数据。打开外部链接、首次获取名单和下载 PDF 仍需要网络。
- 覆盖信息显示“本地名单”或“已保存到本地”，并保留名单实际获取时间。没有自动过期或后台更新；点击 **刷新名单** 才重新获取该年的完整名单。
- 完整下载成功后，通过临时文件替换旧 JSON。网络失败、分页未完成、分页期间取消或文件写入失败时，旧名单保留；界面明确报错或显示取消，可重新点击 **检索 ICML 论文** 读取旧名单。
- 本地文件损坏、格式不支持、数量/会议/年份不一致时明确报错，不自动下载或修复。点击 **刷新名单** 可重新获取并替换文件。
- 各年份独立保存，只加载当前检索年份的名单。当前年份的元数据仍会读入内存，本地保存主要减少重复下载，并支持离线使用。

保存位置跟随 Zotero 数据目录，独立于安装目录；这些 JSON 不参与 Zotero 文献条目同步。若要清理某一年，可关闭检索窗口后删除对应 JSON，下次检索时重新获取。

## 检索与导入规则

- 首版只支持 ICML 的公开 OpenReview 录用名单，按 `ICML.cc/{year}/Conference` 精确筛选；不包含投稿全集或研讨会。没有公开名单时明确提示不可用，不把它解释为没有相关论文。
- 仅在完整分页、数量一致、ID 不重复、会议标识一致后展示结果。获取失败或取消时不提供部分名单。
- 主题词组以分号分隔，匹配任意一个；上下文不为空时，所选字段中还必须命中至少一个上下文词组。匹配沿用 Python 版的大小写、词形、词干和连字符规则，不是语义模型检索，也不是布尔查询表达式。
- 可搜索标题、摘要、关键词和 TLDR。原始摘要和命中片段直接展示，不生成或补写摘要；缺失摘要数量显示在覆盖信息中。
- 按 DOI 或区分大小写的 OpenReview ID 去重，也识别已有条目的 OpenReview URL 和 Python 版写入的 `OpenReview ID:`。不按标题相似度合并。多个既有条目匹配时停止后续导入并明确报错。
- 已有条目只追加目标集合、标签及缺失的来源笔记，保留用户修改。来源笔记包含查询条件、命中依据和来源提供的 BibTeX。作者全名以单字段保留，不猜测姓/名拆分；正式引用前可在 Zotero 中调整。
- 新条目和来源笔记在同一 Zotero 事务中保存。导入期间取消或出错时，已经完成的论文保留，结果显示未处理数量。
- PDF 使用 Zotero 原生附件接口。有直接 PDF 地址时交给原生下载与存储功能，没有时调用原生可用文件查找。已有 PDF 会复用。访问限制、验证码或下载失败不影响已导入的文献条目。取消 PDF 操作会在当前论文处理结束后停止后续论文。
- 元数据按年份保存为本地 JSON，不创建额外的数据库。首版不支持群组文献库、PDF 全文内嵌预览或其他会议。

## 从源码构建

构建工具需要 Python 3，使用插件不需要 Python：

```bash
python zotero-plugin/build.py
```

产物为 `dist/search4paper-0.1.1.xpi`。XPI 只包含 manifest、bootstrap 和 HTML/CSS/JavaScript，不包含论文快照、Python 或测试代码。

Zotero 要求 manifest 提供更新清单地址；`updates.json` 当前为空，首版使用手动安装更新。只有将清单发布到仓库地址后，Zotero 才能读取它；本地构建不会发布任何文件。

## 验证

纯 JavaScript 检索及元数据校验测试使用 Node 20+ 自带的测试运行器，无需安装 npm 依赖：

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

本地元数据测试为 `runSearch4PaperMetadataSmoke({ expectedDataDir, expectedIDs, reportPath })`：先运行联网 smoke 测试保存 ICML 2026 名单，再运行此函数；重启 Zotero 后再次加载脚本并运行此函数，可验证跨进程保存。它会阻断检索请求、临时破坏并恢复测试资料库的 2026 JSON，并用 2099/2100 年合成数据验证首次保存、年份隔离和刷新。只能在独立测试资料库中使用，两个合成年份须没有已有文件。

验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：

- 19 项 JavaScript 自动化测试通过。
- 实际安装 XPI，联网完整获取 ICML 2026 的 6,341 篇论文，筛出 11 篇候选，其 OpenReview ID 与 Python 查询结果完全一致；仅搜索标题时为 6 篇。
- 0.1.1 将完整名单保存为 14,476,797 字节（约 13.8 MiB）的 JSON。阻断插件检索请求后，重开窗口及完整重启 Zotero 均能读取名单、保留原获取时间，并筛出相同的 11 篇候选。
- 0.1.1 验证本地文件损坏/年份不符时明确报错，2099/2100 年合成数据按年份独立保存；刷新成功替换内容和获取时间，网络失败、分页中断、取消和实际文件写入失败均保留原文件。新版刷新按钮和覆盖信息在 980×720 窗口下可见。
- 在真实插件窗口中预览、选择和导入 11 篇论文，核对目标父子集合、书目字段、作者原文、标签和来源笔记；重复导入不增加父条目或来源笔记，保留手动修改的标题。
- 验证请求失败、取消请求、修改筛选条件后禁止导入旧选择、事务回滚、标识冲突停止后续导入、插件禁用/启用及 Zotero 重启后的加载。980×720 窗口下主要操作和状态信息可见。
- 通过 Zotero 原生接口下载 [W3C PDF 测试文件](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf)，核验存储的 13,264 字节与对照文件一致，重复导入复用附件。
- 实际 OpenReview 论文 PDF 在这台机器上仍返回 HTTP 403；插件显示具体论文和错误，保留已导入条目。没有把测试文件下载成功当作 OpenReview 全文抓取成功。Windows/macOS 尚未运行验收。

联网测试还发现 OpenReview 偏移量 5,000 的页面曾返回缓存中的空响应；同一请求使用 `Cache-Control: no-cache` 后返回正确页面。插件显式请求重新验证缓存，并继续保留分页完整性检查，不自动重试或用部分名单替代完整名单。

开发接口依据：[Zotero 10 开发说明](https://www.zotero.org/support/dev/zotero_10_for_developers)、[Zotero 10.0.3 附件实现](https://github.com/zotero/zotero/blob/10.0.3/chrome/content/zotero/xpcom/attachments.js)、[OpenReview API v2](https://docs.openreview.net/reference/api-v2/openapi-definition)。
