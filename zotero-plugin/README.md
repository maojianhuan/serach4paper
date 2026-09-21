# search4paper for Zotero 10

完全独立的 XPI。用户安装后即可在 Zotero 内选择会议和年份，获取 ICML / NeurIPS / ICLR / AAAI / ACL / CVPR / ICCV / EMNLP / ECCV / CRYPTO / EUROCRYPT / ASIACRYPT 的官方论文元数据，按关键词筛选并预览候选论文。不需要 Python、Node、外部服务或开启本地通信。

插件运行时使用 `Zotero.HTTP` 访问网络、`IOUtils` 读写元数据，通过 `Zotero.DataDirectory` 和 `PathUtils` 构造路径。XPI 不包含或调用 Python、外部可执行文件或 shell 命令，也不依赖操作系统特定的绝对路径。下文 Python 构建命令仅供源码开发时打包使用。

## 安装与使用

首次启动会在 Zotero 主界面就绪后显示一次简短介绍。关闭欢迎窗口或点击“开始使用”后，使用 Zotero 偏好 `extensions.search4paper.welcomeShown` 记录状态；同一 profile 后续启动和插件更新不会再次自动显示。

当前正在验收同版本的关键词界面、导入修复，以及 AAAI / ACL / CVPR / ICCV / EMNLP / ECCV / CRYPTO / EUROCRYPT / ASIACRYPT 支持，版本号保持 `0.1.3`。本地测试包统一命名为 `dist/search4paper-0.1.3.xpi`；下方 Release 仍是此前发布的安装包，尚未包含这轮修改。确认可用后再更新发布。

