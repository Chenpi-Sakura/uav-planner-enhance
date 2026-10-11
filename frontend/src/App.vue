<template>
  <div class="app-container">
    <MapContainer :active-layer="store.baseMap" />
    
    <!-- Top Header -->
    <div class="top-header glass-panel">
      <Plane :size="24" class="header-logo" />
      <h1 class="header-title">基于WRF湍流数据的青藏高原无人机安全路径规划系统</h1>
    </div>

    <Sidebar />
    <DataChartPanel />
    <LayerSelector v-model="store.baseMap" />
    <!-- 与图层切换同为 z 1000，靠文档顺序压在它上面：右栏展开时整块盖住底图按钮 -->
    <AiSidebar />
    <OverlayLegend />
    <NoticeDialog />
  </div>
</template>

<script setup lang="ts">
import { Plane } from 'lucide-vue-next';
import MapContainer from './components/MapContainer.vue';
import Sidebar from './components/Sidebar.vue';
import AiSidebar from './components/AiSidebar.vue';
import DataChartPanel from './components/DataChartPanel.vue';
import LayerSelector from './components/LayerSelector.vue';
import OverlayLegend from './components/OverlayLegend.vue';
import NoticeDialog from './components/NoticeDialog.vue';
import { store } from './store';
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
