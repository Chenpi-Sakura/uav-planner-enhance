<template>
  <div class="map-container" ref="mapElement" :class="{ 'cursor-crosshair': store.selectingMode }">
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { store, RISK_COLORS, TIER_COLORS, PRIORITY_LABELS } from '../store';
import { API_BASE } from '../config';

const props = defineProps<{
  activeLayer: 'street' | 'satellite' | 'terrain'
}>();

const mapElement = ref<HTMLElement | null>(null);
let map: L.Map | null = null;
let markersLayer = L.layerGroup();
let mockOverlaysLayer = L.layerGroup();
let pathLayer = L.layerGroup(); // layer for the planned route line
let blockLayer = L.layerGroup(); // layer for blocking turbulence regions
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
let overlayDebounce: number = 0;

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
  blockLayer.addTo(map);

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
    if (store.overlays.length === 0) return;
    // 拖拽/缩放会连发 moveend，等视野稳定再取图，避免中途作废的请求反复叠图
    window.clearTimeout(overlayDebounce);
    overlayDebounce = window.setTimeout(renderRealOverlays, 250);
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

const renderBlockingRegions = () => {
  if (!map) return;
  blockLayer.clearLayers();

  const regions = store.highlightRegions || [];
  if (regions.length === 0) return;

  const bounds: L.LatLngExpression[] = [];

  regions.forEach(reg => {
    const center = reg.center as [number, number];

    // 优先使用后端给出的轮廓多边形；否则退化为圆
    if (reg.polygon && reg.polygon.length >= 3) {
      const latlngs = reg.polygon.map(pt => [pt[0], pt[1]] as [number, number]);
      L.polygon(latlngs, {
        color: '#ef4444',
        weight: 2,
        opacity: 0.9,
        fillColor: '#ef4444',
        fillOpacity: 0.28,
        dashArray: '6, 6',
      }).addTo(blockLayer);
      latlngs.forEach(ll => bounds.push(ll));
    } else {
      L.circle(center, {
        radius: reg.radius_km * 1000,
        color: '#ef4444',
        weight: 2,
        opacity: 0.9,
        fillColor: '#ef4444',
        fillOpacity: 0.28,
        dashArray: '6, 6',
      }).addTo(blockLayer);
      bounds.push(center);
      // 圆形占位也纳入视野
      const r = reg.radius_km;
      bounds.push([center[0] + r / 111, center[1]]);
      bounds.push([center[0] - r / 111, center[1]]);
    }

    L.marker(center, {
      icon: L.divIcon({
        className: 'blocking-marker',
        html: `<div class="blocking-tag">${reg.shape} · ${Math.round(reg.area_km2)}km²</div>`,
        iconSize: [120, 20],
        iconAnchor: [60, 10],
      }),
      interactive: false,
    }).addTo(blockLayer);
  });

  if (bounds.length > 1) {
    map.fitBounds(L.latLngBounds(bounds), { padding: [60, 60] });
  }
};

// 未选中档位：各档自己的颜色 + 各自的虚线疏密，压到 30% 只让位不消失
const TIER_FALLBACK_COLOR = '#94a3b8';
const TIER_DASH: Record<string, string> = {
  urgent: '4, 7',
  normal: '9, 7',
  economy: '16, 8',
};

/**
 * 绘制航线：当前选中（或卡片悬停预览）的那一档按风险分段着色画 100%，
 * 另外两档各按自己的档位色画 30% 虚线。
 *
 * @param fit 是否重新套合视野。悬停预览时传 false，否则鼠标每划过一个卡片地图就跳一次。
 */
