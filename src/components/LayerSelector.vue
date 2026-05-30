<template>
  <div class="layer-selector glass-panel">
    <button 
      class="layer-btn" 
      :class="{ active: modelValue === 'street' }"
      @click="switchLayer('street')"
    >
      <MapIcon :size="18" />
      <span>街道图</span>
    </button>
    <button 
      class="layer-btn" 
      :class="{ active: modelValue === 'satellite' }"
      @click="switchLayer('satellite')"
    >
      <Globe :size="18" />
      <span>卫星图</span>
    </button>
    <button 
      class="layer-btn" 
      :class="{ active: modelValue === 'terrain' }"
      @click="switchLayer('terrain')"
    >
      <Mountain :size="18" />
      <span>地形图</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { Map as MapIcon, Globe, Mountain } from 'lucide-vue-next';

const props = defineProps<{
  modelValue: 'street' | 'satellite' | 'terrain'
}>();

const emit = defineEmits(['update:modelValue']);

const switchLayer = (layer: 'street' | 'satellite' | 'terrain') => {
  emit('update:modelValue', layer);
};
</script>

<style scoped>
.layer-selector {
  position: absolute;
  bottom: 20px;
  right: 20px;
  z-index: 1000;
  display: flex;
  flex-direction: column; /* Vertical layout */
  padding: 6px;
  gap: 4px;
}

.layer-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  padding: 10px 14px;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.3s ease;
  font-size: 12px;
}

.layer-btn:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-primary);
}

.layer-btn.active {
  background: var(--accent-color);
  color: white;
}
</style>
