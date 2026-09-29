<template>
  <div class="sidebar-container" :class="{ 'collapsed': store.sidebarCollapsed }">
    <!-- Toggle Button -->
    <button class="toggle-btn" @click="store.sidebarCollapsed = !store.sidebarCollapsed">
      <ChevronLeft v-if="!store.sidebarCollapsed" :size="20" />
      <ChevronRight v-else :size="20" />
    </button>

    <!-- Sidebar Content -->
    <div class="sidebar-content glass-panel">
      
      <!-- 1. Time Selection (Fixed at Top) -->
      <section class="time-section">
        <button class="section-toggle" @click="timeOpen = !timeOpen">
          <span class="section-title"><Clock :size="16" /> 时间选择</span>
          <ChevronDown :size="16" class="chev" :class="{ open: timeOpen }" />
        </button>
        <Transition @enter="foldGrow" @after-enter="foldRelease"
                    @leave="foldShrink" @after-leave="foldRelease">
          <div class="fold-wrap" v-show="timeOpen"><div class="fold-body">
              <div class="form-group">
                <label>具体日期</label>
                <input type="date" class="capsule-input full-width" v-model="store.targetDate" />
              </div>
              <div class="input-row">
                <div class="form-group" style="flex:1">
                  <label>起飞时间</label>
                  <input type="time" step="3600" class="capsule-input full-width" v-model="store.targetTime" />
                </div>
                <div class="form-group" style="flex:1">
                  <label>速度 (km/h)</label>
                  <input type="number" class="capsule-input full-width" v-model="store.uavSpeed" />
                </div>
              </div>
            </div>
          </div>
        </Transition>
      </section>

      <!-- 2. 起降点与途经点：三个标签页共用的输入，所以放在标签页之上而不是某个标签页里 -->
      <section class="route-section">
        <button class="section-toggle" @click="routeOpen = !routeOpen">
          <span class="section-title"><MapPin :size="16" /> 起降点与途经点</span>
          <ChevronDown :size="16" class="chev" :class="{ open: routeOpen }" />
        </button>

        <Transition @enter="foldGrow" @after-enter="foldRelease"
                    @leave="foldShrink" @after-leave="foldRelease">
          <div class="fold-wrap" v-show="routeOpen"><div class="fold-body route-body">
              <!-- Quick Bases -->
              <div class="quick-bases">
                <span class="base-chip" v-for="b in store.bases" :key="b.name" @click="handleBaseClick(b)" title="点击取点后再点击此处快速填入">
                  <MapPin :size="12" /> {{ b.name }}
                </span>
              </div>

              <!-- Start -->
              <div class="waypoint-group">
                <div class="waypoint-header space-between">
                  <div><span class="dot green"></span> 起点 (S)</div>
                  <button class="target-btn" :class="{ active: store.selectingMode === 'start' }" @click="selectPointOnMap('start')">
                    <Crosshair :size="14" /> 取点
                  </button>
                </div>
                <div class="input-labels">
                  <span>经度</span>
                  <span>纬度</span>
                  <span>相对高度</span>
                </div>
                <div class="input-row">
                  <input type="number" placeholder="经度" class="capsule-input mini" v-model="store.start.lng" />
                  <input type="number" placeholder="纬度" class="capsule-input mini" v-model="store.start.lat" />
                  <input type="number" placeholder="相对高度" class="capsule-input mini" v-model="store.start.alt" />
                </div>
              </div>

              <!-- Waypoints -->
              <div class="waypoint-group" v-for="(wp, idx) in store.waypoints" :key="idx">
                <div class="waypoint-header space-between">
                  <div><span class="dot blue"></span> 途经点 {{ idx + 1 }}</div>
                  <div class="flex-actions">
                    <button class="target-btn" :class="{ active: store.selectingMode === 'waypoint' && store.selectingIndex === idx }" @click="selectPointOnMap('waypoint', idx)">
                      <Crosshair :size="14" /> 取点
                    </button>
                    <button class="remove-btn" @click="removeWaypoint(idx)" title="删除"><X :size="14" /></button>
                  </div>
                </div>
                <div class="input-labels">
                  <span>经度</span>
                  <span>纬度</span>
                  <span>相对高度</span>
                </div>
                <div class="input-row">
                  <input type="number" placeholder="经度" class="capsule-input mini" v-model="wp.lng" />
                  <input type="number" placeholder="纬度" class="capsule-input mini" v-model="wp.lat" />
                  <input type="number" placeholder="相对高度" class="capsule-input mini" v-model="wp.alt" />
                </div>
              </div>

              <button class="add-waypoint-btn" @click="addWaypoint">
                <Plus :size="14" /> 添加途经点
              </button>

              <!-- End -->
              <div class="waypoint-group">
                <div class="waypoint-header space-between">
                  <div><span class="dot red"></span> 终点 (E)</div>
                  <button class="target-btn" :class="{ active: store.selectingMode === 'end' }" @click="selectPointOnMap('end')">
                    <Crosshair :size="14" /> 取点
                  </button>
                </div>
                <div class="input-labels">
                  <span>经度</span>
                  <span>纬度</span>
                  <span>相对高度</span>
                </div>
                <div class="input-row">
                  <input type="number" placeholder="经度" class="capsule-input mini" v-model="store.end.lng" />
                  <input type="number" placeholder="纬度" class="capsule-input mini" v-model="store.end.lat" />
                  <input type="number" placeholder="相对高度" class="capsule-input mini" v-model="store.end.alt" />
                </div>
              </div>
            </div>
          </div>
        </Transition>
      </section>

      <!-- 标签页切换：路径规划 / 可行性评估 / 时间窗口 -->
      <div class="tab-bar">
        <button
          class="tab-btn"
          :class="{ active: store.sidebarTab === 'plan' }"
          @click="store.sidebarTab = 'plan'"
        >
          <Navigation :size="14" /> 路径规划
        </button>
        <button
          class="tab-btn"
          :class="{ active: store.sidebarTab === 'timeWindow' }"
          @click="store.sidebarTab = 'timeWindow'"
        >
          <CalendarClock :size="14" /> 时间窗口
        </button>
        <button
          class="tab-btn"
          :class="{ active: store.sidebarTab === 'site' }"
          @click="store.sidebarTab = 'site'"
        >
          <Route :size="14" /> 多航点
        </button>
      </div>

      <!-- Scrollable Area for Path Planning -->
      <div class="scroll-area">
        <template v-if="store.sidebarTab === 'plan'">
          <div class="action-row">
            <button class="capsule-btn full-width" @click="runPlan" :disabled="store.planLoading" :class="{'disabled-btn': store.planLoading}">
              <span v-if="store.planLoading" class="loading-icon"><Loader :size="16" class="spin"/> 正在规划并评估三条航线...</span>
              <span v-else style="display:flex; align-items:center; gap:8px;"><Send :size="16" /> 开始规划</span>
            </button>
          </div>

          <!-- 三档横向卡片 + 当前档的评估详情（原「可行性评估」标签页并入此处） -->
          <RouteTierCards v-if="store.routeOptions" />
          <FeasibilityPanel v-if="store.routeOptions" />
        </template>

        <!-- 时间窗口面板 -->
        <TimeWindowPanel v-if="store.sidebarTab === 'timeWindow'" />

        <!-- 多航点模式：站点自动规划（M7） -->
        <SitePanel v-if="store.sidebarTab === 'site'" />
      </div>

      <!-- 3. Overlays (Fixed at Bottom) -->
      <section class="overlay-section">
        <button class="section-toggle" @click="overlayOpen = !overlayOpen">
          <span class="section-title"><Layers :size="16" /> 叠加层</span>
          <ChevronDown :size="16" class="chev" :class="{ open: overlayOpen }" />
        </button>
        <Transition @enter="foldGrow" @after-enter="foldRelease"
                    @leave="foldShrink" @after-leave="foldRelease">
          <div class="fold-wrap" v-show="overlayOpen"><div class="fold-body checkbox-grid">
              <label class="custom-checkbox" v-for="layer in overlayOptions" :key="layer.id">
                <input type="checkbox" v-model="store.overlays" :value="layer.id" />
                <span class="checkmark"></span>
                {{ layer.label }}
              </label>
            </div>
          </div>
        </Transition>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ChevronLeft, ChevronRight, ChevronDown, Clock, Navigation, Layers, Send, Plus, X, Crosshair, Loader, MapPin, CalendarClock, Route } from 'lucide-vue-next';