const renderPlannedPath = (fit = true) => {
  if (!map) return;
  pathLayer.clearLayers();

  const results = store.routeOptions?.results ?? [];
  const shown = store.hoveredTier ?? store.priority;

  for (const r of results) {
    if (r.priority === shown || r.path.length < 2) continue;
    L.polyline(r.path.map(p => [p.lat, p.lon] as [number, number]), {
      color: TIER_COLORS[r.priority] ?? TIER_FALLBACK_COLOR,
      weight: 5,
      opacity: 0.3,
      dashArray: TIER_DASH[r.priority] ?? '8, 8',
    }).addTo(pathLayer).bindTooltip(
      `${PRIORITY_LABELS[r.priority] ?? r.priority} · ${r.distance.route_km ?? '—'} km`,
      { sticky: true }
    );
  }

  const active = results.find(r => r.priority === shown);
  const path = active?.path ?? [];
  if (path.length === 0) {
    // 没有航线时把推演用的无人机标记也清掉，否则上次跑到一半的点会留在原地
    if (uavMarker) {
      map.removeLayer(uavMarker);
      uavMarker = null;
    }
    return;
  }

  const latlngs: L.LatLngExpression[] = path.map(p => [p.lat, p.lon]);
  const segments = active?.risk?.segments ?? [];
  const selected = shown === store.priority ? store.selectedSegmentIndex : null;
  const hovered = shown === store.priority ? store.hoveredSegmentIndex : null;

  // 按风险等级分段着色：安全绿 / 中风险黄 / 高风险橙 / 不可飞红
  if (segments.length > 0) {
    segments.forEach((seg, idx) => {
      const from = Math.max(0, seg.start_index);
      const to = Math.min(path.length - 1, seg.end_index);
      if (to <= from) return;
      const piece = latlngs.slice(from, to + 1);
      const isFocused = selected === idx || hovered === idx;
      L.polyline(piece, {
        color: RISK_COLORS[seg.level] ?? '#94a3b8',
        weight: isFocused ? 9 : 6,
        opacity: selected !== null && !isFocused ? 0.6 : 1.0,
      }).addTo(pathLayer);
    });
  } else {
    L.polyline(latlngs, {
      color: '#FF3300', // Bright red-orange
      weight: 6,
      opacity: 1.0
    }).addTo(pathLayer);
  }

  if (!fit) return;

  // 聚焦到选中分段，便于查看该段风险
  const focusIdx = selected ?? hovered;
  if (focusIdx !== null && segments[focusIdx]) {
    const seg = segments[focusIdx];
    const from = Math.max(0, seg.start_index);
    const to = Math.min(path.length - 1, seg.end_index);
    if (to > from) {
      map.fitBounds(L.latLngBounds(latlngs.slice(from, to + 1)), { padding: [80, 80] });
      return;
    }
  }

  map.fitBounds(L.latLngBounds(latlngs), { padding: [50, 50] });
};

let lastRenderState = '';

// 覆盖层图片由 (layer, date, hour, bbox) 唯一决定，bbox 必须量化成稳定值，
// 否则每次平移都是新 URL，各级缓存（浏览器 / OpenResty / CF）全部落空。
const quantizeBounds = (bounds: L.LatLngBounds) => {
  const rawStep = (bounds.getNorth() - bounds.getSouth()) / 8;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(rawStep, 1e-4))));
  const n = rawStep / mag;
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  const dec = Math.min(4, Math.max(0, Math.ceil(-Math.log10(step))));
  // 向外吸附：量化后的框始终罩住视口，图片不会露边
  const snap = (v: number, up: boolean) => {
    const q = (up ? Math.ceil(v / step) : Math.floor(v / step)) * step;
    return Number(Math.max(-90, Math.min(90, q)).toFixed(dec));
  };
  const snapLon = (v: number, up: boolean) => {
    const q = (up ? Math.ceil(v / step) : Math.floor(v / step)) * step;
    return Number(Math.max(-180, Math.min(180, q)).toFixed(dec));
  };
  return {
    minLat: snap(bounds.getSouth(), false),
    maxLat: snap(bounds.getNorth(), true),
    minLon: snapLon(bounds.getWest(), false),
    maxLon: snapLon(bounds.getEast(), true),
    dec,
  };
};

// 尚未全部就位的一批覆盖层：新一轮请求发起时直接丢弃，避免多批半透明图叠色
let inflightOverlays: L.ImageOverlay[] = [];
let overlaySeq = 0;

