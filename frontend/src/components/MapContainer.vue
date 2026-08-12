<template>
  <div class="map-container" ref="mapElement" :class="{ 'cursor-crosshair': store.selectingMode }">
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { store } from '../store';
import { Play } from 'lucide-vue-next';
import { API_BASE } from '../config';

const props = defineProps<{
  activeLayer: 'street' | 'satellite' | 'terrain'
}>();

const mapElement = ref<HTMLElement | null>(null);
let map: L.Map | null = null;
let markersLayer = L.layerGroup();
let mockOverlaysLayer = L.layerGroup();
let pathLayer = L.layerGroup(); // layer for the planned route line
let basesLayer = L.layerGroup(); // layer for city bases

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
    zoom: 7,
    zoomControl: false 
  });

  L.control.zoom({ position: 'topright' }).addTo(map);

  currentLayer = layers[props.activeLayer];
  currentLayer.addTo(map);
  basesLayer.addTo(map); // Add bases layer
  markersLayer.addTo(map);
  mockOverlaysLayer.addTo(map);
  pathLayer.addTo(map);

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

  // Render fixed city bases
  renderBases();

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

const renderBases = () => {
  if (!map) return;
  basesLayer.clearLayers();
  
  const createBaseIcon = (name: string) => L.divIcon({
    className: 'base-marker',
    html: `<div class="base-icon">🏰</div><div class="base-label">${name}</div>`,
    iconSize: [40, 40],
    iconAnchor: [20, 20]
  });

  store.bases.forEach(base => {
    const m = L.marker([Number(base.lat), Number(base.lng)], { 
      icon: createBaseIcon(base.name),
      interactive: true,
      zIndexOffset: -100 // keep behind active draggable markers
    }).addTo(basesLayer);

    m.on('click', () => {
      // If user is in selection mode, pick this base
      if (store.selectingMode) {
        if (store.selectingMode === 'start') {
          store.start.lat = base.lat;
          store.start.lng = base.lng;
          store.start.alt = base.alt;
        } else if (store.selectingMode === 'end') {
          store.end.lat = base.lat;
          store.end.lng = base.lng;
          store.end.alt = base.alt;
        } else if (store.selectingMode === 'waypoint') {
          if (store.selectingIndex >= 0 && store.selectingIndex < store.waypoints.length) {
            store.waypoints[store.selectingIndex].lat = base.lat;
            store.waypoints[store.selectingIndex].lng = base.lng;
            store.waypoints[store.selectingIndex].alt = base.alt;
          }
        }
        store.selectingMode = null;
      } else {
        // Just pan to it
        map!.flyTo([Number(base.lat), Number(base.lng)], 9);
      }
    });
  });
};

const renderPlannedPath = () => {
  if (!map) return;
  pathLayer.clearLayers();
  
  if (store.plannedPath && store.plannedPath.length > 0) {
    const latlngs: L.LatLngExpression[] = store.plannedPath.map(p => [Number(p.lat), Number(p.lng)]);
    
    // Draw the main route line
    L.polyline(latlngs, {
      color: '#FF3300', // Bright red-orange
      weight: 6,
      opacity: 1.0,
      dashArray: '10, 10'
    }).addTo(pathLayer);
    
    // Optionally zoom map to fit the path
    const bounds = L.latLngBounds(latlngs);
    map.fitBounds(bounds, { padding: [50, 50] });
  }
};

let lastRenderState = '';

