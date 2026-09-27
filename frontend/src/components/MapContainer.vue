<template>
  <div class="map-container" ref="mapElement" :class="{ 'cursor-crosshair': store.selectingMode }">
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { store, RISK_COLORS, TIER_COLORS, PRIORITY_LABELS, activateRouteTier, fetchCoverage, SITE_AIRCRAFT_COLORS } from '../store';
import { API_BASE, DEFAULT_REL_ALT } from '../config';

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
// 数据覆盖框单独一层：它要压在热图底下，否则框外的暗色会盖住航线与热图
let coverageLayer = L.layerGroup();
let altLayer = L.layerGroup();    // 替代起降点（后端 suggestions.alt_bases）
let peakLayer = L.layerGroup();   // 沿程最危险点（risk.stats.worst_index）
let siteLayer = L.layerGroup();   // 多航点模式：中继序列与段长可达圆（M7）
let siteCatalogLayer = L.layerGroup(); // 多航点模式：站表全量候选点，只在「多航点」页显示

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

  // 覆盖框图层要落在底图之上、热图(overlayPane=400)之下，所以给它一个专用 pane
  map.createPane('coveragePane');
  const coveragePane = map.getPane('coveragePane');
  if (coveragePane) coveragePane.style.zIndex = '350';

  // 推演的无人机标记要在航线(overlayPane=400)与起终点/最危险点等标注(markerPane=600、
  // tooltipPane=650)之上：circleMarker 默认与航线同层，同层按加入顺序叠，重画航线时点就被埋了
  map.createPane('uavPane');
  const uavPane = map.getPane('uavPane');
  if (uavPane) uavPane.style.zIndex = '660';

  currentLayer = layers[props.activeLayer];
  currentLayer.addTo(map);
  coverageLayer.addTo(map);
  basesLayer.addTo(map); // Add bases layer
  markersLayer.addTo(map);
  mockOverlaysLayer.addTo(map);
  pathLayer.addTo(map);
  blockLayer.addTo(map);
  altLayer.addTo(map);
  peakLayer.addTo(map);
  siteLayer.addTo(map);
  siteCatalogLayer.addTo(map);
  map.on('zoomend', () => renderSiteCatalog());
  renderCoverageBox();
  void fetchCoverage().then(renderCoverageBox);

  // 选点只挪位置：相对高度是用户填的，一次点击不该顺手改成别的数。
  // 只有还空着的（新建航点）才补默认值。
  const pickedAlt = (cur: number | string): string =>
    String(cur ?? '').trim() === '' ? String(DEFAULT_REL_ALT) : String(cur);

  // Handle clicks for point selection
  map.on('click', (e: L.LeafletMouseEvent) => {
    if (!store.selectingMode) return;
    
    const lat = e.latlng.lat.toFixed(6);
    const lng = e.latlng.lng.toFixed(6);

    if (store.selectingMode === 'start') {
      store.start.lat = lat;
      store.start.lng = lng;
      store.start.alt = pickedAlt(store.start.alt);
    } else if (store.selectingMode === 'end') {
      store.end.lat = lat;
      store.end.lng = lng;
      store.end.alt = pickedAlt(store.end.alt);
    } else if (store.selectingMode === 'waypoint') {
      if (store.selectingIndex >= 0 && store.selectingIndex < store.waypoints.length) {
        const wp = store.waypoints[store.selectingIndex];
        wp.lat = lat;
        wp.lng = lng;
        wp.alt = pickedAlt(wp.alt);
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

// ==================== 三个「后端早就算出来了、地图上一直没表达」的标注 ====================

/** 全世界外环，和覆盖框组成带洞的多边形，用来把框外压暗 */
const WORLD_RING: L.LatLngExpression[] = [[-90, -180], [-90, 180], [90, 180], [90, -180]];

const degKm = (a: [number, number], b: [number, number]) => {
  const midRad = ((a[0] + b[0]) / 2) * Math.PI / 180;
  return Math.hypot((b[1] - a[1]) * Math.cos(midRad), b[0] - a[0]) * 111.0;
};

/**
 * 数据覆盖框：框外压暗 + 边界虚线 + 角标。
 *
 * 存在的理由只有一条：热图在框外本来就是全透明，"这里没数据"和"这里很安全"
 * 在屏幕上长得一模一样，用户会照着前者起飞。
 */
const renderCoverageBox = () => {
  coverageLayer.clearLayers();
  if (!map || !store.coverage) return;
  const c = store.coverage;
  const box: L.LatLngExpression[] = [
    [c.lat_min, c.lon_min], [c.lat_min, c.lon_max],
    [c.lat_max, c.lon_max], [c.lat_max, c.lon_min],
  ];
  L.polygon([WORLD_RING, box], {
    pane: 'coveragePane',
    color: 'transparent',
    weight: 0,
    fillColor: '#0b1220',
    fillOpacity: 0.35,
    fillRule: 'evenodd',
    interactive: false,      // 别挡住取点点击
  }).addTo(coverageLayer);
  L.polygon(box, {
    pane: 'coveragePane',
    color: '#cbd5e1',
    weight: 1.5,
    opacity: 0.75,
    dashArray: '6, 6',
    fill: false,
    interactive: false,
  }).addTo(coverageLayer);
  L.marker([c.lat_max, c.lon_min], {
    interactive: false,
    icon: L.divIcon({
      className: 'coverage-marker',
      html: '<div class="coverage-tag">气象数据覆盖范围</div>',
      iconSize: [200, 28],
      iconAnchor: [8, 34],
    }),
  }).addTo(coverageLayer);
};

/** 当前在地图上展示的那一档（悬停卡片时是悬停档，不是选中档） */
const shownResult = () => {
  const shown = store.hoveredTier ?? store.priority;
  return store.routeOptions?.results.find(r => r.priority === shown) ?? store.feasibility;
};

/** 替代起降点：只在当前档未得出可飞结论、且后端确实给出替代点时画 */
const renderAltBases = () => {
  altLayer.clearLayers();
  if (!map) return;
  const feas = shownResult();
  if (!feas || feas.feasible) return;
  const bases = feas.suggestions?.alt_bases ?? [];

  bases.forEach(b => {
    const anchor = b.role === 'start' ? store.start : store.end;
    const a: [number, number] = [Number(anchor.lat), Number(anchor.lng)];
    const p: [number, number] = [Number(b.end.lat), Number(b.end.lon)];
    if (!Number.isFinite(a[0]) || !Number.isFinite(a[1])) return;

    // 虚线把"原来的点"和"挪到的点"连起来，方向一眼可读
    L.polyline([a, p], {
      color: '#e2e8f0', weight: 2, opacity: 0.7, dashArray: '2, 6', interactive: false,
    }).addTo(altLayer);
    L.circleMarker(p, {
      color: '#e2e8f0', weight: 2.5, fillColor: RISK_COLORS[b.level] ?? '#94a3b8',
      fillOpacity: 1, radius: 9,
    }).addTo(altLayer).bindTooltip(
      `${b.role_label} · ${b.level}<br>${b.description}<br>`
      + `距原点 ${degKm(a, p).toFixed(0)} km · 按此起飞里程 ${b.distance_km} km`,
      { direction: 'top', offset: [0, -12] }
    );
    L.marker(p, {
      interactive: false,
      icon: L.divIcon({
        className: 'alt-marker',
        html: `<div class="alt-tag">${b.role_label}</div>`,
        iconSize: [130, 26],
        iconAnchor: [65, -12],
      }),
    }).addTo(altLayer);
  });
};

/**
 * 多航点模式：把双机对照的中继序列画成两条实线（颜色区分机型，不靠线型）。
 *
 * 只画"能飞的"那台；被拒的那台在侧栏卡片里给结论与原因，不在地图上留残线。
 * 可达圆只画当前选中那台的起跳点一圈 —— 213 个站点铺满屏幕不会让任何人更懂这条线，
 * 而"段长上限是个圆"这一眼就看出来了（池外灰点按 2026-09-27 裁定不进一期）。
 */
const renderSitePlan = () => {
  siteLayer.clearLayers();
  if (!map || !store.siteResult) return;
  const opts = (store.siteResult.options ?? []).filter(o => o.feasible);
  const active = store.activeSiteAircraft;

  opts.forEach(o => {
    const color = SITE_AIRCRAFT_COLORS[o.aircraft] ?? '#94a3b8';
    const isActive = !active || active === o.aircraft;
    const pts: [number, number][] = [];
    (o.legs ?? []).forEach((l, i) => {
      if (i === 0) pts.push([l.from.lat, l.from.lon]);
      pts.push([l.to.lat, l.to.lon]);
    });
    if (pts.length < 2) return;
    L.polyline(pts, {
      color, weight: 3, opacity: isActive ? 0.95 : 0.35,
      interactive: false,
    }).addTo(siteLayer);

    (o.legs ?? []).forEach(l => {
      const e = l.to;
      if (!e || String(e.site_id).startsWith('OD-')) return;
      const w = l.weather || {};
      L.circleMarker([e.lat, e.lon], {
        color, weight: 2, fillColor: color, fillOpacity: isActive ? 1 : 0.4, radius: 7,
      }).addTo(siteLayer).bindTooltip(
        `${e.name} · 第 ${l.seq} 段落地<br>`
        + `海拔 ${e.elevation_m ?? '—'} m · 直线 ${l.straight_km} km`
        + `${l.route_km != null ? ' · 实飞 ' + l.route_km + ' km' : ' · 未复验'}<br>`
        + `${w.label ?? ''}${l.level ? ' · 风险 ' + l.level : ''}`,
        { direction: 'top', offset: [0, -10] }
      );
      // 落地站必须自带地名：只给一个彩色圆点，看上去就跟随机取点连线没区别
      if (isActive) {
        L.marker([e.lat, e.lon], {
          interactive: false,
          icon: L.divIcon({
            className: 'site-relay-marker',
            html: `<div class="site-relay-label">${l.seq} ${e.name}</div>`,
            iconSize: [1, 1], iconAnchor: [0, 0],
          }),
        }).addTo(siteLayer);
      }
    });

    if (!isActive) return;
    const start = pts[0];
    L.circle(start, {
      radius: o.leg_limit_km * 1000, color, weight: 1, opacity: 0.5,
      dashArray: '4, 6', fill: false, interactive: false,
    }).addTo(siteLayer);
    L.marker(start, {
      interactive: false,
      icon: L.divIcon({
        className: 'alt-marker',
        html: `<div class="alt-tag">段长上限 ${o.leg_limit_km} km（直线）</div>`,
        iconSize: [220, 26], iconAnchor: [110, -14],
      }),
    }).addTo(siteLayer);
  });
};

/** 站表全量候选点：进「多航点」页就带地名画出来，不让人对着一串无名圆点猜算法随机落了哪儿 */
const renderSiteCatalog = () => {
  siteCatalogLayer.clearLayers();
  if (!map || store.sidebarTab !== 'site') return;
  // 默认 zoom 7 铺 210 条地名会糊成一片：远景只标机场，放大后才连县城一起标
  const labelAll = map.getZoom() >= 8;
  (store.siteCatalog?.sites ?? []).forEach(s => {
    if (s.usable === false) return;
    if (!labelAll && s.type !== 'airport') return;
    L.marker([s.lat, s.lon], {
      zIndexOffset: -200,
      icon: L.divIcon({
        className: 'site-cat-marker',
        html: `<div class="site-cat-dot"></div><div class="site-cat-label">${s.name}</div>`,
        iconSize: [12, 12], iconAnchor: [6, 6],
      }),
    }).addTo(siteCatalogLayer).bindTooltip(
      `${s.name} · ${s.elevation_m ?? '—'} m`,
      { direction: 'top', offset: [0, -6] });
  });
};

/** 沿程最危险点：risk.stats.worst_index 指向的那个样本，标在真正飞的那条线上 */
const renderPeakWorst = () => {
  peakLayer.clearLayers();
  if (!map) return;
  const feas = shownResult();
  const risk = feas?.risk;
  if (!risk) return;
  const idx = risk.stats?.worst_index;
  const s = (risk.samples ?? []).find(x => x.i === idx);
  if (!s || s.lat == null || s.lon == null) return;

  const p: [number, number] = [Number(s.lat), Number(s.lon)];
  const color = RISK_COLORS[s.level] ?? '#94a3b8';
  L.circleMarker(p, {
    color: '#0b1220', weight: 2.5, fillColor: color, fillOpacity: 1, radius: 9,
  }).addTo(peakLayer);

  const cum = typeof s.cum_km === 'number' ? s.cum_km : null;
  const lines = [`${s.level} · TKE ${s.tke.toFixed(2)} / EDR ${s.edr.toFixed(3)}`];
  if (cum !== null) {
    lines.push(`距起点 ${cum.toFixed(0)} km`);
    const speed = Number(store.uavSpeed) || 60;
    const [h0, m0] = String(store.targetTime).split(':');
    const mins = (parseInt(h0 || '0') * 60 + (parseInt(m0 || '0') || 0)) + Math.round(cum / speed * 60);
    lines.push(`到达约 ${String(Math.floor(mins / 60) % 24).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`);
  }
  L.marker(p, {
    interactive: false,
    icon: L.divIcon({
      className: 'peak-marker',
      html: `<div class="peak-tag" style="border-color:${color};color:${color}">沿程最危险</div>`,
      iconSize: [150, 26],
      iconAnchor: [75, -14],
    }),
  }).addTo(peakLayer).bindTooltip(lines.join('<br>'), { direction: 'top', offset: [0, -22] });
};

// 未选中档位：各档自己的颜色 + 各自的虚线疏密，压到 30% 只让位不消失
const TIER_FALLBACK_COLOR = '#94a3b8';
const TIER_DASH: Record<string, string> = {
  urgent: '4, 7',
  normal: '9, 7',
  economy: '16, 8',
};

/** 当前档主线的线宽。选中/悬停不改它，只改别的段的透明度。 */
const SEGMENT_WEIGHT = 6;

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
    const line = L.polyline(r.path.map(p => [p.lat, p.lon] as [number, number]), {
      color: TIER_COLORS[r.priority] ?? TIER_FALLBACK_COLOR,
      weight: 5,
      opacity: 0.5,
      dashArray: TIER_DASH[r.priority] ?? '8, 8',
    }).addTo(pathLayer);
    line.bindTooltip(
      `${PRIORITY_LABELS[r.priority] ?? r.priority} · ${r.distance.route_km ?? '—'} km`,
      { sticky: true }
    );
    // 直接点地图上的线也能选档，不必回侧栏找卡片
    line.on('click', () => activateRouteTier(r.priority));
    line.on('mouseover', () => { if (map) map.getContainer().style.cursor = 'pointer'; });
    line.on('mouseout', () => { if (map) map.getContainer().style.cursor = ''; });
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
  //
  // 每段画两条：先一条深色"描边"再叠彩色主线。卫星图/地形图本身就是土黄色系，
  // 单靠提亮主线压不住底图，描边给出一条与底图无关的暗色边界，线段才分得出来。
  if (segments.length > 0) {
    const anyFocus = selected !== null || hovered !== null;
    segments.forEach((seg, idx) => {
      const from = Math.max(0, seg.start_index);
      const to = Math.min(path.length - 1, seg.end_index);
      if (to <= from) return;
      const piece = latlngs.slice(from, to + 1);
      const isFocused = selected === idx || hovered === idx;
      const dimmed = anyFocus && !isFocused;
      L.polyline(piece, {
        color: '#0b1220',
        weight: SEGMENT_WEIGHT + 4,
        opacity: dimmed ? 0.18 : 0.5,
      }).addTo(pathLayer);
      L.polyline(piece, {
        color: RISK_COLORS[seg.level] ?? '#94a3b8',
        weight: SEGMENT_WEIGHT,
        // 选中只靠"别的段变淡"来表达：加粗会让航线在点击瞬间粗一截，视觉直跳
        opacity: dimmed ? 0.3 : 1.0,
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

// 热图渲染算法版本：行纬度自 2026-09 起改按 Web Mercator 重采样（旧图等纬度行在
// 中视口最多漂 ~1°）。覆盖层 URL 被浏览器/OpenResty/CF 按 max-age=86400 缓存一天，
// 服务端出图几何一变必须改这个常量击穿旧缓存，否则线上继续吃一天错图。
const OVERLAY_RENDER_VERSION = 'merc1';

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

  // 热图跟着推演游标走（未推演时游标就等于起飞时间）。
  // 日期也要跟着走：长途线飞过午夜之后，只带起飞那天 = 给无人机看 24 小时前那一帧。
  const currentHour = parseInt(store.simulationTime.split(':')[0] || '8');
  const overlayDate = store.simulationDate || store.targetDate;
  // State guard to prevent flickering: only re-fetch if layers, date, HOUR, or quantized bounds change
  const currentStateStr = `${store.overlays.join(',')}_${overlayDate}_${currentHour}_${minLat}_${maxLat}_${minLon}_${maxLon}`;

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
    const url = `${API_BASE}/api/weather/overlay?min_lat=${minLat.toFixed(dec)}&max_lat=${maxLat.toFixed(dec)}&min_lon=${minLon.toFixed(dec)}&max_lon=${maxLon.toFixed(dec)}&layer=${layer}&date=${overlayDate}&start_time=${hourStr}&render=${OVERLAY_RENDER_VERSION}`;

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

watch(() => store.coverage, () => {
  renderCoverageBox();
});

// 切档 / 悬停预览 / 重新规划都要跟着重画：这三项全部跟着「地图上那条线」走
watch([() => store.routeOptions, () => store.priority, () => store.hoveredTier], () => {
  renderAltBases();
  renderPeakWorst();
});

// 多航点模式：换看哪一台、重跑一次规划，都要跟着重画
watch([() => store.siteResult, () => store.activeSiteAircraft], () => {
  renderSitePlan();
});

// 站表只在「多航点」页铺出来；首次进页时目录可能还没取到，取到后再画一次
watch([() => store.sidebarTab, () => store.siteCatalog], () => {
  renderSiteCatalog();
});

watch([() => store.overlays, () => store.targetDate, () => store.simulationTime, () => store.simulationDate], () => {
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
      pane: 'uavPane',
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
    // 时刻推进与热图取帧都在 DataChartPanel 里按【物理里程】算，这里只负责移动标记
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

/* 站表候选点：小圆点 + 地名，刻意不用城堡图标（那是起降预设点专属） */
:deep(.site-cat-marker) {
  display: flex;
  align-items: center;
  justify-content: center;
}
:deep(.site-cat-marker .site-cat-dot) {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e2e8f0;
  border: 1px solid rgba(0, 0, 0, 0.75);
  box-shadow: 0 1px 2px rgba(0,0,0,0.5);
}
:deep(.site-cat-marker .site-cat-label) {
  position: absolute;
  top: 9px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 12px;
  color: rgba(255,255,255,0.9);
  text-shadow: 0 1px 3px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.7);
  white-space: nowrap;
  pointer-events: none;
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

/* 三类新标注的标签沿用同一形状语言：小胶囊 + 半透明深底，
   只有颜色区分「数据边界（中性灰）/ 替代点（中性灰）/ 危险点（风险色）」 */
:deep(.coverage-marker),
:deep(.alt-marker),
:deep(.peak-marker) {
  display: flex;
  align-items: center;
  justify-content: center;
}
:deep(.coverage-tag),
:deep(.alt-tag),
:deep(.peak-tag),
:deep(.site-relay-label) {
  font-size: 14px;
  font-weight: 700;
  line-height: 1.35;
  padding: 4px 12px;
  border-radius: 9999px;
  white-space: nowrap;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.5);
  /* 与 .glass-panel 同一档底：纯黑 0.75 + 同一份颗粒纹理 */
  background-color: var(--panel-bg);
  background-image: var(--panel-noise);
}
:deep(.coverage-tag) {
  color: #e2e8f0;
  border: 1px dashed rgba(203, 213, 225, 0.9);
}
:deep(.alt-tag) {
  color: #e2e8f0;
  border: 1px solid rgba(226, 232, 240, 0.65);
}
:deep(.peak-tag) {
  border: 1px solid;
}
:deep(.site-relay-label) {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  /* 描边与字色取同一个值：机型色只留给航线与圆点，标签本身不再二色 */
  border: 1px solid #e2e8f0;
  color: #e2e8f0;
}
</style>