import { store, runPlan } from '../store';
import type { BaseStation } from '../store';
import FeasibilityPanel from './FeasibilityPanel.vue';
import RouteTierCards from './RouteTierCards.vue';
import TimeWindowPanel from './TimeWindowPanel.vue';
import SitePanel from './SitePanel.vue';

/** 三个固定区都能折：展开优先给高频输入，纵向不够时收起来换内容区高度 */
const timeOpen = ref(true);
const routeOpen = ref(true);
const overlayOpen = ref(true);

const FOLD_TRANSITION = 'height 200ms ease';

/**
 * 收起/展开的过程动画：高度由 JS 量出来直接写在元素上。
 *
 * 不用 CSS 类驱动，也不动 `grid-template-rows`——前者要求 JS 与 CSS 两份产物同时到位
 * （差一个就退化成瞬间跳），后者的插值要 Chrome/Edge 107+。`height` 的过渡没有这两个前提。
 */
const foldGrow = (el: Element) => {
  const h = el as HTMLElement;
  h.style.height = '0px';
  void h.offsetHeight;                 // 强制回流：0 没被渲染过，auto→N 是不可插值的
  h.style.transition = FOLD_TRANSITION;
  h.style.height = `${h.scrollHeight}px`;
};

const foldShrink = (el: Element) => {
  const h = el as HTMLElement;
  h.style.transition = 'none';
  h.style.height = `${Math.round(h.getBoundingClientRect().height)}px`;
  void h.offsetHeight;                 // 起点必须是一个已渲染的确定像素值
  h.style.transition = FOLD_TRANSITION;
  h.style.height = '0px';
};

