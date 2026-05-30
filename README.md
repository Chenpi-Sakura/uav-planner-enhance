# UAV Planner (无人机气象航线规划系统)

基于 Vue 3 + TypeScript 构建的现代 Web 应用程序，专为无人机飞行任务提供专业的气象数据可视化与航线环境分析。通过融合地图引擎与多维度数据图表，直观展示沿途地形起伏及核心气象指标，协助操作员规划最安全的飞行路线。

## ✨ 核心特性

- **🗺️ 高级地图交互**：基于 Leaflet 实现的高性能底图与航线渲染，支持自由缩放与平移。
- **⛅ 气象图层叠加**：支持热力图 (强弱分布)、等值线 (轮廓密度) 等多种数据叠加层，直观展示全局气象态势。
- **📊 沿线数据切面 (ECharts)**：底部集成动态折线图，直观展示航线剖面的：
  - 地面海拔高度 vs 无人机相对高度
  - TKE (湍流动能) 与 EDR (涡流耗散率)
  - 温度 (°C)、湿度 (%) 及降雨量等关键气象指数
- **🎨 现代化 UI 质感**：采用高级暗色模式与精细的磨砂玻璃 (Frosted Glassmorphism) 质感，搭配经典的 Flat UI 色彩体系，提供极佳的沉浸式视觉体验。

## 🛠️ 技术栈

- **核心框架**: Vue 3 (Composition API, `<script setup>`)
- **开发语言**: TypeScript
- **构建工具**: Vite (极速的冷启动与 HMR)
- **地图引擎**: Leaflet
- **图表渲染**: Apache ECharts
- **图标组件**: Lucide-Vue-Next

## 🚀 快速开始

### 1. 环境准备
请确保你的本地已安装 [Node.js](https://nodejs.org/) (推荐 18.x 及以上版本)。

### 2. 安装依赖
克隆项目到本地后，在项目根目录运行以下命令安装所需依赖：

```bash
npm install
```

### 3. 启动开发服务器
```bash
npm run dev
```
启动成功后，浏览器访问终端中输出的本地地址（通常为 `http://localhost:5173`）即可预览项目。

### 4. 构建生产版本
```bash
npm run build
```
构建后的文件将输出到 `dist` 目录中。

## 📁 核心目录结构说明

- `src/components/`：核心 Vue 组件
  - `DataChartPanel.vue`：底部气象剖面折线图组件
  - `OverlayLegend.vue`：地图气象图层图例组件
  - `Sidebar.vue`：侧边栏控制面板
- `src/style.css`：全局样式文件（包含 Glassmorphism 样式定义与主题色 CSS 变量）
- `src/App.vue`：应用主入口点

---

*本项目使用 [Vite](https://vitejs.dev/) 模板初始化构建。*
