# Repository Guidelines

本指南面向 UAV 4D Weather-Aware Path Planner 仓库的贡献者，覆盖模块结构、常用命令、代码风格、测试与提交规范。

## Project Structure & Module Organization（项目结构与模块组织）

- `backend/` — FastAPI 后端。`main.py` 为 API 入口，`planner.py` 实现 4D A* 寻路，`data_loader.py` 负责 NetCDF 气象与 SRTM 高程加载，`schemas.py` 定义请求/响应模型，`config.py` 集中管理阈值等配置，`requirements.txt` 声明依赖。
- `frontend/` — Vue 3 + TypeScript + Vite。`src/components/` 存放组件（PascalCase 命名，如 `MapContainer.vue`），`src/utils/` 存放工具函数，`src/store.ts` 管理全局状态，`public/` 为静态资源。
- `data/` — 气象 NetCDF 文件（六个约 103 GB/个，实测共 614 GB）与 `data/srtm/*.hgt` 高程切片；已 gitignore，不提交。
- `frp/` — 内网穿透隧道的二进制与含 auth token 的配置；已 gitignore，不提交（token 入库等于把隧道交出去）。
- `scripts/setup_core.py` — 跨平台环境安装逻辑，由根目录 `setup.bat` / `setup.sh` 调用。
- `config.json` — 根目录统一配置文件，后端 host/port 的唯一来源（可选的 `backend.demo_dir` 指定最小演示子集，环境变量 `UAV_DEMO_DIR` 覆盖它）；`vite.config.ts` 的 dev 代理目标也从它读。
  注意 `frp/frpc.toml` 里的 `localPort` 是**手工对齐**它的手写值，不会自动跟随。

## Build, Test, and Development Commands（构建、测试与开发命令）

- `setup.bat`（Windows 推荐）或 `setup.sh`：检测 Python 3.12+（必要时自动下载便携版）并安装后端依赖。依赖走 `pip install -r backend/requirements.txt` 装进该解释器，**不用 conda**（仓库没有 conda 环境文件）。
- `start.bat`：启动后端（`python -m uvicorn main:app --reload`，端口由根目录 `config.json` 决定，默认 9090）与前端（`npm run dev`，端口 5173），自动打开浏览器。
- `start.bat public`：同上，额外做两件事——`npm run build:watch` 持续产出 dist、启动 `frp\frpc.exe` 把服务挂到公网（关闭本窗口会一并停掉隧道）。不带 `public` 时任何端口都不对外，日常开发默认走这条。
  两种模式都会**先清场再起服务**：`scripts\stop_previous.ps1` 杀掉上一轮残留（含 `start_watcher.ps1` —— 它读的是共享的 `%TEMP%\uav_planner_pids.txt`，晚一步退出就会把新栈整棵带走）并等后端端口释放；public 模式还会在起后端**之前**阻塞跑一次 `npm run build`（`build:watch` 头一遍会清空 dist，而 `/` 的静态挂载只在后端 import 时判一次，撞上就是整轮页面 404）。因此旧窗口关掉后不再影响新起的栈，停服务只关最新那个窗口。
- 手动开发：后端先 `pip install -r backend/requirements.txt`，再运行 `python main.py`（API 文档见 `/docs`，端口由 `config.json` 决定）；前端先 `npm install`，再 `npm run dev`。
- 后端热更新靠 `--reload` + `watchfiles`（已列入 `requirements.txt`）。**没装 watchfiles 时 `--reload` 是空转的**：uvicorn 退回轮询实现，实测改了 `backend/*.py` 也不会重启，进程一直跑旧代码——2026-09 那次湍流公式换口径后公网仍在出旧刻度就是这个原因。
  **装上 watchfiles 也不等于可靠**：2026-09-24 实测，用 `Start-Process`（日志重定向到文件）起的实例，改/新增 `backend/*.py` 会在 1~2 s 内打出 `WatchFiles detected changes ... Reloading...` 并重启 worker；而 `start.bat`（`cmd /k` 可见窗口）起的实例改了 `config.py`、新建 `.py` 都不触发，worker PID 不变、配置值不刷新，原因未查清。
  所以判断生效只有一条硬证据：**去该实例的日志里找 `Started reloader process [...] using WatchFiles` 和随后的 `Reloading...`**；看不到就当作没生效，直接重启该进程。改完 `backend/*.py` 若要让公网生效，默认按"必须重启"来规划。热重载真触发时**公网会抖 1~2 秒**、正在跑的规划会中断，不想被打断就去掉 `--reload`。判断某个进程用的是哪套代码，别看端口，看它是不是重启过。
