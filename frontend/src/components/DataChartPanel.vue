<template>
  <div class="data-chart-container" :class="{ 'collapsed': store.chartCollapsed }">
    <!-- Toggle Button -->
    <div class="toggle-wrapper" @click="store.chartCollapsed = !store.chartCollapsed">
      <div class="toggle-handle glass-panel">
        <ChevronDown v-if="!store.chartCollapsed" :size="20" />
        <ChevronUp v-else :size="20" />
        <span class="toggle-text">{{ store.chartCollapsed ? '展开折线图' : '收起折线图' }}</span>
      </div>
    </div>

    <!-- Main Panel -->
    <div class="chart-panel glass-panel">
      <div class="panel-content">
        <!-- Left: Legend -->
        <div class="legend-area">
          <h3 class="area-title"><Activity :size="16" /> 图例</h3>
          <div class="legend-content">
            <div class="legend-item" v-for="(item, index) in legends" :key="index">
              <span class="color-box" :style="{ backgroundColor: item.color }"></span>
              <span class="legend-name">{{ item.name }}</span>
            </div>
          </div>
          <!-- Simulation Controls -->
          <div class="simulation-controls" v-if="store.plannedPath.length > 0">
            <h3 class="area-title" style="margin-top: 16px;"><Play :size="16" /> 推演控制</h3>
            <div class="sim-actions">
              <button class="sim-btn" @click="toggleSimulation" :class="{ active: store.isSimulating }">
                <Pause v-if="store.isSimulating" :size="16" />
                <Play v-else :size="16" />
              </button>
              <button class="sim-btn stop" @click="stopSimulation"><Square :size="16" /></button>
              <span class="sim-clock" :title="simClockText">
                {{ simClockText }}
              </span>
            </div>
            <div class="sim-speed">
              <label>速度: {{ (store.simulationSpeed / 1000).toFixed(1) }}s/步</label>
              <input type="range" min="100" max="2000" step="100" v-model="store.simulationSpeed" />
            </div>
          </div>
        </div>
        
        <!-- Right: Chart -->
        <div class="echarts-wrap">
          <div class="echarts-area" ref="chartRef"></div>
          <div class="chart-empty" v-if="store.plannedPath.length === 0">
            <span class="chart-empty-text">暂无路线</span>
            <span class="chart-empty-hint">请先在左侧规划航线</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, shallowRef, watch } from 'vue';
import { ChevronDown, ChevronUp, Activity, Play, Pause, Square } from 'lucide-vue-next';
import * as echarts from 'echarts';
import { store, anchorSimulationClock } from '../store';
import { clockAt, dayPrefix, daysBetween } from '../utils/simClock';
import { API_BASE, TURB } from '../config';
const chartRef = ref<HTMLElement | null>(null);
const chartInstance = shallowRef<echarts.ECharts | null>(null);
// 折线图各采样点的累计里程（物理公里）。推演游标时刻就按它推，与横轴、与 A* 同一把尺子。
const profileDistKm = ref<number[]>([]);

// TKE 在新口径下是 0–30 量级，与温度/湿度同轴直接可读；EDR 只有 0.x，按倍数放大。
const TKE_SERIES = `TKE (${TURB.tkeUnit})`;
const EDR_SERIES = `EDR (x${TURB.edrChartFactor})`;

// Updated legend based on requirement
const legends = ref([
  { name: '地面海拔高度', color: '#3498db' },
  { name: '无人机飞行海拔', color: '#e2e8f0' },
  { name: TKE_SERIES, color: '#1abc9c' },
  { name: EDR_SERIES, color: '#2ecc71' },
  { name: '温度 (°C)', color: '#f39c12' },
  { name: '相对湿度 (%)', color: '#8e44ad' },
  { name: '水汽混合比 (x10)', color: '#e74c3c' }
]);