const renderRealOverlays = () => {
  if (!map) return;
  
  if (store.overlays.length === 0) {
    mockOverlaysLayer.clearLayers();
    lastRenderState = '';
    return;
  }

  const bounds = map.getBounds();
  const min_lat = bounds.getSouth();
  const max_lat = bounds.getNorth();
  const min_lon = bounds.getWest();
  const max_lon = bounds.getEast();
  
  const currentHour = parseInt(store.targetTime.split(':')[0] || '8');
  // State guard to prevent flickering: only re-fetch if layers, date, HOUR, or map bounds change
  const currentStateStr = `${store.overlays.join(',')}_${store.targetDate}_${currentHour}_${min_lat.toFixed(4)}_${max_lat.toFixed(4)}_${min_lon.toFixed(4)}_${max_lon.toFixed(4)}`;
  
  if (currentStateStr === lastRenderState) {
    return; // Skip redundant updates! (e.g. simulation minute ticking)
  }
  lastRenderState = currentStateStr;

  // Create bounds for the ImageOverlay
  const imageBounds: L.LatLngBoundsExpression = [[min_lat, min_lon], [max_lat, max_lon]];

  // Keep a reference to current layers to remove later to prevent flickering
  const oldLayers = mockOverlaysLayer.getLayers();
  let loadedCount = 0;

  store.overlays.forEach(layer => {
    const hourStr = currentHour.toString().padStart(2, '0') + ':00';
    // Removed t=Date.now() to allow browser caching and explicitly use hourStr
    const url = `${API_BASE}/api/weather/overlay?min_lat=${min_lat}&max_lat=${max_lat}&min_lon=${min_lon}&max_lon=${max_lon}&layer=${layer}&date=${store.targetDate}&start_time=${hourStr}`;
    
    const overlay = L.imageOverlay(url, imageBounds, {
      opacity: 0.8,
      interactive: false,
      crossOrigin: true
    });
    
    overlay.on('load', () => {
      loadedCount++;
      // Once all new layers are loaded, remove old layers
      if (loadedCount === store.overlays.length) {
        oldLayers.forEach(l => mockOverlaysLayer.removeLayer(l));
      }
    });
    
    overlay.addTo(mockOverlaysLayer);
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

watch([() => store.overlays, () => store.targetDate, () => store.targetTime], () => {
  renderRealOverlays();
}, { deep: true });

let uavMarker: L.CircleMarker | null = null;
let lastHour: number = 0;

watch(() => store.simulationIndex, (newIdx) => {
  const path = store.plannedPath;
  if (!path || path.length === 0 || !map) return;
  
  // Initialize marker if not exists
  if (newIdx === 0 || !uavMarker) {
    if (uavMarker) map.removeLayer(uavMarker);
    uavMarker = L.circleMarker([Number(path[0].lat), Number(path[0].lng)], {
      color: '#00ff00',
      fillColor: '#00ff00',
      fillOpacity: 1,
      radius: 6,
      weight: 2
    }).addTo(map);
    lastHour = parseInt(store.targetTime.split(':')[0] || '8');
    return;
  }
  
  if (newIdx < path.length) {
    const p1 = path[newIdx - 1];
    const p2 = path[newIdx];
    uavMarker.setLatLng([Number(p2.lat), Number(p2.lng)]);
    
    // Check if we need to update weather (hour crossed)
    // For simplicity, we calculate total distance up to this point
    let totalDist = 0;
    for (let i = 0; i < newIdx; i++) {
      totalDist += Math.hypot(Number(path[i+1].lng) - Number(path[i].lng), Number(path[i+1].lat) - Number(path[i].lat)) * 111.0;
    }
    
    const startHour = parseInt(store.targetTime.split(':')[0] || '8'); // This is a bit flaky if targetTime updates, but okay for demo
    const elapsedHours = totalDist / store.uavSpeed;
    // Assuming startHour is preserved from the beginning of simulation.
    // Actually, store.targetTime updates. So let's rely on DataChartPanel to manage time updates, or just do it simply:
    // We just move the marker here. DataChartPanel can handle time progression!
  }
});

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

:deep(.base-marker) {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer !important;
}
:deep(.base-marker .base-icon) {
  font-size: 18px;
  background: rgba(255,255,255,0.8);
  border-radius: 50%;
  padding: 4px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.3);
  transition: transform 0.2s;
}
:deep(.base-marker:hover .base-icon) {
  transform: scale(1.2);
  background: white;
}
:deep(.base-marker .base-label) {
  margin-top: 4px;
  font-size: 14px;
  font-weight: 800;
  color: white;
  text-shadow: 0 2px 4px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.8);
  white-space: nowrap;
}
</style>
