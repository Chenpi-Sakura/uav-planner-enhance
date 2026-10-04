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
/**
 * 档位色 = 侧栏胶囊底色 = 地图上该档候选线的线色，只有一个来源。
 * 未选中档在地图上按 30~50% 透明虚线画，所以这里的色相要能在卫星图/地形图上
 * 与风险四档色序（绿→黄→橙→红）分得开。
 */
export const TIER_COLORS: Record<string, string> = {
  urgent: '#f07167',
  normal: '#0081a7',
  economy: '#6a994e',
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
  /** 右栏（AI 助手）的收起状态。与左栏各自独立：两块内容互不依赖，不该一起藏起来 */
  aiSidebarCollapsed: boolean;
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
  /**
   * AI 读完图后落的标记：图钉 / 方框 / 圆圈，对应它在正文里说的那些方位。
   *
   * 整组替换而不是追加 —— 每一批标记都对应**某一张特定的图**（某日某时刻某个要素），
   * 攒着多批只会互相矛盾。source 记下出处，让用户知道这些点是从哪张图上读出来的。
   */
  aiMarks: AiMarkSet | null;
  selectedSegmentIndex: number | null;   // 结果面板：选中的分段
  // ---- 功能二：时间窗口 ----
  timeWindowLoading: boolean;
  timeWindow: TimeWindowResult | null;   // 功能二评估结果
  forecastDays: number;                  // 7 天推演天数（0 = 不推演）
  hoveredSegmentIndex: number | null;    // 地图联动：悬停的分段
  pendingTimeWindowScan: boolean;        // 从功能一跳转功能二时自动执行一次扫描
  planNotice: PlanNotice | null;         // 路径规划失败弹窗（null = 不显示）
  /**
   * 待生成的 AI 评估报告。手动点「开始规划」算完之后填一份，AI 侧栏 watch 到就
   * 自动出一段报告。
   *
   * 为什么绕一圈 store 而不是让 runPlan 直接写侧栏：runPlan 是**规划逻辑**，
   * 侧栏是**呈现**，两者之间只该有一条单向通知。让 store 主动去改侧栏内部状态，
   * 迟早出现「规划组件长出一个只服务于侧栏的字段」。
   */
  reportRequest: { nonce: number; task: string; payload: unknown } | null;
  // ---- 功能三：多航点模式（站点自动规划） ----
  siteLoading: boolean;
  siteResult: SitePlanResult | null;      // 最近一次站点规划结果（一台一份）
  siteCatalog: SiteCatalog | null;        // 站表与机型能力包（进程内取一次）
  hoveredSiteLegIndex: number | null;     // 地图联动：悬停的中继航段
  siteForm: SiteForm;
  /**
   * AI 正在驱动一次联动（识别意图 + 规划 + 取数，整段约十几秒）。
   *
   * 左栏那些「开始规划」「规划中继站点」「扫描时间窗口」必须看它：
   * 一次多航点联动内部会连着跑站点复验与三档 A*，期间用户从左栏再点一次，
   * 后到的响应会覆盖 store，而 AI 已经按先到的结果组织回答了 ——
   * 表现就是「AI 讲的和面板上显示的不是同一份」。并发写同一份结果谁都拦不住，
   * 只能从入口禁掉第二条路径。
   */
  aiBusy: boolean;
  /**
   * 地图视野请求。AI 说「定位到曲松县」时写这里，MapContainer watch 到就移动。
   *
   * 走「命令 + nonce」而不是直接调 map 的方法：map 实例是 MapContainer 的局部变量，
   * AI 那侧拿不到。而按钮路径本来也在组件内部，两边都 watch 同一个 store 字段，
   * 就不会出现「AI 那条路自己画一遍线」。
   */
  mapFocus: { lat: number; lng: number; zoom?: number; nonce: number } | null;
  /**
   * 推演控制命令。同上：播放/暂停/前进/重置的实现在 DataChartPanel 里（它持有
   * 那个 setTimeout 循环），由它 watch 这个字段去调自己的函数，AI 不重复实现一遍。
   */
  simCommand: { action: 'play' | 'pause' | 'toggle' | 'step' | 'reset' | 'stop'; nonce: number } | null;
}

