/**
 * 决策平台前端类型定义。
 *
 * 与后端 `backend/schemas.py` 的 Pydantic 模型一一对应；命名保持 snake_case，
 * 便于直接消费接口返回的 JSON，避免中间层转换带来的字段漂移。
 */

/** 风险等级（与后端 risk.py 的分级口径一致） */
export type RiskLevel = '低风险' | '中风险' | '高风险' | '不可飞';

/** 时间窗口：24h 内的一个安全时段（北京时间 HH:MM） */
export interface TimeWindowWindow {
  start: string;
  end: string;
  level: RiskLevel;
  max_tke: number;
  max_edr: number;
}

/** 推荐窗口（含起飞/降落时刻，北京时间 ISO） */
export interface RecommendedWindow {
  takeoff: string;
  window: string[];
  landing: string;
  level: RiskLevel;
  max_tke: number | null;
  max_edr: number | null;
}

/** 湍流强度变化曲线（起飞前 1h 至降落后 1h） */
export interface TurbulenceCurve {
  times: string[];
  max_tke: number[];
  max_edr: number[];
  mean_tke: number[];
}

/** 单个扫描槽位的风险分析结果 */
export interface TimeWindowSlot {
  time: string;
  max_tke: number | null;
  max_edr: number | null;
  level: RiskLevel;
  safe: boolean;
}

/** 7 天推演中的单日结果 */
export interface ForecastDay {
  date: string;
  has_window: boolean;
  best_window: string[] | null;
  level: RiskLevel | null;
  max_tke: number | null;
  safe_slot_count: number;
}

/** 7 天推演汇总 */
export interface ForecastInfo {
  enabled: boolean;
  days: ForecastDay[];
  first_available_date: string | null;
}

/** 其他预警提示（降水/结冰/低温/大风） */
export interface TimeWindowWarning {
  type: string;
  level: string;
  text: string;
  value: number | null;
}

/** Top-K 候选之一的真实航线（按该候选自己的起飞时刻规划，非走廊） */
export interface TimeWindowRoute {
  takeoff: string;
  window: string[];
  /** 粗排（固定走廊查表）看到的风险档 */
  scan_level: RiskLevel | '';
  scan_max_tke: number | null;
  /** 该时刻真实航线的整体风险档；不可飞时为空 */
  overall_level: string;
  feasible: boolean;
  reason: string | null;
  path: { lat: number; lon: number }[];
  total_distance: number | null;
  detour_ratio: number | null;
  flight_time_h: number | null;
  max_tke: number | null;
  max_edr: number | null;
  moderate_ratio: number | null;
  high_ratio: number | null;
  /** 功能二只按「平衡」档规划，切档回功能一 */
  priority: string;
}

/** 功能二：时间窗口规划响应 */
export interface TimeWindowResult {
  date: string;
  has_window: boolean;
  recommended: RecommendedWindow | null;
  /** 细排冠军（真实航线口径）；老响应里没有时回退到 recommended */
  recommended_final?: RecommendedWindow | null;
  routes?: TimeWindowRoute[];
  curve: TurbulenceCurve;
  backup_windows: TimeWindowWindow[];
  warnings: TimeWindowWarning[];
  slot_analysis: TimeWindowSlot[];
  forecast: ForecastInfo;
  reason: string | null;
  meta: {
    data_label_is_utc?: boolean;
    tz_offset_hours?: number;
    scan_step_hours?: number;
    geometry_fallback?: boolean;
    geometry_blocked?: boolean;
    window_count?: number;
    [key: string]: unknown;
  };
}

/** 功能二「不可飞原因」文案映射 */
export const TIME_WINDOW_REASONS: Record<string, string> = {
  no_safe_slot: '当日及推演期内均无安全时段',
  route_permanently_blocked: '航线被地形或数据缺失区永久封死',
  data_coverage_insufficient: '起降点超出气象数据覆盖范围',
};
