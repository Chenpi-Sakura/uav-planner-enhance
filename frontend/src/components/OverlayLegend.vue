<template>
  <div class="overlay-legend glass-panel" v-if="activeOverlays.length > 0">
    <h3 class="legend-title">叠加层图例</h3>
    
    <!-- Heatmap legends dynamically based on active overlays -->
    <div class="legend-section" v-for="layerId in activeOverlays" :key="layerId">
      <template v-if="legendConfigs[layerId]">
        <div class="section-label">{{ legendConfigs[layerId].label }}</div>
        <div class="color-scale" :style="{ background: legendConfigs[layerId].gradient }"></div>
        <div class="scale-labels">
          <span>{{ legendConfigs[layerId].min }}</span>
          <span>{{ legendConfigs[layerId].mid }}</span>
          <span>{{ legendConfigs[layerId].max }}</span>
        </div>
      </template>
    </div>
    
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { store } from '../store';

const activeOverlays = computed(() => store.overlays);

const legendConfigs: Record<string, any> = {
  'tke': {
    label: 'TKE 湍动能 (强度)',
    gradient: 'linear-gradient(to right, #ffffb2, #fd8d3c, #bd0026)', // YlOrRd
    min: '0.0',
    mid: '0.7',
    max: '1.5+',
  },
  'edr': {
    label: 'EDR 涡动耗散率',
    gradient: 'linear-gradient(to right, #000004, #721f81, #f1605d, #fcfdbf)', // Magma
    min: '0.0',
    mid: '0.007',
    max: '0.015+',
  },
  'temp': {
    label: '温度 (°C) @ 500hPa',
    gradient: 'linear-gradient(to right, #313695, #e0f3f8, #a50026)', // coolwarm
    min: '-15',
    mid: '-2',
    max: '10+',
  },
  'flow': {
    label: '风速 (m/s)',
    gradient: 'linear-gradient(to right, #00007F, #007FFF, #7FFF7F, #FF7F00, #7F0000)', // jet
    min: '0',
    mid: '15',
    max: '30+',
  },
  'humidity': {
    label: '相对湿度 (%)',
    gradient: 'linear-gradient(to right, #f7fcf0, #7bccc4, #084081)', // GnBu approx
    min: '0%',
    mid: '50%',
    max: '100%',
  },
  'qvapor': {
    label: '水汽混合比 (g/kg)',
    gradient: 'linear-gradient(to right, #f1eef6, #74a9cf, #045a8d)', // PuBu
    min: '0',
    mid: '5',
    max: '10+',
  }
};
</script>

<style scoped>
.overlay-legend {
  position: absolute;
  bottom: 20px;
  left: 20px;
  z-index: 800; /* Below sidebar (1000) and chart (900) */
  padding: 14px;
  width: 200px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.legend-title {
  font-size: 13px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--panel-border);
  padding-bottom: 8px;
  margin: 0;
}

.legend-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.section-label {
  font-size: 12px;
  color: var(--text-primary);
  font-weight: 500;
}

.color-scale {
  height: 8px;
  border-radius: 4px;
}

.scale-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
}

.isoline-item {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--text-secondary);
}

.line {
  flex: 1;
  height: 0;
}

.contour-line {
  border-top: 1px solid rgba(255, 255, 255, 0.4);
}
</style>
