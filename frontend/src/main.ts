import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

import { fetchCoverage, fetchSiteCatalog } from './store'

/**
 * 启动期数据：进 Vue 之前就发出去，比任何组件的 onMounted 都早。
 *
 * 站表尤其不能等面板挂载 —— 它是 resolvePlace 的唯一依据，而 Agent 对话
 * 在页面刚打开就能发消息。曾经在 AiSidebar/SitePanel 的 onMounted 里取，
 * 那是个「发了就不管」的 fire-and-forget：用户在它返回之前发消息，工具看到
 * 空表，于是回一句事实错误的「站表里没有『曲松县』」（曲松县其实在表里，
 * CT-84）。放在这里就没有那个窗口。
 *
 * 不 await：这两份都是几十 KB 的静态数据，卡住首屏不值得。取不到也不影响
 * 地图和面板 —— 各处按 null 判空，真需要时（如 Agent 发消息前）还会再取一次。
 */
void fetchSiteCatalog();
void fetchCoverage();

createApp(App).mount('#app')
