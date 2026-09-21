# UAV 4D Weather-Aware Path Planner

这是一个先进的无人机（UAV）路线规划系统，专注于在复杂地形和恶劣气象条件（如青藏高原地区）下，为无人机提供安全的避障与绕飞航线。

## 核心功能

1. **四维 (4D) 时空寻路**：考虑到无人机在飞行过程中时间的流逝，算法不仅在三维空间中寻找避障路线，同时会随着物理距离的推移计算时间偏移，并抓取该**绝对时间**下的天气数据进行安全评估。
2. **多重气象感知**：系统集成了真实的 NetCDF 气象预测数据，实时评估航线上的：
   - 湍流动能 (TKE)
   - 涡旋耗散率 (EDR)
   - 500hPa 综合风速、温度、相对湿度等环境约束
3. **前端仿真推演**：支持在交互式地图上拖拽查看不同时间节点的气象云图（热力图、等值线），并伴有飞行剖面折线图，可以重放和控制无人机模拟飞行进度。

---

## 核心算法实现细节

系统的核心寻路引擎是一个经过高度定制化的 **时空 A* (A-Star) 启发式搜索算法**。它位于 `backend/planner.py` 中。

### 1. 启发式函数 (Heuristic Function)
为了保证 A* 算法在网格中的搜索效率，我们使用了**八向切比雪夫/对角线距离 (Octile Distance)** 作为基础启发式评估函数。
由于是在经纬度坐标系下进行规划，为了保持严格的网格对称性，我们在计算 `h(n)` 时使用了度数（Degree）空间距离，而不是物理距离（米），从而避免启发式函数被不同纬度下的经度形变扭曲。

为防止路径在网格中呈现“狗腿状”（Zig-zag）异常，我们在 `h(n)` 中加入了一个**向量叉乘平局决胜 (Tie-breaker)** 机制，使得算法在多条距离相同的等价路径中，倾向于选择最贴近起终点直线的路径。

### 2. 时空状态预测 (4D Prediction)
在每一次扩展邻居节点时，算法会进行物理时间的测算：
- 节点间的 A* 寻路 Cost 使用基于度数的网格距离。
- 飞行时间的流逝，使用**物理大圆距离 (Haversine/Hypot 距离转换为 km)** 结合无人机设定的航速推算得出。
- 每前进一步，算法将起始时间加上这部分物理距离消耗的时间，换算出**无人机抵达该邻居节点时的绝对物理时间**。
- 随后，系统使用这个绝对时间，从预加载的 4D 气象数据立方体（Time, Level, Lat, Lon）中提取对应的 TKE 和 EDR。

### 3. O(1) 矢量化内存查询 (Vectorized Lookup)
为了实现毫秒级的规划响应速度，我们在 `data_loader.py` 中彻底剥离了高频的磁盘 I/O：
- **气象数据**：在寻路开始前，截取包含起终点的局部 Bounding Box，并将其转化为纯 Numpy Array 驻留在内存中。通过 `np.argmin` 寻找最近坐标轴索引，实现 O(1) 的超高速气象查询。
- **高程数据**：对于包含数万个格点的搜索区域，传统的在线 API 或是基于库的离散插值会导致严重的性能瓶颈。我们设计了 `FastSRTM` 本地缓存方案，利用 `np.memmap` 将切片的 SRTM `.hgt` 二进制文件映射入内存，并在搜索前预构建一个对齐到 A* 分辨率的二维 Numpy Elevation Grid，将十万级别的地形查询耗时降至几毫秒。

### 4. 成本函数与软硬约束 (Cost Function & Constraints)
算法将安全因素划分为两类：

* **硬约束 (Hard Constraints) - 直接抛弃节点**
  - 地形碰撞：如果在给定的相对起飞高度下，绝对高度低于目标网格地形，视作不可逾越的障碍。
  - 极端气象：TKE 超过 1.0，或者 EDR 超过 0.3。
  - 数据缺失区（NaN）：无气象数据的区域视作不可飞越的禁区。

