# TSK-117 · 补 gate 两条：时间方差当 TKE 代理的先例与质疑 / 采样效应新颖性

> 参数：`stage=pre · report_level=standard · caller_role=design · format=report`（发起者 Mika，TSK-113 的 gate 补漏）
> 编制：2026-10-13　本轮**零新检索**：证据全部来自 2026-10-12 已落盘的 `c1–c8.json`（Crossref 检索结果，8 批 stderr 全空）
> 与 `dois{,2,3,4}.json`（按 DOI 逐条回读的注册表元数据 + 摘要）。元数据、标题、作者、年份、DOI 一律从脚本输出复制，未手写。
>
> **引用形态说明（先说清，免得被当成漏项）**：这两个面在 arXiv 上没有对口文献——大气湍流观测方法学的同行评审产出落在
> BLM / MWR / JAM / JTECH / Tellus / WES / Energy 类期刊。故**本轮零 arXiv 引用**，全部给 DOI 落地页 URL
> （`https://doi.org/…`，可访问、版本不可变）。`docs/references/arxiv-index.json` 本轮**无增量**，未触碰该文件。
>
> **一条通道受阻，不算负结果**：Semantic Scholar 两路（`s1.json`/`s2.json`）返回 **HTTP 429**，命中数组为空。
> 这是「没看成」，不是「查无此文」；相应结论只依据 Crossref 面，并已列入文末未解决清单。
> 中文期刊面（CNKI/《高原气象》《应用气象学报》《气象学报》）不在 Crossref 覆盖内，**本轮未覆盖**——这是 Q1 先例侧最大的洞。

## 0. 被判定的是什么

我们的主张（照 `[P1 §4.1]` 原文）：用滑动时间窗方差定义 k，把 WRF **逐小时**格点风场按 2/3/6/12 h 抽稀后
用**同一窗口参数**重算，量出「相对逐小时参照偏多少」，并把偏差传播到 8/16/26 三档阈值。

生产口径实读 `backend/meteorology.py:49-100`（一手代码命中）：
`k = ½(⟨u′²⟩+⟨v′²⟩+⟨w′²⟩)`，′ = 减去 ±half_hours 中心滑动窗均值，输入是**逐小时气压层** u/v/w；
`edr_from_tke()` 再按 `ε = C_ε·k^1.5/L`、L=2 km 折成 EDR。**该换算按 2026-10-13 附加令处置为「无量纲排序量」**，依据与选择理由见 §6.1。

这个主张要拆成两层判，混在一起判必然得出错结论：

| 层 | 内容 | 判据对象 |
| --- | --- | --- |
| **A 概念层** | 「把格点场的时间方差称作湍流动能 / 脉动强度 / TI」这一**命名与解释**是否成立 | Q1 |
| **B 实验层** | 「同一场抽稀→重算同一窗方差→得相对偏差场」这一**做法**是否有人发过 | Q1 + Q2 |

---

## 1. Q1 判定：三选一 → **已被质疑、需要正面回应的做法**

**判定：第二项。** 且质疑不是旁敲侧击，是同行评审里点名的一类误用。

| 候选 | 盘上证据 | 排除/采信理由 |
| --- | --- | --- |
| ① 已有先例的常规做法 | **不采信** | 未检索到「小时级格点场时间方差 = TKE/TI」的直接先例（8 批检索式、约 160 条命中，无一命中该具体做法）。同族先例存在但**对象不同**：90 m LES 逐步长时间序列、1-min 杯式风速、模式自带的 subgrid TKE 预报量——都不是小时尺度格点方差 |
| ② **已被质疑需正面回应** | **采信** | [1] Archer 2025, *Wind Energy Science* 10:1433–1438（6 引用）全文正文明写：小时到天量级的窗算出的 σ/Ū「**不应被称为湍流强度**」，中尺度/天气尺度扰动在**谱隙左侧**、不叫湍流；并给出处置建议「extreme caution or avoided altogether」。[2][3] Treviño & Andreas（*BLM* 2000 / 2008）从平均窗合法性一侧质疑固定窗雷诺平均 |
| ③ 无人做过需自证合理性 | **不采信为总判定，但 B 层成立** | B 层（抽稀→重算→相对偏差场→传播到分档）确实**未检索到直接先例**。但「无人做过」作为总判定是错的：概念层有人做且被公开质疑，我们的风险来自质疑方，不来自空白 |

一句话给下游：**我们最大的风险不是「没人做过」，是「有人做过并且公开说过这种做法不该叫湍流」。**
所以 Q1 的实际交付是一条**改名令**（见 §6），不是许可证。