const initChart = () => {
  if (!chartRef.value) return;
  
  chartInstance.value = echarts.init(chartRef.value);
  
  const xAxisData: string[] = [];
  
  const option: echarts.EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(0, 0, 0, 0.75)',
      borderColor: 'rgba(255, 255, 255, 0.2)',
      className: 'panel-noise',
      textStyle: { color: '#f8fafc', fontSize: 12 }
    },
    grid: {
      top: 30,
      right: 20,
      bottom: 20,
      left: 40,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: xAxisData,
      axisLabel: { color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: [
      {
        type: 'value',
        name: '高度(m)',
        nameTextStyle: { color: '#94a3b8' },
        axisLabel: { color: '#94a3b8' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
      },
      {
        type: 'value',
        name: '气象指数',
        nameTextStyle: { color: '#94a3b8' },
        axisLabel: { color: '#94a3b8' },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '地面海拔高度',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 0,
        itemStyle: { color: '#3498db' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(52, 152, 219, 0.4)' },
            { offset: 1, color: 'rgba(52, 152, 219, 0)' }
          ])
        },
        data: []
      },
      {
        name: '无人机飞行海拔',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 0,
        // 紫色系已给湿度，这条取亮中性色，否则图例上两条并排看不出谁是谁。
        itemStyle: { color: '#e2e8f0' },
        lineStyle: { type: 'solid' },
        data: []
      },
      {
        name: TKE_SERIES,
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#1abc9c' },
        lineStyle: { width: 2 },
        data: []
      },
      {
        name: EDR_SERIES,
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#2ecc71' },
        data: []
      },
      {
        name: '温度 (°C)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#f39c12' },
        data: []
      },
      {
        name: '相对湿度 (%)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#8e44ad' },
        data: []
      },
      {
        name: '水汽混合比 (x10)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#e74c3c' },
        data: []
      }
    ]
  };
  
  chartInstance.value.setOption(option);
  
  // Enable dragging/scrubbing on the chart canvas
  let isDragging = false;
  chartInstance.value.getZr().on('mousedown', () => { isDragging = true; pauseSimulation(); });
  chartInstance.value.getZr().on('mouseup', () => { isDragging = false; });
  
  const handleChartInteraction = (params: any) => {
    if (store.plannedPath.length > 0 && chartInstance.value) {
      const pointInPixel = [params.offsetX, params.offsetY];
      if (chartInstance.value.containPixel('grid', pointInPixel)) {
        const pointInGrid = chartInstance.value.convertFromPixel({ seriesIndex: 0 }, pointInPixel) as number[];
        const xIndex = Math.max(0, Math.min(store.plannedPath.length - 1, Math.round(pointInGrid[0])));
        store.simulationIndex = xIndex;
        updateChartMarkLine(store.simulationIndex);
        updateTimeForIndex(store.simulationIndex);
      }
    }
  };

  chartInstance.value.getZr().on('mousemove', (params: any) => {
    if (isDragging) handleChartInteraction(params);
  });
  chartInstance.value.getZr().on('click', (params: any) => {
    handleChartInteraction(params);
  });
};

/**
 * setOption 是增量合并，只改数据不会自动抹掉旧曲线，所以清空要逐条喂空数组，
 * 并去掉推演游标 markLine，否则「暂无路线」时底下仍留着上一次的地形剖面。
 */
const clearChart = () => {
  profileDistKm.value = [];
  if (!chartInstance.value) return;
  chartInstance.value.setOption({
    xAxis: { data: [] },
    series: legends.value.map((item, i) => ({
      name: item.name,
      data: [],
      ...(i === 0 ? { markLine: { data: [] } } : {})
    }))
  });
};

