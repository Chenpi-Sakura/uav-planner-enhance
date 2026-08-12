# Repository Guidelines

本指南面向 UAV 4D Weather-Aware Path Planner 仓库的贡献者，覆盖模块结构、常用命令、代码风格、测试与提交规范。

## Project Structure & Module Organization（项目结构与模块组织）

- `backend/` — FastAPI 后端。`main.py` 为 API 入口，`planner.py` 实现 4D A* 寻路，`data_loader.py` 负责 NetCDF 气象与 SRTM 高程加载，`schemas.py` 定义请求/响应模型，`config.py` 集中管理阈值等配置，`requirements.txt` 声明依赖。
- `frontend/` — Vue 3 + TypeScript + Vite。`src/components/` 存放组件（PascalCase 命名，如 `MapContainer.vue`），`src/utils/` 存放工具函数，`src/store.ts` 管理全局状态，`public/` 为静态资源。
- `data/` — 气象 NetCDF 文件与 `data/srtm/*.hgt` 高程切片；已 gitignore，不提交。
- `scripts/setup_core.py` — 跨平台环境安装逻辑，由根目录 `setup.bat` / `setup.sh` 调用。
- `config.json` — 根目录统一配置文件，后端 host/port 的唯一来源（前端 API 地址由它生成）。

## Build, Test, and Development Commands（构建、测试与开发命令）

- `setup.bat`（Windows 推荐）或 `setup.sh`：检测 Python 3.10+（必要时自动下载便携版）并安装后端依赖。
- `start.bat`：启动后端（`python main.py`，端口由根目录 `config.json` 决定，默认 9090）与前端（`npm run dev`，端口 5173），自动打开浏览器。
- 手动开发：后端先 `pip install -r backend/requirements.txt`，再运行 `python main.py`（API 文档见 `/docs`，端口由 `config.json` 决定）；前端先 `npm install`，再 `npm run dev`。
- `npm run build`：执行 `vue-tsc -b && vite build`（类型检查 + 构建），提交前端改动前必须通过。
- `npm run preview`：本地预览生产构建。

## Coding Style & Naming Conventions（代码风格与命名约定）

- Python 遵循 PEP 8，4 空格缩进；函数/变量用 `snake_case`，常量用 `UPPER_SNAKE_CASE`；公开函数加类型标注和 docstring；注释可用中文。
- Vue/TypeScript：使用 Vue 3 `<script setup>` 单文件组件；组件文件 `PascalCase.vue`，工具模块 `camelCase.ts`；保持类型安全（`vue-tsc` 把关）。
- 文件统一 UTF-8；目前未配置 linter/formatter，推荐安装 Volar 扩展（见 `frontend/.vscode/extensions.json`）。

## Testing Guidelines（测试规范）

- 仓库目前无自动化测试框架与测试目录。新增后端测试时使用 pytest，放在 `backend/tests/`，命名 `test_*.py`，用 `python -m pytest` 运行。
- 重点覆盖：A* 路径正确性（含狗腿/平局决胜）、气象阈值硬/软约束、数据缺失区处理。
- 前端改动需通过 `npm run build`，并在本地地图界面手工验证。

## Commit & Pull Request Guidelines（提交与 PR 规范）

- 提交信息遵循 Conventional Commits（参考现有 git 历史）：`feat(planner): ...`、`fix(frontend): ...`、`perf(data_loader): ...`、`chore: ...`、`docs: ...`。
- 一次提交只做一件事；开发在临时分支（如 `feat-260812-desc`）进行，合并到 `main` 时 squash 为单条记录并保留开发分支供追溯。
- PR 需说明改动原因与影响面，涉及前端时附截图，并关联相关 issue（如有）。
