<template>
  <div class="sidebar-container" :class="{ 'collapsed': isCollapsed }">
    <!-- Toggle Button -->
    <button class="toggle-btn" @click="toggleSidebar">
      <ChevronLeft v-if="!isCollapsed" :size="20" />
      <ChevronRight v-else :size="20" />
    </button>

    <!-- Sidebar Content -->
    <div class="sidebar-content glass-panel">
      
      <!-- 1. Time Selection (Fixed at Top) -->
      <section class="time-section">
        <h3 class="section-title"><Clock :size="16" /> 时间选择相关</h3>
        <div class="form-group">
          <label>具体日期</label>
          <input type="date" class="capsule-input full-width" v-model="form.date" />
        </div>
        <div class="form-group">
          <label>时间段</label>
          <select class="capsule-input full-width" v-model="form.timeSlot">
            <option value="morning">早八点 - 晚八点</option>
            <option value="night">晚八点 - 早八点</option>
          </select>
        </div>
      </section>

      <!-- Scrollable Area for Path Planning -->
      <div class="scroll-area">
        <!-- 2. Path Planning -->
        <section class="control-section">
          <h3 class="section-title"><Navigation :size="16" /> 路径规划相关</h3>
          
          <!-- Start -->
          <div class="waypoint-group">
            <div class="waypoint-header space-between">
              <div><span class="dot green"></span> 起点 (S)</div>
              <button class="target-btn" :class="{ active: store.selectingMode === 'start' }" @click="selectPointOnMap('start')">
                <Crosshair :size="14" /> 取点
              </button>
            </div>
            <div class="input-row">
              <input type="number" placeholder="经度" class="capsule-input mini" v-model="store.start.lng" />
              <input type="number" placeholder="纬度" class="capsule-input mini" v-model="store.start.lat" />
              <input type="number" placeholder="海拔" class="capsule-input mini" v-model="store.start.alt" />
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
            <div class="input-row">
              <input type="number" placeholder="经度" class="capsule-input mini" v-model="wp.lng" />
              <input type="number" placeholder="纬度" class="capsule-input mini" v-model="wp.lat" />
              <input type="number" placeholder="海拔" class="capsule-input mini" v-model="wp.alt" />
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
            <div class="input-row">
              <input type="number" placeholder="经度" class="capsule-input mini" v-model="store.end.lng" />
              <input type="number" placeholder="纬度" class="capsule-input mini" v-model="store.end.lat" />
              <input type="number" placeholder="海拔" class="capsule-input mini" v-model="store.end.alt" />
            </div>
          </div>

          <div class="action-row">
            <button class="capsule-btn full-width" @click="handlePlan">
              <Send :size="16" /> 开始规划
            </button>
          </div>
        </section>
      </div>

      <!-- 3. Overlays (Fixed at Bottom) -->
      <section class="overlay-section">
        <h3 class="section-title"><Layers :size="16" /> 多种叠加层</h3>
        <div class="checkbox-grid">
          <label class="custom-checkbox" v-for="layer in overlayOptions" :key="layer.id">
            <input type="checkbox" v-model="store.overlays" :value="layer.id" />
            <span class="checkmark"></span>
            {{ layer.label }}
          </label>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue';
import { ChevronLeft, ChevronRight, Clock, Navigation, Layers, Send, Plus, X, Crosshair } from 'lucide-vue-next';
import { store } from '../store';

const isCollapsed = ref(false);

const toggleSidebar = () => {
  isCollapsed.value = !isCollapsed.value;
};

const form = reactive({
  date: new Date().toISOString().split('T')[0],
  timeSlot: 'morning'
});

const overlayOptions = [
  { id: 'tke', label: 'TKE (湍动能)' },
  { id: 'edr', label: 'EDR (耗散率)' },
  { id: 'flow', label: '高端流区' },
  { id: 'temp', label: '温度' },
  { id: 'humidity', label: '湿度' },
  { id: 'rain', label: '降雨量' },
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

const handlePlan = () => {
  console.log('Planning path with:', store);
};
</script>

<style scoped>
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
  width: 380px; 
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
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
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

.scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.control-section {
  margin-bottom: 10px;
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

.full-width {
  width: 100%;
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
  margin-top: 16px;
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
</style>