const renderRealOverlays = () => {
  if (!map) return;

  if (store.overlays.length === 0) {
    window.clearTimeout(overlayDebounce);
    mockOverlaysLayer.clearLayers();
    inflightOverlays = [];
    overlaySeq++;
    lastRenderState = '';
    return;
  }

  const { minLat, maxLat, minLon, maxLon, dec } = quantizeBounds(map.getBounds());

  const currentHour = parseInt(store.targetTime.split(':')[0] || '8');
  // State guard to prevent flickering: only re-fetch if layers, date, HOUR, or quantized bounds change
  const currentStateStr = `${store.overlays.join(',')}_${store.targetDate}_${currentHour}_${minLat}_${maxLat}_${minLon}_${maxLon}`;

  if (currentStateStr === lastRenderState) {
    return; // Skip redundant updates! (e.g. simulation minute ticking)
  }
  lastRenderState = currentStateStr;

  inflightOverlays.forEach(l => mockOverlaysLayer.removeLayer(l));
  inflightOverlays = [];
  const seq = ++overlaySeq;

  // Create bounds for the ImageOverlay，与 URL 用同一组量化值保证像素对齐
  const imageBounds: L.LatLngBoundsExpression = [[minLat, minLon], [maxLat, maxLon]];

  // Keep a reference to current layers to remove later to prevent flickering
  const oldLayers = mockOverlaysLayer.getLayers();
  let loadedCount = 0;

  store.overlays.forEach(layer => {
    const hourStr = currentHour.toString().padStart(2, '0') + ':00';
    // Removed t=Date.now() to allow browser caching and explicitly use hourStr
    const url = `${API_BASE}/api/weather/overlay?min_lat=${minLat.toFixed(dec)}&max_lat=${maxLat.toFixed(dec)}&min_lon=${minLon.toFixed(dec)}&max_lon=${maxLon.toFixed(dec)}&layer=${layer}&date=${store.targetDate}&start_time=${hourStr}`;

    const overlay = L.imageOverlay(url, imageBounds, {
      opacity: 0.8,
      interactive: false,
      crossOrigin: true
    });

    // 失败也要计数，否则这一批永远不就位，旧图层会一直压在新图层下面
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      if (seq !== overlaySeq) return; // 已被更新的请求取代
      loadedCount++;
      // Once all new layers are loaded, remove old layers
      if (loadedCount === store.overlays.length) {
        inflightOverlays = [];
        oldLayers.forEach(l => mockOverlaysLayer.removeLayer(l));
      }
    };

    overlay.on('load', settle);
    overlay.on('error', settle);
    inflightOverlays.push(overlay);
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

watch(() => [store.selectedSegmentIndex, store.hoveredSegmentIndex], () => {
  renderPlannedPath();
}, { deep: true });

// 三档结果落地或切换选中档 → 重画选中线与两条对比线
watch(() => [store.priority, store.routeOptions], () => {
  renderPlannedPath();
}, { deep: true });

// 卡片悬停只做临时预览：不套合视野，否则鼠标划过卡片地图就跳
watch(() => store.hoveredTier, () => {
  renderPlannedPath(false);
});
watch(() => store.highlightRegions, () => {
  renderBlockingRegions();
}, { deep: true });

watch([() => store.overlays, () => store.targetDate, () => store.targetTime], () => {
  renderRealOverlays();
}, { deep: true });

let uavMarker: L.CircleMarker | null = null;

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
    return;
  }
  
  if (newIdx < path.length) {
    const p2 = path[newIdx];
    uavMarker.setLatLng([Number(p2.lat), Number(p2.lng)]);
    
    // Check if we need to update weather (hour crossed)
    // For simplicity, we calculate total distance up to this point
    let totalDist = 0;
    for (let i = 0; i < newIdx; i++) {
      totalDist += Math.hypot(Number(path[i+1].lng) - Number(path[i].lng), Number(path[i+1].lat) - Number(path[i].lat)) * 111.0;
    }
    
    // 时间推进交由 DataChartPanel 处理，这里只负责移动无人机标记
    void totalDist;
  }
});

onUnmounted(() => {
  window.clearTimeout(overlayDebounce);
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

/* 阻塞湍流区标注 */
:deep(.blocking-marker) {
  display: flex;
  align-items: center;
  justify-content: center;
}
:deep(.blocking-tag) {
  background: rgba(239, 68, 68, 0.92);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 9999px;
  white-space: nowrap;
  box-shadow: 0 2px 6px rgba(0,0,0,0.5);
  border: 1px solid rgba(255,255,255,0.5);
}
</style>