/** 收尾把内联样式交还给 CSS：展开态高度要跟着「添加途经点」继续自己长 */
const foldRelease = (el: Element) => {
  const h = el as HTMLElement;
  h.style.height = '';
  h.style.transition = '';
};

const overlayOptions = [
  { id: 'tke', label: 'TKE (湍动能)' },
  { id: 'edr', label: 'EDR (涡动耗散率)' },
  { id: 'flow', label: '综合风速' },
  { id: 'temp', label: '温度' },
  { id: 'humidity', label: '相对湿度' },
  { id: 'qvapor', label: '水汽混合比' },
];

const addWaypoint = () => {
  store.waypoints.push({ lng: '', lat: '', alt: '' });
};

const removeWaypoint = (idx: number) => {
  store.waypoints.splice(idx, 1);
  if (store.selectingMode === 'waypoint' && store.selectingIndex === idx) {
    store.selectingMode = null;
  }
};

const selectPointOnMap = (mode: 'start' | 'end' | 'waypoint', idx: number = -1) => {
  if (store.selectingMode === mode && store.selectingIndex === idx) {
    store.selectingMode = null; // Toggle off
  } else {
    store.selectingMode = mode;
    store.selectingIndex = idx;
  }
};

const handleBaseClick = (base: BaseStation) => {
  if (store.selectingMode) {
    if (store.selectingMode === 'start') {
      store.start.lat = base.lat;
      store.start.lng = base.lng;
      store.start.alt = base.alt;
    } else if (store.selectingMode === 'end') {
      store.end.lat = base.lat;
      store.end.lng = base.lng;
      store.end.alt = base.alt;
    } else if (store.selectingMode === 'waypoint') {
      if (store.selectingIndex >= 0) {
        store.waypoints[store.selectingIndex].lat = base.lat;
        store.waypoints[store.selectingIndex].lng = base.lng;
        store.waypoints[store.selectingIndex].alt = base.alt;
      }
    }
    store.selectingMode = null;
  } else {
    alert('请先点击下方的“取点”按钮激活选择模式，再点击城市名称快速填入！');
  }
};

</script>

<style scoped>
.tab-bar {
  display: flex;
  gap: 6px;
  padding: 10px 20px 0 20px;
  flex-shrink: 0;
}
.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 8px 4px;
  font-size: 12px;
  border-radius: 8px 8px 0 0;
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}
.tab-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.tab-btn.active {
  color: var(--accent-color);
  border-bottom-color: var(--accent-color);
  background: rgba(52, 152, 219, 0.12);
  font-weight: 600;
}

