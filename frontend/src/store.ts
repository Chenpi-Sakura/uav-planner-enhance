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

/**
 * 结论四态。feasible=False 不等于「飞不了」：
 * over_budget 是「有安全航线、只是超出本档时间预算」，
 * undetermined 是「A* 没搜完，有没有航线还不知道」。
 * 文案取后端的 conclusion，这里只管选色调与图标。
 */
export type Verdict = 'feasible' | 'infeasible' | 'over_budget' | 'undetermined';

export const VERDICT_TONES: Record<Verdict, 'ok' | 'bad' | 'warn' | 'unknown'> = {
  feasible: 'ok',
  infeasible: 'bad',
  over_budget: 'warn',
  undetermined: 'unknown',
};

export const verdictTone = (verdict: Verdict | undefined | null) =>
  VERDICT_TONES[verdict as Verdict] ?? 'bad';

export interface FeasibilityResult {
  feasible: boolean;
  conclusion: string;
  verdict: Verdict;
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
  /** A* 真正研究过的路口数 / 本次展开预算 / 展开花掉的秒数；没跑到搜索时为 null */
  search_effort: null | { expansions: number; budget: number; elapsed_s?: number };
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
  /**
   * 推演游标所在的【日期】（YYYY-MM-DD），起飞那天是初值，飞过午夜就进位。
   *
   * 以前只有 HH:MM：长途线（1600 km 要飞 27 小时）跨过午夜后，小时数被 %24 折回当天，
   * 热图请求于是带着【起飞那一天】的同一小时 —— 地图上给无人机上色的是 24 小时前那一帧。
   */
  simulationDate: string;
  bases: BaseStation[];
  /** 气象数据覆盖框；null = 后端数据未加载或还没问到（地图据此跳过该图层） */
  coverage: CoverageBox | null;
  // ---- 决策平台新增 ----
  sidebarTab: 'plan' | 'timeWindow' | 'site';   // 侧边栏标签页（可行性已并入路径规划）
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
  // ---- 功能三：多航点模式（M7 站点自动规划） ----
  siteLoading: boolean;
  siteResult: SitePlanResult | null;      // 最近一次双机对照结果
  siteCatalog: SiteCatalog | null;        // 站表与机型能力包（进程内取一次）
  siteForm: SiteForm;
  activeSiteAircraft: string | null;      // 地图上展示哪一台的序列
}

/** 功能三的参数：载重是**自变量**，站点数是它的输出，所以这里不给"站点数量"这种反因果的输入 */
export interface SiteForm {
  aircraft: string;            // '' = 双机对照
  payload_kg: number;
  mission: 'cargo' | 'crew';
  text: string;                // 自然语言任务（只预填表单，不直接触发规划）
  parseSource: '' | 'llm' | 'rule';
}

export interface SiteLegEndpoint {
  name: string;
  lat: number;
  lon: number;
  site_id: string;
  elevation_m: number | null;
}

export interface SiteLegWeather {
  fly_ratio?: number | null;
  worst_tke?: number | null;
  nan_ratio?: number;
  label?: string;
}

export interface SiteLeg {
  seq: number;
  from: SiteLegEndpoint;
  to: SiteLegEndpoint;
  straight_km: number;
  route_km: number | null;
  flight_time_h?: number | null;
  level?: string;
  reverified: boolean;
  over_range?: boolean;
  note?: string;
  weather?: SiteLegWeather;
  feasible?: boolean;
  reason?: string | null;
  verdict?: string;
}

export interface SiteOption {
  aircraft: string;
  label: string;
  certified: boolean;
  payload_kg: number;
  mission: string;
  leg_limit_km: number;
  margin_k: number;
  feasible: boolean;
  verdict: string;
  conclusion: string;
  reason?: string | null;
  message?: string;
  legs: SiteLeg[];
  relay_count: number;
  total_distance: number;
  total_time_h?: number | null;
  worst_level?: string;
  usable_sites: number;
  diagnostics: Record<string, any>;
  search_effort?: { expansions: number; budget: number; elapsed_s?: number } | null;
  notes: string[];
  elapsed_s?: number;
}