- `npm run build`：执行 `vue-tsc -b && vite build`（类型检查 + 构建），提交前端改动前必须通过。
- `npm run build:watch`：`vite build --watch` 增量重建 `frontend/dist`，供后端托管、公网长期访问时使用。**它跳过类型检查**，不能代替 `npm run build`。
- `npm run preview`：本地预览生产构建。
- **调试日志只有两个合法落点**：仓库根的 `logs\`（手工排查）与 `%TEMP%\uav_*`（`start.bat` 自己的运行时产物，两者互不重叠）。**不要往仓库根、`backend\` 或源码目录丢 `.log`**——`logs/` 整棵与所有 `*.log` 虽已 gitignore，但排查完仍应手工删掉。
  应用代码不写任何文件日志（无 `basicConfig`/`FileHandler`、无 `open(..., 'w')`），所以 `backend\` 下出现 `.log` 只可能是手工重定向留下的。
  新增 `.bat` 时**注释一律用英文**：cmd.exe 按系统 ANSI 码页读 `.bat`，无 BOM 的 UTF-8 中文注释会被解析成命令而不是注释。
- 公网访问：只需转发后端一个端口（`dist` 与 `/api` 同源），详见 `docs/方案设计/公网访问部署.md`。
  当前形态：`Cloudflare → OpenResty(443) → frps → 本机 9090`，**有意不加鉴权**（公开可用）。
  **禁止把 Vite dev server 暴露到公网**：dev 模式下仓库内文件可经 `/@fs/` 原文读取，且首屏是数百个未压缩模块请求。

## Coding Style & Naming Conventions（代码风格与命名约定）

- Python 遵循 PEP 8，4 空格缩进；函数/变量用 `snake_case`，常量用 `UPPER_SNAKE_CASE`；公开函数加类型标注和 docstring；注释可用中文。
- Vue/TypeScript：使用 Vue 3 `<script setup>` 单文件组件；组件文件 `PascalCase.vue`，工具模块 `camelCase.ts`；保持类型安全（`vue-tsc` 把关）。
- 文件统一 UTF-8；目前未配置 linter/formatter，推荐安装 Volar 扩展（见 `frontend/.vscode/extensions.json`）。
- **已知取舍，不要"顺手优化"**：① `backend/requirements.txt` 有意不钉版本（后端是单机部署，钉死反而会在别的机器上卡在 netcdf4/h5netcdf 这类带 C 扩展的轮子上）；② `start.bat` 有意不校验解释器版本，拿到哪个解释器就用哪个。
- 前端请求一律走同源相对路径 `${API_BASE}/api/...`，`src/config.ts` 的 `API_BASE` 保持空串；**不要写死 `http://localhost:<port>`**（公网访问者的浏览器会去连自己的本机）。

## Testing Guidelines（测试规范）

- 后端测试在 `backend/tests/`：`test_*.py` 若干 + 一个基准数据助手 `golden_plan.py`。用**标准库 `unittest`**，不是 pytest——`pytest` 既不在 `requirements.txt` 里也没装，别去找它。
  运行：在 `backend/` 目录下执行 `python -m unittest discover -s tests -t .`。**用例数与耗时不写进本文**：它们每轮都在涨，要多少以那次运行自己的输出为准。
- 有一个类默认跳过：`test_grid_cache.py::TestRealDataEquivalence` 要拿真实 NetCDF 做新旧取数路径的逐比特比对，需显式设 `UAV_REAL_DATA_TESTS=1` 才跑。**动 `data_loader` 的取数/缓存路径时必须带这个环境变量复跑一次**——它守的就是「优化不改结论」这条底线。
- 前端测试在 `frontend/tests/*.test.mjs`：`simClock.test.mjs`（推演时钟的跨天、折线与游标取数）与 `chips.test.mjs`（建议胶囊的保留/合并算子），用 `node:test` + `node:assert/strict`，需 Node ≥ 22.18（它们直跑 `.ts`）。运行：`cd frontend && npm test`（等价于 `node --test tests/*.test.mjs`，新增测试文件不用改脚本）。
- 重点覆盖：A* 路径正确性（栅格阶梯拉直与平局决胜）、气象阈值硬/软约束、NaN/数据缺失区处理、Agent 工具契约与界面控件的键表一致（`test_overlay_keys.py` 按文本比对前端，界面改 id 要同步它）。
- 前端改动需通过 `npm run build`，并在本地地图界面手工验证。
- 改动涉及同源/托管链路时，补一步：`npm run build` 后请求 `http://127.0.0.1:<后端端口>/` 应返回页面，`/api/meta/cities` 仍返回 JSON（根路径挂载会兜走所有请求，注册顺序错了就找不到）。

## Commit & Pull Request Guidelines（提交与 PR 规范）

- 提交信息遵循 Conventional Commits（参考现有 git 历史）：`feat(planner): ...`、`fix(frontend): ...`、`perf(data_loader): ...`、`chore: ...`、`docs: ...`。
- 一次提交只做一件事；开发在临时分支（如 `feat-260812-desc`）进行，合并到 `main` 时 squash 为单条记录并保留开发分支供追溯。
- PR 需说明改动原因与影响面，涉及前端时附截图，并关联相关 issue（如有）。
