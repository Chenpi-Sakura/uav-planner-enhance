<template>
  <div class="map-container" ref="mapElement" :class="{ 'cursor-crosshair': store.selectingMode }"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { store } from '../store';

const props = defineProps<{
  activeLayer: 'street' | 'satellite' | 'terrain'
}>();

const mapElement = ref<HTMLElement | null>(null);
let map: L.Map | null = null;
let markersLayer = L.layerGroup();
let mockOverlaysLayer = L.layerGroup();
let pathLayer = L.layerGroup(); // <--- layer for the planned route line

// Map Layers
const layers: Record<string, L.TileLayer> = {
  street: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap'
  }),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri'
  }),
  terrain: L.tileLayer('https://tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom: 17,
    attribution: 'Map style: &copy; OpenTopoMap (CC-BY-SA)'
  })
};

let currentLayer: L.TileLayer | null = null;

onMounted(() => {
  if (!mapElement.value) return;

  map = L.map(mapElement.value, {
    center: [29.65, 91.1], // Lhasa
    zoom: 12,
    zoomControl: false 
  });

  L.control.zoom({ position: 'topright' }).addTo(map);

  currentLayer = layers[props.activeLayer];
  currentLayer.addTo(map);
  markersLayer.addTo(map);
  mockOverlaysLayer.addTo(map);
  pathLayer.addTo(map); // <--- Add path layer

  // Handle clicks for point selection
  map.on('click', (e: L.LeafletMouseEvent) => {
    if (!store.selectingMode) return;
    
    const lat = e.latlng.lat.toFixed(6);
    const lng = e.latlng.lng.toFixed(6);

    if (store.selectingMode === 'start') {
      store.start.lat = lat;
      store.start.lng = lng;
      store.start.alt = 3650; // default Lhasa alt approx
    } else if (store.selectingMode === 'end') {
      store.end.lat = lat;
      store.end.lng = lng;
      store.end.alt = 3650;
    } else if (store.selectingMode === 'waypoint') {
      if (store.selectingIndex >= 0 && store.selectingIndex < store.waypoints.length) {
        store.waypoints[store.selectingIndex].lat = lat;
        store.waypoints[store.selectingIndex].lng = lng;
        store.waypoints[store.selectingIndex].alt = 3650;
      }
    }
    
    // Auto-disable selection mode after picking
    store.selectingMode = null;
  });

  // Re-fetch overlays when map is moved or zoomed
  map.on('moveend', () => {
    if (store.overlays.length > 0) {
      renderRealOverlays();
    }
  });
});

const renderMarkers = () => {
  if (!map) return;
  markersLayer.clearLayers();

  const createIcon = (colorClass: string, text: string) => L.divIcon({
    className: `custom-marker ${colorClass}`,
    html: `<div class="marker-dot"><span>${text}</span></div>`,
    iconSize: [24, 24], // Larger clickable area
    iconAnchor: [12, 12]
  });

  // Start Marker
  if (store.start.lat !== '' && store.start.lng !== '') {
    const m = L.marker([Number(store.start.lat), Number(store.start.lng)], { 
      icon: createIcon('start-marker', 'S'),
      draggable: true
    }).addTo(markersLayer);
    
    m.on('dragend', (e) => {
      const pos = e.target.getLatLng();
      store.start.lat = pos.lat.toFixed(6);
      store.start.lng = pos.lng.toFixed(6);
    });
  }
  
  // Waypoint Markers
  store.waypoints.forEach((wp, idx) => {
    if (wp.lat !== '' && wp.lng !== '') {
      const m = L.marker([Number(wp.lat), Number(wp.lng)], { 
        icon: createIcon('waypoint-marker', String(idx + 1)),
        draggable: true
      }).addTo(markersLayer);

      m.on('dragend', (e) => {
        const pos = e.target.getLatLng();
        store.waypoints[idx].lat = pos.lat.toFixed(6);
        store.waypoints[idx].lng = pos.lng.toFixed(6);
      });
    }
  });

  // End Marker
  if (store.end.lat !== '' && store.end.lng !== '') {
    const m = L.marker([Number(store.end.lat), Number(store.end.lng)], { 
      icon: createIcon('end-marker', 'E'),
      draggable: true
    }).addTo(markersLayer);

    m.on('dragend', (e) => {
      const pos = e.target.getLatLng();
      store.end.lat = pos.lat.toFixed(6);
      store.end.lng = pos.lng.toFixed(6);
    });
  }
};

