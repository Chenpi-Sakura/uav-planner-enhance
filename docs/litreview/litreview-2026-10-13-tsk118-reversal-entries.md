# TSK-118 · 翻案入口三条落点：中文期刊面 / S2 面 / [1][2][3] 判词落盘

> 参数：`stage=pre · report_level=standard · caller_role=design · format=report`（发起者 Mika，补 TSK-117 §7 自报的三条缺口）
> 编制：2026-10-13　本轮**只做三条，不重跑 deep、不重判已定的格**（按 issue 边界）。
> 三条的取证通道：中文面＝引擎检索 + 期刊正文/摘要页实读；S2 面＝官方 API 脚本（`s2_retry.py`/`alt_channels.py`，命中数组全落盘）；
> 判词面＝打开出版社页面正文抽取并落盘为 `verdict_evidence.json`/`.txt`。标题/作者/卷页/DOI 一律从脚本或页面输出复制，未手写。
> **零 arXiv 引用**：这三个面（中文气象期刊 / 观测方法学 / 平均窗理论）的同行评审产出不在 arXiv，按 DOI 或期刊落地页 URL 给引用。

## 0. 三条结论速览

| # | 入口 | 落点 | 对 Q1 的作用 |
| --- | --- | --- | --- |
| **一** | 中文期刊面（上轮**完全未检索**） | **未检索到对口先例**；且检索到 5 条**反向证据**：中文界把 TI/TKE 明确锚定在 ≤3 s 采样 / 10 Hz + 10 min 窗 / 10 min 时距，模式侧则用参数化方案**自带的 TKE 预报量** | **中文面不但没翻案，反而加强 ②** |
| **二** | S2 面（上轮 HTTP 429 未看成） | `/paper/search` 本轮**仍 429**（4 次退避全败，`probe.json`）；改用官方 **`/paper/search/bulk`** 端点后**通道打通**（HTTP 200），上轮 5 条原文检索式**零命中** | 「没看成」升级为「看成了，且零命中」 |
| **三** | [1][2][3] 判词 | 三条判词**原文全部维持**；但 [2][3] 的**取证层级写错了**，须由「一手正文命中」降为「一手摘要页命中」 | 判词不塌，**TSK-117 §3 表的取证列需要更正一行** |

**对 Q1 判定的一句话影响（DoD ④）：维持 ②（已被质疑、需要正面回应的做法），且置信度上升——三条翻案入口全部走完，没有一条给出对口先例。**

---

## 1. 入口一 · 中文期刊面

**目标**：判「小时级格点场时间方差称作 TKE / 脉动强度」在中文文献里是否已有对口先例。

### 1.1 检索留痕（8 条检索式，2026-10-13，时间窗不限）

CNKI / 万方无 API 与账号入口，本轮实际走「通用检索引擎 → 期刊官网正文页落地」路径：命中条目**全部落到期刊网站正文/PDF**（`gyqx.ac.cn`、`iapjournals.ac.cn/dqkx`、`qxxb.cmsjournal.net`、`html.rhhz.net/qxxb_cn`、`html.rhhz.net/yyqxxb`、`html.rhhz.net/dqwlxb`、`kqdlxxb.xml-journal.net`、`qxqk.nmc.cn`、`qikan.camscma.cn`、`smylzqx.ijournals.cn`），不是转抄自二手汇总表。

| 检索式 | 结果类型 |
| --- | --- |
| 脉动强度 + 小时 + 平均风速标准差 | 命中均为 10 min / 30 min 口径（见 1.2） |
| 湍流强度 + 标准差 + 小时平均 | 命中 10 min 定义；小时侧仅 CDM 项目文件（非同行评审） |
| TKE + 逐小时 + WRF | 命中的 TKE 全部是**边界层参数化方案自带的预报量**（MYJ/QNSE），非时间方差 |
| 高原 + 湍流 + 小时资料 | 命中《高原气象》大理 30 min 涡动相关、《大气科学》改则/当雄 TIPEX 高频湍流观测 |
| 沙漠与绿洲气象 + 湍流 + 小时 | 命中该刊 2026 测风塔风切变文（高时间分辨率塔观测，非格点小时场） |
| 逐小时风速标准差 估算湍流强度 | 命中《全国风能资源评价技术规定》（国家能源局，官方）与《气象》IEC 适用性文 |
| 青藏高原 + 脉动风特性 + 时距 | 命中《空气动力学学报》2026 西藏高原大风（**10 min 平均风速**取样） |
| WRF + 格点 + 湍流强度（中文） | 命中的是 WRF→LES 耦合，湍流由 LES 分辨，不从小时格点方差导出 |

