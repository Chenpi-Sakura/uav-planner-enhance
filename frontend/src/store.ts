import { reactive } from 'vue';
import type { TimeWindowResult } from './types/assessment';

export interface Point {
  lat: number | string;
  lng: number | string;
  alt: number | string;
}

export interface BaseStation {
  name: string;
  lat: string;
  lng: string;
  alt: string;
}

/** 风险等级 */
export type RiskLevel = '低风险' | '中风险' | '高风险' | '不可飞';

export interface RiskSegment {
  from_name: string;
  to_name: string;
  level: RiskLevel;
  max_tke: number;
  max_edr: number;
  distance_km: number;
  start_index: number;
  end_index: number;
}

export interface RiskStats {
  max_tke: number;
  max_edr: number;
  mean_tke: number;
  mean_edr: number;
  moderate_ratio: number;
  high_ratio: number;
  max_exposure_km: number;
  n_samples: number;
  worst_index: number;
}

export interface BlockingRegion {
  center: [number, number];
  radius_km: number;
  area_km2: number;
  shape: string;
  aspect_ratio: number;
  max_tke: number;
  max_edr: number;
  mean_tke: number;
  crosses_corridor: boolean;
  corridor_distance_km: number;
  polygon?: [number, number][] | null;
}

export interface FeasibilityResult {
  feasible: boolean;
  conclusion: string;
  reason: string | null;
  priority: string;
  detour_limit: number;
  distance: {
    /** false = 未规划出航线，距离/时间/绕行指标不适用 */
    applicable: boolean;
    straight_km: number;
    route_km: number | null;
    detour_ratio: number | null;
    flight_time_h: number | null;
    time_cost_h: {
      applicable: boolean;
      extra: number | null;
      limit: number;
      limit_ratio: number;
      exceeded: boolean;
    };
  };
  flight_time_h: number | null;
  risk: null | {
    overall: RiskLevel;
    stats: RiskStats;
    samples: { i: number; lat?: number; lon?: number; tke: number; edr: number; level: RiskLevel }[];
    segments: RiskSegment[];
  };
  blocking: null | { regions: BlockingRegion[]; dominant_shape: string | null };
  suggestions: {
    alt_windows: unknown[];
    alt_bases: {
      role: 'start' | 'end';
      role_label: string;
      end: { lat: number; lon: number };
      level: RiskLevel;
      distance_km: number;
      description: string;
    }[];
    has_alt_bases: boolean;
    text: string;
  };
  path: { lat: number; lon: number }[];
  meta: {
    endpoints?: {
      start?: { safe: boolean; tke: number | null; edr: number | null; describe: string };
      end?: { safe: boolean; tke: number | null; edr: number | null; describe: string };
    };
    unsafe_endpoint?: string | null;
    [key: string]: unknown;
  };
}

/** 三档一次评估的响应（/api/assess/feasibility-multi） */
export interface RouteOptions {
  /** 顺序与 meta.tiers 一致：urgent / normal / economy */
  results: FeasibilityResult[];
  active: string;
  meta: {
    tiers: string[];
    active: string;
    n_feasible: number;
    /** 三条几何完全相同：通常是沿程湍流未超过中风险下沿，软惩罚未参与代价 */
    all_identical: boolean;
    distinct_paths: number;
  };
}

/** 档位 -> 地图上的对比线颜色（选中线用风险色，不用这套） */
export const TIER_COLORS: Record<string, string> = {
  urgent: '#38bdf8',
  normal: '#a78bfa',
  economy: '#34d399',
};

