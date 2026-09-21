# search4paper · Zotero 10 插件

在 Zotero 内检索、预览和导入会议论文，支持按专业领域和 CCF 分类筛选。

[下载 0.1.3 XPI](https://github.com/maojianhuan/serach4paper/releases/download/zotero-v0.1.3/search4paper-0.1.3.xpi) · [Release](https://github.com/maojianhuan/serach4paper/releases/tag/zotero-v0.1.3)

## 安装和使用

在 Zotero 10 的 **工具 → 插件 → 从文件安装插件** 中选择 XPI，重启后从工具菜单打开 search4paper。同版本覆盖安装也需重启。同一 XPI 适用于 Windows、Linux、macOS；运行不需要 Python、Node、EXE 或本地服务。

- **论文检索**：选择会议、年份，输入关键词；分号分隔词组，AND 全部满足，OR 任一满足。勾选的查找字段取并集。点击标题预览，勾选论文后导入目标集合，可新建子集合并添加标签。
- **全文获取**：先在 Zotero 主窗口选中文献，再点击获取全文。IEEE 北航订阅访问需在插件窗口手动完成 SSO；同一会话复用 Cookie，机构下载逐篇进行，开始时间至少间隔 10 秒。全文不保证可得；失败时保留书目条目，已知限制见 [TODO](../TODO.md)。

ACM 条目优先按 DOI 请求官方公开 PDF；遇到 403 时显示“ACM 访问验证”，在 Zotero 窗口中手动完成验证后，再点击“获取全文”。验证窗口与请求共享独立的临时 Cookie 会话，不保存登录信息。网站验证仍可能阻止后台下载，需在 Windows 上实测。

元数据保存在 `<Zotero 数据目录>/search4paper/<会议>/<年份>.json`，支持离线筛选。点击“刷新名单”更新；失败或取消不会覆盖旧名单。缺失的摘要等字段保持为空。首次欢迎提示仅显示一次，状态保存在 Zotero profile 的 `extensions.search4paper.welcomeShown` 偏好中。

## 支持的来源和年份

共 25 个会议；分类使用仓库 CCF 2026 目录。仅已公开且结构受支持的年份可用；不可用或结构异常明确报错，不以 DBLP 替代录用名单。

| 会议 | 官方来源与范围 | 当前支持年份 |
| --- | --- | --- |
| ICML / NeurIPS / ICLR | OpenReview API v2，`会议.cc/年份/Conference` 录用名单 | 所选年份须已公开 |
| AAAI | AAAI Proceedings，Technical Tracks | 2010 起 |
| ACL | ACL Anthology，2020 起主会 long；更早为 Pyy 第 1 卷 | 2000 起 |
| EMNLP | ACL Anthology，2020 起主会 main；更早为 Dyy 第 1 卷 | 2007 起 |
| CVPR / ICCV | CVF 主会论文集 | 2013 起；ICCV 仅奇数年 |
| ECCV | ECVA 主会论文集 | 2018 起，仅偶数年 |
| CRYPTO / EUROCRYPT / ASIACRYPT | IACR CryptoDB 年度目录与详情，排除邀请报告 | 2000 起，目录须公开 |
| ICDE | [2025](https://ieee-icde.org/2025/research-papers/) / [2026](https://icde2026.github.io/accepted-papers.html) 官方 Research Papers | 2025、2026 |
| SIGMOD | [Accepted Papers](https://sigmodconf.hosting.acm.org/2025/sigmod_papers.shtml)，四轮研究论文 | 2025 |
| KDD | OpenReview Research Track 两周期；不可访问时明确标注使用[官方两周期名单](https://kdd.org/kdd2025/research-track-papers-2/) | 2025 |
| SIGIR | [Accepted Papers](https://sigir2025.dei.unipd.it/accepted-papers.html)，Full / Short Papers | 2025 |
| VLDB | [PVLDB 第 18 卷](https://www.vldb.org/pvldb/volumes/18/)，第 1–11 期 Research Track | 2025 |
| FAST | [Technical Sessions](https://www.usenix.org/conference/fast25/technical-sessions)，研究论文 | 2025 |
| NSDI | [Technical Sessions](https://www.usenix.org/conference/nsdi25/technical-sessions)，研究论文 | 2025 |
| OSDI | [Technical Sessions](https://www.usenix.org/conference/osdi25/technical-sessions)，研究论文 | 2025 |
| USENIX Security | [Technical Sessions](https://www.usenix.org/conference/usenixsecurity25/technical-sessions)，研究论文 | 2025 |
| CCS | [Accepted Papers](https://www.sigsac.org/ccs/CCS2025/accepted-papers/)，页面使用的[两周期 JSON](https://www.sigsac.org/ccs/CCS2025/assets/accepted-papers.json) | 2025 |
| NDSS | [Accepted Papers](https://www.ndss-symposium.org/ndss2025/accepted-papers/)，Summer / Fall 合并名单 | 2025 |
| SIGCOMM | [Accepted Papers](https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/)，仅 Full papers | 2025 |
| FSE | [Researchr Research Papers](https://conf.researchr.org/track/fse-2025/fse-2025-research-papers) 的 Accepted Papers 表 | 2025 |

USENIX 四会共用解析路径，并读取详情中的作者、摘要和 PDF 地址，不下载 PDF。Security 优先最终技术日程；仅当其返回 404 时，读取同届官网链接的已公布周期名单，合并去重并标注“非最终完整 proceedings”。CCS 合并 firstCycle / secondCycle；NDSS 官网已合并两周期，页面给出计数时核对完整性。缓存和导入笔记记录实际来源、周期及名单类型，不混入旧缓存。

SIGCOMM shorts 按 [2025 CFP](https://conferences.sigcomm.org/sigcomm/2025/cfp/) 不属于正式会议论文，故排除；官网 Full papers 未逐篇区分 research / experience。FSE 仅 Research Papers，排除 Industry、Journal First、演示等轨道；此处 FSE 指软件工程会议。

KDD 周期为 `KDD.org/2025/Research_Track_August`、`KDD.org/2025/Research_Track_February`。VLDB 年份按[官方卷期归属](https://www.vldb.org/pvldb/vol18/FrontMatterVol18No12.pdf)，不按出版日历年推断；第 13 期归属下一届。新来源均导入 `conferencePaper`，保留官方可得的作者、规范会议名、出版物名、DOI、页码、摘要与链接，不生成缺失元数据。

## 开发和发布

在仓库根目录运行：

```sh
node --test tests/test_zotero_plugin.cjs tests/test_zotero_ieee.cjs tests/test_zotero_database.cjs tests/test_zotero_systems.cjs tests/test_zotero_acm.cjs
python3 zotero-plugin/build.py
```

Windows 可用 `py -3 zotero-plugin/build.py`。Python 3 标准库仅用于打包，输出 `dist/search4paper-0.1.3.xpi`；不需要 EXE 生成器。源码变化后重新打包一次，跨平台无需重建。

Git 跟踪源码、构建脚本和版本号；XPI 存放在 Release，不提交到 Git。推送带注释的 `zotero-v<版本>` 标签会运行[发布工作流](../.github/workflows/release-zotero.yml)，从标签源码测试、打包，创建或更新同名 Release 和安装包。此轮按用户要求更新已有 0.1.3；后续通常使用新版本号。更新清单当前为空，用户手动下载安装。

离线测试使用保存的页面片段；`tests/zotero_systems_fixtures.js` 在 Zotero 原生 DOMParser 中运行。联网验证与普通测试分开：2025 年 FAST 36、NSDI 83、OSDI 53、Security 438、CCS 316、NDSS 211、SIGCOMM 74、FSE 135 篇。USENIX 610 篇均保留摘要和官方 PDF 地址。Linux Zotero 10.0.3 用于缓存、预览和导入验证；本轮未验证 Windows/macOS GUI 或订阅 PDF 下载。
