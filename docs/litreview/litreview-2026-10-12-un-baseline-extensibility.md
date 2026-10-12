# 以《低空湍流监测及最优航路规划》为基线的可扩展点 · 判定表

> 参数：`report_level=deep · stage=pre · caller_role=design · format=report`（TSK-113，发起者 Mika）
> 编制：2026-10-12　本文是本轮 run 的**缩窄交付**：判定表 + 结论 + 未覆盖清单。
> deep 的其余章节（§4 多视角、§5 数据集与基准、§6 方法演进时间线）**本轮未写**，见文末缺口清单。
>
> **证据边界（硬）**：本文只使用本机盘上已有的五份调研产出 `sub/A.md B.md C.md E.md F.md`、
> 参照报告正文抽取件 `un.txt`、项目盘点 `论文/数据资产-规划现状-与论文选题.md`（下称 [P1]）、
> 以及本轮实测的引用核验输出 `cites_wave1.tsv` + `docs/references/arxiv-index.json`。
> **本轮零次新检索、零次 git 操作。**
> `sub/D.md` 是 201 字节的空壳（正文 `Reached the maximum turn limit (150)`），
> 它承担的四路子题（NWP 湍流诊断枚举、再分析湍流技巧数、外尺度 L 的推导先例、
> 「时间方差 TKE 用于模式场」的先例）在相应单元格明写**未覆盖**，不补推测。
> 另有 6 路后台调研（站测 vs 模式偏差 / AGL 低空定义 / 高原湍流观测 / 采样效应新颖性 /
> 数据与代码可得性）随上一轮 run 退出成为孤儿，同样按未覆盖处理。
>
> **引用记法**：`A#4` = `sub/A.md` 参考来源第 4 条；`P1` = 项目盘点文档；`R` = 参照报告正文本身。
> 每张表逐格标取证方式，只有「一手全文命中」进事实列。
## 1. 可扩展点判定表（A–E 五段 + 规划段）

「它做了什么」一律取自参照报告正文抽取件 `un.txt`（记 `[R]`，一手全文命中，命中词与次数见 §6）；
「文献最好水平」与「我们能支撑到哪」的锚点在 §5 参考来源逐条回源。