export interface StoreState {
  selectingMode: 'start' | 'waypoint' | 'end' | null;
  selectingIndex: number;
  start: Point;
  end: Point;
  waypoints: Point[];
  plannedPath: Point[];
  overlays: string[];
  targetDate: string;
  targetTime: string;
  uavSpeed: number;
  sidebarCollapsed: boolean;
  chartCollapsed: boolean;
  isSimulating: boolean;
  simulationSpeed: number;
  simulationIndex: number;
  bases: BaseStation[];
  // ---- 决策平台新增 ----
  sidebarTab: 'plan' | 'timeWindow';   // 侧边栏标签页（可行性已并入路径规划）
  priority: 'urgent' | 'normal' | 'economy';  // 当前选中的方案档位
  routeOptions: RouteOptions | null;  // 一次规划产出的三档评估（null = 还没规划过）
  hoveredTier: string | null;         // 卡片悬停时临时在地图上展示的档位
  feasibility: FeasibilityResult | null; // 当前选中档位的评估结果
  highlightRegions: BlockingRegion[];    // 地图高亮：阻塞区
  selectedSegmentIndex: number | null;   // 结果面板：选中的分段
  // ---- 功能二：时间窗口 ----
  timeWindowLoading: boolean;
  timeWindow: TimeWindowResult | null;   // 功能二评估结果
  forecastDays: number;                  // 7 天推演天数（0 = 不推演）
  hoveredSegmentIndex: number | null;    // 地图联动：悬停的分段
  pendingTimeWindowScan: boolean;        // 从功能一跳转功能二时自动执行一次扫描
  planNotice: PlanNotice | null;         // 路径规划失败弹窗（null = 不显示）
}

/** 规划失败原因：后端 reason 直接透传，request_failed 表示请求本身没走通 */
export type PlanNoticeReason = 'blocked' | 'no_data' | 'iteration_limit' | 'request_failed';

export interface PlanNotice {
  reason: PlanNoticeReason;
}

export const store = reactive<StoreState>({
  selectingMode: null,
  selectingIndex: -1,
  start: { lat: '29.650000', lng: '91.130000', alt: '600' },
  end: { lat: '29.660000', lng: '94.360000', alt: '600' },
  waypoints: [],
  plannedPath: [],
  overlays: [],
  targetDate: '2022-07-01',
  targetTime: '08:00',
  uavSpeed: 60,
  sidebarCollapsed: false,
  chartCollapsed: true,
  isSimulating: false,
  simulationSpeed: 500,
  simulationIndex: 0,
  bases: [
    { name: '拉萨', lat: '29.650000', lng: '91.130000', alt: '600' },
    { name: '日喀则', lat: '29.270000', lng: '88.880000', alt: '600' },
    { name: '林芝', lat: '29.660000', lng: '94.360000', alt: '600' },
    { name: '昌都', lat: '31.150000', lng: '97.180000', alt: '600' }
  ],
  sidebarTab: 'plan',
  priority: 'normal',
  routeOptions: null,
  hoveredTier: null,
  feasibility: null,
  highlightRegions: [],
  selectedSegmentIndex: null,
  timeWindowLoading: false,
  timeWindow: null,
  forecastDays: 7,
  hoveredSegmentIndex: null,
  pendingTimeWindowScan: false,
  planNotice: null,
});

/** 风险等级 -> 展示色 */
export const RISK_COLORS: Record<string, string> = {
  '低风险': '#22c55e',
  '中风险': '#eab308',
  '高风险': '#f97316',
  '不可飞': '#ef4444',
};

export const PRIORITY_LABELS: Record<string, string> = {
  urgent: '紧急',
  normal: '平衡',
  economy: '经济',
};

/**
 * 把当前档位切到指定方案：地图主航线、结论卡、折线图、AI 报告全部跟随。
 *
 * 三档的评估结果在同一次请求里已经拿到，所以切换只改状态、不再发请求。
 */
export function activateRouteTier(priority: string): boolean {
  const hit = store.routeOptions?.results.find(x => x.priority === priority);
  if (!hit) return false;

  store.priority = hit.priority as StoreState['priority'];
  store.feasibility = hit;
  store.selectedSegmentIndex = null;
  store.hoveredSegmentIndex = null;
  store.highlightRegions = [];
  store.isSimulating = false;
  store.simulationIndex = 0;

  const startAlt = Number(store.start.alt || 600);
  const endAlt = Number(store.end.alt || 600);
  const n = hit.path.length;
  store.plannedPath = hit.path.map((p, i) => ({
    lat: p.lat,
    lng: p.lon,
    alt: n > 1 ? startAlt + (endAlt - startAlt) * (i / (n - 1)) : startAlt,
  }));

  // 不可飞时把阻塞区摊开：这是旧单档评估的行为，合并后不能丢
  if (!hit.feasible && hit.blocking?.regions?.length) {
    store.highlightRegions = hit.blocking.regions;
    if (!store.overlays.includes('tke')) store.overlays.push('tke');
  }
  return true;
}
