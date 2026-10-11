/**
 * 工具执行器：后端 Agent 决定「调什么」，这里负责「真的做」。
 *
 * 纪律：**每个工具都必须复用界面上已有的那条实现**，不许另写一套。
 * 手点「开始规划」和 AI 调 run_plan 要走同一个 ``runPlan()``；手点播放推演和
 * AI 调 play_simulation 要落到 DataChartPanel 里的同一个函数。两处各写一遍，
 * 迟早长出「AI 说改好了、面板上是另一个」的行为差异。
 *
 * 工具名与参数必须与 ``backend/tools.py`` 一一对应 —— 那份是模型可见的契约。
 */

import {
  store, runPlan, runSitePlan, runTimeWindowScan, queryWeather, resolvePlace,
  activateRouteTier,
} from './store';
import type { AiMark, ChatTarget, SiteCatalogSite, StoreState } from './store';
import { API_BASE } from './config';

export interface ToolResult {
  tool: string;
  ok: boolean;
  /** 轨迹里显示的那一行，如「已开启 TKE 叠加层」 */
  label: string;
  /** 补充说明（数字、失败原因） */
  detail?: string;
  /** 看图工具产出的图（data URL）。既喂给模型下一轮，也在对话里贴给用户看 */
  image?: string;
  /**
   * 回灌给模型的真实结果。
   * **只放工具真的拿到的数据** —— 模型只能引用这里的东西，所以这一栏为空时
   * 模型就该说「查不到」，而不是自己编。
   */
  data?: unknown;
}

let nonce = 0;
const nextNonce = () => ++nonce;

/**
 * 用户这次随消息发来的图片（data URL）。
 *
 * 放在模块级而不是 store：它是**一次性的输入**，不是界面状态 ——
 * 存进 store 会让它跟着清空对话、切页签一起被重置，而附件的生命周期只到本轮结束。
 * AiSidebar 在发送时写入，发送完即清。
 */
let attachedImages: string[] = [];
export const setAttachedImages = (imgs: string[]) => { attachedImages = imgs; };
export const getAttachedImages = (): string[] => attachedImages;

const OK = (tool: string, label: string, detail?: string, data?: unknown): ToolResult =>
  ({ tool, ok: true, label, detail, data });
/** 失败也要能带 data：「查了，确实没有」和「没查成」是两回事，
 *  模型只有拿到前者才会如实说「附近没有备降点」而不是含糊过去。 */
const FAIL = (tool: string, label: string, detail?: string, data?: unknown): ToolResult =>
  ({ tool, ok: false, label, detail, data });

/** 键名就是界面上那六个复选框的 id（见 Sidebar.vue 的 overlayOptions），一个都不能多。 */
const OVERLAY_NAMES: Record<string, string> = {
  tke: 'TKE（湍动能）', edr: 'EDR（湍动耗散率）', flow: '综合风速',
  temp: '温度', humidity: '相对湿度', qvapor: '水汽混合比',
};
/** 同理，与 LayerSelector 那三个按钮一一对应 */
const BASE_MAP_NAMES: Record<string, string> = {
  street: '街道图', satellite: '卫星图', terrain: '地形图',
};
const TAB_NAMES: Record<string, string> = {
  plan: '路径规划', timeWindow: '时间窗口', site: '多航点',
};
const PRIORITY_NAMES: Record<string, string> = {
  urgent: '紧急', normal: '平衡', economy: '安全',
};
const MISSION_NAMES: Record<string, string> = { cargo: '运货', crew: '载人' };
const SIM_NAMES: Record<string, string> = {
  play: '播放推演', pause: '暂停推演', toggle: '播放/暂停',
  step: '前进一帧', reset: '重置推演', stop: '停止推演',
};
const AIRCRAFT_NAMES: Record<string, string> = { td550: 'TD550（油动）', v2000cg: 'V2000CG（纯电）' };