| 段 | 它做了什么 / 它报的数 `[R]` | 文献里同类工作的最好水平（锚点） | 我们现有数据能支撑到哪一段 | 判定 |
| --- | --- | --- | --- | --- |
| **A 多仪器融合真值反演** | RWP+MWR 时空匹配→统一网格→算 Ri/TKE/速度谱宽→综合判定湍流强度；湍流识别准确率 **98.4%**；Ri 的 RMSE **2.46**，称传统法≈**12** | 与在体真值比，单颗地基雷达 ε 反演现实一致性 **r=0.67、RMS≈0.6 log 单位（≈因子 4 散差）** `[A#4]`；谱宽法系数未定、只能取 **0.1–0.2** `[A#3]`；高原 RWP 组网反演 ε 已有同行评审工作 `[A#1]`；主流对连续量报 r/RMSE/bias、对判别报 **POD+FAR/TSS/AUC 成对**，且点名 ~85% 零值下总 accuracy 虚高、FAR 单独用不建议 `[A#29][A#30][A#7]` | **零支撑**：我们没有任何 RWP/MWR/多普勒雷达资料 `[P1 §1]`。能支撑的只有口径层——指标该怎么写、该配什么基线，属纯方法学，不需要新数据 | 不可复制。**能占的位是「无真值时指标口径该换成什么」**，不是复现 98.4% |
| **B 单源 NN 预测 Ri** | 6 特征 + 1 隐藏层 ReLU，**5 折交叉验证**，R²=**0.987**、RMSE=2.4568、MAE=1.5436；残差集中 [−2,2]；「以A模型为基准进行验证」 | 以**独立观测**为标签的可信量级：AUC **0.904**（XGBoost/PIREP）`[B#1]`、**0.92**（RF/PIREP 冬季）`[B#5]`、RF 0.924 vs 逻辑回归 0.915 `[E#4]`；对机载 in-situ EDR，ECMWF 业务 NWP 的 CAT12 相关系数仅 **0.35（确定性）/0.40（集合）** `[E#1]`；随机 CV 在时空相关数据上**系统性低估预测误差** `[B#20]`；模式导出的诊断量自身带不确定性 `[B#10]`、点诊断遮蔽物理联系且再分析不足以当真值 `[B#16]`；**未检索到**任何经独立观测验证仍报 R²>0.95 的先例（负结果）`[B §4]` | **可复现其结构、不可复现其数字**：我们的 k/EDR 由同一 WRF 场派生，特征与标签同源 `[P1 §1.1]`，正是文献所指的循环形态。能支撑的改造是标签-特征解耦 + 独立月份与空间分块验证 + 指标改 AUC/r | 报告的 0.987 **不可对标**。我们若产出 R²，读者只会读成自洽度——**这条要写进论文的限制段** |
| **C 稀疏网三维重构** | 600 站 + RWP + S/X 雷达，Delaunay 三角剖分 + 线性插值，边界外最近邻外推；**水平 100 m / 垂直 50 m**；**空间覆盖率 221.8%**（报告自注：每个自动站平均被 **2.2 部**雷达同时观测）；变差函数「空间变化平滑、无突变，符合大气连续性」 | 代表性范围是规范定义量：WMO-No.8 地面观测代表站周 **10–100 km** `[C#18]`；估计量平滑是「众所周知的危险效应」，kriging 不能同时复现直方图与半变异函数、需谱后处理 `[C#24][C#23]`；格点化**增低频事件、大幅降低强事件频率** `[C#29]`；同类量（边界层高度）用探空+卫星+ERA5+regression kriging 也只做到 **25 km 月均** `[C#26]`；低空湍流须 eddy-resolving，**25 m 网格**才重现湍流水平与峰值事件 `[C#11]`；业务 ICAO EDR 产品实际交付 **GTG3 13 km / 下一代 3 km** `[C#35][C#36][C#37]`；稀疏→细网格在 2025 年被当作需 DL+地形的开放难题 `[C#28]`；**未检索到**任何把 coverage 报成 >100% 并用来支撑分辨率的同行评审出版物（负结果）`[C §4]` | **强支撑**：同时握有 (i) 全年逐小时 ×19 层 ×~5 km 模式场 `[P1 §1.1]`、(ii) 226 个落在框内、**独立于该模式场**的地面定时观测 2015–2025 `[P1 §1.2]`。把逐小时场按 2/3/6/12 h 抽稀后重算同一滑动窗方差，即在同一数据源上量出「定时观测低估多少脉动方差」，结论形如相对偏差、可复现可否证 `[P1 §4.1]` | **这是可占的强位**。主张应为「名义分辨率下丢掉了什么」，**不是**任何新的分辨率数字 |
| **D NWP 驱动的湍流诊断与预报场** | R：输入 WRF 三维风/温/压 → 算 TKE、风切变、涡度 → 分位数标准化 + **理查逊数修正因子** + **加权融合**成综合湍流指数 TI；「以模型 C 处理的观测数据为基准，对比模型 D 的湍流场，**调整 TI 权重系数降低系统误差**」；验证给出 **TI 平均误差 0.08**、高湍流区匹配率 **85%**、优化后 05–08 时预报 **RMSE 降至 0.15** | 从 NWP 气压层量导出湍流指数是**既有标准范式**：Frehlich & Sharman 2004 `[B#8]`；GTG/ICAI 的权重是**拟合观测 PIREPs** 得到的、验证也是对 yes/no PIREPs 的正确探测率 `[B#7]`；Sharman & Pearson 把诊断量**统计映射**到 EDR（lognormal + 机载实测峰值定标）`[B#6]`；以机载 in-situ EDR 为标签的树模型仅「显著降低 EDR<0.1 误差、提升 POD」`[B#4]`；业务级上限：ECMWF CAT12 相关系数 **0.35/0.40** `[E#1]`；误差应拆成 NWP 预报不确定性 **+ 由模式输出诊断湍流本身的不确定性** 两项 `[B#10]` | **可复现其计算、不可复现其检验**：我们有全年逐小时 ×19 层 ×~5 km 的 WRF d02 场 `[P1 §1.1]`，TKE/切变/涡度/Ri 都能按同样公式算出。但 (i) 我们的 k/EDR 与标签同源，**没有任何独立观测或 PIREP/EDR 真值可用来定权重**；(ii) 19 层等压面、无近地层，Ri 与切变的垂直代表性受限。**能做的替代检验只有两条**：与 226 站独立地面观测做误差结构刻画 `[P1 §4.2]`；或与另一条模式链/再分析做**一致性**（须明写不是 accuracy） | 「对比模型 C 来调 D 的权重」= **两个同源产品互校**，0.08 / 85% / 0.15 属自洽度指标，不是对大气的技巧 `[B#10][B#16][B#20]`。分位数标准化后的 TI 无量纲，**其阈值不可跨模式/跨区域迁移**。**本轮该段文献锚点不完整**：D 视角四路子题未覆盖（见 §4），上述锚点全部取自 B/E 两份产出 |
| **E 1–3 h 时间序列预测** | R：线性趋势外推、移动平均、指数平滑、多变量回归、时空相关五种；**05–06 时**多变量回归 **RMSE 0.12 / MAE 0.09 / R² 0.85**；「能提前 1 小时预警高湍流区域」 | 本领域**不存在「指数平滑/移动平均」这一公认基线**：可核验工作的标准对照是 NWP 诊断指导产品 + 独立机载 in-situ EDR 检验 `[E#1][E#3]`；提升短时效的正规手段是**时滞集合** `[E#3]` 与概率化 `[E#1]`；简单回归几乎追平 ML（RF 0.924 vs 逻辑回归 0.915）`[E#4]`，故「回归打败平滑」不构成贡献；0–3 h 的 **persistence 地板**量化**未检索到一手文献**（负结果，`sub/E.md §8`） | **结构性不支持**：WRF 输出为**逐小时** `[P1 §1.1]`，时间分辨率**粗于** 1–3 h 预报时效，且我们只有模式诊断量、无湍流真值——用模式诊断当真值即循环验证。**我们能支撑的是批评层**：把基线换成 persistence + climatology、指标改为阈值化 EDR 的 CSI/ETS/AUC 并给分母，这才是可审计形式 `[E#1][E#4]` | 0.12/0.09/0.85 **未给 EDR 单位、未给真值来源、未给基线**，量纲与对象都无法对标；R²=0.85 远超已验证的相关天花板 0.35–0.40 `[E#1]`，强烈提示技巧来自目标自相关/平滑或切分泄漏。**结论：该段我们既不能复现、也不该模仿其报告方式** |
| **规划段（改进 A\*）** | R：约束 35–50 m/s、爬升率 ≤5 m/s、高度 50–2000 m、转弯角速度 ≤30°/s、**TI ≤ 0.7 视为高风险**；多目标（最小风险/距离/时间、最大安全评分）；**水平 500 m × 垂直 400 m** 三维网格，且称「与湍流数据分辨率精确匹配」；高风险区 **×5 倍惩罚**；移动平均平滑；短途城际/对角线长途/中等高度巡航/南京–上海/长途变高度**均成功**；**规划成功率 100%**、平均安全评分 **0.94** | A\*/栅格/时间扩展 + 硬 no-go + 线性罚是**数十年成熟范式**，综述把「基于搜索(A\*)—栅格—全局最优」列为既有方法体系 `[F#3]`，多目标进化与 APF-SA 禁飞区同类工作已密集存在 `[F#14][F#15]`；中文低空界更把「**自适应四维网格剖分 + 路径规划算法**」定位为既有基础设施 `[F#34]`。真正稀缺的位置是「**业务级湍流预报场驱动低空 UAV 网格路由**」：巡航侧已命名 TATOP（EDR from ERA5/LiDAR、full/partial avoidance 双策略）`[F#19]`、CAT-aware routing（避湍流使往返燃油最多 **+8%**）`[F#20]`，低空侧只到 CFD/城市风 + RRT/优化 `[F#17][F#18]`；**未检索到**「低空 + NWP 湍流预报场 + 网格 A\*」的直接命中（负结果 `sub/F.md Q2`）；本轮调研里**唯一活跃、许可证允许直接复用**的公开实现是 **ARENA（BSD-3-Clause，最后提交 2026-03-04）**，但它是连续 NURBS + 遗传、非网格 A\* `[F#29]` | **已实现、可写工程章**：我们的 A\* 用 0.045°（约 5 km）网格、8 向邻居，硬阈值 `TKE≥26 / EDR≥0.376`、**缺测同判不可通行**，罚项按超阈线性 `(k−8)×1.7`、`(EDR−0.208)×180` 折成等效绕行公里，三档软倍率 0.25/1.0/4.0，预算闸 18 s/30 s 且**超时判「未算出」而非「不可飞」**，输出四态「可飞/不可飞/超预算/未算出」`[P1 §2.2–2.3]`。**不能支撑的**：地形/禁飞区/最低安全高度**不进搜索**（缺空域矢量数据，`[P1 §2.2]` 待办 B6 已判不排期），故无法对标对方的场景真实性与约束完备性 | novelty 归零：不能以「planner 新颖」立论 `[F#3][F#34]`。「成功率 100%」在同类文献里只出现在**已披露分母的感知子任务** `[F#18]`，可信侧报概率保证/代价百分比/分级成功率 `[F#19][F#20]`，综述把**算力/超时（resource-aware）**列为可信必备 `[F#25]`——A\* 在少量手工选定 OD 对上取值必然接近 100%，**不构成鲁棒性证据**。我们的可写亮点是把「超预算/未算出」**计数化、公开化**；但须如实标注：**「缺测=不可通行」与「timeout=未知≠不安全」只有概念级支撑**（机会约束/鲁棒规划），**未检索到**把缺测格点显式设 no-fly 并系统评估的直接先例 `sub/F.md §Q3` |