export interface SitePlanResult {
  options: SiteOption[];
  meta: Record<string, any>;
}

export interface SiteCatalogSite {
  id: string;
  name: string;
  type: string;
  region?: string;
  lat: number;
  lon: number;
  elevation_m: number;
  elev_kind?: string;
  geodatum?: string;
  hajin?: boolean;
  usable?: boolean;
  verify_priority?: number;
}

export interface SiteCatalog {
  stats: Record<string, any>;
  attribution?: string;
  aircraft: Array<{ key: string; label: string; power: string; certified: boolean;
                    cert: string; max_payload_kg: number;
                    cruise_kmh: number; cruise_note: string }>;
  sites: SiteCatalogSite[];
}

/** 规划弹窗种类：由后端 reason 映射而来，request_failed 表示请求本身没走通 */
export type PlanNoticeReason =
  | 'blocked' | 'over_budget' | 'no_data' | 'iteration_limit' | 'request_failed'
  | 'rejected';

export interface PlanNotice {
  reason: PlanNoticeReason;
  /** 「未算出」时带上的搜索力气（"已研究 N 个路口 / 预算 M"），空串 = 后端没给 */
  effort?: string;
  /** reason = rejected 时后端给的那句中文原因 */
  detail?: string;
}

/** 后端以 400 拒绝：请求本身不成立，连规划都没启动 */
export class RequestRejected extends Error {
  readonly detail: string;
  constructor(detail: string) {
    super(detail);
    this.detail = detail;
  }
}

/** 非 2xx 的统一出口：400 的 detail 是写给人看的原因，其余只算「后端没正常响应」 */
export async function failRequest(response: Response, fallback: string): Promise<never> {
  if (response.status === 400) {
    const detail = await response.json()
      .then(b => (typeof b?.detail === 'string' ? b.detail : ''))
      .catch(() => '');
    if (detail) throw new RequestRejected(detail);
  }
  throw new Error(fallback);
}

export const noticeForError = (error: unknown): PlanNotice =>
  error instanceof RequestRejected
    ? { reason: 'rejected', detail: error.detail }
    : { reason: 'request_failed' };

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
  simulationDate: '2022-07-01',
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
  siteLoading: false,
  siteResult: null,
  siteCatalog: null,
  siteForm: { aircraft: '', payload_kg: 200, mission: 'cargo', text: '', parseSource: '' },
  activeSiteAircraft: null,
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
  store.simulationDate = store.targetDate;
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

  // 未得出可飞结论时把阻塞区摊开：这是旧单档评估的行为，合并后不能丢
  //（超预算档后端不产阻塞区，所以这里实际只会摊出「不可飞 / 未算出」的）
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

/** 评估接口的「未得出可飞结论」原因码 -> 弹窗文案键 */
const FEAS_REASON_TO_NOTICE: Record<string, PlanNoticeReason> = {
  no_safe_path: 'blocked',
  // 超预算与「没算完」各有自己的弹窗：说成「被封死」就是另一种误导了
  detour_exceeds_threshold: 'over_budget',
  data_coverage_insufficient: 'no_data',
  search_iteration_limit: 'iteration_limit',
};

/** 评估接口的 reason 码 -> 中文说明（结论卡与三档卡片共用一份） */
export const FEAS_REASON_TEXT: Record<string, string> = {
  no_safe_path: '航路被高强度湍流区封死，无法规划安全航线',
  detour_exceeds_threshold: '存在安全航线，但绕行成本超出本档时间预算',
  data_coverage_insufficient: '起降点超出气象数据覆盖范围',
  search_iteration_limit: '搜索未收敛，是否存在安全航线尚未确定',
};