---

## 2. Q1 支撑清单 · 先例侧（他们怎么命名、怎么辩护）

「量的到底是什么」这一列是判定的关键：**同族先例没有一篇的对象是「逐小时格点场的时间方差」**。
每格标取证方式，只有「一手命中」进事实列。

| # | 工作（venue·年） | 它算的量 / 它怎么命名 | 输入场的时间与空间分辨率 | 取证方式 |
| --- | --- | --- | --- | --- |
| [13] | Rohanizadegan, Petrone, Pomeroy & Kosovic, *JGR Atmos.* 130, 2025 | 「**turbulence kinetic energy (TKE)**」随高程与地形变化 | 三台**高频涡度相关**（在体）+ **LES 90 m** 网格 | **一手摘要命中**（Crossref abstract：high-frequency eddy-covariance systems / LES at 90 m / complex terrain） |
| [14] | Kang & Ryu, *Earth and Space Science*, 2026 | 「**turbulence intensity (TI)**」= 24 h、**1-min** 序列的 σ/Ū | 杯式风速计 **1-min**（WMO 地面站） | **一手摘要命中**：原文 "cup anemometers are widely used but **limited by coarse sampling in representing turbulence**" |
| [19] | Tai, Berg, Krishnamurthy, Newsom & Kirincich, *WES* 8:433–448, 2023 | 「**TI as simulated by WRF**」，提出在线算 TI 的算法 | WRF 模式场（近海） | **一手摘要命中**："the **TI derivation from atmospheric models has not been well examined**" |
| [15] | Frehlich & Sharman, *Mon. Wea. Rev.* 132:2308–2324, 2004 | 从 NWP 模式输出估湍流（诊断 + 数据同化） | NWP 输出 | **仅元数据命中**（标题/作者/卷页/45 引用）；**它是否用「时间方差」未核实**，不可当 Q1 的直接先例用 |
| [17] | Szintai, Kaufmann & Rotach, *Adv. Sci. Res.* 3:79, 2009 | 模式**自带的 subgrid TKE 预报量**（1.5 阶闭合）→ 扩散应用 | COSMO 6.6 / 2.2 km | **一手摘要命中**：prognostic equation for TKE |
| [16] | Shakina, Skriptunova & Zav'yalova, *Meteorol. i Gidrologiya*, 2021 | 地形湍流：重力波**临界振幅**法 + **wave drag** 定强度 | COSMO-Ru7 | **一手摘要命中**（未用时间方差） |
| [18] | Kumer, Reuder, Dorninger, Zauner & Grubišić, *Renew. Energy* 99:898–910, 2016 | 「TKE estimates from profiling wind LiDAR」 | 测风 LiDAR | **仅元数据命中**；机制未核读 |

**先例侧小结（能下什么结论）**：把「时间方差型 TKE/TI」用在**能真正分辨脉动的输入**上（在体高频 EC、1-min 杯式、90 m LES、LiDAR）
是成熟做法，命名统一叫 TKE / TI。它们共同的口径依据是「**10-min 平均**才算湍流」的 IEC 61400-1 定义 `[21]`
（该标准原文本轮**未核读**，转抄自 [1] 正文引述）；至于各先例论文是否逐条引用了该标准，**未核实**，不作事实使用。
**不能下的结论**：不能说「把逐小时 5 km 格点场的时间方差称作 TKE」也有同行评审先例——**8 批检索式约 160 条命中里未检索到**。
这条空白既是我们的机会（B 层无人做过），也是我们的坑（A 层没有先例替我们背书）。

## 3. Q1 支撑清单 · 质疑侧（点名、有 venue、有论据）