## 2. 结论（5 条，只给判定，不给推测）

- **唯一可占的强位是 C 段方向**，理由不是我们的方法更好，而是**数据组合是独有的**：同时握有「全年逐小时 ×19 层 ×~5 km 模式场」`[P1 §1.1]` 与「226 个落在研究框内、独立于该模式场的地面定时观测」`[P1 §1.2]`。把逐小时场按 2/3/6/12 h 抽稀后重算同一滑动窗方差，就能在同一数据源上**量出**「定时观测低估多少脉动方差」——形如相对偏差、可复现可否证 `[P1 §4.1]`。主张应写成「名义分辨率下丢掉了什么」，不是任何新的分辨率数字。
- **A 段零支撑**（无 RWP/MWR/多普勒雷达资料 `[P1 §1]`），能占的是方法学位：无真值时指标口径怎么写、配什么基线。
- **B / D / E 三段报告的数字一律不可对标**，且病灶相同：**标签与特征同源**（B 以模型 A 为基准、D 以模型 C 为基准调权）+ **随机 5 折 CV** + **无独立观测真值**。文献对这条的定性是循环/自洽，不是精度 `[B#10][B#16][B#20]`。我们的替代路径是把「自洽 vs 准确」显式分离、改用独立月份 + 空间分块验证、指标换 AUC/POD-FAR/r。
- **规划段 novelty 归零**：4D 网格 A\* + 硬 no-go + 线性罚是既有范式，中文低空界已把「自适应四维网格剖分」当作基础设施 `[F#34]`。我们只剩**工程可靠性口径**可写（四态判定、缺测判不可通行、预算耗尽计数），且这两条设计**只有概念级支撑、未检索到直接先例** `[sub/F.md §Q3]`——写成系统设计章，不写成算法创新。
- **给评审的一句话**：我们的差异化不来自更大的数字，而来自一个对方没有的量——**「在标称分辨率下方差/极值被低估了多少」**，外加一条独立地面观测。