/**
 * 「未算出」要说的是没算完而不是没路，光讲道理不如给这对数字：
 * 顶到预算 = 再等等或放宽阈值，远没用完 = 走廊真的封死了。没有数字时返回空串。
 * 用时那一截区分两种停法：预算付完 vs 墙钟先到（见 planner 的 iteration_limit / time_limit）。
 * 三档卡片、评估详情、规划弹窗、功能二候选行共用这一条措辞。
 */
export function effortText(e?: { expansions: number; budget: number; elapsed_s?: number } | null): string {
  if (!e || !e.budget) return '';
  const spent = e.elapsed_s ? ` / 用时 ${Math.round(e.elapsed_s)} 秒` : '';
  return `已尝试 ${e.expansions} 种走法 / 上限 ${e.budget}${spent}`;
}

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

    if (!response.ok) await failRequest(response, '规划失败');

    const result = await response.json();
    const results: FeasibilityResult[] = result.results ?? [];
    if (results.length === 0) throw new Error('规划失败');
    store.routeOptions = { results, active: result.active, meta: result.meta };

    // 三档都没得出「可飞」也要把详情摊开（阻塞区、替代起降点就在里面），
    // 所以照样 activate 一档，只是额外补一个说明弹窗。
    const wanted = store.priority;
    const target = results.find(x => x.priority === wanted && x.feasible)
      ?? results.find(x => x.feasible)
      ?? results.find(x => x.priority === result.active)
      ?? results[0];
    activateRouteTier(target.priority);
    if (result.meta.n_feasible === 0) {
      store.planNotice = {
        reason: FEAS_REASON_TO_NOTICE[target.reason ?? ''] ?? 'blocked',
        effort: effortText(target.search_effort),
      };
    }

    // 三档卡片和评估详情都在侧栏里，折叠侧栏等于把选择入口藏起来，所以这里不折叠
    store.chartCollapsed = false;
  } catch (error) {
    console.error('规划请求失败:', error);
    store.planNotice = noticeForError(error);
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

// 手改「起飞时间/日期」= 重新定义这条线什么时候开始，游标跟着归位。
// 反过来不成立：推演只动 simulationTime/simulationDate，永远不回写 targetTime。
watch([() => store.targetTime, () => store.targetDate], () => {
  anchorSimulationClock();
});

// ==================== 功能三 · 多航点模式（M7 站点自动规划） ====================

/** 双机对照的配色：油动暖色、纯电冷色，与风险档配色刻意不撞（数据线一律实线那条规矩同样适用） */
export const SITE_AIRCRAFT_COLORS: Record<string, string> = {
  td550: '#f59e0b',
  v2000cg: '#38bdf8',
};

export const SITE_REASON_TEXT: Record<string, string> = {
  payload_exceeds_max: '任务重量超出本机最大商载',
  payload_beyond_published_envelope: '该载重档没有公开数据，不作答',
  network_unreachable: '可用站网不连通，缺中途落地点',
  no_candidate_in_corridor: '走廊内没有满足门限的站点',
  detour_exceeds_threshold: '逐段复验后实际里程超出段长上限',
  time_limit: '本次时间上限已到，尚未算完',
  iteration_limit: '搜索未收敛，结论尚未确定',
};

/** 站表与机型能力包：进程内取一次（站表是随仓库版本化的静态数据，不会自己变） */
export async function fetchSiteCatalog(): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/api/meta/sites`);
    if (!response.ok) return;
    store.siteCatalog = await response.json();
  } catch (error) {
    console.error('站点表请求失败:', error);
  }
}

/**
 * 自然语言任务 → 表单预填。**只回填，不触发规划**：
 * 模型认不出的字段留空由用户补，绝不能让一句话直接把一次 A* 踢出去。
 */
export async function parseSiteTask(): Promise<void> {
  const text = store.siteForm.text.trim();
  if (!text) return;
  try {
    const response = await fetch(`${API_BASE}/api/site/parse-task`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error('parse failed');
    const result = await response.json();
    const f = result.fields ?? {};
    if (f.payload_kg != null) store.siteForm.payload_kg = Number(f.payload_kg);
    if (f.aircraft) store.siteForm.aircraft = String(f.aircraft);
    if (f.mission) store.siteForm.mission = f.mission === 'crew' ? 'crew' : 'cargo';
    const byName = (name?: string | null) => {
      if (!name) return null;
      return store.bases.find(b => b.name === name) ?? null;
    };
    const s = byName(f.start_name);
    const e = byName(f.end_name);
    if (s) { store.start.lat = s.lat; store.start.lng = s.lng; }
    if (e) { store.end.lat = e.lat; store.end.lng = e.lng; }
    store.siteForm.parseSource = result.source === 'llm' ? 'llm' : 'rule';
  } catch (error) {
    console.error('任务解析请求失败:', error);
    store.siteForm.parseSource = 'rule';
  }
}

/**
 * 一次「站点规划」= 双机对照（不带 aircraft 时后端两台各出一份）。
 *
 * 与 runPlan 一样放在 store：地图上点站点、侧栏点按钮、结果卡上点"换一台"
 * 都要能发起同一个请求，各写一遍迟早会出现行为差异。
 */
export async function runSitePlan(): Promise<void> {
  if (!store.start.lat || !store.start.lng || !store.end.lat || !store.end.lng) {
    alert('请先设置起点和终点！');
    return;
  }
  store.siteLoading = true;
  try {
    const body: Record<string, any> = {
      start: { lat: Number(store.start.lat), lon: Number(store.start.lng) },
      end: { lat: Number(store.end.lat), lon: Number(store.end.lng) },
      waypoints: store.waypoints
        .filter(wp => wp.lat !== '' && wp.lng !== '')
        .map(wp => ({ lat: Number(wp.lat), lon: Number(wp.lng) })),
      date: store.targetDate,
      start_time: store.targetTime,
      // 双机对照不给统一速度：一次请求两份结果，两台各按能力包的巡航速度推时间轴
      speed: store.siteForm.aircraft ? Number(store.uavSpeed) : null,
      payload_kg: Number(store.siteForm.payload_kg),
      mission: store.siteForm.mission,
      priority: store.priority,
    };
    if (store.siteForm.aircraft) body.aircraft = store.siteForm.aircraft;
    const response = await fetch(`${API_BASE}/api/site/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) await failRequest(response, 'site plan failed');
    const result: SitePlanResult = await response.json();
    store.siteResult = result;
    const first = (result.options ?? []).find(o => o.feasible) ?? result.options?.[0];
    store.activeSiteAircraft = first ? first.aircraft : null;
  } catch (error) {
    console.error('站点规划请求失败:', error);
    store.planNotice = noticeForError(error);
    store.siteResult = null;
  } finally {
    store.siteLoading = false;
  }
}

/**
 * 把选中的中继序列落到现有航线上：起点/终点保持用户所填，中继写进 waypoints。
 *
 * 之后一切照旧 —— 「开始规划」的逐段 A* 与功能二的曲线都吃同一份 waypoints，
 * 不在这里另画一条线（那等于再造一个会出现差异的入口）。
 */
export function applySiteOption(opt: SiteOption): void {
  // legs 的最后一跳就是终点（site_id 以 OD- 开头），所以 relays 天然只含中途落地站
  const relays = (opt.legs ?? []).map(l => l.to).filter(e => e && !String(e.site_id).startsWith('OD-'));
  const last = opt.legs?.[opt.legs.length - 1]?.to;
  if (last) {
    store.end.lat = String(last.lat);
    store.end.lng = String(last.lon);
  }
  store.waypoints = relays.map(e => ({
    lat: String(e.lat), lng: String(e.lon), alt: '',
  }));
  store.activeSiteAircraft = opt.aircraft;
}
