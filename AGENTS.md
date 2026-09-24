# Repository Guidelines

本指南面向 UAV 4D Weather-Aware Path Planner 仓库的贡献者，覆盖模块结构、常用命令、代码风格、测试与提交规范。

## Project Structure & Module Organization（项目结构与模块组织）

- `backend/` — FastAPI 后端。`main.py` 为 API 入口，`planner.py` 实现 4D A* 寻路，`data_loader.py` 负责 NetCDF 气象与 SRTM 高程加载，`schemas.py` 定义请求/响应模型，`config.py` 集中管理阈值等配置，`requirements.txt` 声明依赖。
- `frontend/` — Vue 3 + TypeScript + Vite。`src/components/` 存放组件（PascalCase 命名，如 `MapContainer.vue`），`src/utils/` 存放工具函数，`src/store.ts` 管理全局状态，`public/` 为静态资源。
- `data/` — 气象 NetCDF 文件（六个约 103 GB/个，实测共 614 GB）与 `data/srtm/*.hgt` 高程切片；已 gitignore，不提交。
- `frp/` — 内网穿透隧道的二进制与含 auth token 的配置；已 gitignore，不提交（token 入库等于把隧道交出去）。
- `scripts/setup_core.py` — 跨平台环境安装逻辑，由根目录 `setup.bat` / `setup.sh` 调用。
- `config.json` — 根目录统一配置文件，后端 host/port 的唯一来源；`vite.config.ts` 的 dev 代理目标也从它读。
  注意 `frp/frpc.toml` 里的 `localPort` 是**手工对齐**它的手写值，不会自动跟随。

## Build, Test, and Development Commands（构建、测试与开发命令）

- `setup.bat`（Windows 推荐）或 `setup.sh`：检测 Python 3.10+（必要时自动下载便携版）并安装后端依赖。
- `start.bat`：启动后端（`python main.py`，端口由根目录 `config.json` 决定，默认 9090）与前端（`npm run dev`，端口 5173），自动打开浏览器。
- `start.bat public`：同上，额外做两件事——`npm run build:watch` 持续产出 dist、启动 `frp\frpc.exe` 把服务挂到公网（关闭本窗口会一并停掉隧道）。不带 `public` 时任何端口都不对外，日常开发默认走这条。
- 手动开发：后端先 `pip install -r backend/requirements.txt`，再运行 `python main.py`（API 文档见 `/docs`，端口由 `config.json` 决定）；前端先 `npm install`，再 `npm run dev`。
- `npm run build`：执行 `vue-tsc -b && vite build`（类型检查 + 构建），提交前端改动前必须通过。
- `npm run build:watch`：`vite build --watch` 增量重建 `frontend/dist`，供后端托管、公网长期访问时使用。**它跳过类型检查**，不能代替 `npm run build`。
- `npm run preview`：本地预览生产构建。
- 公网访问：只需转发后端一个端口（`dist` 与 `/api` 同源），详见 `docs/公网访问部署.md`。
  当前形态：`Cloudflare → OpenResty(443) → frps → 本机 9090`，**有意不加鉴权**（公开可用）。
  **禁止把 Vite dev server 暴露到公网**：dev 模式下仓库内文件可经 `/@fs/` 原文读取，且首屏是数百个未压缩模块请求。

## Performance Facts（性能与数据访问，2026-09-21 实测；2026-09-23 湍流改脉动方差后复测）

- 后端进程 RSS 只有约 0.2 GB —— NetCDF **不是**常驻内存的，16 GB 内存也装不下 614 GB；别按「数据全在内存里」推理。
- 单次 `/api/route/plan`（230 km，含一次网格取数）热态约 0.3 s，A* 搜索本体约 0.003 s；
  取数路径首次落到新日期时受 USB 机械盘冷读影响可到数秒（实测首条 6.4 s），与算法无关。
- 一次 `get_meteorology_grid`（37×81×123）热态 0.27–0.32 s，其中约 0.19 s 花在 dask 的图构建与优化上——是 xarray `chunks=` 带来的框架开销，不是 I/O 也不是算力。改取数路径时优先处理这条。
  2026-09-23 起时间维从 25 h 扩到 37 h（脉动方差要 ±6 h 窗），但多出的 12 帧与中心帧落在同一次 dask 图上，
  冷读仅比旧口径多约 0.5 s，热读几乎不变——**不要**为省这点开销把窗口缩回去或拆成多次取数。
- 并发瓶颈的真实描述是「16 GB 内存 + 单 worker 串行 + 数据盘是 USB 外置机械盘的随机读」，不是「NetCDF 占内存」。
- 4D A* 是串行搜索，GPU 帮不上；能加速的是取数、把每邻居的 `np.abs(...).argmin()` 换成预算索引表、以及 numba/Cython（均未安装）。

## Coding Style & Naming Conventions（代码风格与命名约定）

- Python 遵循 PEP 8，4 空格缩进；函数/变量用 `snake_case`，常量用 `UPPER_SNAKE_CASE`；公开函数加类型标注和 docstring；注释可用中文。
- Vue/TypeScript：使用 Vue 3 `<script setup>` 单文件组件；组件文件 `PascalCase.vue`，工具模块 `camelCase.ts`；保持类型安全（`vue-tsc` 把关）。
- 文件统一 UTF-8；目前未配置 linter/formatter，推荐安装 Volar 扩展（见 `frontend/.vscode/extensions.json`）。
- 前端请求一律走同源相对路径 `${API_BASE}/api/...`，`src/config.ts` 的 `API_BASE` 保持空串；**不要写死 `http://localhost:<port>`**（公网访问者的浏览器会去连自己的本机）。

## Testing Guidelines（测试规范）

- 仓库目前无自动化测试框架与测试目录。新增后端测试时使用 pytest，放在 `backend/tests/`，命名 `test_*.py`，用 `python -m pytest` 运行。
- 重点覆盖：A* 路径正确性（含狗腿/平局决胜）、气象阈值硬/软约束、数据缺失区处理。
- 前端改动需通过 `npm run build`，并在本地地图界面手工验证。
- 改动涉及同源/托管链路时，补一步：`npm run build` 后请求 `http://127.0.0.1:<后端端口>/` 应返回页面，`/api/meta/cities` 仍返回 JSON（根路径挂载会兜走所有请求，注册顺序错了就找不到）。

## Commit & Pull Request Guidelines（提交与 PR 规范）

- 提交信息遵循 Conventional Commits（参考现有 git 历史）：`feat(planner): ...`、`fix(frontend): ...`、`perf(data_loader): ...`、`chore: ...`、`docs: ...`。
- 一次提交只做一件事；开发在临时分支（如 `feat-260812-desc`）进行，合并到 `main` 时 squash 为单条记录并保留开发分支供追溯。
- PR 需说明改动原因与影响面，涉及前端时附截图，并关联相关 issue（如有）。