## 3. C 段补强（本轮从参照报告正文新读到、可直接引用的三处一手细节 `[R]`）

| 报告原文（命中词） | 为什么对我们有用 |
| --- | --- |
| 「总体覆盖率达 221.8%，此指标意味着区域内每个自动气象站平均可被 **2.2 部雷达（S 或 X 波段）同时观测**」 | 报告**自己**把 >100% 定义为**重叠冗余度**，不是精度。这把 `sub/C.md §4` 里我们标为「自证推断」的那条，升级为对方文本的直接陈述：冗余高 ⇒ 观测不独立，与「可分辨 100 m」在方向上相反 |
| 「单部 S 波段雷达（编号 920）…**230 公里**的探测范围对全部 **559** 个自动气象站实现了全面覆盖，覆盖率达到 **100%**」；「三部风廓线雷达联合覆盖了 **229** 个站，覆盖率 **41.0%**」 | 同一份报告里 600 站 / 559 站混用；「覆盖」在此明确是**圆面积对站点集合的几何包含**（0/1 意义），与分辨率无关；且**垂直探测网只覆盖 41% 的站**——即 100 m 垂直层的廓线信息在多数站点上并不存在 |
| 「仅有 **11.4%** 的气象站处于距离最近雷达 **10 公里**内，**48.7%** 分布于 **10–30 公里**区间」；「X 波段雷达各自的覆盖范围约 **60 公里**」 | 站点–雷达距离中位数在几十公里量级 ⇒ 雷达在 100 m AGL 处的波束高度/体元远大于该间距，**几何插值网格 ≠ 观测支撑**。这是可以复算的、不依赖对方代码的质疑 |

## 4. 未覆盖清单（deep 缺什么、为什么缺、怎么补）

| 缺口 | 状态与原因 | 影响哪一格 | 补法 |
| --- | --- | --- | --- |
| 视角 D 四路子题：NWP 湍流诊断指数**系统枚举**、ERA5/再分析湍流**技巧数**、**外尺度 L₀** 的推导先例、「**时间方差 TKE 用于模式场**」是否已有先例 | `sub/D.md` = 201 字节空壳（正文 `Reached the maximum turn limit (150)`）；D2/D4/D6 三条 task 仍 pending | D 行「文献最好水平」只能借 B/E 锚点，缺同族对比；**第 4 子题直接决定我们自己的「用逐小时方差代表脉动 TKE」会不会被判概念误用** | 优先补第 4 子题（对我们风险最大），单独立一条 `question` issue，只问「NWP 格点时间方差被当作湍流/TKE 代理发表过的先例 + 其被质疑的记录」 |
| 6 路孤儿调研：站测 vs 模式偏差量级 / **AGL 低空定义与运行层** / 高原湍流观测 / **采样效应新颖性核查** / 数据可得性与许可 / 代码许可 | 随上一轮 run 退出成为孤儿，结果永不返回（Mika 已定性「缺的是收口动作，不是检索能力」） | 「**采样效应新颖性**」是 §2 第 1 条强位的**前提**；未核验 ⇒ 该判定只到「方法学上站得住」，不到「文献里没人做过」 | 下一轮第一件事：单独跑「sampling effect / diurnal sampling / temporal representativeness of hourly variance」的新颖性核查；AGL/运行层影响选题三 `[P1 §4.3]` |
| deep 报告 §4 多视角、§5 数据集与基准对比、§6 方法演进时间线 | 本轮按 Mika 机制（冻结 git + 分块写 + 缩窄范围）主动放弃 | 整篇的深度档位 | 五份产出已含这些章节的原料，可在不改证据的前提下二次拼装 |
| `sub/A.md` 第 30 条（AMS 95th Annual，「约 85% 观测为零 ⇒ 命中率虚高」）落地页 URL 在原产出中被省略号截断 | 链接不可达（2026-10-12 复核），**不等于**该文献不存在 | A 行「accuracy 虚高」的第二条支撑 | 该格结论已由 A#29（URL 完整）独立支撑，可不补；若要补则需重新检索 confex 存档 |
| 24 个 arXiv ID 的核验状态 | 全部 `MATCH`（`cites_wave1.tsv`：24 checked / 0 problem / 0 blocked / 16 命中索引 / 8 次 live fetch）；仅 `2401.10519v2` 带 **WARNING: marked withdrawn/retracted** | 撤稿那篇在 A/B/C/E/F 中均未被用作事实支撑 | 索引已更新至 `docs/references/arxiv-index.json`（27 条）；下一轮直接复用，不重建 |