### 1.2 横断证据表 · 中文口径实际怎么定义这个量

| 出处（venue·年） | 它怎么定义 / 怎么算 | 取证方式 |
| --- | --- | --- |
| 国家能源局《全国风能资源评价技术规定》2012 | 「测站湍流强度（I）：**有风速脉动观测记录**的测点…u′ 脉动风速值（**采样时间间隔 ≤3 s**）」 | **一手正文命中**（nea.gov.cn 通知全文） |
| 《气象》2016(12) IEC 的风机选型参数估算方法在中国的适用性研究 | 「σ 为风速标准差，指阵风（一般 1~3 s 时距）相对平均（一般 **10 min 时距**）风速的标准偏差；V 为 **10 min 平均风速**」；对 193 座测风塔**逐个 10 min 样本**算 TI | **一手正文命中**（qxqk.nmc.cn HTML 全文） |
| 《地球物理学报》梁捷宁等 2014（黄土高原 LLJ 对近地层湍流的影响） | 「用 **10 min 的平均窗区**，对 EC 以 **10 Hz** 频率采样的数据进行处理计算**湍动能 e**」 | **一手正文命中**（html.rhhz.net/dqwlxb 全文） |
| 《高原气象》杨智等 2010(1):117–（云贵高原大理近地层湍流特征） | 「湍流资料处理中，**平均时间尺度采用 30 min**」；I_α = σ_α/Ū，由**涡动相关法**直接计算 | **一手正文命中**（sciengine PDF） |
| 《空气动力学学报》2026（青藏高原西藏区域近地层大风脉动风特性） | 「利用浪卡子和狮泉河两站**超声波风速实测**资料，选取 **10 min 平均风速** > 8 m/s 的大风样本」 | **一手摘要命中**（期刊 PDF 摘要页） |
| 《大气科学》梁捷宁等 2013(37):113–（黄土高原中尺度运动影响的稳定边界层） | 谱隙实测：**感热通量在 112.4~449.9 s 存在谱隙**；「对 EC 资料采用常用的 **30 min 甚至更长时间**的平均，得到的三维风速标准差与 u\* 的关系**表现出极大的离散性，湍流强度不是常数**」；通量平均时间 τ_F 稳定条件下取 1 h，但与湍流平均窗**区分开** | **一手正文命中**（LUVS PDF） |
| 《地球物理学报》张碧辉等 2012（MYJ/YSU 对 WRF 边界层要素的影响） | 「在 MYJ 方案中 **TKE 方法**计算的 PBLH 最大…YSU 方案中**未计算湍流动能**」 | **一手正文命中**（data.geophy.cn HTML） |

**这一节的结论**：能下——**中文界对这个量有明确的时距共识，而且该共识与我们要做的事相反**：TI/TKE 的 σ 必须来自 ≤3 s 采样或 10 Hz 高频涡动相关资料、配 10 min（工程）或 30 min（通量）平均窗；模式侧若要 TKE，用的是边界层方案自带的**预报量**，不是逐小时格点风的时间方差。梁捷宁 2013 甚至给出了与 Archer 同方向的**中文谱隙论据**（30 min 以上平均把中尺度运动混进 σ，致其离散、不再是常数）。
不能下——不能说「中文文献里绝不存在这种做法」：CNKI/万方的关键词库没有系统性扫过，只覆盖了能从公开引擎落到期刊正文的部分（见 §5）。

---

## 2. 入口二 · Semantic Scholar 面

### 2.1 原端点仍不可用（DoD ②要求的 429 证据）

`s2_retry.py`（stdlib，带退避与 `Retry-After` 记录）对**上轮同一条检索式**打 `/graph/v1/paper/search`：

```
status = BLOCKED
attempt 0 HTTP 429 Retry-After=None
attempt 1 HTTP 429 Retry-After=None
attempt 2 HTTP 429 Retry-After=None
attempt 3 HTTP 429 Retry-After=None
```

原始留痕：`probe.json`（`s2_retry.py --queries-file s2_queries.json`，2026-10-13T02:19:57）。
**结论：不是退避不够，是端点级拒绝**（无 `Retry-After` 头，官方对未认证流量的一贯行为）。

### 2.2 换官方 bulk 端点后通道打通

