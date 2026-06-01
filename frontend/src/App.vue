<template>
  <div class="app-container">
    <MapContainer :active-layer="activeLayer" />
    
    <!-- Top Header -->
    <div class="top-header glass-panel">
      <Plane :size="24" class="header-logo" />
      <h1 class="header-title">基于WRF湍流数据的青藏高原无人机安全路径规划系统</h1>
    </div>

    <Sidebar />
    <DataChartPanel />
    <LayerSelector v-model="activeLayer" />
    <OverlayLegend />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { Plane } from 'lucide-vue-next';
import MapContainer from './components/MapContainer.vue';
import Sidebar from './components/Sidebar.vue';
import DataChartPanel from './components/DataChartPanel.vue';
import LayerSelector from './components/LayerSelector.vue';
import OverlayLegend from './components/OverlayLegend.vue';

// Default map layer set to satellite
const activeLayer = ref<'street' | 'satellite' | 'terrain'>('satellite');
</script>

<style>
/* Global styles are imported in main.ts from style.css */
.app-container {
  width: 100vw;
  height: 100vh;
  position: relative;
  overflow: hidden;
}

.top-header {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 24px;
  border-radius: 9999px; /* Pill shape */
}

.header-logo { 
  color: var(--accent-color); 
}

.header-title { 
  font-size: 18px; 
  font-weight: 700; 
  margin: 0; 
  letter-spacing: 1px; 
}
</style>