## 5. 参考来源（本轮判定表实际引用的锚点；标题/作者/venue/URL 一律从盘上产出复制，未手写）

**取自 `sub/A.md`（地基遥感反演湍流）**
- A#1 Meng, Guo, et al. *Elucidating the boundary layer turbulence dissipation rate using high-resolution measurements from a radar wind profiler network over the Tibetan Plateau.* **Atmos. Chem. Phys.** 24:8703–8720, 2024. DOI 10.5194/acp-24-8703-2024; https://acp.copernicus.org/articles/24/8703/2024/ [期刊·同行评审][full-text]
- A#3 Luce, Yabuki. *Turbulence kinetic energy dissipation rate estimated from a WindCube Doppler lidar and the LQ7 1.3 GHz radar wind profiler in the convective boundary layer.* **AMT** 18:1193–1208, 2025. DOI 10.5194/amt-18-1193-2025; https://amt.copernicus.org/articles/18/1193/2025/ [期刊][full-text]（谱宽法系数 0.1–0.2 未定）
- A#4 Shupe, Brooks, Canut. *Evaluation of turbulent dissipation rate retrievals from Doppler cloud radar.* **AMT** 5:1375–1391, 2012. DOI 10.5194/amt-5-1375-2012; https://amt.copernicus.org/articles/5/1375/2012/amt-5-1375-2012.pdf [期刊][full-text]（对独立在体真值 r=0.67）
- A#7 Foudad, Teixeira, Williams, Kaluza. *A Generalized Richardson Number Diagnostic for Turbulence in the Free Atmosphere.* **arXiv:2602.21770v2**, 2026; https://arxiv.org/abs/2602.21770v2 [预印本，under review in J. Atmos. Sci.][full-text HTML]（验证用 2.47 亿条飞机在体报告，指标 AUC/POD@FAR/TSS）
- A#29 *Using in situ eddy dissipation rate (EDR) observations for turbulence forecast verification.* AMS confex; https://ams.confex.com/ams/pdfpapers/101400.pdf [会议][full-text]（「FAR 单独用于湍流预报验证不建议」）
- A#30 *The Schneider Electric Aviation Numerical Turbulence Forecast Verification.* AMS 95th Annual Meeting [会议][full-text]——**链接不可达（2026-10-12 复核：原产出中 URL 含省略号截断）**；「约 85% 观测为零」这一事实暂只由该文摘要级记录支撑，不作强证据。

**取自 `sub/B.md`（ML 湍流诊断与验证规范）**
- B#1 Gokdeniz, Ulku. *Predictive Modeling of High-Altitude Clear Air Turbulence in the United States: A Machine Learning Approach.* **arXiv:2607.11899v1**, 2026; https://arxiv.org/abs/2607.11899v1 [预印本，未同行评审][MATCH]（XGBoost，标签=PIREP，AUC 0.904）
- B#4 Muñoz-Esparza, Sharman, Deierling. *Aviation Turbulence Forecasting at Upper Levels with Machine Learning Techniques Based on Regression Trees.* **J. Appl. Meteor. Climatol.** 59(11), 2020. DOI 10.1175/JAMC-D-20-0116.1; https://doi.org/10.1175/JAMC-D-20-0116.1 [期刊·AMS][abstract-level]
- B#5 Shao, Zhuang, Yu, et al. *The Prospective Application of Machine Learning in Turbulence Forecasting over China.* **JAMC** 63(10), 2024. DOI 10.1175/JAMC-D-23-0236.1; https://doi.org/10.1175/JAMC-D-23-0236.1 [期刊·AMS][abstract-level]（RF AUC 0.92，标签=PIREP）
- B#6 Sharman, Pearson. *Prediction of Energy Dissipation Rates for Aviation Turbulence. Part I.* **JAMC**, 2016/2017. DOI 10.1175/JAMC-D-16-0205.1; https://doi.org/10.1175/JAMC-D-16-0205.1 [期刊][abstract-level]（诊断量→EDR 的 lognormal 统计定标）
- B#7 Sharman, Tebaldi, Wiener, Wolff. *An Integrated Approach to Mid- and Upper-Level Turbulence Forecasting (GTG/ICAI).* **Wea. Forecasting** 21, 2006. DOI 10.1175/WAF924.1; https://doi.org/10.1175/WAF924.1 [期刊][abstract-level]（权重拟合观测 PIREPs）
- B#8 Frehlich, Sharman. *Estimates of Turbulence from Numerical Weather Prediction Model Output…* **Mon. Wea. Rev.** 132, 2004. DOI 10.1175/1520-0493(2004)132<2308:EOTFNW>2.0.CO;2; https://doi.org/10.1175/1520-0493(2004)132%3C2308:EOTFNW%3E2.0.CO;2 [期刊][abstract-level]（「由 NWP 输出导出湍流指数」的源头范式）
- B#10 Shin, Deierling, Sharman. *A Comparative Study of Various Approaches for Producing Probabilistic Forecasts of Upper-Level Aviation Turbulence.* **Wea. Forecasting** 38(1), 2022. DOI 10.1175/WAF-D-22-0086.1; https://doi.org/10.1175/WAF-D-22-0086.1 [期刊][abstract-level]（误差=预报不确定性 + **诊断本身的不确定性**）
- B#16 Lee, Sprenger. *Clear-air turbulence derived from in situ aircraft observation – a weather feature-based typology using ERA5 reanalysis.* **Weather and Climate Dynamics** 6:1583–1604, 2025. DOI 10.5194/wcd-6-1583-2025; https://doi.org/10.5194/wcd-6-1583-2025 [期刊·Copernicus][full-text]（点诊断遮蔽物理联系；再分析不足以充当真值）
- B#20 Roberts, Bahn, Ciuti, et al. *Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure.* **Ecography** 40(2):913–929, 2017. DOI 10.1111/ecog.02881; https://doi.org/10.1111/ecog.02881 [期刊][abstract-level，含关键句]（随机 CV ⇒ **严重低估预测误差**；建议 block CV）