`/graph/v1/paper/search/bulk` 返回 **HTTP 200**，上轮 5 条检索式**原文照跑**（`s1.json` 的 `query` 字段逐条复制，未重新设计）：

| 检索式（原文复制自 TSK-117 `s1.json`） | bulk 状态 | 命中数 |
| --- | --- | --- |
| turbulent kinetic energy estimated from hourly wind speed standard deviation reanalysis | **OK** | **0** |
| turbulence intensity derived from numerical weather prediction model time series | **OK** | **0** |
| grid point wind variance as turbulence proxy model climatology | **OK** | **0** |
| mesoscale wind variance interpreted as turbulence underestimate | **OK** | **0** |
| temporal resolution too coarse turbulence statistics underestimate | **OK** | **0** |
| eddy covariance flux averaging time period sensitivity（`s2.err` 前缀 recovery） | OK | 18（多不相关） |
| sampling error of turbulence statistics temporal resolution（同上） | OK | 9（多不相关） |

`OpenAlex` 并行通道：7 条全部 **BLOCKED（HTTP 429 ×3 次尝试）**，未提供替代证据。

**能下的结论**：S2 面从「没看成」升级为「**看成了，且直接检索式零命中**」——它与 Crossref 的 40 式 160 命中指向同一件事：英文侧也**没有**把「小时格点方差 = TKE/TI」写出来并命名的工作。
**必须同时下的限定**：bulk 端点是**词面布尔式匹配**，不是语义相关检索，且返回体**不带 citationCount/venue**（见 `alt_out.json`，字段大面积 null）。所以「0 命中」的证据力**弱于**它听起来的样子——它只能证「这串词的组合不成文」，不能证「这个概念组合无人做过」。这也正是 TSK-117 §7 第三条缺口自报的同一局限，换端点没有消解它。
**顺带捞到的两条**（bulk 面唯一有价值的增量，已用 Crossref 逐条回读核验：`s2_new_dois.json`）：Metzger & Holmes 2007 *BLM* 126(1):29–50（湍流**时间尺度**，32 引用）、Mauder et al. 2020 *AMT* 13(2):969–983（**采样率/仪器对比**导致的湍流统计量偏差，摘要一手，19 引用）——两条都可作 Q2 侧「平均窗/采样影响已被研究」的追加弹药。

---

## 3. 入口三 · [1][2][3] 判词落盘复核

方法：打开出版社页面 → 抽正文/摘要纯文本 → 按关键词定位**短句引用**（每条 ≤300 字符，只判词、不转载正文）→ 连同回源 URL 落盘。
落盘文件：`docs/litreview/evidence/tsk118-verdict-evidence.json`（结构化，含 `citation_*` 元数据 + 逐条 quote）与同名 `.txt`（可读版）。

| # | 断言（TSK-117 里的写法） | 复核到的原文 | 判定 | 取证层级 |
| --- | --- | --- | --- | --- |
| **[1]** Archer 2025 *WES* 10:1433–1438 | 小时–天量级窗算出的比值「不应被称为湍流强度」 | "…calculated over longer time intervals (i.e., hours to days), can still be obtained, but **it should not be called a turbulence intensity**." ；"TI should refer only to fluctuations of the wind at the **microscale** (i.e., time averages of the order of minutes) and thus to the **right of the spectral gap**" ；"…mesoscale or synoptic-scale features belong to the **left of the spectral gap** and **should not be called turbulent**." ；处置建议 "…**done with extreme caution or avoided altogether**." | **维持**（四句逐字命中，含 issue 点名的两句） | **一手正文命中**（Copernicus 文章页为**全文 HTML**，200 OK） |
| **[3]** Treviño & Andreas 2008 *BLM* 128:303–311 | Reynolds 平均成立要求序列既平稳又互不相关；主张用中位数 | "…**validity of Reynolds averaging** for estimating the (ensemble) mean of a turbulence time series **requires that the series values be both stationary and uncorrelated**." ；"…jointly designated as **independent identically distributed (i.i.d.)**." ；"Alternatively the **median** (as opposed to the mean) is recommended…" | **维持** | ⚠ **须更正**：Springer 页给出的是**摘要页**（正文 8117 字符，含 Abstract、无 Introduction/References 标题）→ 应记「一手摘要页命中」，TSK-117 §3 写的「一手正文命中」**层级标高了** |
| **[2]** Treviño & Andreas 2000 *BLM* 95:231–247 | 提出 ΔT 与 AW 的确定方法；两者随机变化；AW ≠ ΔT | 摘要："…define the **smallest time interval ΔT**…" ；"…the **optimal interval (or averaging window) AW**…" ；"**Both ΔT and AW vary randomly with time** and depend on physical properties of the turbulence. ΔT also depends on the **accuracy of the measurements** and is thus **independent of AW**… the **non-equality between AW and ΔT**." | **维持**（判词逐字在摘要里；我最初用 "minimum averaging interval" 探针**没命中**，原因是原文写 smallest 且 Δ 字形被页面转码破坏——换探针后命中，非判词有变） | ⚠ **须更正**：同为**摘要页**（正文 8363 字符） |