| # | 质疑方（venue·年） | 质疑的是什么 | 论据（原文口径） | 取证方式 |
| --- | --- | --- | --- | --- |
| [1] | **Archer, *WES* 10:1433–1438, 2025**（Brief communication，6 引用） | **正是我们的 A 层**：小时–天量级窗算出的 σ/Ū 被称作 TI | 谱隙论据：TI 只该指 microscale（分钟量级平均）、谱隙**右侧**；中尺度/天气尺度扰动在谱隙**左侧**，"should not be called turbulent"；小时到天算出的比值"can still be obtained, but **it should not be called a turbulence intensity**"；处置建议 "**done with extreme caution or avoided altogether**" | **一手正文命中**（2026-10-12 打开 Copernicus 文章 HTML 页 extract，本轮未落盘；回源 [1] URL） |
| [3] | **Treviño & Andreas, *BLM* 128:303–311, 2008** | 「取个窗平均就是雷诺平均」这一做法 | Reynolds 平均成立要求序列**既平稳又互不相关**（即 i.i.d.）；序列相关时必须知道相关性才能可靠估均值；还主张该用**中位数**而非均值 | **一手正文命中**（同上，Springer 文章页 extract；本轮未落盘） |
| [2] | **Treviño & Andreas, *BLM* 95:231–247, 2000** | 固定平均窗对非平稳湍流不合适 | 提出**最小可平均区间 ΔT** 与**最优平均窗 AW** 的确定方法；两者**随时间随机变化**且取决于湍流物理性质与测量精度；并指出 AW ≠ ΔT | **一手摘要命中**（Springer 页面正文摘要） |
| [20] | **Gao, Zhang & Qi, *Aerospace* 12:293, 2025** | EDR 估计对采样率的依赖（ aviation 侧） | "the **minimum series length to estimate EDR** was determined **under different flight data sampling rate**"；惯性子区间按 Blackman–Tukey 定 | **一手摘要命中**（Crossref abstract） |

**质疑侧小结**：质疑存在、在同行评审里、venue 不边缘（WES / BLM / Aerospace），而且**论据就是 averaging time / 谱隙**——
这直接回答了本 issue 问的交叉项：**是的，质疑方用的就是 averaging-time 论据**，详见 §5。

---

## 4. Q2 判定：**换皮风险成立——但只在「泛命题」这一层成立**

**一句话结论**：够。涡度相关/通量界（外加风工程与 EDR 界）已有的 averaging-time 文献，
**足以**让审稿人把选题一按「采样间隔影响脉动统计量」这个**命题形态**读成已知结论的换皮；
**不足以**否掉它的，是我们真正独有的那件具体事——**无真值、模式场本身不含湍流子区时，该指数的偏差符号与阈值分档漂移**，这一格在盘上证据里没有对口工作说完了它。

分水岭证据（按对新颖性的杀伤力排序，全部元数据/摘要一手命中）：

| 杀伤 | 工作 | 它已经把什么说完了 |
| --- | --- | --- |
| **致命** | [8] **Deacon, *Aust. Meteorol. Mag.* 13:11–14, 1965**「Wind gust speed: averaging time relationship」 | 摘要给出**对数律** `R(t:2) = a − b·log₁₀(t+1.5)`（t 秒阵风 / 2 秒阵风之比），且**常数随下垫面类型（地形粗糙度）变**。我们要做的「偏差随采样间隔的折线族 + 分层/分地形」在**函数形态与依赖项**上 1965 年就有了 |
| **致命** | [4] Moncrieff, Clement, Finnigan & Meyers, *Handbook of Micrometeorology* ch.2, pp.7–31, 2003（**212 引用**） | 「Averaging, **Detrending**, and Filtering」已是**手册级标准章节**——平均窗不是待研究的问题，是待遵守的流程 |
| **强** | [2] Treviño & Andreas 2000（*BLM* 95） | 该用多长的窗，已有**确定的方法**（ΔT 与 AW），且结论是「窗长不该是常数」 |
| **强** | [5] Feng, Zhang, Wei & Xu, *BLM* 165:545–551, 2017 | **标题即结论**：Effects of Averaging Period on Energy Fluxes and the Energy-Balance Ratio |
| **强** | [7] Hughes, Bourassa, Rolph & Smith, *J. Atmos. Oceanic Technol.* 29:974–986, 2012 | 把「平均导致系统性偏差」**量化到数字**：月平均风/温/湿代入拖拽公式，LH 偏差在西部边界流冬季可达 **90 W m⁻²**、天气活跃热带 **>40 W m⁻²**；机理是**亚月协方差**。这正是"相对偏差可量化"这一同类贡献的已发表样板 |
| **强** | [20] Gao, Zhang & Qi 2025（*Aerospace*） | **EDR 这一支**已被明确研究过：不同**采样率**下估 EDR 所需的**最小序列长度** |
| 中 | [6] Vickers & Göckede, *Tellus B* 62:87, 2010 | 「**1-h 平均**湍流通量（CO₂/LH/SH）的不确定度」专文 |
| 中 | [10] Atta, *Annu. Rev. Fluid Mech.* 6:75–91, **1974**「Sampling Techniques in Turbulence Measurements」 | 采样对湍流量测量的影响 1974 年已是**综述题目** |
| 中 | [9] Kwon & Kareem, *J. Struct. Eng.* 140, 2014 | 规范侧（ASCE 7）的 gust averaging time 重估 |
| 中 | [12] Papageorge & Sutton, *Exp. Fluids* 57, 2016 | 有限记录长度下统计量的**收敛性** |
| 中 | [14] Kang & Ryu 2026 | 欠采样时**混叠把未分辨方差再分配进低频** |

