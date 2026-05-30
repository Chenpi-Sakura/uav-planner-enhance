<template>
  <div class="data-chart-container" :class="{ 'collapsed': isCollapsed }">
    <!-- Toggle Button -->
    <div class="toggle-wrapper" @click="toggleChart">
      <div class="toggle-handle glass-panel">
        <ChevronDown v-if="!isCollapsed" :size="20" />
        <ChevronUp v-else :size="20" />
        <span class="toggle-text">{{ isCollapsed ? '展开折线图' : '收起折线图' }}</span>
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
        </div>
        
        <!-- Right: Chart -->
        <div class="echarts-area" ref="chartRef"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, shallowRef } from 'vue';
import { ChevronDown, ChevronUp, Activity } from 'lucide-vue-next';
import * as echarts from 'echarts';

const isCollapsed = ref(false);

const toggleChart = () => {
  isCollapsed.value = !isCollapsed.value;
};
const chartRef = ref<HTMLElement | null>(null);
const chartInstance = shallowRef<echarts.ECharts | null>(null);

// Updated legend based on requirement
const legends = ref([
  { name: '地面海拔高度', color: '#3498db' },
  { name: '无人机相对高度', color: '#9b59b6' },
  { name: 'TKE (x10)', color: '#1abc9c' },
  { name: 'EDR (x100)', color: '#2ecc71' },
  { name: '温度 (°C)', color: '#f39c12' },
  { name: '湿度 (%)', color: '#8e44ad' },
  { name: '降雨量 (x10)', color: '#e74c3c' }
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
        name: '无人机相对高度',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 0,
        itemStyle: { color: '#9b59b6' },
        lineStyle: { type: 'dashed' },
        data: [170, 250, 500, 650, 630, 770, 500, 350]
      },
      {
        name: 'TKE (x10)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#1abc9c' },
        lineStyle: { width: 2 },
        data: [2, 4, 8, 12, 9, 5, 3, 2]
      },
      {
        name: 'EDR (x100)',
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
        name: '湿度 (%)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#8e44ad' },
        data: [40, 45, 55, 60, 58, 65, 50, 45]
      },
      {
        name: '降雨量 (x10)',
        type: 'line',
        smooth: true,
        symbol: 'none',
        yAxisIndex: 1,
        itemStyle: { color: '#e74c3c' },
        data: [0, 0, 0, 50, 20, 0, 0, 0]
      }
    ]
  };
  
  chartInstance.value.setOption(option);
};

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
  padding: 16px 12px; /* Smaller padding */
  border-right: 1px solid var(--panel-border);
  display: flex;
  flex-direction: column;
  overflow-y: auto; /* In case items overflow */
}

/* Scrollbar for legend if it overflows */
.legend-area::-webkit-scrollbar {
  width: 4px;
}

.area-title {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px; /* Tighter gap */
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
</style>
