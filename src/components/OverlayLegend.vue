<template>
  <div class="overlay-legend glass-panel" v-if="activeOverlays.length > 0">
    <h3 class="legend-title">叠加层图例</h3>
    
    <!-- Heatmap legend (Mock) -->
    <div class="legend-section">
      <div class="section-label">热力图区 (强弱)</div>
      <div class="color-scale heatmap-scale"></div>
      <div class="scale-labels">
        <span>低</span>
        <span>中</span>
        <span>高</span>
      </div>
    </div>
    
    <!-- Isoline legend (Mock) -->
    <div class="legend-section">
      <div class="section-label">等值线区 (轮廓)</div>
      <div class="isoline-item">
        <div class="line solid-line"></div>
        <span>高密度/强值</span>
      </div>
      <div class="isoline-item">
        <div class="line dashed-line"></div>
        <span>低密度/弱值</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { store } from '../store';

const activeOverlays = computed(() => store.overlays);
</script>

<style scoped>
.overlay-legend {
  position: absolute;
  bottom: 20px;
  left: 20px;
  z-index: 800; /* Below sidebar (1000) and chart (900) */
  padding: 16px;
  width: 220px;
  display: flex;
  flex-direction: column;
  gap: 16px;
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
  gap: 8px;
}

.section-label {
  font-size: 12px;
  color: var(--text-primary);
}

.heatmap-scale {
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(to right, rgba(0,255,0,0.6), rgba(255,255,0,0.6), rgba(255,0,0,0.6));
  border: 1px solid rgba(255,255,255,0.2);
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

.solid-line {
  border-top: 2px solid #3b82f6;
}

.dashed-line {
  border-top: 2px dashed #8b5cf6;
}
</style>