**还有一条对措辞的独立警告（比换皮更容易被审稿人一击致命）**：
`[P1 §4.1]` 与 TSK-113 曾把预期写成「抽稀→**低估**脉动方差」（**TSK-113 的两处已于 2026-10-13 R2 改掉，见 §6.1**）。盘上证据不支持先验定这个符号——
[14] 明确说混叠会把未分辨的高频方差**搬进低频**（对低频段是**抬升**），
[1] 说小时–天量级的窗会把**中尺度/天气尺度方差纳进来**（也是**抬升**），
而丢高频脉动是**压低**。两股反向效应同时作用，**净符号未知**。
⇒ 主张里「系统性低估」四个字必须改成「系统性影响/偏差（符号由实测定）」，否则这是一条**可被直接否证**的先验断言。

**本节能下的结论**：泛命题层面已被占死，「采样间隔影响方差/TKE/EDR/谱」不能再当贡献写。
**不能下的结论**：不能说整个选题一已被做完——没找到把该结论做在「小时格点场 + 业务时次抽稀 + 运行阈值分档」上并给偏差场的工作（中文面未覆盖，见 §7）。

---

## 5. Q1 × Q2 交叉：质疑方用的就是 averaging-time 论据

本 issue 问：先例若同时是「已被质疑」的，质疑方是否也用 averaging time 论据。**答：是，而且这是唯一一条论据。**

| 质疑方 | 它用的论据 | 与 EC/通量界的关系 |
| --- | --- | --- |
| [1] Archer 2025 | **谱隙位置 + IEC 的 10-min 定义**（分钟级=湍流；小时–天级=中尺度） | 与 [4][2] 同一套谱/平均窗语言，只是结论方向相反：EC 界说"窗要够长才能收敛通量"，[1] 说"窗太长就不是湍流了" |
| [2][3] Treviño & Andreas 2000/2008 | **平均区间长度与序列相关性的矛盾**（窗长不该固定；均值需要 i.i.d.） | 同属 averaging-time 一支 |

**这条交叉对本 gate 的含义（最重要的一句）**：我们不能拿「EC 界早已知道 averaging time 有影响」当挡箭牌去回应 Q1 的质疑——
**因为质疑方和我们的"已知文献"用的是同一套论据**。同一套论据既能证明"这问题被研究过"（杀新颖性），
也能证明"我们的量落在谱隙左侧、不叫湍流"（杀命名）。我们只剩一条出路：把论据反过来用——
**明确宣称我们的量不是湍流，而是一个可复现的模式场诊断量，并只对它做采样敏感性**。

## 6. 对 TSK-113 §2 第 1 条强位的影响：**降级，附两条改写要求**

TSK-113 §2 第 1 条现在写的是「这是可占的强位 / 方法学上站得住」。按本轮证据，判定改为：

> **降级**：从「方法学上站得住」→「**新颖性有条件成立，且当前措辞不可投**」。
> 不能升为「已核验新颖性」——两道 gate 都没通过：Q1 有同行评审的正面质疑 `[1][2][3]`，Q2 的泛命题已被 `[8][4][2][5][7][20]` 占住。

两条改写要求（**2026-10-13 Chenpi 裁决 R1、R2 认，已执行，见 §6.1**；原题目草案《时间采样间隔对**脉动方差型湍流指数**的系统性影响》会被 [1] 直接命中，已重拟）：