**取自 `sub/C.md`（稀疏网重构与代表性尺度）**
- C#11 Muñoz-Esparza, Sharman, Sauer, Kosović. *Toward Low-Level Turbulence Forecasting at Eddy-Resolving Scales.* **GRL** 45(16):8655–8664, 2018. DOI 10.1029/2018GL078642; https://doi.org/10.1029/2018GL078642 [期刊][abstract-level，含 25 m 与 "several kilometers" 原句]
- C#18 WMO-No. 8 *Guide to Meteorological Instruments and Methods of Observation*, Part I Ch.1 §1.1.2 "Representativeness"（地面观测代表站周 10–100 km）; https://www.weather.gov/media/epz/mesonet/CWOP-WMO8.pdf [官方规范文档][full-text(节选)]
- C#23 Journel, Kyriakidis, Mao. *Correcting the Smoothing Effect of Estimators: A Spectral Postprocessor.* **Mathematical Geology** 32(7):787–813, 2000. DOI 10.1023/A:1007544406740; https://doi.org/10.1023/A:1007544406740 [期刊][metadata+落地页]
- C#24 Yamamoto. *Correcting the Smoothing Effect of Ordinary Kriging Estimates.* **Mathematical Geosciences** 37(1):69–94, 2005. DOI 10.1007/s11004-005-8748-7; https://doi.org/10.1007/s11004-005-8748-7 [期刊][full-text 摘要]（「众所周知的危险效应」）
- C#26 Argañaraz, et al. *Modelling monthly Boundary Layer Height maps combining radiosonde, satellite, and reanalysis over Europe.* **AMT** 19:5697, 2026; https://amt.copernicus.org/articles/19/5697/2026/ [期刊·开放获取][full-text 摘要]（同类量仅 25 km 月均）
- C#28 Baki, Pierzyna, Basu. *S2G-DI: A Deep Learning Framework for Generating High-Resolution Wind Gust Fields from Sparse Mesonet Observations.* ESSOpenEarth 预印本 v1 2025 / v3 2026. DOI 10.22541/essoar.176031304.41826798/v3; https://doi.org/10.22541/essoar.176031304.41826798/v3 [预印本][full-text 摘要]（Barnes 插值被当作待打败的 baseline）
- C#29 Ensor, Robeson. *Statistical Characteristics of Daily Precipitation: Comparisons of Gridded and Point Datasets.* **JAMC** 47(9):2468–2476, 2008. DOI 10.1175/2008JAMC1757.1; https://doi.org/10.1175/2008JAMC1757.1 [期刊][abstract-level，含关键句]（格点化增低频事件、大幅降低强事件频率）
- C#35 Kim, Sharman, Strahan, et al. *Improvements in Nonconvective Aviation Turbulence Prediction for the World Area Forecast System.* **BAMS** 99(11):2295–2311, 2018. DOI 10.1175/BAMS-D-17-0117.1; https://doi.org/10.1175/BAMS-D-17-0117.1 [期刊][full-text 摘要+正文节选]（ICAO EDR^(1/3)；产品 0.5°×0.5°）
- C#36 FAA NextGen AWRP *Turbulence*（GTG3 业务 13 km / GTG4 计划 3 km / 15 min 更新）; https://www.faa.gov/nextgen/programs/weather/awrp/turbulence [官方文档]——**本 agent 二次直取 HTTP 403，未二次核验**（数值系检索工具抓取，2026-10-12）
- C#37 The Weather Company *GTG3 Turbulence Forecast* API 文档（13-km resolution，强度 0–7）; https://developer.weather.com/docs/openapi/gtg3-turbulence-forecast-2-0 [产品文档][full-text 页面]