**这一节的结论**：三条判词**没有一条需要撤回**，R1 改名令与 R2 去方向断言所依赖的原句都站得住、且已落盘可回源。
需要更正的是 TSK-117 §3 表的**取证列**两行：[2][3] 由「一手正文命中」→「一手摘要页命中」。按本角色口径（四类取证里只有一手全文命中进事实列），[2][3] 仍是「一手」但**不是全文**——这不影响 [1]（全文），而 R1 的承重句本来就挂在 [1] 上，所以**结论不塌**。

---

## 4. 对已定格的影响（不重判，只记影响）

| 已定的格 | 本轮影响 | 依据 |
| --- | --- | --- |
| **Q1 = ②已被质疑** | **维持，且置信度上调**。三条翻案入口全走完：中文面无对口先例、S2 面零命中、[1] 判词全文复核站住 | §1–§3 |
| **Q2 = 泛命题换皮成立** | **维持**。中文面另给出两条同结论的中文证据（梁捷宁 2013 的 30 min+ 平均使 σ 离散、τ_F=1 h 与湍流窗区分；国家能源局 ≤3 s 采样规定） | §1.2 |
| **R1 改名令** | **加强**。原只有 [1] 一条英文判词，现在中文侧有**规范性**依据（技术规定 + IEC 适用性文 + 10 min/30 min 实测口径）——若论文用中文期刊或国内答辩场合，改名令同样成立 | §1.2 |
| **R2 去方向断言** | 不变（本轮未新增符号方向证据） | — |
| TSK-117 §3 取证列 | **需改两行**：[2][3]「一手正文命中」→「一手摘要页命中」 | §3 |

**新出现的、TSK-117 没写的一条可引用素材**：中文口径证据链给了 R1 一个**比 Archer 更好用的写法**——不必引国外标准争论，直接引《全国风能资源评价技术规定》「采样时间间隔 ≤3 s」与《气象》2016「10 min 时距」，说明**国内业务与工程界对这个量的时距定义就是分钟级**，我们的量不满足该定义，故不称 TKE/TI。

## 5. 覆盖自报（下游据此判断能否把某类质疑降为「未核查」）

**已覆盖**：中文 8 条检索式（含 issue 点名的 4 条全跑）、5 本指定期刊面（高原气象 / 应用气象学报 / 气象学报 / 大气科学 / 沙漠与绿洲气象，其中前 4 本命中正文页、第 5 本命中 2026 年风切变文）、S2 原端点 + bulk 端点 + OpenAlex、[1][2][3] 三个出版社页。时间窗：**不限年**，实际命中 1991–2026。

**仍存在的四个洞（如实报，不粉饰）**：

| 洞 | 状态 | 影响 |
| --- | --- | --- |
| CNKI / 万方**未系统扫描** | 本轮走的是引擎 → 期刊官网落地路径，未进 CNKI 关键词库 | 「未检索到」≠「不存在」。若后续要写「中文亦无先例」，建议再补一轮 CNKI 机构账号检索（检索式见 §1.1，可直接复用） |
| S2 bulk 语义局限 | 端点通了，但**词面匹配 + 无排序元数据** | 零命中的证据力有限，见 §2.2 限定句 |
| OpenAlex 仍 429 | 7 条全 BLOCKED | 少一个独立印证通道；关键结论目前靠 Crossref + S2 bulk + 中文期刊页三面 |
| [15][18][19] 具体算式仍未核读 | 本轮不在三条入口范围内，未动 | TSK-117 §7 的第四条缺口**仍然存在**，[19]（WRF 如何导出 TI）仍是下一轮第一件事 |

## 6. 参考来源（本轮新增；标题/作者/卷页/URL 从页面或脚本输出复制）