| # | 必改项 | 依据 | 不改的后果 |
| --- | --- | --- | --- |
| **R1** | **改名**：k 不得称「湍流动能 / 湍流强度 / TKE / TI」。改为「**格点风场时间方差型脉动指数**（非湍流，含中尺度贡献）」，并在方法与限制两节各写一次 | 主引 **[1]** 原文 "should not be called a turbulence intensity"；[2][3]。**并列支撑（不替换主引）**：[22] 国家能源局《全国风能资源评价技术规定》「u′ 脉动风速值（**采样时间间隔 ≤3 s**）」、[23]《气象》2016(12)「σ …指阵风（一般 1~3 s 时距）相对平均（一般 **10 min 时距**）风速的标准偏差」——国内业务与工程界对该量的**时距定义本身就是分钟/秒级**，我们的量不满足该定义 | 审稿人引 [1] 一条即可判"概念误用"，这是**结论作废级**不是表述级；国内期刊/答辩场合另需 [22][23] 这条本地口径 |
| **R2** | **去掉方向性断言**：标题与结论里的「系统性低估」改为「系统性影响（符号由实测定）」 | [14] 混叠把高频方差再分配进低频（抬升）+ [1] 长窗纳入中尺度方差（抬升）vs 丢高频脉动（压低） | 先验断言符号 = 一条可被实测反向否证的硬伤；实测出来是正偏差时全文结论要重写 |
| 附加 | **E 段的 L=2 km 断言**与 `edr_from_tke()` 的 EDR 换算。**本轮已定：走保守项——EDR 降为无量纲排序量**，论文正文不再给 ε 的物理单位（s⁻¹），k 只做分档排序 | 依据两条：① [20] 的结论是"估 EDR 所需的**最小序列长度取决于采样率**"，即该量对采样率敏感，而我们 1 h / 5 km 的输入**达不到任何 EDR 估计的采样要求**；② [1] 的谱隙论证——ε 的定义域在湍流惯性子区，本数据根本不分辨该子区，故 `L=2 km` 是**无推导的断言**（`meteorology.py` docstring 原文只写"取 2 km 量级"，无算式） | 我们 5 km/1 h 场根本没有惯性子区，EDR 单位是给 [1] 送人头 |

**仍然可占的窄格（改写后）**：在**没有湍流真值、输入场本身不分辨湍流子区**的前提下，
把一个业务在用的时间方差型指数，按**真实运行采样时次**（8 时次/日、3 h 输出）抽稀，
量出它的**相对偏差场（符号与幅度）、随高度/地形/季节的分布，以及 8/16/26 分档占比的漂移**。
这一具体组合：本轮**未检索到**对口工作（`[19]` 是最接近的——它说"TI 从模式场导出尚未被很好检验"，可作立论缺口引用）。
这才是选题一该占的格，比「采样间隔影响湍流统计量」窄得多，但也守得住。

### 6.1 改写执行记录（2026-10-13 追加 · Chenpi 已裁决 R1／R2 认 · 本轮零新检索）

**本节只记录改了什么、落在哪，不改 §1／§4 的任何判定格。**

| 项 | 执行位置 | 改法 |
| --- | --- | --- |
| **R1 命名** | 两份报告的全部**自指**处（本报告 §0 主张转述、§6 表 R1 行；TSK-113 报告 §2 第 1 条） | 我们的 k 一律写作「**格点风场时间方差型脉动指数**（非湍流，含中尺度贡献）」。写正文时**方法节与限制节各出现一次**，不得简称 TKE/TI |
| **R1 标题重拟** | §6 上方草案行 | 《时间采样间隔对**格点风场时间方差型脉动指数**（非湍流）的系统性影响——以青藏高原 WRF 逐小时场为例》。原名里的「脉动方差型**湍流**指数」已去除 |
| **R1 并列支撑** | §6 表 R1 行「依据」列 | 主引 [1] **不替换**；新增 [22] 国家能源局《全国风能资源评价技术规定》≤3 s、[23]《气象》2016(12) 10 min 时距，作**国内业务口径的并列支撑**（两条元数据与判词转抄自同分支 `litreview-2026-10-13-tsk118-reversal-entries.md`，其取证方式为**一手正文命中**，本 agent 未重新取页） |
| **R2 方向断言** | 本报告 §4（"⇒ 主张里「系统性低估」四个字必须改成…"）、§6 表 R2 行；TSK-113 报告 §2 第 1 条「定时观测低估多少脉动方差」与 §2 末条「方差/极值被低估了多少」 | 全部改为「**系统性影响／系统性偏差（符号由实测定）**」。TSK-113 那两处已改 |
| **附加 EDR** | 本报告 §0 `edr_from_tke()` 行、§6 表附加行 | **取保守项**：EDR 降为**无量纲排序量**，正文不给 ε 单位（s⁻¹）。理由是拿不到 `L` 的算式推导（docstring 只写"取 2 km 量级"），且 [20] 说明该量对采样率敏感、我们的输入达不到其采样要求。若日后要恢复有量纲 ε，唯一路径是 §4.1 步骤④（用谱／自相关反推外尺度 L）并配惯性子区可分辨性论证 |