* **软约束 (Soft Penalties) - 增加权重 G-Score**
  - **中度气流颠簸**：当 TKE > 0.4 或 EDR > 0.1 时，根据超出的严重程度乘以 50~200 的惩罚系数，促使航线尽早避开不稳定的气流边缘。
  - **爬升/地形起伏**：计算前后节点的坡度（Slope = Δh / Distance），施加地形起伏惩罚，鼓励无人机在地势平缓的河谷地带飞行。
  - **高海拔惩罚**：对绝对海拔超过 3000m 的节点施加线性惩罚，驱使无人机寻找低海拔的垭口，避免不必要的爬升。

## 运行与部署

### 快速开始 (Windows / 推荐)

```bat
:: 1. 第一次使用: 自动配置 Python 环境 (会按需下载便携 Python + pip install)
setup.bat

:: 2. 启动前后端服务 (会自动开浏览器)
start.bat
```

`setup.bat` 行为：
- 检测系统 Python 3.10+；没有则自动从 python.org 下载 embeddable Python (~10 MB) 到 `backend/.python/portable/`
- `pip install -r backend/requirements.txt` 安装后端依赖
- 整个仓库 clone 后只跑这两条命令即可

### 手动部署

```bat
cd frontend
npm install
npm run dev
```
另开一个终端:
```bat
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py   # 端口统一从根目录 config.json 读取（默认 9090）
```

### 离线/内网环境
把 `backend/.python/portable/` 整个目录打包后放到 release 资产里，clone 完手动解压到 `backend/.python/portable/` 即可跳过下载。

### 让他人通过公网访问

气象 NetCDF 单个文件约 103 GB、六个实测共 614 GB，加上 SRTM 切片，搬到云上不现实。
因此数据始终留在这台 Windows 本机，服务器**只做转发入口**：过隧道的只有页面、热图 PNG 和 JSON。

只需转发后端一个端口——`frontend/dist` 由 FastAPI 挂载，页面与 `/api` 同源：

| 场景 | 怎么跑 |
| --- | --- |
| 自己开发 | `start.bat`（Vite 5173 + 后端 9090，保留热更新），不对外 |
| 给别人访问 | `start.bat public`：额外起 `npm run build:watch` 与 `frpc`，只暴露后端一个端口 |

服务器侧链路是 `Cloudflare → OpenResty(443) → frps → 本机 9090`：TLS 在 Cloudflare 与 OpenResty 之间终结，
`/api/weather/overlay` 的热图 PNG 在服务器上 `proxy_cache`（实测 `X-Cache: MISS → HIT`），
AI 报告的 SSE 单独关掉 `proxy_buffering`。**当前形态是有意不加鉴权的公开服务**，取舍与影响见 runbook 的「鉴权」一节。
完整步骤与配置文件见 [`docs/公网访问部署.md`](docs/公网访问部署.md)。

两条硬性约束：**不要把 Vite dev server 暴露到公网**（dev 模式下仓库文件可经 `/@fs/` 原文读取，含 `frp/frpc.toml` 里的 token），
以及 `frp/` 目录已 gitignore、不要提交。

### AI 评估（可选）

决策平台的「AI 评估报告」默认走离线规则模板版，**不配置也能用**。
若要接入 OpenAI 兼容接口生成更自然的报告，复制模板并填入网关地址与 Key：

```powershell
Copy-Item .env.example .env
# 编辑 .env：UAV_AI_ENABLED=true / UAV_AI_BASE_URL / UAV_AI_MODEL / UAV_AI_API_KEY
```

`.env` 已被 `.gitignore` 忽略，可安全存放真实 Key。
优先级：**真实环境变量 > `.env` > `config.json` 的 `ai` 段 > 默认值**。
详见 [`docs/AI评估接入说明.md`](docs/AI评估接入说明.md)。

推理型模型（MiniMax-M3 / DeepSeek-R1 等）默认已关闭「思考输出」，
报告正文不会再混入分析旁白；配置好 Key 后可用下面的命令一次性验证连通性与正文干净度：

```powershell
cd backend
.python\portable\python.exe scripts\check_ai_report.py
```
### 依赖说明
- **后端依赖**：见 `backend/requirements.txt`，包含 xarray、netCDF4、FastAPI、numpy 等
- **数据依赖**：本地气象 NetCDF 文件 (`.nc`) 与 SRTM 地形切片 (`.hgt`)
- **环境要求**：Python ≥ 3.10, Node.js ≥ 16