**中文面（一手正文/摘要命中）**
1. 全国风能资源评价技术规定（通知） — 国家能源局 — 2012 — https://www.nea.gov.cn/2012-01/04/c_131260326.htm — 官方技术规定 — **强**
2. IEC 的风机选型参数估算方法在中国的适用性研究 — 《气象》2016(12) — http://qxqk.nmc.cn/html/2016/12/20161210.html — 同行评审期刊 — **强**（193 塔逐个 10 min 样本算 TI；σ 定义含 1~3 s / 10 min 时距）
3. 黄土高原复杂地形上边界层低空急流对近地层湍流的影响 — 梁捷宁、张镭 等 — 《地球物理学报》2014 — https://html.rhhz.net/dqwlxb/2014-5-1387.htm — 同行评审期刊 — **强**（10 min 窗 + 10 Hz 算湍动能）
4. 黄土高原复杂地形受中尺度运动影响的稳定边界层湍流特征 — 梁捷宁、张镭、鲍杰、赵世强、黄建平、张武 — 《大气科学》2013, 37(1):113–123 — https://hjp.lzu.edu.cn/hjpnew/upload/files/20210119/32731650305341399ca122a6ab67312a.pdf — 同行评审期刊 — **强**（谱隙 112.4–449.9 s；30 min+ 平均致 σ 离散）
5. 云贵高原西部大理地区近地层湍流特征分析 — 杨智、刘劲松、朱以维、董保举 — 《高原气象》2010(1):117– — https://www.sciengine.com/doi/pdf/2F70D6A7E4414D178D08812938433D5F — 同行评审期刊 — 中
6. 青藏高原西藏区域近地层大风过程脉动风特性研究 — 《空气动力学学报》2026, DOI 10.7638/kqdlxxb-2026.0007 — https://kqdlxxb.xml-journal.net/article/doi/10.7638/kqdlxxb-2026.0007 — 同行评审期刊 — 中（摘要一手）
7. MYJ 和 YSU 方案对 WRF 边界层气象要素模拟的影响 — 张碧辉 等 — 《地球物理学报》2012 — http://data.geophy.cn/dzdqs-data/cjg/html/2012/07/20120710.htm — 同行评审期刊 — 中（模式 TKE = 方案自带预报量）
8. 风切变指数…巴州（测风塔高时间分辨率） — 熊梓立 等 — 《沙漠与绿洲气象》2026,4(4):26–33, DOI 10.12057/j.issn.2097-6801.2505.08206 — https://smylzqx.ijournals.cn — 同行评审期刊 — 弱（仅刊面定位，未取算式）
9. 基于 WRF 和 CFD 软件结合的风能资源数值模拟试验研究 — 《气象》2012(11) — http://qxqk.nmc.cn/html/2012-11-20121107.html — 同行评审期刊 — 中（WRF 逐小时输出作 CFD 入流，TI 由 CFD/测风塔侧算）

**S2 面新增**
10. Time Scales in the Unstable Atmospheric Surface Layer — Meredith Metzger, Heather Holmes — *Boundary-Layer Meteorology* 126(1):29–50, 2007 — https://doi.org/10.1007/s10546-007-9219-0 — 同行评审期刊 — 中（32 引用，Crossref 回读）
11. Comparison of turbulence measurements by a CSAT3B sonic anemometer and a high-resolution bistatic Doppler lidar — Matthias Mauder 等 — *Atmos. Meas. Tech.* 13(2):969–983, 2020 — https://doi.org/10.5194/amt-13-969-2020 — 同行评审期刊 — 中（19 引用；摘要一手）

**判词面（回源 URL，与 TSK-117 相同，本轮复核并落盘）**
12. Archer 2025 — https://doi.org/10.5194/wes-10-1433-2025 — 全文 HTML 已复核
13. Treviño & Andreas 2000 — https://doi.org/10.1023/A:1002632004254 — 摘要页已复核
14. Treviño & Andreas 2008 — https://doi.org/10.1007/s10546-008-9288-8 — 摘要页已复核

**落盘工件**：`docs/litreview/evidence/tsk118-verdict-evidence.json`、`.txt`（判词原文短句 + URL + citation 元数据）；`docs/litreview/evidence/tsk118-s2-channels.json`（429 trace + bulk 命中数组）；`docs/litreview/evidence/tsk118-s2-new-dois.json`（Crossref 回读）。出版社原始 HTML/PDF **不入仓**（版权与体积）。