**残留自查**：`git grep -nE "湍流动能|湍流强度|TKE|\bTI\b"` 在两报告改写后仍有命中，逐条判读后**全部落入允许的三类**——
① **引用他人论文／参照报告的原文措辞**（[13] "turbulence kinetic energy (TKE)"、[18] "TKE estimates from profiling wind LiDAR"、[19] "TI as simulated by WRF"、[17] 模式自带 subgrid TKE 预报量、参照报告 A/D 段与规划段的 TI）；
② **说明本次改名的对照句**（§0 A 概念层行、§1 ① 行、§6 表 R1 行「k 不得称…」、§6.1、TSK-113 §4 未覆盖清单第 4 子题——均已加"旧称/指参照报告那个量"的限定语）；
③ **反引号内的代码标识符与检索式原文**（`TKE≥26 / EDR≥0.376`、`edr_from_tke()`、§7 的 CNKI 检索式「湍流强度 + 标准差 + 小时平均」等）——它们是**被改名对象的实现名与我们当时用的词面**，改动会让代码与检索记录不可追溯，故保留。
**指称我们自己那个量、且不属于以上三类的句子：0 处。** 具体命中行数以交付回帖里的 grep 输出为准。

---

## 7. 覆盖自报（检索式、时间窗、漏面）——下游据此判断能否把某类质疑降为「未核查」

**检索通道与规模**：Crossref REST（`api.crossref.org/works`），40 条检索式、**160 条命中**（每式取前 4），
其中 25 条走 `query.bibliographic`、15 条走 `query.title`；再对 17 个 DOI 逐条回读注册表元数据 + 摘要（`/works/<doi>`）。
另对 3 条打开通往出版社正文页（WES 全文页 / Springer 两文摘要页）取一手正文命中。
**时间窗：不限年**（Crossref 未加 `from_year` 过滤），实际命中跨度 **1965–2026**。检索日 2026-10-12。

已覆盖的子面：`flux averaging time` / `averaging period`、`sampling error of turbulence statistics`、
`10-minute vs hourly TI`、`mesoscale & low-frequency contamination`、`temporal representativeness`、
`NWP/再分析导出湍流指数`、`EDR 采样率`、`gust averaging time`（风工程）、`ERA5 时间分辨率偏差`。

**四条明确缺口（必须如实报）**：

| 缺口 | 状态 | 缺的具体检索式 / 补法 |
| --- | --- | --- |
| **中文期刊面** | **未覆盖**（不是查无） | Crossref 不索引《高原气象》《应用气象学报》《气象学报》《大气科学》中文库。「逐小时风资料脉动方差/TKE」这种用法**在中文文献里很可能有先例**——这是 Q1 先例侧唯一能翻案的入口。补法：CNKI 检索式「脉动强度 + 小时」「湍流强度 + 标准差 + 小时平均」「TKE + 逐小时 + WRF」 |
| **Semantic Scholar 面** | **检索受阻**（HTTP 429，`s1.err`/`s2.err` 非空，返回空命中数组） | 「没看成」≠「不存在」。5 条 S2 检索式待重试（见盘上 `s1.json`/`s2.json` 的 query 字段），它比 Crossref 更擅长这种语义式提问 |
| **Q1 直接先例的证否强度** | 弱 | 我只能说「40 式 160 命中里未检索到」。**未检索到 ≠ 不存在**；且 Crossref 的 `query.bibliographic` 是词面匹配，对"用小时方差当 TKE"这种跨词概念组合召回率低。若中文面 + S2 面仍空白，才接近可下「无人做过」 |
| **[1][2][3] 三条正文命中未落盘** | 部分 | 判词是本轮（2026-10-12）打开出版社页面取回的原文片段，未存成文件；`dois*.json` 里只有 Crossref 摘要。引用前建议复核一次页面原文，回源 URL 见 §8 |

**本轮零 arXiv 引用**：已对本稿跑过 `verify_citations.py --scan`，脚本回「nothing to verify」＝**稿中不含任何 arXiv ID**，
故无 `MATCH/REVIEW` 判定可言，`docs/references/arxiv-index.json` **无增量、未修改**。
这两个面（观测方法学 / 采样统计）的同行评审产出落在气象与风工程期刊，不在 arXiv——这是检索面的事实，不是格式偷懒。

---

## 8. 参考来源（标题/作者/年份/URL 全部从 Crossref 回读输出复制，未手写）