/** 给一个坐标找能读出来的名字：先匹配已有城市基点，否则取站表里最近的（20 km 内）。 */
function placeName(lat: number, lng: number): string | null {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const byBase = store.bases.find(b => Number(b.lat) === lat && Number(b.lng) === lng);
  if (byBase) return byBase.name;
  const R = 6371, rad = (d: number) => (d * Math.PI) / 180;
  let best: { name: string; d: number } | null = null;
  for (const s of store.siteCatalog?.sites ?? []) {
    const lat1 = rad(lat), lat2 = rad(Number(s.lat));
    const dLat = lat2 - lat1, dLon = rad(Number(s.lon) - lng);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    const d = 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
    if (d <= 20 && (!best || d < best.d)) best = { name: s.name, d };
  }
  return best ? best.name : null;
}
const currentStartName = () => placeName(Number(store.start.lat), Number(store.start.lng));
const currentEndName = () => placeName(Number(store.end.lat), Number(store.end.lng));

/** 界面状态快照：只发工具决策真正需要的字段，不发整份 store。 */
export function buildAgentState(): Record<string, unknown> {
  return {
    tab: store.sidebarTab,
    overlays: [...store.overlays],
    base_map: store.baseMap,
    // 端点**必须带地名**。只给坐标的话，模型分不清 (29.66, 94.36) 是林芝还是昌都，
    // 于是会拿现存的林芝当昌都去规划，再把林芝的结论说成昌都的 ——
    // 数字是真的、结论完全错，比编数字更难被发现。
    start: { name: currentStartName() ?? '（未命名）', lat: Number(store.start.lat), lng: Number(store.start.lng) },
    end: { name: currentEndName() ?? '（未命名）', lat: Number(store.end.lat), lng: Number(store.end.lng) },
    waypoints: store.waypoints
      .filter(w => w.lat !== '' && w.lng !== '')
      .map((w, i) => ({ i: i + 1, lat: Number(w.lat), lng: Number(w.lng) })),
    date: store.targetDate,
    time: store.targetTime,
    speed: store.uavSpeed,
    priority: store.priority,
    payload_kg: store.siteForm.payload_kg,
    mission: store.siteForm.mission,
    aircraft: store.siteForm.aircraft || '（后端默认）',
    forecast_days: store.forecastDays,
    has_plan: !!store.routeOptions,
    has_site: !!store.siteResult,
    has_time_window: !!store.timeWindow,
    planned_km: store.feasibility?.distance?.route_km ?? null,
    planned_hours: store.feasibility?.distance?.flight_time_h ?? null,
    risk: store.feasibility?.risk?.overall ?? null,
  };
}

/**
 * 地名解析失败时该怎么说。**「站表没加载成功」和「站表里没这个地点」必须分开。**
 *
 * 合在一起会制造一句事实错误的断言：站表没到时 resolvePlace 必然返回 null，
 * 于是工具回「站表里没有『曲松县』」——而曲松县**确实在**站表里（CT-84）。
 * 用户看到这句会以为那片没有备降点，照着改任务区域就改错了方向。
 * 工具宁可说「我不知道」，也不能编一个能推翻用户决定的结论。
 */
function placeMiss(name: string): string {
  if (!store.siteCatalog) {
    return '站表还没加载成功（网络或后端未就绪），**无法判断这个地名是否存在**；'
      + '不要据此断定「这里没有」，也不要改起降点或任务区域，先让用户重试一次';
  }
  return `站表里没有「${name}」`;
}

/** 站表本地检索。站表已经在 store 里（220 条），不必再发一次请求。 */
type SiteHit = { site: SiteCatalogSite; km: number | null };

function searchSitesLocal(query: string, nearName?: string, radiusKm?: number): SiteCatalogSite[] {
  const kw = (query || '').trim();
  let pool = (store.siteCatalog?.sites ?? []).filter(s => s.usable !== false);
  if (kw) {
    pool = pool.filter(s => s.name.includes(kw) || s.type?.includes(kw) || s.region?.includes(kw));
  }

  // 给了参照点和半径就算距离：用户脑子里的「附近 50 km」不是地图上的 zoom，
  // 也不该让模型自己换算 —— 它换算就会错。
  let origin: { lat: number; lng: number } | null = null;
  if (radiusKm && nearName) origin = resolvePlace(nearName);

  const hits: SiteHit[] = pool.map(s => {
    if (!origin) return { site: s, km: null };
    const R = 6371;
    const rad = (d: number) => (d * Math.PI) / 180;
    const lat1 = rad(origin.lat), lat2 = rad(Number(s.lat));
    const dLat = lat2 - lat1, dLon = rad(Number(s.lon) - origin.lng);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return { site: s, km: 2 * R * Math.asin(Math.min(1, Math.sqrt(a))) };
  });

  const kept = origin
    ? hits.filter(h => (h.km ?? Infinity) <= radiusKm!).sort((a, b) => (a.km ?? 0) - (b.km ?? 0))
    : hits;
  return kept.slice(0, 12).map(h => ({ ...h.site, __km: h.km == null ? undefined : Math.round(h.km) } as SiteCatalogSite));
}

