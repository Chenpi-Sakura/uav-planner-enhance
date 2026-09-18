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
            </div>
            <div class="sim-speed">
              <label>速度: {{ (store.simulationSpeed / 1000).toFixed(1) }}s/步</label>
              <input type="range" min="100" max="2000" step="100" v-model="store.simulationSpeed" />
            </div>
          </div>
        </div>
        
        <!-- Right: Chart -->
        <div class="echarts-area" ref="chartRef"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, shallowRef, watch } from 'vue';
import { ChevronDown, ChevronUp, Activity, Play, Pause, Square } from 'lucide-vue-next';
import * as echarts from 'echarts';
import { store } from '../store';
import { API_BASE } from '../config';
const chartRef = ref<HTMLElement | null>(null);
const chartInstance = shallowRef<echarts.ECharts | null>(null);

// Updated legend based on requirement
const legends = ref([
  { name: '地面海拔高度', color: '#3498db' },
  { name: '无人机飞行海拔', color: '#9b59b6' },
  { name: 'TKE (x50)', color: '#1abc9c' },
  { name: 'EDR (x5000)', color: '#2ecc71' },
  { name: '温度 (°C)', color: '#f39c12' },
  { name: '相对湿度 (%)', color: '#8e44ad' },
  { name: '水汽混合比 (x10)', color: '#e74c3c' }
]);

const initChart = () => {
  if (!chartRef.value) return;
  
  chartInstance.value = echarts.init(chartRef.value);
  
  const xAxisData = ['0km', '5km', '10km', '15km', '20km', '25km', '30km', '35km'];
  
  const option: echarts.EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.9)',
      borderColor: 'rgba(255, 255, 255, 0.2)',
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
        data: [120, 200, 450, 600, 580, 720, 450, 300]
      },
      {
        name: '无人机飞行海拔',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 0,
        itemStyle: { color: '#9b59b6' },
        lineStyle: { type: 'dashed' },
        data: [170, 250, 500, 650, 630, 770, 500, 350]
      },
      {
        name: 'TKE (x50)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#1abc9c' },
        lineStyle: { width: 2 },
        data: [2, 4, 8, 12, 9, 5, 3, 2]
      },
      {
        name: 'EDR (x5000)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#2ecc71' },
        data: [10, 30, 60, 80, 70, 40, 20, 10]
      },
      {
        name: '温度 (°C)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#f39c12' },
        data: [22, 21, 18.5, 17, 17.5, 16, 18, 19]
      },
      {
        name: '相对湿度 (%)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#8e44ad' },
        data: [40, 45, 55, 60, 58, 65, 50, 45]
      },
      {
        name: '水汽混合比 (x10)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#e74c3c' },
        data: [5, 6, 5.5, 4, 3, 5, 8, 9]
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

const fetchProfileData = async () => {
  if (!store.plannedPath || store.plannedPath.length === 0) return;
  
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
        start_time: store.targetTime
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

const updateChartWithData = (data: any[]) => {
  if (!chartInstance.value) return;
  
  const originalHour = parseInt(store.targetTime.split(':')[0] || '8');
  const xAxisData = data.map(d => {
    const elapsedHours = d.distance_from_start / store.uavSpeed;
    const currentHour = Math.floor(originalHour + elapsedHours);
    const m = Math.floor((elapsedHours % 1) * 60);
    const hStr = (currentHour % 24).toString().padStart(2, '0');
    const mStr = m.toString().padStart(2, '0');
    return `${d.distance_from_start.toFixed(1)}km\n${hStr}:${mStr}`;
  });
  
  // Dramatically increase scaling factors so these values are visible on a 0-100 scale (driven by Humidity)
  const tkeData = data.map(d => Number((d.tke * 50).toFixed(2))); // x50
  const edrData = data.map(d => Number((d.edr * 5000).toFixed(2))); // x5000
  const tempData = data.map(d => Number((d.tc > 100 ? d.tc - 273.15 : d.tc).toFixed(1)));
  
  // Use REAL elevation data from backend (SRTM90m API)
  const elevData = data.map(d => Number((d.elevation).toFixed(1))); 
  // Drone altitude = Ground Elevation + User-defined Relative Altitude
  const relAltData = data.map((_d, i) => Number(store.plannedPath[i]?.alt || 600));
  const absAltData = elevData.map((elev, i) => elev + relAltData[i]);
  
  // Real humidity and qvapor data
  const humData = data.map(d => Number((d.rh).toFixed(1)));
  const qvaporData = data.map(d => Number((d.qvapor * 1000 * 10).toFixed(2))); // x10
  
  chartInstance.value.setOption({
    xAxis: { data: xAxisData },
    series: [
      { name: '地面海拔高度', data: elevData }, 
      { name: '无人机飞行海拔', data: absAltData }, 
      { name: 'TKE (x50)', data: tkeData },
      { name: 'EDR (x5000)', data: edrData },
      { name: '温度 (°C)', data: tempData },
      { name: '相对湿度 (%)', data: humData }, 
      { name: '水汽混合比 (x10)', data: qvaporData } 
    ]
  });
};

// --- Simulation Logic ---
let simInterval: number | null = null;
let originalStartTimeMinutes = 8 * 60; // Track original start time in minutes

const toggleSimulation = () => {
  if (store.isSimulating) {
    pauseSimulation();
  } else {
    startSimulation();
  }
};

const startSimulation = () => {
  if (store.plannedPath.length === 0) return;
  
  // If we are at the end, reset to start automatically
  if (store.simulationIndex >= store.plannedPath.length - 1) {
    store.simulationIndex = 0;
    const hStr = Math.floor(originalStartTimeMinutes / 60).toString().padStart(2, '0');
    const mStr = (originalStartTimeMinutes % 60).toString().padStart(2, '0');
    store.targetTime = `${hStr}:${mStr}`;
  } else if (store.simulationIndex === 0) {
    const parts = store.targetTime.split(':');
    const h = parseInt(parts[0] || '8');
    const m = parseInt(parts[1] || '0');
    originalStartTimeMinutes = h * 60 + m;
  }
  
  store.isSimulating = true;
  runSimulationStep();
};

const updateTimeForIndex = (index: number) => {
  let totalDist = 0;
  for (let i = 0; i < index; i++) {
    const dx = Number(store.plannedPath[i+1].lng) - Number(store.plannedPath[i].lng);
    const dy = Number(store.plannedPath[i+1].lat) - Number(store.plannedPath[i].lat);
    totalDist += Math.hypot(dx, dy) * 111.0;
  }
  const elapsedHours = totalDist / store.uavSpeed;
  const totalMinutes = originalStartTimeMinutes + Math.round(elapsedHours * 60);
  const currentHour = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  const hStr = (currentHour % 24).toString().padStart(2, '0');
  const mStr = m.toString().padStart(2, '0');
  store.targetTime = `${hStr}:${mStr}`;
};

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
  store.simulationIndex = 0;
  updateChartMarkLine(-1);
  if (store.plannedPath.length > 0) {
    const hStr = Math.floor(originalStartTimeMinutes / 60).toString().padStart(2, '0');
    const mStr = (originalStartTimeMinutes % 60).toString().padStart(2, '0');
    store.targetTime = `${hStr}:${mStr}`;
  }
};

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

.echarts-area {
  flex: 1;
  height: 100%;
  padding: 10px;
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