**Q1 质疑侧（判定②的直接依据）**
1. Brief communication: A note on the variance of wind speed and turbulence intensity — Cristina Lozej Archer — *Wind Energy Science* 10:1433–1438, 2025 — https://doi.org/10.5194/wes-10-1433-2025 — 同行评审期刊 — **强**（正文判词一手命中）
2. Averaging Intervals For Spectral Analysis Of Nonstationary Turbulence — George Treviño, Edgar L. Andreas — *Boundary-Layer Meteorology* 95:231–247, 2000 — https://doi.org/10.1023/A:1002632004254 — 同行评审期刊 — 中
3. On Reynolds Averaging of Turbulence Time Series — George Treviño, Edgar L. Andreas — *Boundary-Layer Meteorology* 128:303–311, 2008 — https://doi.org/10.1007/s10546-008-9288-8 — 同行评审期刊 — 中

**Q2 已知结论侧（换皮判定依据）**
4. Averaging, Detrending, and Filtering of Eddy Covariance Time Series — John Moncrieff, Robert Clement, John Finnigan, Tilden Meyers — *Handbook of Micrometeorology* (Atmos. & Ocean. Sci. Lib. 22), pp. 7–31, 2003 — https://doi.org/10.1007/1-4020-2265-4_2 — 同行评审书章 — **强**（212 引用）
5. Effects of Averaging Period on Energy Fluxes and the Energy-Balance Ratio as Measured with an Eddy-Covariance System — Junting Feng, Baozhong Zhang, Zheng Wei, Di Xu — *Boundary-Layer Meteorology* 165:545–551, 2017 — https://doi.org/10.1007/s10546-017-0284-8 — 同行评审期刊 — 中
6. Uncertainty estimates for 1-h averaged turbulence fluxes of carbon dioxide, latent heat and sensible heat — Dean Vickers, Mathias Göckede — *Tellus B* 62:87, 2010 — https://doi.org/10.1111/j.1600-0889.2009.00449.x — 同行评审期刊 — 中
7. Averaging-Related Biases in Monthly Latent Heat Fluxes — Paul J. Hughes, Mark A. Bourassa, Jeremy J. Rolph, Shawn R. Smith — *J. Atmos. Oceanic Technol.* 29:974–986, 2012 — https://doi.org/10.1175/JTECH-D-11-00184.1 — 同行评审期刊 — **强**（摘要给量化偏差 90 / >40 W m⁻²）
8. Wind gust speed: averaging time relationship — E. L. Deacon — *Australian Meteorological Magazine* 13:11–14, 1965 — https://doi.org/10.1071/ES65025 — 同行评审期刊 — **强**（摘要给对数律 + 地形依赖）
9. Revisiting Gust Averaging Time and Gust Effect Factor in ASCE 7 — Dae Kun Kwon, Ahsan Kareem — *J. Structural Engineering* 140, 2014 — https://doi.org/10.1061/(ASCE)ST.1943-541X.0001102 — 同行评审期刊 — 中
10. Sampling Techniques in Turbulence Measurements — C. W. V. Atta — *Annu. Rev. Fluid Mech.* 6:75–91, 1974 — https://doi.org/10.1146/annurev.fl.06.010174.000451 — 综述 — 中
11. Errors in turbulence measurements with a sonic anemometer — A. L. M. Grant, R. D. Watkins — *BLM* 46:181–194, 1989 — https://doi.org/10.1007/BF00118453 — 同行评审期刊 — 弱（正文未据其下判定，仅备查）
12. Statistical processing and convergence of finite-record-length time-series measurements from turbulent flows — Michael Papageorge, Jeffrey A. Sutton — *Experiments in Fluids* 57, 2016 — https://doi.org/10.1007/s00348-016-2211-z — 同行评审期刊 — 中
20. Inertial Subrange Optimization in Eddy Dissipation Rate Estimation and Aircraft-Dependent Bumpiness Estimation — Zhenxing Gao, Qilin Zhang, Kai Qi — *Aerospace* 12:293, 2025 — https://doi.org/10.3390/aerospace12040293 — 同行评审期刊 — 中