const renderPlannedPath = () => {
  if (!map) return;
  pathLayer.clearLayers();
  
  if (store.plannedPath && store.plannedPath.length > 0) {
    const latlngs: L.LatLngExpression[] = store.plannedPath.map(p => [Number(p.lat), Number(p.lng)]);
    
    // Draw the main route line
    L.polyline(latlngs, {
      color: '#3498db',
      weight: 4,
      opacity: 0.8,
      dashArray: '10, 10'
    }).addTo(pathLayer);
    
    // Optionally zoom map to fit the path
    const bounds = L.latLngBounds(latlngs);
    map.fitBounds(bounds, { padding: [50, 50] });
  }
};

const renderRealOverlays = () => {
  if (!map) return;
  mockOverlaysLayer.clearLayers();
  
  if (store.overlays.length === 0) return;

  const bounds = map.getBounds();
  const min_lat = bounds.getSouth();
  const max_lat = bounds.getNorth();
  const min_lon = bounds.getWest();
  const max_lon = bounds.getEast();
  
  // Create bounds for the ImageOverlay
  const imageBounds: L.LatLngBoundsExpression = [[min_lat, min_lon], [max_lat, max_lon]];

  store.overlays.forEach(layer => {
    // Add timestamp to prevent caching when panning
    const url = `http://localhost:8000/api/weather/overlay?min_lat=${min_lat}&max_lat=${max_lat}&min_lon=${min_lon}&max_lon=${max_lon}&layer=${layer}&t=${Date.now()}`;
    
    L.imageOverlay(url, imageBounds, {
      opacity: 0.8,
      interactive: false,
      crossOrigin: true
    }).addTo(mockOverlaysLayer);
  });
};

watch(() => props.activeLayer, (newLayer) => {
  if (map && currentLayer) {
    map.removeLayer(currentLayer);
    currentLayer = layers[newLayer];
    currentLayer.addTo(map);
  }
});

watch(() => [store.start, store.end, store.waypoints], () => {
  renderMarkers();
}, { deep: true });

watch(() => store.plannedPath, () => {
  renderPlannedPath();
}, { deep: true });

watch(() => store.overlays, () => {
  renderRealOverlays();
}, { deep: true });

onUnmounted(() => {
  if (map) {
    map.remove();
  }
});
</script>

<style scoped>
.map-container {
  width: 100vw;
  height: 100vh;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 0; 
}
.map-container.cursor-crosshair {
  cursor: crosshair !important;
}

:deep(.leaflet-control-zoom a) {
  background-color: var(--panel-bg);
  color: var(--text-primary);
  border-color: var(--panel-border);
  backdrop-filter: blur(16px);
}
:deep(.leaflet-control-zoom a:hover) {
  background-color: var(--accent-hover);
  color: white;
}
:deep(.leaflet-container) {
  background-color: #0b0f19;
}

/* Marker Styling */
:deep(.custom-marker) {
  display: flex;
  align-items: center;
  justify-content: center;
}
:deep(.custom-marker .marker-dot) {
  width: 20px; /* Slightly larger */
  height: 20px;
  border-radius: 50%;
  border: 2px solid white;
  box-shadow: 0 0 10px rgba(0,0,0,0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: white;
  font-family: sans-serif;
}
:deep(.start-marker .marker-dot) { background-color: var(--success-color); }
:deep(.end-marker .marker-dot) { background-color: var(--danger-color); }
:deep(.waypoint-marker .marker-dot) { background-color: var(--accent-color); }
</style>
