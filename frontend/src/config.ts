/** 后端 API 基础地址：开发时由 Vite `server.proxy` 转发，公网时由后端直接托管页面，
 *  两种场景浏览器都只请求本站 `/api/*`，因此保持同源、留空。 */
export const API_BASE = ''

/**
 * 湍流量的显示刻度。数值取自后端 `backend/config.py` 的同一轮标定（2026-09-23，
 * 口径改为申报书的脉动方差），前端只负责画出来。
 *
 * 集中一处是为了避免「图例写 1.5+、色带按别的截、柱状条按 1.0 满量程」这种
 * 各写一套的漂移；后端重标定阈值时必须同步改这里。
 */
export const TURB = {
  /** 脉动方差的滑动窗口半径（小时），与后端 TURB_WINDOW_HALF_HOURS 一致 */
  windowHalfHours: 6,
  tkeUnit: 'm²/s²',
  /** 硬阈值（不可飞）：TKE >= 此值，同时作为风险条的满量程 */
  tkeUnflyable: 26,
  /** 覆盖层色带上沿，比硬阈值略高，让满红可辨 */
  tkeColorMax: 30,

  /** EDR 是 ε 的立方根，单位 m^(2/3)/s；旧文案写的 m²/s³ 是 ε 的单位 */
  edrUnit: 'm^(2/3)/s',
  edrUnflyable: 0.376,
  edrColorMax: 0.4,
  /** 与温度/湿度共用一根轴的折线图里，EDR 只有 0.x，按此倍数放大才看得见 */
  edrChartFactor: 250,
} as const

/** 图例刻度：0 → 中值 → 色带上沿，与后端 contourf 的 vmin/vmax 一致 */
export const turbTicks = (max: number) => ({
  min: '0.0',
  mid: String(max / 2),
  max: `${max}+`,
})

/** 湍流量读数：TKE 新口径是几十量级，EDR 仍是 0.x，两者小数位不同。 */
export const fmtTke = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(2)
export const fmtEdr = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(4)