**取自 `sub/E.md`（短临预测与时序基线）**
- E#1 Bechtold, Bramberger, Dörnbrack, Leutbecher, Isaksen. *Forecasting clear-air turbulence.* **ECMWF Newsletter** No.168, 2021-07; https://www.ecmwf.int/en/newsletter/168/meteorology/forecasting-clear-air-turbulence [机构报告][full-text]（CAT12 corr 0.35 det / 0.40 ens；MAE 0.045 m^(2/3)s^(−1)；真值=MADIS 机载 max EDR）
- E#3 Kim, Chan, Sridhar, Sharman. *Combined Winds and Turbulence Prediction System for Automated Air-Traffic Management Applications.* **JAMC** 54:766–784, 2015. DOI 10.1175/JAMC-D-14-0216.1; https://doi.org/10.1175/JAMC-D-14-0216.1 [期刊][abstract-level]（时滞集合是提升短时效的正规手段）
- E#4 Williams. *Using random forests to diagnose aviation turbulence.* **Machine Learning** 95(1):51–70, 2014. DOI 10.1007/s10994-013-5346-7; https://doi.org/10.1007/s10994-013-5346-7 [期刊][full-text]（RF 0.924 / 逻辑回归 0.915 / GTG 0.816）

**取自 `sub/F.md`（天气感知路由与可复现性）**
- F#3 Vivaldini, Pěnička, Saska. *Decision-Making-Based Path Planning for Autonomous UAVs: A Survey.* **arXiv:2508.09304v1**, 2025; https://arxiv.org/abs/2508.09304v1 [预印本综述][MATCH]（「基于搜索(A\*)—栅格—全局最优」列为既有方法体系）
- F#14 Chen, Luo, Lin, Song, Chang. *Evolutionary Biparty Multiobjective UAV Path Planning.* **arXiv:2603.21544v1**, 2026; https://arxiv.org/abs/2603.21544v1 [预印本][MATCH]（效率/安全双决策者多目标）
- F#15 Mao, Jia, Gu, et al. *Robust UAV Path Planning with Obstacle Avoidance for Emergency Rescue.* **arXiv:2501.09338v1**, 2025; https://arxiv.org/abs/2501.09338v1 [预印本][MATCH]（3D 障碍+禁飞区 robust APF-SA）
- F#17 Aldao Pensado, Fontenla Carrera, Veiga López, et al. *Turbulence-Aware UAV Path Planning in Urban Environments.* **ICUAS 2024**, IEEE. DOI 10.1109/ICUAS60882.2024.10556934; https://ieeexplore.ieee.org/document/10556934 [会议·同行评审][abstract-level]（湍流来自 CFD，非业务数值预报；代码未核实）
- F#18 Shrivastava, Gupta, Obradovic. *Real-Time Path Planning for UAVs in Windy Environments Without Computational Fluid Dynamics.* **AAAI** 40(22):18540–18548, 2026-03-14. DOI 10.1609/aaai.v40i22.38920; https://ojs.aaai.org/index.php/AAAI/article/view/38920 [会议·同行评审][full-text]（「100% success rate」针对感知子任务并给出分母；连续量用 90–95% 区间）
- F#19 Zhu, Zhang, Ng, Chan, Delahaye. *Enhancing air traffic safety through clear-air turbulence prediction and trajectory optimisation.* **Transportation Research Part C** 190:105791, 2026. DOI 10.1016/j.trc.2026.105791; https://www.sciencedirect.com/science/article/pii/S0968090X26002792 [期刊][abstract-level]（TATOP；EDR from ERA5/LiDAR；full/partial avoidance）
- F#20 Foudad, Cassou, et al. *Clear-air turbulence-aware flight routing over the North Atlantic using weather regimes.* **Environ. Res. Lett.** 21:074001, 2026-03-30. DOI 10.1088/1748-9326/ae5673; https://iopscience.iop.org/article/10.1088/1748-9326/ae5673 [期刊][full-text]（避湍流使往返燃油最多 +8%；代码仓库未核实）
- F#25 Johnson, Shafaei, et al. *A Survey of Risk-Calibrated Certifiably Safe and Resource-Aware (RCSR) Path Planning for UAVs.* **Drones** 10(5):351, 2026-05-07. DOI 10.3390/drones10050351; https://doi.org/10.3390/drones10050351 [期刊综述][abstract-level]（resource-aware＝算力/超时是可信评估必备维度）
- F#29 SAFiRLab/ARENA. *Adaptive Risk-aware and Energy-efficient NAvigation*（C++）; https://github.com/SAFiRLab/ARENA [GitHub 仓库][confirmed]（**License BSD-3-Clause**；18 stars；最后提交 2026-03-04，经 commits API 确认；连续 NURBS + 遗传优化，非网格 A\*）
- F#34 廖小罕、徐晨晨、叶虎平. 低空经济发展与低空路网基础设施建设的效益和挑战[J]. **中国科学院院刊** 39(11), 2024; https://www.csgpc.org/detail/24652.html [中文期刊]（「自适应四维网格剖分与编码 + 路径规划算法」被定位为既有基础设施）

