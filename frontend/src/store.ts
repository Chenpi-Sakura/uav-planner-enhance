import { reactive, watch } from 'vue';
import { API_BASE } from './config';
import type { TimeWindowResult, TimeWindowRoute } from './types/assessment';

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

/** 气象数据的经纬度覆盖框（GET /api/meta/coverage） */
export interface CoverageBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
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
    samples: { i: number; lat?: number; lon?: number; cum_km?: number | null; tke: number; edr: number; level: RiskLevel }[];
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
  /**
   * 推演游标当前指示的时刻（HH:MM）。
   *
   * 它和 targetTime 必须分开：targetTime 是「起飞时间」这个输入项，会进规划请求、
   * 剖面请求和热图取数；旧实现让推演直接改写它，于是播放到哪儿、下次就从哪儿起飞，
   * 切档/换线时也不会归位（演示时表现为"点了另一条线，时间没回到这条线的起点"）。
   */
  simulationTime: string;
  bases: BaseStation[];
  /** 气象数据覆盖框；null = 后端数据未加载或还没问到（地图据此跳过该图层） */
  coverage: CoverageBox | null;
  // ---- 决策平台新增 ----
  sidebarTab: 'plan' | 'timeWindow';   // 侧边栏标签页（可行性已并入路径规划）
  planLoading: boolean;                // 一次「开始规划」请求是否在飞（侧栏与功能二共用）
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
  simulationTime: '08:00',
  bases: [
    { name: '拉萨', lat: '29.650000', lng: '91.130000', alt: '600' },
    { name: '日喀则', lat: '29.270000', lng: '88.880000', alt: '600' },
    { name: '林芝', lat: '29.660000', lng: '94.360000', alt: '600' },
    { name: '昌都', lat: '31.150000', lng: '97.180000', alt: '600' }
  ],
  coverage: null,
  sidebarTab: 'plan',
  planLoading: false,
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
  // 原来是 #eab308（土黄），在卫星图/地形图上跟底图同色系，线段几乎描不出来。
  // 提亮到接近柠檬黄，靠明度差 + 地图上的深色描边分离，四档色序（绿→黄→橙→红）不变。
  '中风险': '#fde047',
  '高风险': '#f97316',
  '不可飞': '#ef4444',
};

export const PRIORITY_LABELS: Record<string, string> = {
  urgent: '紧急',
  normal: '平衡',
  economy: '安全',
};

/**
 * 把推演游标归位到「这条航线的起飞时刻」。
 *
 * 换航线（切档、重新规划）和手改起飞时间都要走这里，否则游标会停在上一次的时刻上，
 * 而那条线根本不是同一条。
 */
export function anchorSimulationClock(): void {
  store.isSimulating = false;
  store.simulationIndex = 0;
  store.simulationTime = store.targetTime;
}

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
  anchorSimulationClock();

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

/** 清掉航线与全部派生结果（换起终点、重新规划前都要先走这一步） */
export function clearPlannedRoute(): void {
  store.plannedPath = [];
  store.routeOptions = null;
  store.hoveredTier = null;
  anchorSimulationClock();
  store.feasibility = null;
  store.highlightRegions = [];
  store.selectedSegmentIndex = null;
  store.hoveredSegmentIndex = null;
}

/**
 * 取一次气象数据覆盖框。进程内问一次就够：数据集是启动时预加载的静态文件，
 * 框不会变；拿不到（数据未加载）就留 null，地图跳过该图层而不是猜一个框。
 */
export async function fetchCoverage(): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/api/meta/coverage`);
    if (!response.ok) return;
    const result = await response.json();
    if (result && result.available) {
      store.coverage = {
        lat_min: Number(result.lat_min), lat_max: Number(result.lat_max),
        lon_min: Number(result.lon_min), lon_max: Number(result.lon_max),
      };
    }
  } catch (error) {
    console.error('覆盖范围请求失败:', error);
  }
}

/** 评估接口的不可飞原因 -> 弹窗文案键 */
const FEAS_REASON_TO_NOTICE: Record<string, PlanNoticeReason> = {
  no_safe_path: 'blocked',
  detour_exceeds_threshold: 'blocked',
  data_coverage_insufficient: 'no_data',
  search_iteration_limit: 'iteration_limit',
};

/**
 * 一次「开始规划」= 三档几何 + 三档完整评估（/api/assess/feasibility-multi）。
 *
 * 放在 store 而不是 Sidebar 里，是因为功能二的候选列表也要能触发同一个请求：
 * 两处各写一遍迟早会出现「按钮 A 出三档、按钮 B 出一档」的行为差异。
 */
export async function runPlan(): Promise<void> {
  if (!store.start.lat || !store.start.lng || !store.end.lat || !store.end.lng) {
    alert('请先设置起点和终点！');
    return;
  }

  store.planLoading = true;
  store.planNotice = null;
  clearPlannedRoute();
  try {
    const response = await fetch(`${API_BASE}/api/assess/feasibility-multi`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start: { lat: Number(store.start.lat), lon: Number(store.start.lng) },
        end: { lat: Number(store.end.lat), lon: Number(store.end.lng) },
        waypoints: store.waypoints
          .filter(wp => wp.lat !== '' && wp.lng !== '')
          .map(wp => ({ lat: Number(wp.lat), lon: Number(wp.lng) })),
        priority: store.priority,
        date: store.targetDate,
        start_time: store.targetTime,
        speed: Number(store.uavSpeed),
      }),
    });

    if (!response.ok) throw new Error('规划失败');

    const result = await response.json();
    const results: FeasibilityResult[] = result.results ?? [];
    if (results.length === 0) throw new Error('规划失败');
    store.routeOptions = { results, active: result.active, meta: result.meta };

    // 三档都不可飞也要把详情摊开（阻塞区、替代起降点就在里面），
    // 所以照样 activate 一档，只是额外补一个失败弹窗。
    const wanted = store.priority;
    const target = results.find(x => x.priority === wanted && x.feasible)
      ?? results.find(x => x.feasible)
      ?? results.find(x => x.priority === result.active)
      ?? results[0];
    activateRouteTier(target.priority);
    if (result.meta.n_feasible === 0) {
      store.planNotice = { reason: FEAS_REASON_TO_NOTICE[target.reason ?? ''] ?? 'blocked' };
    }

    // 三档卡片和评估详情都在侧栏里，折叠侧栏等于把选择入口藏起来，所以这里不折叠
    store.chartCollapsed = false;
  } catch (error) {
    console.error('规划请求失败:', error);
    store.planNotice = { reason: 'request_failed' };
  } finally {
    store.planLoading = false;
  }
}

/**
 * 采用功能二某个候选：把它的起飞时刻写回规划条件，切到「路径规划」并立刻重规划。
 *
 * 不在功能二里另画一条线 —— 同一个时刻、同一组起终点走同一个 planner，
 * 两条必然一致；另起一套渲染就等于再造一个会出现差异的入口（B10 的原始抱怨）。
 */
export function applyTimeWindowRoute(route: TimeWindowRoute): void {
  const [date, time] = String(route.takeoff).split('T');
  if (!date || !time) return;
  store.targetDate = date;
  store.targetTime = time.slice(0, 5);
  store.sidebarTab = 'plan';
  void runPlan();
}

// 手改「起飞时间」= 重新定义这条线什么时候开始，游标跟着归位。
// 反过来不成立：推演只动 simulationTime，永远不回写 targetTime。
watch(() => store.targetTime, () => {
  anchorSimulationClock();
});