下载：[search4paper-0.1.3.xpi](https://github.com/maojianhuan/serach4paper/releases/download/zotero-v0.1.3/search4paper-0.1.3.xpi) · [版本说明与源码标签](https://github.com/maojianhuan/serach4paper/releases/tag/zotero-v0.1.3) · [全部版本](https://github.com/maojianhuan/serach4paper/releases)。选择 Release 中的 `.xpi` 附件；GitHub 自动提供的 Source code 压缩包是源码，不能直接作为插件安装。

同一个 XPI 用于 Windows、Linux、macOS，无需为不同系统重新生成。目前 manifest 适配 Zotero 10.0.x；Linux + Zotero 10.0.3 已实测，Windows/macOS 尚未验收。安装者只需要 Zotero 和 XPI，不需要 Python、Node 或任何 EXE。

1. 在 Zotero 10 中打开 **工具 → 插件（Tools → Plugins）**，点击齿轮菜单的 **从文件安装插件（Install Plugin From File）**，选择下载或构建的 XPI（当前验收使用 `search4paper-0.1.3.xpi`）。安装后重启 Zotero；覆盖同版本测试包时也要重启，使脚本和窗口样式重新加载。
2. 打开 **工具 → search4paper：检索会议论文…**。
3. 在当前测试版中，从下拉框选择 **ICML / NeurIPS / ICLR / AAAI / ACL / CVPR / ICCV / EMNLP / ECCV / CRYPTO / EUROCRYPT / ASIACRYPT**，输入或调节年份，在统一的 **关键词** 框中输入以分号分隔的词组，再选择 **全部满足（AND）** 或 **任意满足（OR）**，点击 **搜索论文**。例如会议 `ICLR`、年份 `2026`、关键词 `anomaly detection; time series`、关系 `AND`。界面实时显示当前表达式和查找位置。
4. 点击候选标题查看作者、摘要和逐字段命中依据；勾选论文左侧的复选框，或点击“选中本页”，确认“已选”数量大于 0 后才能导入。点击标题仅预览，不会自动勾选。结果每页 100 篇，跨页选择保留，“选中本页”只选当前页。
5. 选择“我的文献库”中的目标集合。填写“新建子集合”时，在该位置下创建或复用唯一的同名子集合；留空则直接导入所选集合。
6. 按需填写标签、勾选 **导入后让 Zotero 获取 PDF**，点击 **导入选中论文**。

当前测试包支持将上述八个会议的选中论文导入指定集合或新建子集合，书目会议名称按论文来源填写。没有勾选论文或修改检索条件后尚未重新筛选时，导入按钮保持禁用；鼠标悬停可查看提示。

修改关键词、AND/OR 关系或查找位置后，点击 **应用筛选**，无需重新下载名单；修改会议或年份后需重新搜索。检索优先使用该会议、年份的本地名单，需要获取最新数据时点击 **刷新名单**。集合选择默认使用 Zotero 中唯一选中的个人文献库集合，也可手动选择其他位置。

以 `anomaly detection; time series` 为例：

| 关键词关系 | 命中条件 |
| --- | --- |
| 全部满足（AND，默认） | 两个词组都要命中，可以分别出现在标题和摘要等不同字段 |
| 任意满足（OR） | 任一词组命中即可 |

**查找位置**决定在哪些字段中匹配。勾选标题、摘要、关键词、TLDR 会扩大查找范围，不要求四项都命中。空格连接同一个词组：`time series` 按词序连续匹配，不拆成两个独立关键词；多个关键词用分号 `;` 或 `；` 分开。关系由下拉框选择，不在输入框内解析 AND/OR/NOT 或括号表达式。

ICML、NeurIPS、ICLR 沿用 OpenReview API v2 的精确 `venueid` 查询：

| 会议 | Venue ID |
| --- | --- |
| ICML | `ICML.cc/{年份}/Conference` |
| NeurIPS | `NeurIPS.cc/{年份}/Conference` |
| ICLR | `ICLR.cc/{年份}/Conference` |

新增会议直接访问官方论文库，运行时仍只使用 Zotero/JavaScript API：

| 会议 | 来源与检索范围 | 当前年份范围 |
| --- | --- | --- |
| AAAI | [AAAI Proceedings OAI](https://ojs.aaai.org/index.php/AAAI/oai?verb=ListSets) 的年度 Technical Tracks，含该分组中的 AI Alignment / AI for Social Impact；排除 IAAI、EAAI、学生摘要、演示、Senior Member、Journal Track | 2010 年起（旧论文集分组与新年度分组） |
| ACL | [ACL Anthology 官方 XML](https://aclanthology.org/faq/api/) 的主会 `long` 卷；2020 年前使用 `Pyy.xml` 的第 1 卷，保留该卷范围；排除其他分卷 | 2000 年起 |
| EMNLP | 同一官方 XML 中的主会 `main` 卷；2007–2019 年使用 `Dyy.xml` 的第 1 卷，保留该卷范围；排除其他分卷 | 2007 年起 |
| CVPR | [CVF Open Access](https://openaccess.thecvf.com/CVPR2025?day=all) 主会索引；2023 年起优先使用官方日程摘要，更早年份逐篇读取官方详情页 | 2013 年起 |
| ICCV | [CVF Open Access](https://openaccess.thecvf.com/ICCV2025?day=all) 主会索引；2025 年起优先使用官方日程摘要，更早年份逐篇读取官方详情页 | 2013 年起，仅奇数年 |
| ECCV | [ECVA 官方论文库](https://www.ecva.net/papers.php)，按年份筛选主会索引，逐篇读取详情页摘要 | 2018 年起，仅偶数年 |
| CRYPTO | [IACR CryptoDB](https://www.iacr.org/cryptodb/data/conf.php?year=2024&venue=crypto) 年度目录与论文详情；排除明确标注的 Invited talk/paper | 2000 年起，需官方目录已公开 |
| EUROCRYPT | [IACR CryptoDB](https://www.iacr.org/cryptodb/data/conf.php?year=2024&venue=eurocrypt)，范围同上 | 2000 年起，需官方目录已公开 |
| ASIACRYPT | [IACR CryptoDB](https://www.iacr.org/cryptodb/data/conf.php?year=2024&venue=asiacrypt)，范围同上 | 2000 年起，需官方目录已公开 |

这是插件当前支持的数据格式范围，仍需所选年份的官方数据已公开；不保证未来年份或尚未发布的名单可用。ICCV 2026 不对应一届会议，应选择 2025 等有效年份。EMNLP、ECCV 按用户指定纳入，不能据此把全部支持会议视为 CCF A。

AAAI 2024 年起按年度专用 OAI 分组分页；2010–2023 年从官方历史论文集的技术分组确定名单，再用 OAI `GetRecord` 读取元数据并核对会议年份；不遍历包含其他年份的旧分组分页。旧目录通过官方卷号定位年份（第 24 卷为 2010 年，第 37 卷为 2023 年），逐篇以论文集年份再次校验。不把记录的最后修改时间误当作会议年份。CVPR/ICCV 以主会论文集索引决定名单；官方日程的 Oral/Poster 重复展示不生成重复论文，按规范化后完全相同的标题关联摘要，存在歧义或标题差异时读取对应的官方论文详情页；详情标题与索引也有差异时，要求其官方 PDF 地址与索引一致。不会按相似标题合并论文。早期 CVF 还会遍历官方按日期划分的索引。ECCV 是独立会议，不自动替换偶数年的 ICCV。

IACR 三个会议先读取年度目录，再逐篇读取官方详情中的 DOI、BibTeX、摘要和已有论文链接。仅识别 Download 栏中的直接 PDF 链接，不把演讲幻灯片或搜索链接当作论文，也不访问 Google 或自动搜索 ePrint。官方缺失的历史摘要保持为空，界面显示缺失数量；这类论文仍可按标题检索。

旧 AAAI 和 IACR 需逐篇读取元数据，早期 CVF 和 ECCV 需逐篇请求摘要，首次抓取可能明显更慢；界面显示进度，可取消。只有完整抓取成功才更新现有本地元数据；后续检索可直接读取本地名单。

首次获取需要下载完整元数据，AAAI 还需要多个分页请求，可能耗时数分钟；状态栏显示进度，可取消。没有自动重试或 DBLP 等备用来源；来源请求失败、未公开或格式异常时明确报错，不把失败当成零命中。

## 本地元数据

首次检索某会议、年份时，从对应官方来源获取上述范围内的完整论文名单并保存为 JSON：

```text
<Zotero 数据目录>/search4paper/<会议名>/<年份>.json
```

文件包含格式版本、会议标识、年份、获取时间、论文总数，以及标题、作者、摘要、关键词、TLDR、DOI、来源/PDF 链接和来源 BibTeX。不保存来源原始响应或 PDF 内容，也不会把完整名单自动导入文献库；只有选中的论文才会成为 Zotero 条目。

- 重开窗口或重启 Zotero 后，点击 **搜索论文** 直接读取所选会议、年份的本地文件，可以离线筛选和预览。已有 ICML 本地文件可直接复用。打开外部链接、首次获取名单和下载 PDF 仍需要网络。
- 覆盖信息显示“本地名单”或“已保存到本地”，并保留名单实际获取时间。没有自动过期或后台更新；点击 **刷新名单** 才重新获取该年的完整名单。
- 完整下载成功后，通过临时文件替换旧 JSON。网络失败、分页未完成、分页期间取消或文件写入失败时，旧名单保留；界面明确报错或显示取消，可重新点击 **搜索论文** 读取旧名单。
- 本地文件损坏、格式不支持、数量/会议/年份不一致时明确报错，不自动下载或修复。点击 **刷新名单** 可重新获取并替换文件。
- 各会议、年份独立保存，只加载当前检索的名单。该名单的元数据仍会读入内存，本地保存主要减少重复下载，并支持离线使用。

保存位置跟随 Zotero 数据目录，独立于安装目录；这些 JSON 不参与 Zotero 文献条目同步。若要清理某一年，可关闭检索窗口后删除对应 JSON，下次检索时重新获取。

## 检索与导入规则

- 搜索支持上述八个会议，按对应官方来源和主会范围获取；不包含投稿全集或研讨会。没有公开名单时明确提示不可用，不把它解释为没有相关论文。
- 仅在完整分页、数量一致、ID 不重复、会议标识一致后展示结果。获取失败或取消时不提供部分名单。
- 单一关键词框用分号分隔词组；AND 要求全部词组命中，OR 要求至少一个命中。不同关键词可在不同的勾选字段中出现，单个词组不能跨字段拼接。每个词组沿用已有的大小写、词形、词干和连字符匹配规则；这是关键词匹配，不是语义模型检索。
- 可搜索标题、摘要、关键词和 TLDR。来源没有提供的关键词、TLDR、DOI、BibTeX 保持为空，不从标题或摘要自动生成。原始摘要和命中片段直接展示；缺失摘要数量显示在覆盖信息中。
- 按 DOI 或来源 ID 去重：保留区分大小写的 OpenReview ID；新增 AAAI 文章 ID、ACL Anthology ID、CVF 论文路径，并识别已有条目的对应官方 URL。继续识别 Python 版写入的 `OpenReview ID:`。不按标题相似度合并。多个既有条目匹配时停止后续导入并明确报错。
- 已有条目只追加目标集合、标签及缺失的来源笔记，保留用户修改。来源笔记包含查询条件、命中依据和来源提供的 BibTeX。作者全名以单字段保留，不猜测姓/名拆分；正式引用前可在 Zotero 中调整。
- 新条目和来源笔记在同一 Zotero 事务中保存。导入期间取消或出错时，已经完成的论文保留，结果显示未处理数量。
- PDF 使用 Zotero 原生附件接口。有直接 PDF 地址时先下载，失败后继续调用 Zotero 原生全文查找；没有直接地址时直接调用原生全文查找。两种方式都失败时保留条目，并分别显示失败原因；成功取得 PDF 后不再显示前一步失败信息。已有 PDF 会复用。访问限制、验证码或下载失败不影响已导入的文献条目。取消 PDF 操作会在当前论文处理结束后停止后续论文。
- 元数据按会议、年份保存为本地 JSON，不创建额外的数据库。不支持群组文献库、PDF 全文内嵌预览或上述八种以外的会议。

## 从源码构建

打包入口是本目录的 `build.py`，只使用 Python 3 标准库，不需要安装 pip/npm 依赖。在仓库根目录运行：

```text
python zotero-plugin/build.py
```

若 Python 3 的命令名不同，Linux/macOS 可用 `python3 zotero-plugin/build.py`，Windows 可用 `py -3 zotero-plugin/build.py`。这是开发者打包步骤，插件运行时不会执行这些命令。

版本号从 `manifest.json` 读取；当前产物为 `dist/search4paper-0.1.3.xpi`。构建脚本将 manifest、bootstrap 和 HTML/CSS/JavaScript 打包为 ZIP 格式的 XPI，不包含论文快照、Python、测试代码或平台二进制。源码或版本变化后重新打包一次即可，跨操作系统使用无需重新构建。

| 文件 | 用途 |
| --- | --- |
| `zotero-plugin/build.py` | 生成 Zotero 插件 XPI；仅打包时需要 Python 3 |
| `dist/search4paper-0.1.3.xpi` | 用户安装到 Zotero 的插件 |
| `search4paper-ui.exe` | 另一个独立 Windows 应用，不生成 XPI |
| `code/build_ui_exe.py` | 使用 PyInstaller 构建上述 EXE，与 XPI 无关 |

## 版本与发布

Git 跟踪插件源码、`build.py`、`manifest.json` 中的版本号和发布工作流。`dist/` 保持在 `.gitignore` 中；正式 XPI 作为对应 GitHub Release 的附件保存，不提交到 Git。

本轮在 `0.1.3` 内迭代验收，只提供本地测试 XPI，不创建新版本标签、不更新 Release。待用户确认可用后，再处理 `0.1.3` 的正式发布更新。

发布工作流为 [release-zotero.yml](../.github/workflows/release-zotero.yml)，推送 `zotero-v*` 标签时运行。它校验标签与 manifest 版本一致，运行 JavaScript 测试，从标签指向的源码打包，然后创建 Release 并上传 XPI。Release 说明取自带注释标签的消息。Python、Node 和 GitHub CLI 只在 GitHub 的构建环境中使用，不进入 XPI。

发布新版本时：

1. 更新 `manifest.json` 版本、README 中的版本下载链接和验证记录，提交并推送源码。
2. 对该提交创建带注释标签，并在标签消息中说明变更和实际验证范围。下面以 `0.1.3` 为示例；后续发布需替换成新版本号，不覆盖已发布标签或安装包。

   ```bash
   git tag -a zotero-v0.1.3 -m "search4paper for Zotero 0.1.3"
   git push origin zotero-v0.1.3
   ```

3. 在仓库 Actions 页面确认发布工作流成功，并从 Release 下载 XPI 核对。普通源码推送、本地打包不会创建 Release。

若标签推送后没有运行记录，可在 [Release Zotero plugin 工作流](https://github.com/maojianhuan/serach4paper/actions/workflows/release-zotero.yml) 中点击 **Run workflow**，选择 `main` 分支，输入已存在的标签（例如 `zotero-v0.1.3`）后启动。工作流仍从该标签打包，不会修改标签；已存在的 Release 不会被自动覆盖。

Zotero 要求 manifest 提供更新清单地址；`updates.json` 当前为空，用户从 Release 下载后手动安装更新。发布 Release 本身不会启用插件自动更新。

## IEEE Xplore 北航机构访问（本地 0.1.3 测试包）

展开插件底部的 **IEEE Xplore · 北航机构访问**，点击 **IEEE 北航机构登录**。插件使用 `Zotero.HTTP.newCookieContext()` 创建会话，交给带相同 `userContextId` 的 Zotero 可见浏览器。用户自行点击 IEEE 的 Institutional Sign In，选择 **Beihang University**，完成现有 SSO。插件不读取账号密码、不解析 SAML、不从外部浏览器复制 Cookie，也不将认证信息写入插件文件。Cookie 由 Zotero 管理；关闭检索窗口后继续保留本次插件会话，禁用插件或正常退出时释放上下文。

在 Zotero 主窗口选中已有 IEEE 文献条目，回到插件点击 **为 Zotero 中选中的条目下载 IEEE PDF**。条目的 URL 栏需填写 `https://ieeexplore.ieee.org/document/论文编号`，或带 `arnumber` 的 IEEE `stamp/stamp.jsp` / `stampPDF/getPDF.jsp` 地址。此功能不增加 IEEE 检索来源。下载使用同一个 Cookie 上下文；按 Zotero 返回的所选条目顺序逐篇处理，前一篇处理结束才开始下一篇，连续请求开始至少相隔 10 秒，失败及跨批次也计入间隔。已有可用 PDF 直接复用。响应须通过 PDF 文件头与结束标记检查，再使用 Zotero 附件 API 加入原条目；失败保留条目并逐篇说明原因。

状态提示区区分“尚未验证”和“PDF 访问已验证”；插件不会仅凭打开登录页或成功下载开放获取论文就宣称北航认证成功。真实机构权限应在 IEEE 页面及已知受限论文上确认。

Windows 手动验收：

1. 从本次构建的 `dist/search4paper-0.1.3.xpi` 安装并重启 Zotero 10；此前 Release 尚不包含此功能。
2. 准备至少四篇北航订阅范围内、已知非开放获取的 IEEE 文献条目，URL 填写上述 IEEE 地址，且没有现有 PDF 附件。
3. 打开 **工具 → search4paper：检索会议论文…**，展开 IEEE 区域并点击登录。只在弹出的 Zotero 浏览器中选择北航、手动完成 SSO，确认 IEEE 页面显示机构访问；不要改用外部浏览器登录。
4. 在 Zotero 主窗口选中第一篇，回到插件点击 IEEE 下载按钮。确认成功提示、原条目下的 PDF 附件，以及附件可正常打开。
5. 选中第二篇重复下载，不再次点击登录。确认仍成功；关闭再打开检索窗口后也应保留本次会话。
6. 同时选中另外两篇无 PDF 的条目，运行一次 IEEE 下载。确认按列表中的选择顺序逐篇处理，界面逐篇显示成功/失败与“请求开始”时间；相邻开始时间至少相差 10 秒，前一篇完成后才处理下一篇。
7. 对无权限或已失效会话的论文确认报告失败且原条目仍在，不把登录 HTML 添加成 PDF。可取消当前批次，确认不会继续下载后面的论文。

## 验证

首次欢迎窗口：46 项 Node 测试通过（新增 2 项），验证 UI 就绪、关闭/确认记录偏好、重启/更新不重复显示及关闭主窗口后不弹出。Linux Zotero 10.0.3 隔离 profile 实测：默认偏好未设置时显示，点击“开始使用”后偏好为 true，退出并重启后不再显示；540 × 370 窗口完整显示内容。Windows/macOS 未实测本次欢迎窗口。

IEEE 机构访问（2026-09-21）：44 项 Node 测试通过（其中 8 项 IEEE 测试），覆盖共享上下文、顺序/间隔/跨批次、403 和登录 HTML、截断 PDF、URL 范围、已有附件、取消及释放。安装后的 XPI 在 Linux + Zotero 10.0.3 中通过本地模拟站点检查：可见浏览器与 HTTP 共享测试 Cookie、上下文隔离、两篇 PDF 的真实附件导入、实际开始间隔 10,001.779 ms、关闭/重开检索窗口保留上下文，以及释放后 Cookie 清除。此测试未完成真实北航 SSO，也未验证真实 IEEE 订阅 PDF；需按上面的 Windows 步骤验收。版本仍为 0.1.3，未更新 Release。

PDF 下载后续查找修改（2026-09-21）：36 项 Node 测试通过，其中 6 项新增测试使用模拟 Zotero API 验证直接下载 403 / 异常后执行原生全文查找、直接成功不再查找、两步失败保留条目并报告两个原因、无直接链接、已有 PDF 复用和取消后不启动查找。本次未验证 OpenReview 上受限 PDF 的实际下载成功率，也未重新进行真实 Zotero 下载联调。XPI 继续使用 0.1.3。

本轮 IACR 三会议验证（2026-09-21，Linux + Zotero 10.0.3）：30 项 Node 测试通过；原生 DOMParser 测试覆盖三个会议、邀请报告排除、跨会议/年份校验、重复 ID、缺失摘要，以及 DOI / 论文链接与演讲幻灯片的区分。官方全量抓取结果：

| 会议与年份 | 论文数 | 缺少摘要 | 有 DOI |
| --- | ---: | ---: | ---: |
| CRYPTO 2024 | 143 | 0 | 143 |
| EUROCRYPT 2024 | 105 | 0 | 105 |
| ASIACRYPT 2024 | 124 | 0 | 122 |
| CRYPTO 2000 | 32 | 32 | 32 |

最终 XPI 覆盖安装并正常重启后，12 个会议下拉项及文献库父子集合的实际鼠标选择通过，原有 OpenReview 三会议本地检索结果保持一致；上述四组数据均通过离线 JSON 检索、刷新失败保留原文件、摘要/缺失摘要提示、实际鼠标勾选、导入子集合和重复导入去重。默认研究词组没有命中，导入验收使用各组真实论文的标题。

官方未提供的摘要或 DOI 保持为空，不通过其他来源猜测补全。以上是已全量测试的年份，其他年份未逐年全量验证；Windows/macOS 和 PDF 下载未实测。三个会议共用现有年份、AND/OR 关键词、查找位置、预览、本地 JSON 与 Zotero 导入流程。版本保持 0.1.3，尚未更新 Release。可使用 `runSearch4PaperOfficialSourcesSmoke` 的 `cases: [['CRYPTO', 2024], ['EUROCRYPT', 2024], ['ASIACRYPT', 2024]]` 复测。

本轮历史年份与 ECCV 验证记录（2026-09-21，Linux + Xvfb + Zotero 10.0.3）：29 项 Node 测试通过；已安装的 XPI 内原生 DOMParser 测试通过，覆盖旧目录编号、AAAI 年度论文集分组/年份核验、CVF 按日期索引/相对 PDF 地址/详情页摘要、ECCV 年份隔离与不同作者格式。

| 实际完整抓取 | 论文数 | 缺少摘要 |
| --- | ---: | ---: |
| AAAI 2010 | 259 | 0 |
| ACL 2019 | 660 | 0 |
| EMNLP 2019 | 681 | 0 |
| CVPR 2013 | 471 | 0 |
| ICCV 2013 | 454 | 0 |
| ECCV 2018 | 776 | 0 |

最终 XPI 覆盖安装、正常重启后，九个会议下拉选项、AND/OR 和文献库父子集合的真实鼠标选择通过；原有 OpenReview 三个会议的本地检索结果保持一致。以上六组真实数据均通过本地 JSON 读取、离线搜索、刷新失败保留文件、预览、真实鼠标勾选、导入子集合和重复导入去重。默认研究词组在上述年份均未命中，导入测试使用真实论文标题。AAAI 2023 核验了年度技术分组名单（1,720 篇）及前 3 篇元数据，未完成该年的全量元数据下载；ECCV 2020 / 2022 / 2024 分别解析完整索引 1,358 / 1,645 / 2,387 篇，并检查每年一篇真实摘要，未全量抓取这三年的摘要。另验证 ACL 2005 / EMNLP 2007 的完整 XML 解析，以及 CVPR 2018 / ICCV 2019 的单日官方索引。

AAAI 旧 OAI 共享分组的分页曾实际返回 HTTP 500，因此旧年份改为年度论文集分组加逐篇 `GetRecord`，不跳过失败页面或用不完整名单替代。以上是实际验证样本，不表示所有年份均已全量测试。运行时仍仅使用 Zotero/JavaScript API；本轮未测试 PDF 下载、Windows 或 macOS。版本保持 0.1.3，未更新 Release。

本轮 0.1.3 新增会议验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：28 项 Node 测试通过；Zotero 原生 DOMParser 的 XML/HTML、分组排除、分页完整性、取消、年份限制和官方标题差异测试通过。真实联网获取结果如下，均没有缺失摘要：

| 会议（2025） | 获取论文数 | `anomaly detection; time series` 按 AND 命中数 |
| --- | ---: | ---: |
| AAAI | 3,182 | 4 |
| ACL | 1,602 | 1 |
| CVPR | 2,871 | 0 |
| ICCV | 2,701 | 0 |
| EMNLP | 1,809 | 0 |

上述数量仅对应本次获取的官方范围与匹配规则。零命中时，导入验收改用一篇真实论文的标题作为检索词，不把测试条目当作研究方向命中。五个会议分别通过本地 JSON 保存、阻断检索网络后重搜、刷新失败保留旧文件、摘要/命中依据预览、实际鼠标勾选、导入目标子集合和重复导入去重；未新增重复条目或来源笔记。AAAI 的特殊技术分卷与普通分卷名称不同，CVF 部分索引与详情页标题存在拼写差异，均已按官方分组或论文 PDF 身份核验并加入回归测试。

最终安装包覆盖安装并正常重启后，八个会议选项及文献库父子集合的真实鼠标选择回归通过；原有 ICML 2026、NeurIPS 2025、ICLR 2026 的本地名单仍分别为 6,341 / 5,286 / 5,351 篇，默认 AND 仍命中 11 / 9 / 17 篇。事务回滚、重复标识冲突、批次取消回归通过。XPI 保持 `0.1.3`，包含 8 个与源码一致的文件；未更新 Release。本轮未验证 PDF 下载，Windows/macOS 尚未实测。

新增来源的真实 Zotero 验收脚本为 `tests/zotero_plugin_sources_smoke.js`。在独立测试 profile/data 中加载后，运行：

```javascript
Services.scriptloader.loadSubScriptWithOptions(
  "file:///ABSOLUTE/REPO/tests/zotero_plugin_sources_smoke.js", { target: window, ignoreCache: true }
);
return await runSearch4PaperOfficialSourcesSmoke({
  expectedDataDir: "/ABSOLUTE/ISOLATED/ZOTERO/DATA",
  reportPath: "/ABSOLUTE/TEST/OFFICIAL-SOURCES-REPORT.json"
});
```

默认联网刷新 AAAI / ACL / CVPR / ICCV / EMNLP 的 2025 年名单和 ECCV 2024 年名单，核对元数据、本地离线复用和刷新失败保留文件，实际鼠标勾选候选，并将每个会议的一篇论文导入新建测试子集合，再重复导入核对去重。不下载 PDF。可用 `cases` 指定会议/年份，`refresh: false` 只在没有本地名单时联网。同一文件中的 `runSearch4PaperSourceFixtureSmoke({ expectedDataDir, reportPath })` 使用合成协议数据和 Zotero 原生 DOMParser，测试 XML/HTML 解析、主会卷筛选、AAAI 分页计数和重复、CVF 日程重复展示与标题差异、取消和年份限制，不联网、不写入文献库。

本轮 0.1.3 导入修复验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：修复前通过鼠标勾选和“选中本页”复现 NeurIPS / ICLR 按钮仍禁用，ICML 正常。修复后 26 项 JavaScript 测试通过；原生窗口测试分别勾选并导入三个会议的合成条目，核对目标子集合和正确的会议名称。使用已保存的真实名单，将 ICML 2026 的 11 篇已有条目加入测试子集合，新建并导入 NeurIPS 2025 的 9 篇、ICLR 2026 的 17 篇条目；各批次重复导入均复用已有条目，没有新增重复条目或笔记。来源笔记写入失败时的事务回滚、重复标识冲突及批次取消回归通过。

同轮还修正主按钮悬停时变浅、白色文字难以辨认的样式。最终 `search4paper-0.1.3.xpi` 已覆盖安装并正常重启 Zotero（不使用清缓存启动参数），验证仍为 0.1.3、ICLR 勾选后可导入、悬停保持深蓝色。安装包全部 7 个文件与源码一致。本轮未测试 PDF 下载，Windows/macOS 尚未实测，正式 Release 尚未更新。

本轮 0.1.3 关键词测试包验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3，尚未更新 Release）：25 项 JavaScript 测试通过，覆盖 AND/OR、跨字段命中、词组边界和查询校验。在真实插件中展开并用鼠标选择 AND/OR，同时回归三个会议、文献库根目录及父子集合选择。阻断检索网络请求后，使用已保存的 ICML 2026 名单（6,341 篇），`anomaly detection; time series` 在全部字段中按 AND 命中 11 篇、OR 命中 212 篇；只查标题的 AND 命中 6 篇。切换关系后的筛选、禁止导入旧选择、摘要及命中依据展示、空关键词/空查找位置报错、中英文分号均已验证；980×720 窗口中条件说明和底部操作完整可见。Windows/macOS 尚未实测。

0.1.3 修复会议和导入位置下拉菜单无法点击、文字重叠的问题：窗口加载 Gecko 原生控件样式，并保留原生下拉箭头。搜索和导入逻辑沿用原实现。

0.1.3 验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：24 项 JavaScript 测试通过。新增原生窗口回归测试通过坐标发送鼠标事件，实际展开并点击菜单，不直接赋值 `select.value`；原始 0.1.2 XPI 在首次选择 NeurIPS 时失败，0.1.3 通过三个会议、文献库根目录、父集合、子集合的选择，刷新后保留选中位置，并把一个明确标注的测试条目导入所选子集合。重启 Zotero、重开插件后，键盘选择会议和集合、980×720 窗口显示也已验证；选择 ICLR 2026 后从已有本地名单读取 5,351 篇论文、筛出 17 篇候选。此前的检索测试直接设置控件值，未覆盖菜单展开与实际点击；此处补齐该检查。Windows/macOS 尚未实测。

纯 JavaScript 检索及元数据校验测试使用 Node 20+ 自带的测试运行器，无需安装 npm 依赖：

```bash
node --test tests/test_zotero_plugin.cjs
```

下拉菜单回归测试为 `tests/zotero_plugin_smoke.js` 中的 `runSearch4PaperDropdownSmoke({ expectedDataDir, reportPath })`，按下文真实 Zotero 脚本的方式加载。仅在独立测试 profile/data 中运行；会实际选择八个会议选项，创建测试父子集合及原有三个 OpenReview 会议各一个合成文献条目，通过实际鼠标勾选、点击导入并核对书目会议名称，不联网抓取论文或下载 PDF。它需要真实 Zotero 窗口，不能用 Node 测试代替。手工验收时还应分别用鼠标和键盘展开会议、关键词关系和导入位置三个下拉框，确认选项、下拉箭头和完整集合路径可见，选中后值确实改变。

会议/年份搜索的真实 Zotero 测试脚本为 `tests/zotero_plugin_search_smoke.js`，只获取和保存元数据、筛选并预览，不创建文献条目。安装新版 XPI 后，在独立测试 profile/data 中通过 **Tools → Developer → Run JavaScript** 运行：

```javascript
Services.scriptloader.loadSubScriptWithOptions(
  "file:///ABSOLUTE/REPO/tests/zotero_plugin_search_smoke.js", { target: window, ignoreCache: true }
);
return await runSearch4PaperConferenceSmoke({
  expectedDataDir: "/ABSOLUTE/ISOLATED/ZOTERO/DATA",
  reportPath: "/ABSOLUTE/TEST/SEARCH-REPORT.json"
});
```

该脚本测试 ICML 2026、NeurIPS 2025、ICLR 2026/2025 的分页获取、字段筛选、摘要与命中依据展示、本地名单复用及会议/年份隔离。

0.1.2 验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：24 项 JavaScript 测试通过；真实 XPI 分别获取 ICML 2026 的 6,341 篇、NeurIPS 2025 的 5,286 篇、ICLR 2026 的 5,351 篇、ICLR 2025 的 3,703 篇论文。主题 `anomaly detection`、上下文 `time series` 在默认字段下分别命中 11、9、17、8 篇，离线读取后结果一致。会议/年份切换、摘要和命中依据预览、字段筛选、旧 ICML 本地文件复用及文件隔离均通过。此轮只测试检索，不执行导入或 PDF 下载；Windows/macOS 尚未实测。

联网过程中曾遇到分页中返回空名单/总数不一致的响应，原有完整性检查正确终止并保留旧文件；手动复测后取得完整名单。没有加入自动重试或放宽分页检查。

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

已有 ICML 功能及 0.1.1 本地元数据验证记录（2026-09-20，Linux + Xvfb + Zotero 10.0.3）：

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