**Q1 先例侧（命名与辩护的参照）**
13. Analysis of Turbulence and Turbulence Kinetic Energy Dynamics in Complex Terrain — Mina Rohanizadegan, Richard M. Petrone, John W. Pomeroy, Branko Kosovic — *JGR Atmospheres* 130, 2025 — https://doi.org/10.1029/2023JD040558 — 同行评审期刊 — **强**（摘要一手：高频 EC + LES 90 m）
14. Estimating Atmospheric Turbulence Intensity From Cup Anemometer Data — S.-L. Kang, J.-H. Ryu — *Earth and Space Science*, 2026 — https://doi.org/10.1029/2025EA004816 — 同行评审期刊 — 中（摘要一手：coarse sampling / aliasing）
15. Estimates of Turbulence from Numerical Weather Prediction Model Output with Applications to Turbulence Diagnosis and Data Assimilation — Rod Frehlich, Robert Sharman — *Mon. Wea. Rev.* 132:2308–2324, 2004 — https://doi.org/10.1175/1520-0493(2004)132%3C2308:EOTFNW%3E2.0.CO;2 — 同行评审期刊 — 中（**仅元数据**；是否用时间方差**未核实**）
16. Orographic Turbulence Forecasting from Numerical Model Output Data — N. P. Shakina, E. N. Skriptunova, A. A. Zav'yalova — *Meteorologiya i Gidrologiya*, 2021 — https://doi.org/10.52002/0130-2906-2021-1-40-52 — 同行评审期刊（俄） — 中（摘要一手：wave drag 定强度，非时间方差）
17. Deriving turbulence characteristics from the COSMO numerical weather prediction model for dispersion applications — B. Szintai, P. Kaufmann, M. W. Rotach — *Adv. Sci. Res.* 3:79–, 2009 — https://doi.org/10.5194/asr-3-79-2009 — 同行评审期刊 — 中（摘要一手：模式自带 TKE 预报量）
18. Turbulent kinetic energy estimates from profiling wind LiDAR measurements and their potential for wind energy applications — Valerie-M. Kumer, Joachim Reuder, Manfred Dorninger, Rudolf Zauner, Vanda Grubišić — *Renewable Energy* 99:898–910, 2016 — https://doi.org/10.1016/j.renene.2016.07.014 — 同行评审期刊 — 弱（仅元数据，机制未核读）
19. Validation of turbulence intensity as simulated by the Weather Research and Forecasting model off the US northeast coast — Sheng-Lun Tai, Larry K. Berg, Raghavendra Krishnamurthy, Rob Newsom, Anthony Kirincich — *Wind Energy Science* 8:433–448, 2023 — https://doi.org/10.5194/wes-8-433-2023 — 同行评审期刊 — **强**（摘要一手："TI derivation from atmospheric models has not been well examined"）
21. IEC 61400-1 关于「10 min 平均」的湍流定义 — **二手转述**：转抄自 [1] 正文引述，标准原文未核读；回源路径即 [1] 的 URL — 标准 — 不作事实列使用

**R1 并列支撑（§6.1 追加，转抄自同分支 TSK-118 报告，未重新取页）**
22. 全国风能资源评价技术规定（通知） — 国家能源局 — 2012 — https://www.nea.gov.cn/2012-01/04/c_131260326.htm — 官方技术规定 — **强**（判词："u′ 脉动风速值（**采样时间间隔 ≤3 s**）"；一手正文命中，取证记录见 TSK-118 §1.2）
23. IEC 的风机选型参数估算方法在中国的适用性研究 — 《气象》2016(12) — http://qxqk.nmc.cn/html/2016-12-20161210.html — 同行评审期刊 — **强**（判词：σ "指阵风（一般 1~3 s 时距）相对平均（一般 **10 min 时距**）风速的标准偏差"；对 193 座测风塔逐个 10 min 样本算 TI；一手正文命中）

**本地一手（非出版物）**：`论文/数据资产-规划现状-与论文选题.md` §4.1（主张原文）；`backend/meteorology.py:49-100`（k 与 EDR 的实际算式与窗参数）。

---

## 9. 对接信息

- **覆盖**：Q1 + Q2 两条 gate；40 检索式 / 160 命中 / 17 个 DOI 回读 / 3 篇正文页开取；年份 1965–2026 不限窗。
- **来源分布**：同行评审期刊 20 条 + 书章 1 条 + 标准（二手）1 条；顶刊侧 *WES / BLM / MWR / JGR / Tellus / JTECH / Annu Rev*；零预印本、零博客。
- **证据强度**：强 6 条（[1][4][7][8][13][19]）/ 中 10 条 / 弱 4 条（[11][18] 及两条仅元数据推断）。
- **未解决**：① 中文期刊面未覆盖（可能翻案 Q1 先例侧）；② S2 通道 429 未重试；③ [1][2][3] 判词未落盘、需复核页面；
  ④ [15][18][19] 的**具体算式**未核读全文——[19] 尤其关键，它是「WRF 的 TI 怎么导出」的对口先例，**下一轮第一件事就是把它读穿**。
- **给 design 的三条可执行结论**：R1 改名、R2 去方向断言（§6 表）；把 [19] 列为必读并据此定「我们的量与它的量差在哪」；
  若要走「无人做过」这条线，先补中文面检索（§7 给了检索式），补不上就只能写「未检索到对口先例」，不能写「首次」。