/** 功能三的参数：载重是**自变量**，站点数是它的输出，所以这里不给"站点数量"这种反因果的输入 */
export interface SiteForm {
  aircraft: string;            // '' = 未选，后端按 SITE_DEFAULT_AIRCRAFT 出一台
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
  // 注意：后端不返回顶层 elapsed_s，用时在 diagnostics.elapsed_s 里
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
  | 'rejected' | 'assess_timeout';

export interface PlanNotice {
  reason: PlanNoticeReason;
  /** 「未算出」时带上的搜索力气（"已研究 N 个路口 / 预算 M"），空串 = 后端没给 */
  effort?: string;
  /** reason = rejected / assess_timeout 时后端给的那句中文原因 */
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

/**
 * 后端以 504 回话：请求成立了，只是没在 `ASSESS_TIMEOUT_S` 内算完。
 *
 * 与「后端没响应」必须分开：后端那句写的是首次冷读要时间、重试会复用已载入的数据，
 * 照着「请确认后端服务已启动」去重启只会让缓存更冷，越试越慢。
 */
export class UpstreamTimeout extends Error {
  readonly detail: string;
  constructor(detail: string) {
    super(detail);
    this.detail = detail;
  }
}

/** 后端非 2xx 的 body 里那句写给人的话；不是 JSON 或没带 detail 就当没有 */
async function readDetail(response: Response): Promise<string> {
  return response.json()
    .then(b => (typeof b?.detail === 'string' ? b.detail : ''))
    .catch(() => '');
}

/** 非 2xx 的统一出口：400 与 504 的 detail 都是写给人看的原因，其余只算「后端没正常响应」 */
export async function failRequest(response: Response, fallback: string): Promise<never> {
  const detail = await readDetail(response);
  if (response.status === 400 && detail) throw new RequestRejected(detail);
  if (response.status === 504) throw new UpstreamTimeout(detail || fallback);
  throw new Error(fallback);
}

export const noticeForError = (error: unknown): PlanNotice => {
  if (error instanceof RequestRejected) return { reason: 'rejected', detail: error.detail };
  if (error instanceof UpstreamTimeout) return { reason: 'assess_timeout', detail: error.detail };
  return { reason: 'request_failed' };
};

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
  aiSidebarCollapsed: false,
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
  aiMarks: null,
  selectedSegmentIndex: null,
  timeWindowLoading: false,
  timeWindow: null,
  forecastDays: 7,
  hoveredSegmentIndex: null,
  pendingTimeWindowScan: false,
  planNotice: null,
  reportRequest: null,
  siteLoading: false,
  siteResult: null,
  siteCatalog: null,
  hoveredSiteLegIndex: null,
  siteForm: { aircraft: '', payload_kg: 200, mission: 'cargo', text: '', parseSource: '' },
  aiBusy: false,
  mapFocus: null,
  simCommand: null,
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
  store.aiMarks = null;
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

/** 报告请求的序号。每次 +1，AI 侧栏只认最新那一次 —— 连点两次时后一次直接盖掉前一次，
 *  没必要在对话里堆两份几乎一样的报告。 */
let reportSeq = 0;

/**
 * 一次「开始规划」= 三档几何 + 三档完整评估（/api/assess/feasibility-multi）。
 *
 * 放在 store 而不是 Sidebar 里，是因为功能二的候选列表也要能触发同一个请求：
 * 两处各写一遍迟早会出现「按钮 A 出三档、按钮 B 出一档」的行为差异。
 *
 * ``fromAI`` 用来关掉自动报告：AI 调 run_plan 时它自己会在正文里给结论，
 * 再自动糊一份报告上去等于同一件事说两遍。用户手点的那次没有「说」这个环节，
 * 所以才需要报告把结论展开。
 */
export async function runPlan(opts: { fromAI?: boolean } = {}): Promise<void> {
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

    // 手动规划完成 → 通知 AI 侧栏自动出一份评估报告。
    // 只发当前展示的那一档：报告讲的是「这条航线行不行」，三档都塞进去会变成三段
    // 互相比选的文字，而档位对比左栏的三张卡片已经做得很清楚了。
    if (!opts.fromAI) {
      store.reportRequest = { nonce: ++reportSeq, task: 'feasibility', payload: target };
    }
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
 * 两条必然一致；另起一套渲染就等于再造一个会出现差异的入口。
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

// AI 那批标记是从**某一张特定的图**上读出来的，换了日期/时刻就是另一张图了。
// 留着旧标记等于让用户对着过期的位置做判断，所以一并收掉。
// 只看时刻不看图层：坐标不随图层变，切个 TKE/EDR 不该让标记消失。
watch([() => store.targetDate, () => store.simulationDate, () => store.simulationTime], () => {
  store.aiMarks = null;
});

// ==================== 功能三 · 多航点模式（站点自动规划） ====================

/** 机型配色：油动暖色、纯电冷色，与风险档配色刻意不撞（数据线一律实线那条规矩同样适用） */
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
    // 速度框跟着机型换：段长是「航时锚点 × 巡航速度」折出来的，两个数必须同一把尺。
    // 这个同步原先住在 SitePanel 的 watch 里，而解析入口现在常驻右栏、那页可能根本没挂载。
    const pack = store.siteCatalog?.aircraft.find(a => a.key === store.siteForm.aircraft);
    if (pack) store.uavSpeed = pack.cruise_kmh;
    store.siteForm.parseSource = result.source === 'llm' ? 'llm' : 'rule';
  } catch (error) {
    console.error('任务解析请求失败:', error);
    store.siteForm.parseSource = 'rule';
  }
}

/**
 * 一次「站点规划」= 一台机型的一份答复（`aircraft` 留空时后端取默认那台）。
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
  // 旧结果上悬停的段号不能带到新链上：段的条数会变，索引会指错地方
  store.hoveredSiteLegIndex = null;
  try {
    const body: Record<string, any> = {
      start: { lat: Number(store.start.lat), lon: Number(store.start.lng) },
      end: { lat: Number(store.end.lat), lon: Number(store.end.lng) },
      waypoints: store.waypoints
        .filter(wp => wp.lat !== '' && wp.lng !== '')
        .map(wp => ({ lat: Number(wp.lat), lon: Number(wp.lng) })),
      date: store.targetDate,
      start_time: store.targetTime,
      // 不发 speed：时间轴交给该机能力包的巡航速度，与段长换算同一把尺。
      // 界面上那个速度框仍会在选机型时被填成同值，但它是给另两个标签页用的。
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
    const data = await response.json();
    store.siteResult = data;
    // 表单没预选时，后端按它自己那台默认机出。结果卡上已经不再单列机型名，
    // 就把实际飞过的那台回填成按钮的选中态，否则这条链是谁飞的界面上无从得知。
    if (!store.siteForm.aircraft && data.meta?.aircraft) {
      store.siteForm.aircraft = data.meta.aircraft;
    }
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
}

// ==================== 对话层：意图识别 -> 左侧联动 ====================

/** 后端 intent 的取值，与 backend/intent.py 的 INTENTS 一一对应 */
export type Intent =
  | 'plan' | 'time_window' | 'site' | 'weather' | 'chat';

export interface IntentFields {
  start_name?: string | null;
  end_name?: string | null;
  place_names?: string[];
  payload_kg?: number | null;
  aircraft?: string | null;
  mission?: string | null;
  date?: string | null;
  time?: string | null;
  priority?: string | null;
  forecast_days?: number | null;
}

export interface IntentResult {
  intent: Intent;
  fields: IntentFields;
  /** 缺的关键实体。非空时**不要**硬规划，先按 clarify 反问用户。 */
  missing: string[];
  clarify: string | null;
  source: 'llm' | 'rule';
  /** false = LLM 不可用、只走了窄兜底，界面上要如实说明 */
  available: boolean;
}

/** 一次规划产出的对话上下文：task 决定后端用哪个上下文构建器 */
export interface ChatTarget {
  task: 'feasibility' | 'time_window' | 'weather';
  payload: Record<string, any> | null;
  /** 这一轮是为了回答什么（用于胶囊预测与界面提示） */
  note?: string;
}

export interface WeatherResult {
  available: boolean;
  reason?: string | null;
  query_time?: string | null;
  timezone?: string | null;
  level_hpa?: number | null;
  level_note?: string | null;
  points: Record<string, any>[];
  thresholds?: Record<string, any> | null;
}

export interface SuggestChip {
  label: string;
  prompt: string;
}

/** AI 落的单个标记。三个几何键按 kind 取用，缺哪个就按点处理 */
export interface AiMark {
  kind: 'pin' | 'rect' | 'circle';
  /** 地图上显示的短名，如「西侧高值区」 */
  label: string;
  lat: number;
  lon: number;
  /** rect 的东北角 */
  lat2?: number;
  lon2?: number;
  /** circle 的半径，公里 */
  radius_km?: number;
  /** 点标记悬停时显示的补注，如读数 */
  note?: string;
  /** 配色语义：high 红 / low 绿 / neutral 蓝 */
  tone?: 'high' | 'low' | 'neutral';
}

export interface AiMarkSet {
  /** 出处，如「读图估计 · TKE 500hPa 2022-07-01 08:00」 */
  source: string;
  marks: AiMark[];
}

/** 一句话 -> 模式 + 实体。裁判在模型，规则只在模型不可用时兜一层窄判断。 */
export async function detectIntent(text: string): Promise<IntentResult> {
  const response = await fetch(`${API_BASE}/api/assess/intent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      context: {
        sidebar_tab: store.sidebarTab,
        has_result: !!(store.feasibility || store.timeWindow || store.siteResult),
        date: store.targetDate,
        time: store.targetTime,
      },
    }),
  });
  if (!response.ok) throw new Error(`intent ${response.status}`);
  return await response.json();
}

/**
 * 地名 -> 坐标。
 *
 * 之前只认 store.bases 那 4 个城市，于是「曲松县」「加查县」这类站表里明明有的
 * 地点认不出来。这里把 220 个站点一起建索引；长名优先（「林芝米林机场」要先于
 * 「林芝」被吃掉，否则短名会把长名切断）。
 */
export function resolvePlace(name?: string | null): { lat: number; lng: number } | null {
  if (!name) return null;
  const target = String(name).trim();
  if (!target) return null;

  const candidates: { name: string; lat: number; lng: number }[] = [
    ...store.bases.map(b => ({ name: b.name, lat: Number(b.lat), lng: Number(b.lng) })),
    ...(store.siteCatalog?.sites ?? [])
      .filter(s => s.lat != null && s.lon != null)
      .map(s => ({ name: s.name, lat: Number(s.lat), lng: Number(s.lon) })),
  ];
  if (!candidates.length) return null;

  // 精确优先；再退到「输入里包含站点名」（用户常只说「米林」而不说「米林县」）
  const exact = candidates.find(c => c.name === target);
  if (exact) return { lat: exact.lat, lng: exact.lng };
  const partial = candidates.find(c => c.name.includes(target) || target.includes(c.name));
  return partial ? { lat: partial.lat, lng: partial.lng } : null;
}

/** 按地点与时刻取真实气象实况（TKE / EDR / 风温湿） */
export async function queryWeather(
  points: { name: string; lat: number; lng: number }[],
  date = store.targetDate,
  time = store.targetTime,
): Promise<WeatherResult> {
  // 全站经度字段有 lng / lon 两种写法：store 的 Point 用 lng，后端 WeatherPoint 用 lon。
  // 这里必须显式改名 —— 后端 pydantic 会把多余的 lng 静静丢掉，lon 留 null，
  // 于是所有点都被过滤掉，接口回 available:false，看起来像「没数据」而不是「字段错了」。
  const body = points.map(p => ({ name: p.name, lat: p.lat, lon: p.lng }));
  const response = await fetch(`${API_BASE}/api/weather/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ points: body, date, time }),
  });
  if (!response.ok) throw new Error(`weather ${response.status}`);
  return await response.json();
}