/** 一次工具调用。慢的（规划类）await 完再回结果，快的同步改完 store。 */
export async function invokeTool(tool: string, args: Record<string, any> = {}): Promise<ToolResult> {
  switch (tool) {
    // ---------------- 界面操作 ----------------
    case 'toggle_overlay': {
      const key = args.layer as string;
      // 只认界面上有复选框的那六个键。放一个陌生键过去等于造出幽灵图层：
      // 图上画出来了、复选框却没勾上，用户关不掉，再手动勾同一层还叠两张热图。
      const name = OVERLAY_NAMES[key];
      if (!name) return FAIL(tool, '图层名不认得', `界面里没有「${key || '（空）'}」这个叠加层`);
      const on = args.on !== false;
      const has = store.overlays.includes(key);
      if (on && !has) store.overlays.push(key);
      if (!on && has) store.overlays.splice(store.overlays.indexOf(key), 1);
      return OK(tool, `${on ? '已开启' : '已关闭'}${name}叠加层`,
        `${store.overlays.length} 个图层当前开启`, { overlays: [...store.overlays] });
    }

    case 'set_base_map': {
      const key = args.map as string;
      const name = BASE_MAP_NAMES[key];
      if (!name) return FAIL(tool, '底图不认得', '界面里只有街道图、卫星图、地形图三种底图');
      store.baseMap = key as StoreState['baseMap'];
      return OK(tool, `底图已切到${name}`, undefined, { base_map: key });
    }

    case 'focus_region': {
      let lat = args.lat as number | undefined;
      let lng = args.lon as number | undefined;
      let name = (args.name as string) || '';
      if ((lat == null || lng == null) && name) {
        const p = resolvePlace(name);
        if (p) { lat = p.lat; lng = p.lng; }
      }
      if (lat == null || lng == null) {
        return FAIL(tool, '没能定位到该地点', name ? placeMiss(name) : '缺少坐标或地名');
      }
      // 给了 km 就折算成缩放层级：km 是用户脑子里的距离，zoom 不是
      const zoom = args.zoom ?? (args.km ? Math.max(5, Math.min(13, Math.round(12 - Math.log2(args.km)))) : 9);
      store.mapFocus = { lat, lng, zoom, nonce: nextNonce() };
      const label = name || `${lat.toFixed(3)}, ${lng.toFixed(3)}`;
      return OK(tool, `地图已定位到${label}`, args.km ? `半径约 ${args.km} km` : undefined,
        { name: label, lat, lng, zoom });
    }

    /**
     * 把 AI 正文里说过的那些方位（西侧高值区、东北角平稳…）真正画到地图上。
     *
     * 这里做三件校验，缺一不可 —— 端点在公网无鉴权，模型给的参数不能直接落到 Leaflet 上：
     *   1. 坐标取值域（纬度 ±90、经度 ±180），越界的整条丢掉而不是夹逼：夹出来的是假位置，
     *      留在图上比不画更糟。
     *   2. rect 的对角要真的存在且和 (lat,lon) 构成非退化矩形，否则降级成点。
     *   3. circle 半径封顶，省得一个 1e9 km 把整个视野拉没。
     * 全部不合法就整组失败并把原因回给模型 —— 宁可让它重说一次，也不要在图上画假东西。
     */
    case 'mark_regions': {
      const raw = Array.isArray(args.marks) ? (args.marks as any[]) : [];
      if (!raw.length) return FAIL(tool, '没有收到任何标记', 'marks 是空的');

      const inRange = (v: any, lim: number) =>
        typeof v === 'number' && Number.isFinite(v) && v >= -lim && v <= lim;
      const marks: AiMark[] = [];
      const dropped: string[] = [];

      for (const m of raw.slice(0, 8)) {
        const label = String(m?.label ?? '').trim().slice(0, 20) || '未命名';
        if (!inRange(m?.lat, 90) || !inRange(m?.lon, 180)) {
          dropped.push(`${label}（坐标越界）`);
          continue;
        }
        const kind: AiMark['kind'] =
          m?.kind === 'rect' || m?.kind === 'circle' ? m.kind : 'pin';
        const tone = m?.tone === 'high' || m?.tone === 'low' ? m.tone : 'neutral';
        const mark: AiMark = {
          kind, label,
          lat: m.lat as number,
          lon: m.lon as number,
          note: m?.note ? String(m.note).slice(0, 60) : undefined,
          tone,
        };
        if (kind === 'rect') {
          // 对角缺失或退化（与主点重合）就退回成图钉：画一个看不见的方框等于没画
          if (inRange(m?.lat2, 90) && inRange(m?.lon2, 180)
              && (m.lat2 !== m.lat || m.lon2 !== m.lon)) {
            mark.lat2 = m.lat2 as number;
            mark.lon2 = m.lon2 as number;
          } else {
            dropped.push(`${label}（rect 缺对角，已降级为图钉）`);
            mark.kind = 'pin';
          }
        } else if (kind === 'circle') {
          const r = Number(m?.radius_km);
          mark.radius_km = Number.isFinite(r) && r > 0 ? Math.min(200, Math.max(0.5, r)) : 10;
        }
        marks.push(mark);
      }

      if (!marks.length) {
        return FAIL(tool, '这些标记一个都画不出来',
          dropped.length ? dropped.join('；') : '坐标格式不对');
      }

      const source = String(args.source ?? '').trim().slice(0, 80) || 'AI 读图估计';
      store.aiMarks = { source, marks };

      const kindText = { pin: '图钉', rect: '方框', circle: '圆圈' };
      const parts = marks.map(m => `${kindText[m.kind]}「${m.label}」`);
      const detail = dropped.length ? `已丢弃 ${dropped.length} 个：${dropped.join('；')}` : source;
      return OK(tool, `已在地图上标出 ${marks.length} 处`, `${parts.join('、')}${detail ? ' · ' + detail : ''}`,
        { count: marks.length, source, labels: marks.map(m => m.label) });
    }

    case 'add_waypoint': {
      const name = (args.name as string) || '';
      const p = resolvePlace(name);
      if (!p) return FAIL(tool, '没能把这个地点加为途经点', name ? placeMiss(name) : '没给地名');
      const idx = args.index as number | undefined;
      const wp = { lat: String(p.lat), lng: String(p.lng), alt: '' };
      if (idx && idx >= 1 && idx <= store.waypoints.length) store.waypoints.splice(idx - 1, 0, wp);
      else store.waypoints.push(wp);
      return OK(tool, `已添加途经点：${name}`, `第 ${store.waypoints.length} 个`,
        { waypoints: store.waypoints.length, last: name });
    }

    case 'remove_waypoint': {
      const idx = args.index as number;
      if (!idx || idx < 1 || idx > store.waypoints.length) {
        return FAIL(tool, '删不了这个途经点', `当前只有 ${store.waypoints.length} 个途经点`);
      }
      store.waypoints.splice(idx - 1, 1);
      return OK(tool, `已删除第 ${idx} 个途经点`, `剩余 ${store.waypoints.length} 个`,
        { waypoints: store.waypoints.length });
    }

    case 'update_waypoint': {
      const idx = args.index as number;
      const name = (args.name as string) || '';
      if (!idx || idx < 1 || idx > store.waypoints.length) {
        return FAIL(tool, '改不了这个途经点', `当前只有 ${store.waypoints.length} 个途经点`);
      }
      const p = resolvePlace(name);
      if (!p) return FAIL(tool, '没能改成这个地点', name ? placeMiss(name) : '没给地名');
      store.waypoints[idx - 1] = { lat: String(p.lat), lng: String(p.lng), alt: '' };
      return OK(tool, `第 ${idx} 个途经点已改为${name}`, undefined, { index: idx, name });
    }

    case 'set_endpoints': {
      const idx = args.index as number;
      const name = (args.name as string) || '';
      const p = (args.lat != null && args.lon != null)
        ? { lat: args.lat as number, lng: args.lon as number }
        : resolvePlace(name);
      if (!p) return FAIL(tool, '没能设置这个端点', name ? placeMiss(name) : '缺少坐标或地名');
      if (idx === 0) { store.start.lat = String(p.lat); store.start.lng = String(p.lng); }
      else { store.end.lat = String(p.lat); store.end.lng = String(p.lng); }
      const which = idx === 0 ? '起点' : '终点';
      return OK(tool, `${which}已设为${name || `${p.lat}, ${p.lng}`}`, undefined, { role: which, name });
    }

    case 'switch_tab': {
      const tab = args.tab as string;
      store.sidebarTab = tab as any;
      // 面板靠这个标志在挂载后自己扫一次；AI 直接扫的话就别再让它扫一遍
      store.pendingTimeWindowScan = false;
      return OK(tool, `已切到「${TAB_NAMES[tab] ?? tab}」页`, undefined, { tab });
    }

    case 'set_time': {
      const parts: string[] = [];
      if (args.date) { store.targetDate = args.date; parts.push(args.date); }
      if (args.time) { store.targetTime = args.time; parts.push(args.time); }
      if (!parts.length) return FAIL(tool, '没给日期或时刻');
      return OK(tool, `时间已设为 ${parts.join(' ')}`, undefined, { date: store.targetDate, time: store.targetTime });
    }

    case 'set_priority': {
      const p = args.priority as string;
      // 三档是「一次规划全算出来」的（feasibility-multi 一次返回三档几何+评估），
      // 所以切档**不需要重跑**：直接从已有结果里切过去即可。
      // 让模型为了切个档再调一次 run_plan，就是白等十几秒拿回一模一样的数字 ——
      // 而且重跑完三档几何通常还完全一样（all_identical），等于白烧一次 A*。
      if (activateRouteTier(p)) {
        const f = store.feasibility;
        const d = f?.distance;
        return OK(tool, `方案档位已切到「${PRIORITY_NAMES[p] ?? p}」`,
          '三档在同一次规划里已全部算出，直接切换，未重跑',
          {
            priority: p, feasible: f?.feasible ?? null, verdict: f?.verdict ?? null,
            route_km: d?.route_km ?? null, flight_time_h: d?.flight_time_h ?? null,
            detour_ratio: d?.detour_ratio ?? null,
            risk: f?.risk?.overall ?? null,
            max_tke: f?.risk?.stats?.max_tke ?? null,
            segments: (f?.risk?.segments ?? []).map(sg => ({
              from: sg.from_name, to: sg.to_name,
              distance_km: sg.distance_km, level: sg.level,
              max_tke: sg.max_tke, max_edr: sg.max_edr,
            })),
          });
      }
      // 没有可切换的结果（还没规划过，或这一档没算出来）才退而求其次改输入
      store.priority = p as any;
      return OK(tool, `方案档位已设为「${PRIORITY_NAMES[p] ?? p}」`,
        '当前没有已算出的结果可切换，需要重新规划才会生效', { priority: p });
    }

    case 'set_payload_kg': {
      const kg = Number(args.payload_kg);
      if (!Number.isFinite(kg) || kg <= 0) return FAIL(tool, '载重不合法', String(args.payload_kg));
      store.siteForm.payload_kg = kg;
      return OK(tool, `载重已设为 ${kg} 公斤`, undefined, { payload_kg: kg });
    }

    case 'set_aircraft': {
      const a = args.aircraft as string;
      store.siteForm.aircraft = a;
      const pack = store.siteCatalog?.aircraft.find(x => x.key === a);
      if (pack) store.uavSpeed = pack.cruise_kmh;
      return OK(tool, `机型已切到 ${AIRCRAFT_NAMES[a] ?? a}`,
        pack ? `巡航速度同步为 ${pack.cruise_kmh} km/h` : undefined, { aircraft: a });
    }

    case 'set_mission': {
      const m = args.mission as string;
      store.siteForm.mission = m as any;
      return OK(tool, `任务类型已设为${MISSION_NAMES[m] ?? m}`, undefined, { mission: m });
    }

    case 'set_speed': {
      const v = Number(args.speed);
      if (!Number.isFinite(v) || v <= 0) return FAIL(tool, '速度不合法', String(args.speed));
      store.uavSpeed = v;
      return OK(tool, `巡航速度已设为 ${v} km/h`, undefined, { speed: v });
    }

    case 'set_forecast_days': {
      const n = Number(args.forecast_days);
      if (!Number.isFinite(n) || n < 0 || n > 15) return FAIL(tool, '推演天数不合法', String(args.forecast_days));
      store.forecastDays = n;
      return OK(tool, n === 0 ? '已改为只看当天' : `推演天数已设为 ${n} 天`, undefined, { forecast_days: n });
    }

    case 'toggle_chart': {
      store.chartCollapsed = !store.chartCollapsed;
      return OK(tool, store.chartCollapsed ? '已收起气象剖面图' : '已展开气象剖面图',
        undefined, { chart_collapsed: store.chartCollapsed });
    }

    case 'play_simulation': {
      const a = args.action as 'play' | 'pause' | 'toggle' | 'step' | 'reset' | 'stop';
      if (a !== 'pause' && a !== 'toggle' && !store.plannedPath.length) {
        return FAIL(tool, '还没有航线可以推演', '先跑一次规划');
      }
      if (args.speed) store.simulationSpeed = Number(args.speed);
      store.simCommand = { action: a, nonce: nextNonce() };
      return OK(tool, SIM_NAMES[a] ?? a,
        args.speed ? `步长 ${(Number(args.speed) / 1000).toFixed(1)} 秒/步` : undefined, { action: a });
    }

    case 'clear_route': {
      store.plannedPath = [];
      store.routeOptions = null;
      store.feasibility = null;
      store.siteResult = null;
      store.timeWindow = null;
      return OK(tool, '已清空航线与规划结果', undefined, {});
    }

    // ---------------- 计算（复用界面上那套）----------------
    case 'run_plan': {
      if (!store.start.lat || !store.end.lat) return FAIL(tool, '起终点没设全', '无法规划');
      // fromAI：AI 自己会在正文里给结论，别再自动糊一份报告上来
      await runPlan({ fromAI: true });
      const d = store.feasibility?.distance;
      if (!store.feasibility) return FAIL(tool, '规划没跑通', '没有拿到结果');
      return OK(tool, '航线规划完成',
        d?.route_km != null ? `${d.route_km} km / ${d.flight_time_h} h / ${store.feasibility.risk?.overall ?? '—'}` : undefined,
        {
          feasible: store.feasibility.feasible,
          verdict: store.feasibility.verdict,
          conclusion: store.feasibility.conclusion,
          route_km: d?.route_km ?? null,
          flight_time_h: d?.flight_time_h ?? null,
          detour_ratio: d?.detour_ratio ?? null,
          risk: store.feasibility.risk?.overall ?? null,
          max_tke: store.feasibility.risk?.stats?.max_tke ?? null,
          max_edr: store.feasibility.risk?.stats?.max_edr ?? null,
          // 分段必须一起回灌：用户问「哪一段最危险 / 把各段列成表」时，
          // 只给全程汇总的话模型只能答「我拿不到分段」——而左栏结果卡上明明有。
          segments: (store.feasibility.risk?.segments ?? []).map(sg => ({
            from: sg.from_name, to: sg.to_name,
            distance_km: sg.distance_km, level: sg.level,
            max_tke: sg.max_tke, max_edr: sg.max_edr,
          })),
        });
    }

    case 'run_site_plan': {
      if (!store.start.lat || !store.end.lat) return FAIL(tool, '起终点没设全', '无法规划');
      await runSitePlan();
      const opt = (store.siteResult?.options ?? [])[0];
      if (!opt) return FAIL(tool, '多航点规划没跑通', '没有拿到方案');
      return OK(tool, '多航点方案已算出',
        `${opt.relay_count} 个中继 / ${opt.total_distance} km / ${opt.worst_level ?? '—'}`,
        {
          feasible: opt.feasible,
          verdict: opt.verdict,
          relay_count: opt.relay_count,
          total_distance: opt.total_distance,
          total_time_h: opt.total_time_h ?? null,
          worst_level: opt.worst_level ?? null,
          legs: (opt.legs ?? []).map(l => ({
            from: l.from?.name, to: l.to?.name,
            km: l.route_km ?? l.straight_km, level: l.level ?? null, note: l.note ?? null,
          })),
        });
    }

    case 'run_time_window': {
      if (!store.start.lat || !store.end.lat) return FAIL(tool, '起终点没设全', '无法扫描');
      await runTimeWindowScan();
      const r = store.timeWindow as any;
      if (!r) return FAIL(tool, '时间窗口扫描没跑通', '没有拿到结果');
      const rec = r.recommended || {};
      return OK(tool, '时间窗口扫描完成',
        rec.takeoff ? `推荐 ${rec.takeoff} / ${rec.level ?? '—'}` : undefined,
        {
          date: r.date, has_window: r.has_window, reason: r.reason ?? null,
          recommended: rec.takeoff ?? null, level: rec.level ?? null,
          safe_slot_count: r.slot_analysis?.filter((s: any) => s.safe).length ?? null,
          backup_windows: r.backup_windows ?? [],
        });
    }

    case 'query_weather': {
      const names = (args.sites as string[]) ?? [];
      const pts = names
        .map(n => ({ name: n, ...(resolvePlace(n) ?? {}) }))
        .filter(p => p.lat != null && p.lng != null) as { name: string; lat: number; lng: number }[];
      if (!pts.length) {
        // 用户点名了地点却一个都没解析出来时，**不能悄悄换成起点**。
        // 问「曲松县现在湍流多少」、回一句拉萨的实况，数字是真的、地点是错的 ——
        // 比明说「查不了」危险得多，因为用户无从察觉被换了地方。
        if (names.length) {
          const bad = names.join('、');
          return FAIL(tool, '取不到实况',
            store.siteCatalog
              ? `没能把「${bad}」解析成坐标，本次没有查询；不要用别处的实况代替`
              : '站表还没加载成功，本次**没有真正查询**；不要用别处的实况代替，'
                + '也不要改起降点，让用户重试一次');
        }
        const e = store.start.lat;
        if (e == null) return FAIL(tool, '取不到实况', '没有可查询的地点');
        pts.push({ name: '起点', lat: Number(store.start.lat), lng: Number(store.start.lng) });
      }
      const w = await queryWeather(pts, store.targetDate, store.targetTime);
      if (!w.available) return FAIL(tool, '取不到实况', w.reason ?? '数据不可用');
      return OK(tool, '实况已取到', `${pts.map(p => p.name).join('、')} · ${w.query_time}`,
        { query_time: w.query_time, level_note: w.level_note, points: w.points });
    }

    case 'search_sites': {
      // km 必须在有参照点时才有意义。模型经常只给 km 不给 name（它以为
      // 「以这条航线为圆心」是默认），这时不接上当前航线的端点，km 就会被
      // 静默忽略，然后模型拿着一个「全国范围」的结果去回答「50 km 内有没有」
      // —— 那会得出「附近没有备降点」这种**要命**的错误结论。
      const wantKm = args.km as number | undefined;
      let originName = args.name as string | undefined;
      if (wantKm && !originName) {
        const inferred = currentStartName();
        if (inferred && resolvePlace(inferred)) originName = inferred;
      }
      const hits = searchSitesLocal((args.query as string) || '', originName, wantKm);

      const scope = originName && wantKm
        ? `${originName} 周边 ${wantKm} km`
        : (args.query ? `站表中匹配「${args.query}」` : '全部可用站点');
      if (!hits.length) {
        // 站表空着的时候，「0 条」不是「附近没有备降点」，是**根本没查**。
        // 这两句意思相反：前者让用户去别处找，后者会让用户放弃一个其实有站的地方。
        if (!store.siteCatalog) {
          return FAIL(tool, `${scope}内没有找到可落地点`,
            '站表还没加载成功，本次**没有真正检索过**；不要据此说「附近没有备降点」，'
            + '也不要改任务区域，让用户重试一次',
            { scope, searched_km: wantKm ?? null, origin: originName ?? null,
              found: 0, sites: [], catalog_loaded: false });
        }
        return FAIL(tool, `${scope}内没有找到可落地点`, '如实告知用户「没有」，不要编造',
          { scope, searched_km: wantKm ?? null, origin: originName ?? null,
            found: 0, sites: [], catalog_loaded: true });
      }
      return OK(tool, `${scope}内找到 ${hits.length} 个可落地点`,
        hits.slice(0, 5).map(s => (s as any).__km != null ? `${s.name}（${(s as any).__km} km）` : s.name).join('、'),
        {
          // scope 一并回灌：模型汇报时必须用这个口径，不能自己换个数字说
          scope,
          searched_km: wantKm ?? null,
          origin: originName ?? null,
          found: hits.length,
          sites: hits.map(s => ({
            name: s.name, type: s.type, region: s.region ?? null,
            lat: s.lat, lon: s.lon, elevation_m: s.elevation_m,
            km: (s as any).__km ?? null,
          })),
        });
    }

    case 'look_at_image': {
      // attachment 源：用户这次发来的图已经随请求带上了，模型下一轮就能看到，
      // 这里不需要再渲染，只需要确认「看第几张」。
      if ((args.source as string) === 'attachment') {
        const shots = getAttachedImages();
        if (!shots.length) {
          return FAIL(tool, '这次对话里没有你发来的图片', '提示用户先附一张图');
        }
        const idx = Math.min(Math.max(1, Number(args.index) || 1), shots.length);
        return OK(tool, `已重新查看你发来的第 ${idx} 张图`, '可以结合这张图描述内容',
          { image: shots[idx - 1], count: shots.length });
      }

      // map 源：让后端按**本区域自身数值范围**渲染一张图。
      // 不能用地图那张（色标写死 TKE 0~30），这条航线实际只有 0.9~5.1，
      // 全挤在色标最低 17%，出来的图一片深蓝，模型看不出任何形态。
      const where = (args.name as string) || '';
      const origin = resolvePlace(where);
      if (!origin) {
        return FAIL(tool, `没能定位到「${where}」`, placeMiss(where));
      }
      const layer = (args.layer as string) || 'tke';
      const km = Number(args.km) || 40;
      const resp = await fetch(`${API_BASE}/api/weather/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lat: origin.lat, lon: origin.lng, km,
          layer, date: store.targetDate, time: store.targetTime,
        }),
      });
      if (!resp.ok) return FAIL(tool, '出图失败', `HTTP ${resp.status}`);
      const pic = await resp.json();
      if (!pic.ok || !pic.image) {
        return FAIL(tool, `${where} 这块地看不了`, pic.reason || '没有可渲染的数据');
      }
      // 同时把地图挪过去，让用户看的和 AI 看的是同一块
      store.mapFocus = { lat: origin.lat, lng: origin.lng, zoom: 9, nonce: nextNonce() };
      return {
        tool, ok: true,
        label: `已渲染${where}周边的${layer.toUpperCase()}分布图`,
        detail: `${store.targetDate} ${store.targetTime} · 半径 ${pic.radius_km} km`,
        image: pic.image as string,
        data: {
          image: pic.image,
          place: where, layer, date: pic.date, time: pic.time,
          center: pic.center, radius_km: pic.radius_km,
        },
      };
    }

    default:
      return FAIL(tool, `工具 ${tool} 不存在`, '模型调了清单外的工具');
  }
}

/** 这一步要的上下文（供 AI 回答时引用）。与旧 chat 端点同一套构建器。 */
export function chatTargetAfter(): ChatTarget {
  if (store.timeWindow && store.sidebarTab === 'timeWindow') {
    return { task: 'time_window', payload: store.timeWindow as any };
  }
  return { task: 'feasibility', payload: store.feasibility as any };
}

export { API_BASE };