const fetchProfileData = async () => {
  if (!store.plannedPath || store.plannedPath.length === 0) {
    clearChart();
    return;
  }
  
  try {
    const response = await fetch(`${API_BASE}/api/weather/profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        waypoints: store.plannedPath.map(wp => ({
          lat: Number(wp.lat),
          lon: Number(wp.lng)
        })),
        date: store.targetDate,
        start_time: store.targetTime,
        // 后端按累计里程/速度算每个点的到达时刻，横轴时间才和这条线一致
        speed: Number(store.uavSpeed)
      })
    });
    
    if (!response.ok) throw new Error('Network response was not ok');
    const result = await response.json();
    
    if (result && result.data && chartInstance.value) {
      updateChartWithData(result.data);
    }
  } catch (error) {
    console.error('Failed to fetch weather profile:', error);
  }
};

/**
 * 剖面里的缺测点：后端 NaN 经 pydantic 序列化成 JSON null，直接 .toFixed 会抛错。
 * 返回 null 让 ECharts 画成断点——一个空格点不该让整张图什么都不剩。
 */
const num = (v: unknown, digits: number, scale = 1): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? Number((v * scale).toFixed(digits)) : null;

const updateChartWithData = (data: any[]) => {
  if (!chartInstance.value) return;
  profileDistKm.value = data.map(d => Number(d.distance_from_start) || 0);
  // 剖面回来时游标可能已经停在某个点上（例如切档后自动推演），让它立刻按新里程重算时刻
  updateTimeForIndex(store.simulationIndex);
  
  const xAxisData = data.map(d => {
    // 与游标同一个函数：1600 km 的线要飞 27 小时，横轴只写 %24 的话
    // 第二天的 09:00 会和第一天的 09:00 长成同一个标签，而热图取的是第二天的帧。
    const clock = clockAt(store.targetDate, store.targetTime,
                          d.distance_from_start, store.uavSpeed);
    return `${d.distance_from_start.toFixed(1)}km\n${dayPrefix(clock.dayOffset)}${clock.time}`;
  });
  
  // 与湿度(0–100)共用的「气象指数」轴：TKE 新口径已是 0–30 量级，直接画；
  // EDR 只有 0.x，按倍数放大才看得见（倍数写在 config 的 TURB.edrChartFactor）。
  const tkeData = data.map(d => num(d.tke, 2));
  const edrData = data.map(d => num(d.edr, 2, TURB.edrChartFactor));
  const tempData = data.map(d => num(typeof d.tc === 'number' && d.tc > 100 ? d.tc - 273.15 : d.tc, 1));
  
  // Use REAL elevation data from backend (SRTM90m API)
  const elevData = data.map(d => num(d.elevation, 1));
  // Drone altitude = Ground Elevation + User-defined Relative Altitude
  const relAltData = data.map((_d, i) => Number(store.plannedPath[i]?.alt || 600));
  const absAltData = elevData.map((elev, i) => (elev === null ? null : Number((elev + relAltData[i]).toFixed(1))));
  
  // Real humidity and qvapor data
  const humData = data.map(d => num(d.rh, 1));
  const qvaporData = data.map(d => num(d.qvapor, 2, 1000 * 10)); // x10
  
  chartInstance.value.setOption({
    xAxis: { data: xAxisData },
    series: [
      { name: '地面海拔高度', data: elevData }, 
      { name: '无人机飞行海拔', data: absAltData }, 
      { name: TKE_SERIES, data: tkeData },
      { name: EDR_SERIES, data: edrData },
      { name: '温度 (°C)', data: tempData },
      { name: '相对湿度 (%)', data: humData }, 
      { name: '水汽混合比 (x10)', data: qvaporData } 
    ]
  });
};

// --- Simulation Logic ---
let simInterval: number | null = null;

const toggleSimulation = () => {
  if (store.isSimulating) {
    pauseSimulation();
  } else {
    startSimulation();
  }
};

const startSimulation = () => {
  if (store.plannedPath.length === 0) return;

  // 跑到头再按播放 = 从头再来一遍（起飞时刻仍取侧栏那个输入值）
  if (store.simulationIndex >= store.plannedPath.length - 1) {
    anchorSimulationClock();
  }

  store.isSimulating = true;
  runSimulationStep();
};

/** 游标所在点的到达时刻 = 起飞时刻 + 已飞里程 / 速度。只写游标，不碰起飞时间。
 *
 * 里程取【后端折线图回传的 distance_from_start】（物理公里，与 A* 判定同一口径），
 * 不再自己按 hypot(dlat,dlon)×111 累加 —— 那是度空间距离，经度差没乘 cos(纬度)，
 * 在 27°N 上把东西向里程高估约 13%，推演时钟因此越飞越晚，而热图正是按这个时钟取帧的：
 * 无人机踩到的是一帧，地图上给它上色的是另一帧。
 */
const updateTimeForIndex = (index: number) => {
  const dist = profileDistKm.value[index];
  if (dist === undefined) return;   // 剖面还没回来：留着上一次的游标，不拿错的里程凑时刻
  const clock = clockAt(store.targetDate, store.targetTime, dist, store.uavSpeed);
  store.simulationTime = clock.time;
  store.simulationDate = clock.date;
};

/** 长途线会飞过午夜（1600 km 要 27 小时），只显示 HH:MM 会看不出"已经是第二天"，
 *  而热图取哪一帧正是由这个日期决定的。 */
const simClockText = computed(() => {
  const day = store.simulationDate;
  const offset = day && day !== store.targetDate ? daysBetween(store.targetDate, day) : 0;
  return `${dayPrefix(offset)}${store.simulationTime}`;
});

const runSimulationStep = () => {
  if (!store.isSimulating) return;
  
  if (simInterval) clearTimeout(simInterval);
  
  simInterval = window.setTimeout(() => {
    if (store.simulationIndex < store.plannedPath.length - 1) {
      store.simulationIndex++;
      updateChartMarkLine(store.simulationIndex);
      updateTimeForIndex(store.simulationIndex);
      
      runSimulationStep();
    } else {
      pauseSimulation();
    }
  }, store.simulationSpeed);
};

const pauseSimulation = () => {
  store.isSimulating = false;
  if (simInterval) clearTimeout(simInterval);
};

const stopSimulation = () => {
  pauseSimulation();
  anchorSimulationClock();
  updateChartMarkLine(-1);
};

/**
 * AI 的推演控制命令落到这里。
 *
 * 推演循环（那个 setTimeout）在本组件里，外面拿不到，所以不把实现搬去 store ——
 * 搬了就得重写这一整套。让 AI 写一个 command 字段、这里 watch 到之后调**上面这些
 * 同一个函数**，按钮路径与 AI 路径就是同一份代码。
 */
watch(() => store.simCommand, (cmd) => {
  if (!cmd) return;
  switch (cmd.action) {
    case 'play': startSimulation(); break;
    case 'pause': pauseSimulation(); break;
    case 'toggle': toggleSimulation(); break;
    case 'stop': stopSimulation(); break;
    case 'reset': stopSimulation(); startSimulation(); break;
    case 'step':
      pauseSimulation();
      if (store.simulationIndex < store.plannedPath.length - 1) {
        store.simulationIndex += 1;
        updateTimeForIndex(store.simulationIndex);
        updateChartMarkLine(store.simulationIndex);
      }
      break;
  }
});

const updateChartMarkLine = (index: number) => {
  if (!chartInstance.value) return;
  if (index < 0) {
    chartInstance.value.setOption({ series: [{ name: '地面海拔高度', markLine: { data: [] } }] });
    return;
  }
  
  chartInstance.value.setOption({
    animationDurationUpdate: 0, // Force 0ms animation duration for immediate update
    series: [
      {
        name: '地面海拔高度', // Attach markLine to first series
        markLine: {
          animation: false, // Disable markLine animation
          symbol: ['none', 'none'],
          label: { show: false },
          lineStyle: { color: '#00ff00', width: 2, type: 'dashed' },
          data: [{ xAxis: index }]
        }
      }
    ]
  });
};

watch(() => store.simulationSpeed, () => {
  if (store.isSimulating) {
    runSimulationStep(); // Restart timeout with new speed
  }
});

watch(() => store.plannedPath, () => {
  fetchProfileData();
}, { deep: true });

const handleResize = () => {
  chartInstance.value?.resize();
};

onMounted(() => {
  initChart();
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
  chartInstance.value?.dispose();
});
</script>

<style scoped>
.data-chart-container {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  width: 75vw; /* Set width to 75% */
  z-index: 900; /* Behind sidebar (1000) */
  display: flex;
  flex-direction: column;
  transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.data-chart-container.collapsed {
  transform: translate(-50%, calc(100% - 10px));
}

.toggle-wrapper {
  display: flex;
  justify-content: center;
  margin-bottom: -1px; 
  z-index: 901;
}

.toggle-handle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 16px;
  border-radius: 12px 12px 0 0;
  border-bottom: none;
  cursor: pointer;
  background: var(--panel-bg);
  color: var(--text-secondary);
  transition: color 0.3s ease;
}

.toggle-handle:hover {
  color: var(--text-primary);
}

.toggle-text {
  font-size: 12px;
  font-weight: 500;
}

.chart-panel {
  height: 33vh; 
  border-radius: 16px;
  overflow: hidden;
  display: flex;
}

.panel-content {
  display: flex;
  width: 100%;
  height: 100%;
}

.legend-area {
  width: 140px; /* Narrower legend */
  padding: 12px 10px; /* Smaller padding */
  border-right: 1px solid var(--panel-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.area-title {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px; /* Tighter gap to avoid scrolling */
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px; /* Smaller text */
}

.color-box {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}

.legend-name {
  color: var(--text-secondary);
  flex: 1;
  line-height: 1.2;
}

.echarts-wrap {
  flex: 1;
  position: relative;
  min-width: 0;
}

.echarts-area {
  width: 100%;
  height: 100%;
  padding: 10px;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  pointer-events: none;
}

.chart-empty-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.chart-empty-hint {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.simulation-controls {
  margin-top: 4px;
  border-top: 1px solid rgba(255,255,255,0.1);
  padding-top: 6px;
}
.sim-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
}
/* 推演游标时刻：只读，不参与任何请求，所以和播放按钮同一行占一小格 */
.sim-clock {
  flex: 0 0 auto;
  align-self: center;
  min-width: 46px;
  text-align: center;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
}
.sim-btn {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: center;
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.2);
  color: white;
  padding: 4px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.sim-btn:hover {
  background: rgba(255,255,255,0.2);
}
.sim-btn.active {
  background: var(--success-color);
  border-color: var(--success-color);
}
.sim-btn.stop:hover {
  background: var(--danger-color);
  border-color: var(--danger-color);
}
.sim-speed label {
  font-size: 11px;
  color: var(--text-secondary);
  display: block;
  margin-bottom: 4px;
}
.sim-speed input {
  width: 100%;
}
</style>