**内部证据源（非公开文献）**
- `[P1]` 项目盘点《数据资产、规划现状、与论文选题》，本节引用其 §1.1（WRF d02 逐小时 19 层 ~5 km）、§1.2（226 站独立地面定时观测 2015–2025）、§2.2–2.3（A\* 网格/阈值/罚项/预算闸/四态；地形与禁飞区不进搜索）、§4.1–4.3（三条选题）。工作区文件路径 `论文/数据资产-规划现状-与论文选题.md`。
- `[R]` 参照报告《低空湍流监测及最优航路规划》（2025 联合国数据马拉松中国赛，团队「湍途智划」）正文抽取件 `un.txt`；命中词与次数见 §6。

## 6. `[R]` 取证表：判定表里每一处报告数字的命中词与次数

取证方式统一为 **一手全文命中**（对象 = 参照报告 PDF 的正文抽取件 `un.txt`，11,033 字符；计数前做空白归一化，消除 PDF 换行插入的空格）。计数为 0 的一律未写入正文。

| 判定表中的表述 | 命中词（原文） | 次数 |
| --- | --- | --- |
| 湍流识别准确率 98.4% | `湍流识别准确率达98.4%` | 2 |
| Ri 的 RMSE 2.46 | `RMSE=2.46` | 1 |
| 「单源独立」的说法 | `单源独立` | 1 |
| B 段 R²=0.987 | `R²=0.987` | 1 |
| B 段 5 折 CV | `5折交叉验证` | 2 |
| B 段以 A 模型为基准 | `A模型为基准进行验证` | 1 |
| C 段 100 m / 50 m | `水平分辨率100m`、`垂直分辨率50m` | 2 / 2 |
| C 段 Delaunay + 线性插值、边界外最近邻外推 | `Delaunay`、`最近邻外推` | 1 / 1 |
| C 段变差函数「平滑无突变」 | `变差函数` | 1 |
| C 段 221.8% | `221.8%` | 4 |
| C 段「每个站平均被 2.2 部雷达同时观测」 | `2.2部雷达`、`同时观测` | 1 / 1 |
| 559 站 / 600 站混用 | `559个自动气象站`、`600个气象站` | 2 / 2 |
| S 波段 230 km 全覆盖 559 站＝100% | `230公里` | 1 |
| 风廓线雷达联合覆盖 229 站＝41.0%；X 波段 452 站＝80.9% | `三部风廓线雷达联合覆盖了229个`、`41.0%`、`80.9%` | 1 / 1 / 1 |
| 11.4% 站 <10 km、48.7% 站 10–30 km | `11.4%`、`48.7%` | 1 / 1 |
| D 段 TKE/风切变/涡度 + 分位数标准化 + Ri 修正因子加权融合 | `分位数标准化`、`理查逊数修正因子` | 1 / 1 |
| D 段「以模型 C 为基准调权重降系统误差」 | `以模型C处理的观测数据为基准`、`调整权重以优化模型性能`、`调整TI权重系数` | 1 / 1 / 1 |
| D 段 TI 平均误差 0.08 / 匹配率 85% / RMSE 降至 0.15 | `TI平均误差为0.08`、`匹配率高至85%`、`RMSE降至0.15` | 1 / 1 / 1 |
| E 段 RMSE 0.12 / MAE 0.09 / R² 0.85（各两处） | `RMSE为0.12`、`MAE为0.09`、`R²达0.85` | 2 / 2 / 2 |
| E 段「提前 1 小时预警」 | `提前1小时预警` | 1 |
| 规划约束（35–50 m/s、≤5 m/s、50–2000 m、≤30°/s、TI≤0.7） | `35-50m/s`、`爬升率≤5m/s`、`50-2000m`、`转弯角速度≤30°/s`、`TI≤0.7` | 各 1 |
| 500 m × 400 m 网格、「与湍流数据分辨率精确匹配」 | `水平500m`、`垂直400m`、`精确匹配` | 1 / 1 / 1 |
| 高风险区 ×5 倍惩罚、移动平均平滑 | `高风险区×5倍`、`移动平均法` | 1 / 1 |
| 场景清单（含南京–上海） | `南京-上海` | 3 |
| 规划成功率 100%、平均安全评分 0.94、预警准确率 ≥85% | `成功率100%`、`0.94`、`预警准确率` | 1 / 2 / 1 |

**报告里未出现、因此我们没有写进正文的说法**：`EDR`、`PIREP`、`in-situ`、`persistence`、`AUC`、`POD`、`block CV`、`representativeness`（以报告未给真值口径的形式理解）。也就是说，判定表里关于「报告缺什么」的每一句，都是**在全文检索后确认缺失**，不是推测。

---

> **篇幅说明**：本轮按 Mika 机制把交付缩为「判定表 + 结论 + 未覆盖清单」。正文（§1–§4）与 `[R]` 取证表之外，§5 参考来源 38 条锚点（含 URL）行使全文超出 ≤2500 字上限；**超出部分全部是参考条目**，因为本角色的硬规则要求每处事实带可访问 URL。若你希望正文压到 2500 字内，下一轮我可把 §5 拆成独立附件、正文只留锚点标签。
> **本轮零新检索、零 git 操作、零后台子 agent。**