.sidebar-container {
  position: absolute;
  top: 50%;
  left: 20px;
  transform: translateY(-50%);
  z-index: 1000; /* Sidebar should be above the chart */
  display: flex;
  height: calc(100vh - 40px);
  max-height: 900px;
  transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.sidebar-container.collapsed {
  transform: translate(calc(-100% - 20px), -50%);
}

.sidebar-content {
  width: var(--rail-w);
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Seamless toggle button */
.toggle-btn {
  position: absolute;
  right: -32px;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 60px;
  border-radius: 0 16px 16px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: var(--panel-bg);
  -webkit-backdrop-filter: blur(20px);
  backdrop-filter: blur(20px);
  border: 1px solid var(--panel-border);
  border-left: none;
  color: var(--text-secondary);
  box-shadow: 4px 0 12px rgba(0,0,0,0.2);
  transition: color 0.3s ease;
  z-index: -1;
}

.toggle-btn:hover {
  color: var(--text-primary);
}

.time-section {
  padding: 20px;
  border-bottom: 1px solid var(--panel-border);
  background: rgba(0,0,0,0.1); 
  flex-shrink: 0;
}

.route-section {
  padding: 16px 20px;
  border-bottom: 1px solid var(--panel-border);
  background: rgba(0,0,0,0.1);
  flex-shrink: 0;
}

/* 折叠只在标题行响应，展开后收起不必去内容里找按钮 */
.section-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
}
.section-toggle .section-title { margin-bottom: 0; }
.section-toggle:hover .section-title { color: var(--text-primary); }
.chev { color: var(--text-secondary); flex-shrink: 0; transform: rotate(-90deg); transition: transform 0.2s ease; }
.chev.open { transform: none; }
/* 折叠动画的壳只负责裁剪。那 14px 间距放在被裁的 .fold-body 里——
   放在壳上时 height 收到 0 也夹不掉 padding，会残留一截。 */
.fold-wrap { overflow: hidden; }
.fold-body { padding-top: 14px; }

/* 途经点可以一直加：这一段自己滚，不要把下面的标签页内容挤没 */
.route-body {
  max-height: 30vh;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
}

.scroll-area {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
}

.overlay-section {
  padding: 20px;
  border-top: 1px solid var(--panel-border);
  background: rgba(0,0,0,0.15); 
  margin-top: auto; 
  flex-shrink: 0;
}

.section-title {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.form-group {
  margin-bottom: 12px;
}
.form-group label {
  display: block;
  font-size: 13px;
  margin-bottom: 6px;
  color: #cbd5e1;
}

.quick-bases {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.base-chip {
  background: rgba(52, 152, 219, 0.15);
  border: 1px solid rgba(52, 152, 219, 0.3);
  color: #3498db;
  padding: 4px 10px;
  border-radius: 9999px;
  font-size: 11px;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.base-chip:hover {
  background: #3498db;
  color: white;
}

.mini {
  width: 0;
  flex: 1;
  padding: 8px 10px;
  font-size: 12px;
  text-align: center;
}

.input-row {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}

.input-labels {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}
.input-labels span {
  flex: 1;
  text-align: center;
  font-size: 11px;
  color: var(--text-secondary);
}

.waypoint-header {
  font-size: 13px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.space-between {
  justify-content: space-between;
}
.flex-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot.green { background-color: var(--success-color); }
.dot.red { background-color: var(--danger-color); }
.dot.blue { background-color: var(--accent-color); }

.target-btn {
  background: transparent;
  border: 1px solid var(--panel-border);
  color: var(--text-secondary);
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.target-btn:hover {
  background: rgba(255,255,255,0.1);
  color: var(--text-primary);
}
.target-btn.active {
  background: var(--accent-color);
  color: white;
  border-color: var(--accent-color);
}

.add-waypoint-btn {
  background: transparent;
  border: 1px dashed var(--panel-border);
  color: var(--text-secondary);
  width: 100%;
  padding: 6px 0;
  border-radius: 9999px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-bottom: 16px;
  font-size: 12px;
  transition: all 0.2s;
}
.add-waypoint-btn:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.remove-btn {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: var(--danger-color);
  cursor: pointer;
  padding: 3px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  transition: all 0.2s;
}
.remove-btn:hover {
  background: var(--danger-color);
  color: white;
}

.action-row {
  margin-bottom: 14px;
}
/* 「开始规划」是这一页的主操作，字号此前一直吃浏览器对 <button> 的默认 13.33px */
.action-row .capsule-btn {
  font-size: 15px;
}

/* Custom Checkbox Grid */
.checkbox-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.custom-checkbox {
  display: flex;
  align-items: center;
  position: relative;
  padding-left: 28px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
}

.custom-checkbox input {
  position: absolute;
  opacity: 0;
  cursor: pointer;
  height: 0;
  width: 0;
}

.checkmark {
  position: absolute;
  top: 0;
  left: 0;
  height: 18px;
  width: 18px;
  background-color: rgba(255, 255, 255, 0.1);
  border: 1px solid var(--panel-border);
  border-radius: 4px;
  transition: all 0.2s ease;
}

.custom-checkbox:hover input ~ .checkmark {
  background-color: rgba(255, 255, 255, 0.2);
}

.custom-checkbox input:checked ~ .checkmark {
  background-color: var(--accent-color);
  border-color: var(--accent-color);
}

.checkmark:after {
  content: "";
  position: absolute;
  display: none;
}

.custom-checkbox input:checked ~ .checkmark:after {
  display: block;
}

.custom-checkbox .checkmark:after {
  left: 6px;
  top: 2px;
  width: 4px;
  height: 8px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

/* Date/Select overrides for dark mode */
input[type="date"]::-webkit-calendar-picker-indicator {
  filter: invert(1);
  opacity: 0.6;
  cursor: pointer;
}
select option {
  background-color: #0f172a;
  color: white;
}

.disabled-btn {
  opacity: 0.7;
  cursor: not-allowed !important;
}
.loading-icon {
  display: flex;
  align-items: center;
  gap: 8px;
}
.spin {
  animation: spin 1.5s linear infinite;
}
@keyframes spin {
  100% { transform: rotate(360deg); }
}
</style>