/** 后续建议胶囊：站在用户视角预测下一步 */
export async function suggestChips(body: {
  task: string;
  payload: Record<string, any> | null;
  last_assistant: string;
  recent: string[];
  state: Record<string, any>;
}): Promise<{ chips: SuggestChip[]; source: 'llm' | 'rule' }> {
  const response = await fetch(`${API_BASE}/api/assess/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) return { chips: [], source: 'rule' };
  return await response.json();
}

/**
 * 一次「扫描时间窗口」。从 TimeWindowPanel 搬进 store：
 * AI 联动要 await 到结果才能据此作答，而面板里的局部函数外部拿不到。
 * 口径与搬之前逐字一致（同一份请求体、同一套错误处理）。
 */
export async function runTimeWindowScan(): Promise<void> {
  if (!store.start.lat || !store.start.lng || !store.end.lat || !store.end.lng) {
    alert('请先设置起点和终点！');
    return;
  }
  store.timeWindowLoading = true;
  try {
    const response = await fetch(`${API_BASE}/api/assess/time-window`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start: { lat: Number(store.start.lat), lon: Number(store.start.lng) },
        end: { lat: Number(store.end.lat), lon: Number(store.end.lng) },
        waypoints: store.waypoints
          .filter(wp => wp.lat !== '' && wp.lng !== '')
          .map(wp => ({ lat: Number(wp.lat), lon: Number(wp.lng) })),
        date: store.targetDate,
        start_time: store.targetTime,
        speed: Number(store.uavSpeed),
        forecast_days: store.forecastDays,
      }),
    });
    if (!response.ok) await failRequest(response, '时间窗口扫描失败');
    store.timeWindow = await response.json();
  } catch (error) {
    console.error('时间窗口扫描请求失败:', error);
    store.planNotice = noticeForError(error);
  } finally {
    store.timeWindowLoading = false;
  }
}

/** 把实体写进左侧表单。只写用户明确说过的字段，没说的绝不覆盖现值。 */
function fillFromFields(fields: IntentFields): { ok: boolean; unknown: string[] } {
  const unknown: string[] = [];

  const s = resolvePlace(fields.start_name);
  if (s) { store.start.lat = String(s.lat); store.start.lng = String(s.lng); }
  else if (fields.start_name) unknown.push(fields.start_name);

  const e = resolvePlace(fields.end_name);
  if (e) { store.end.lat = String(e.lat); store.end.lng = String(e.lng); }
  else if (fields.end_name) unknown.push(fields.end_name);

  if (fields.date) store.targetDate = fields.date;
  if (fields.time) store.targetTime = fields.time;
  if (fields.priority && ['urgent', 'normal', 'economy'].includes(fields.priority)) {
    store.priority = fields.priority as StoreState['priority'];
  }
  if (fields.forecast_days != null) store.forecastDays = fields.forecast_days;

  if (fields.payload_kg != null) store.siteForm.payload_kg = Number(fields.payload_kg);
  if (fields.mission === 'cargo' || fields.mission === 'crew') {
    store.siteForm.mission = fields.mission;
  }
  if (fields.aircraft) {
    store.siteForm.aircraft = fields.aircraft;
    // 速度框跟着机型换：段长与时间轴必须同一把尺，否则距离按 100 说飞得完、
    // 时刻表按 60 说飞多久。
    const pack = store.siteCatalog?.aircraft.find(a => a.key === fields.aircraft);
    if (pack) store.uavSpeed = pack.cruise_kmh;
  }
  // 解析来源如实标出，界面上要让用户看见这句话是模型认的还是兜底猜的
  store.siteForm.parseSource = 'llm';

  // 途经点：多目的地时先按识别到的中间地名填上（无坐标的先跳过）
  if (fields.place_names?.length) {
    const wps = fields.place_names
      .map(n => resolvePlace(n))
      .filter((p): p is { lat: number; lng: number } => !!p)
      .map(p => ({ lat: String(p.lat), lng: String(p.lng), alt: '' }));
    if (wps.length) store.waypoints = wps;
  }
  return { ok: true, unknown };
}

/**
 * **对话联动的唯一入口。**
 *
 * AI 路径与面板上的手��按钮都收敛到这里：切页签、回填表单、发起规划只此一处。
 * 以前对话与面板各写一遍联动，两边迟早长出行为差异（换端点、切档、途经点写入
 * 这些细节只要漏一处就表现为「AI 说填好了、面板上是另一个」）。
 *
 * 规划是真发出去的（不做二次确认）：所以本函数 await 到结果，把**新**结果
 * 作为本轮问答的上下文返回 —— 否则 AI 会对着上一次的结果说话。
 */
export async function applyIntent(result: IntentResult): Promise<ChatTarget> {
  const { intent, fields } = result;

  if (intent === 'chat') {
    // 就当前结果追问：原样用当前页的结果，不动任何输入
    if (store.sidebarTab === 'timeWindow' && store.timeWindow) {
      return { task: 'time_window', payload: store.timeWindow as any };
    }
    return { task: 'feasibility', payload: store.feasibility as any };
  }

  if (intent === 'weather') {
    // 气象问答不切页签。要问的地点：place_names -> 起终点 -> 都没有就取当前起点
    const names = [
      ...(fields.place_names ?? []),
      ...(fields.start_name ? [fields.start_name] : []),
    ];
    let points = names
      .map(n => ({ name: n, ...(resolvePlace(n) ?? {}) }))
      .filter(p => p.lat != null && p.lng != null) as { name: string; lat: number; lng: number }[];

    if (!points.length && store.start.lat && store.start.lng) {
      points = [{
        name: store.bases.find(b => b.name === fields.start_name)?.name ?? '起点',
        lat: Number(store.start.lat),
        lng: Number(store.start.lng),
      }];
    }
    if (!points.length) return { task: 'weather', payload: null };
    const payload = await queryWeather(points, store.targetDate, store.targetTime);
    return { task: 'weather', payload: payload as any };
  }

  // 以下三个都会改规划输入并真的发一次计算
  fillFromFields(fields);

  if (intent === 'time_window') {
    store.sidebarTab = 'timeWindow';
    store.pendingTimeWindowScan = false;   // 自己扫，不等面板挂载后再扫一遍
    await runTimeWindowScan();
    return { task: 'time_window', payload: store.timeWindow as any };
  }

  if (intent === 'site') {
    store.sidebarTab = 'site';
    await runSitePlan();
    // 规划出中继链后自动走完「按此起飞重新规划」那一步：填入途经点再跑三档 A*。
    // 拿不到可用方案时停在这里 —— 硬把没算出来的链写进航线等于编一条线。
    const opt = (store.siteResult?.options ?? []).find(o => o.feasible)
      ?? store.siteResult?.options?.[0];
    if (!opt) return { task: 'feasibility', payload: store.feasibility as any };
    applySiteOption(opt);
    store.sidebarTab = 'plan';
    await runPlan();
    return { task: 'feasibility', payload: store.feasibility as any };
  }

  store.sidebarTab = 'plan';
  await runPlan();
  return { task: 'feasibility', payload: store.feasibility as any };
}
