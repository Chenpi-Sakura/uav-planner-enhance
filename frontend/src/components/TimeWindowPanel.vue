<template>
  <!-- ============ 输入区：查找最佳窗口 ============ -->
  <section class="control-section">
    <div class="field-row">
      <label class="field-label">推演天数</label>
      <div class="choice-row">
        <button
          v-for="opt in forecastOptions"
          :key="opt.value"
          class="choice-btn"
          :class="{ active: store.forecastDays === opt.value }"
          @click="store.forecastDays = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>
    <div class="action-row">
      <button
        class="capsule-btn full-width"
        @click="handleScan"
        :disabled="store.timeWindowLoading"
        :class="{ 'disabled-btn': store.timeWindowLoading }"
      >
        <span v-if="store.timeWindowLoading" class="loading-icon">
          <Loader :size="16" class="spin" /> 正在扫描时间窗口...
        </span>
        <span v-else class="btn-inner">
          <CalendarClock :size="16" /> 查找最佳窗口
        </span>
      </button>
    </div>
  </section>

  <!-- ============ 结果区 ============ -->
  <section v-if="r" class="control-section">
    <!-- 有窗口：推荐卡 -->
    <template v-if="r.has_window && r.recommended">
      <div class="verdict-card ok">
        <div class="verdict-icon"><CheckCircle2 :size="22" /></div>
        <div class="verdict-body">
          <div class="verdict-title">推荐起飞 {{ takeoffLabel }}</div>
          <div class="verdict-sub">
            飞行时段 {{ rec?.window?.[0] }} – {{ rec?.window?.[1] }}，
            预计 {{ landingLabel }}降落
          </div>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">全程风险等级</span>
          <span class="kpi-value" :style="{ color: riskColor(rec?.level ?? '') }">
            {{ rec?.level }}
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">沿程最大 TKE</span>
          <span class="kpi-value">
            {{ fmtTke(rec?.max_tke) }}<em>{{ TURB.tkeUnit }}</em>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">沿程最大 EDR</span>
          <span class="kpi-value">
            {{ fmtEdr(rec?.max_edr) }}<em v-html="TURB.edrUnitHtml"></em>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">安全时段数</span>
          <span class="kpi-value">{{ safeSlotCount }}<em>个</em></span>
        </div>
      </div>

      <!-- Top-K 候选：每条都按该时刻自身重规划过真实航线，点一下回功能一出三档 -->
      <div v-if="routes.length" class="sub-block">
        <h4 class="sub-title"><Navigation :size="14" /> 候选起飞时刻</h4>
        <div class="slot-list">
          <div v-for="(rt, idx) in routes" :key="rt.takeoff" class="slot-item">
            <span class="slot-time">
              {{ hhmm(rt.takeoff) }}
              <em v-if="idx === 0" class="route-flag">推荐</em>
            </span>
            <span class="slot-level" :style="{ color: riskColor(routeLevel(rt)) }">
              {{ routeLevel(rt) }}
            </span>
            <span class="seg-meta">{{ routeMeta(rt) }}</span>
            <button class="route-btn" :disabled="store.planLoading"
                    @click="applyTimeWindowRoute(rt)">按此刻规划</button>
          </div>
        </div>
        <p v-if="rankNote" class="hint-text small">{{ rankNote }}</p>
      </div>

      <!-- 备用时段：有候选航线列表时不再重复列一遍窗口 -->
      <div v-if="r.backup_windows.length && !routes.length" class="sub-block">
        <h4 class="sub-title"><Clock :size="14" /> 备用时段</h4>
        <div class="slot-list">
          <div v-for="(w, idx) in r.backup_windows" :key="idx" class="slot-item">
            <span class="slot-time">{{ w.start }} – {{ w.end }}</span>
            <span class="slot-level" :style="{ color: riskColor(w.level) }">{{ w.level }}</span>
            <span class="seg-meta">TKE {{ fmtTke(w.max_tke) }}</span>
          </div>
        </div>
      </div>

    </template>

    <!-- 无窗口：原因 + 时段风险表 + 推演结果 -->
    <template v-else>
      <div class="verdict-card bad">
        <div class="verdict-icon"><XCircle :size="22" /></div>
        <div class="verdict-body">
          <div class="verdict-title">无安全起飞窗口</div>
          <div class="verdict-sub">{{ reasonText }}</div>
        </div>
      </div>

      <!-- 各时段风险分析 -->
      <div v-if="r.slot_analysis.length" class="sub-block">
        <h4 class="sub-title"><BarChart3 :size="14" /> 各时段风险分析</h4>
        <div class="slot-table">
          <div
            v-for="(s, idx) in r.slot_analysis"
            :key="idx"
            class="slot-row"
            :class="{ unsafe: !s.safe }"
          >
            <span class="slot-time">{{ s.time }}</span>
            <span class="slot-bar">
              <i
                :style="{
                  width: barWidth(s.max_tke) + '%',
                  backgroundColor: riskColor(s.level),
                }"
              ></i>
            </span>
            <span class="slot-level" :style="{ color: riskColor(s.level) }">
              {{ s.safe ? s.level : '不可飞' }}
            </span>
            <span class="seg-meta">{{ fmtTke(s.max_tke) }}</span>
          </div>
        </div>
      </div>

      <!-- 7 天推演 -->
      <div v-if="r.forecast.enabled" class="sub-block">
        <h4 class="sub-title"><CalendarDays :size="14" /> 可飞行日期推演</h4>
        <div class="forecast-list">
          <div
            v-for="(d, idx) in r.forecast.days"
            :key="idx"
            class="forecast-item"
            :class="{ ok: d.has_window }"
          >
            <span class="fc-date">{{ d.date }}</span>
            <span v-if="d.has_window" class="fc-window">
              {{ d.best_window ? d.best_window[0] + ' – ' + d.best_window[1] : '有安全时段' }}
            </span>
            <span v-else class="fc-window muted">无安全时段</span>
            <span
              class="fc-level"
              :style="{ color: d.level ? riskColor(d.level) : '#94a3b8' }"
            >
              {{ d.level ?? '—' }}
            </span>
            <span class="seg-meta">{{ d.safe_slot_count }} 个时段</span>
          </div>
        </div>
        <p v-if="r.meta?.geometry_fallback" class="hint-text small">
          未找到安全航线，以上按时段分析结果仅供参考。
        </p>
      </div>
    </template>
  </section>

  <section v-else class="control-section">
    <p class="hint-text">
      设置起点与终点后，点击「查找最佳窗口」获取推荐起飞时刻与备用时段。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted } from 'vue';
import {
  CalendarClock, Loader, CheckCircle2, XCircle, Clock,
  BarChart3, CalendarDays, Navigation,
} from 'lucide-vue-next';
import { store, RISK_COLORS, applyTimeWindowRoute, effortText, failRequest, noticeForError } from '../store';
import { TIME_WINDOW_REASONS } from '../types/assessment';
import type { TimeWindowRoute } from '../types/assessment';
import { API_BASE, TURB, fmtTke, fmtEdr } from '../config';

const forecastOptions = [
  { value: 0, label: '仅当日' },
  { value: 3, label: '3 天' },
  { value: 7, label: '7 天' },
];

const r = computed(() => store.timeWindow);

const riskColor = (level: string) => RISK_COLORS[level] ?? '#94a3b8';

/** 结论卡跟着**细排冠军**（真实航线口径）；老响应里没这个字段时回退粗排。 */
const rec = computed(() => r.value?.recommended_final ?? r.value?.recommended ?? null);
const routes = computed<TimeWindowRoute[]>(() => r.value?.routes ?? []);

/** 推荐卡的日期时刻。后端给的是完整 ISO（`2022-07-01T07:00`，北京时间），
 *  只截 `HH:MM` 会让"哪天起飞"丢失；降落跨午夜时还必须标出来，否则读成回退。 */
const mmdd = (iso: string) => (iso.includes('T') ? iso.slice(5, 10) : '');
const hhmm = (iso: string) => (iso.includes('T') ? iso.split('T')[1] : iso);
const takeoffLabel = computed(() => {
  const t = rec.value?.takeoff ?? '';
  return t ? `${mmdd(t)} ${hhmm(t)}` : '';
});
const landingLabel = computed(() => {
  const t = rec.value?.landing ?? '';
  if (!t) return '';
  const nextDay = mmdd(t) !== mmdd(rec.value?.takeoff ?? '');
  return `${mmdd(t)} ${hhmm(t)}${nextDay ? '（次日）' : ''}`;
});

/** 候选行的补充文案：里程 / 绕行比 / 未得出航线的原因 */
const fmtKm = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${v.toFixed(1)} km`;
const fmtDetour = (v: number | null | undefined) =>
  v === null || v === undefined ? '' : ` · 绕行 ×${v.toFixed(2)}`;
const PLAN_REASON_TEXT: Record<string, string> = {
  blocked: '该时刻无安全路径',
  no_data: '该时刻无气象数据',
  iteration_limit: '搜索超限，未收敛',
  // 墙钟先到：预算没付完也没算完，同样是「没算完」，不能说成「该时刻真没路」
  time_limit: '时间上限内未算完',
};
const routeMeta = (rt: TimeWindowRoute) => {
  if (rt.feasible) return `${fmtKm(rt.total_distance)}${fmtDetour(rt.detour_ratio)}`;
  const base = PLAN_REASON_TEXT[rt.reason ?? ''] ?? '未规划出航线';
  // 「没算完」要有规模才和「该时刻真没路」分得开
  const unfinished = rt.reason === 'iteration_limit' || rt.reason === 'time_limit';
  const effort = unfinished ? effortText(rt.search_effort) : '';
  return effort ? `${base}（${effort}）` : base;
};
/**
 * 候选行的风险档。没得出航线时印后端的结论词而不是写死「不可飞」——
 * iteration_limit 的候选是「没算完」，riskColor 的兜底灰正好把它和真封死的红分开。
 */
const routeLevel = (rt: TimeWindowRoute) => (rt.feasible ? rt.planned_level : rt.conclusion);
/** 两遍排序口径不同，结论可能相反；这条差异必须让用户看见而不是被抹平 */
const rankNote = computed(() => {
  const coarse = r.value?.recommended;
  const best = routes.value[0];
  if (!coarse || !best) return '';
  if (coarse.takeoff === best.takeoff) return '';
  return `走廊级最优点是 ${hhmm(coarse.takeoff)}，按各时刻真实航线重排后是 ${hhmm(best.takeoff)}。`;
});

const safeSlotCount = computed(
  () => r.value?.slot_analysis.filter(s => s.safe).length ?? 0,
);

const reasonText = computed(() => {
  const key = r.value?.reason ?? '';
  return TIME_WINDOW_REASONS[key] ?? '当前日期内未找到全程安全的时间窗口';
});

/** 时段风险条宽度：以硬阈值（不可飞）为满量程，便于横向比较各时段 */
const barWidth = (tke: number | null | undefined) => {
  if (tke === null || tke === undefined) return 0;
  return Math.min(100, Math.max(2, (Number(tke) / TURB.tkeUnflyable) * 100));
};

// 从功能一「查看替代时段」跳转过来时自动执行一次扫描
onMounted(() => {
  if (store.pendingTimeWindowScan) {
    store.pendingTimeWindowScan = false;
    nextTick(() => { void handleScan(); });
  }
});

const handleScan = async () => {
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
};
</script>

<style scoped>
/* 面板内的分区间距：本组件使用 scoped 样式，父组件 Sidebar.vue 的
   .control-section 规则无法穿透选择器作用域，需要在此单独声明，
   否则「查找最佳窗口」按钮会与下方结果卡贴死。 */
.control-section {
  margin-bottom: 16px;
}
.hint-text {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 12px 0;
}
.hint-text.small { font-size: 13px; opacity: 0.75; }

.btn-inner {
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}
.field-label {
  font-size: 14px;
  color: var(--text-secondary);
}
.action-row { margin-top: 16px; }
/* 主操作按钮：与路径规划页的「开始规划」同一字号，不吃 <button> 的默认 13.33px */
.action-row .capsule-btn { font-size: 15px; }
.loading-icon { display: flex; align-items: center; gap: 8px; justify-content: center; }
.spin { animation: spin 1.5s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }

.verdict-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border-radius: 12px;
  margin-bottom: 14px;
  border: 1px solid;
}
.verdict-card.ok {
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.45);
  color: #4ade80;
}
.verdict-card.bad {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.45);
  color: #f87171;
}
.verdict-title { font-size: 21px; font-weight: 700; }
.verdict-sub { font-size: 13px; color: var(--text-secondary); margin-top: 2px; line-height: 1.6; }

.kpi-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 14px;
}
.kpi-card {
  background: rgba(0,0,0,0.2);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.kpi-label { font-size: 12px; color: var(--text-secondary); }
.kpi-value { font-size: 19px; font-weight: 700; }
.kpi-value em { font-size: 12px; font-weight: 400; font-style: normal; opacity: 0.7; margin-left: 2px; }
/* 同上：v-html 的上标要用 :deep 才命中，且不参与行高 */
.kpi-value em :deep(sup) { font-size: 0.78em; line-height: 0; }

.sub-block {
  margin-bottom: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--panel-border);
}
.sub-title {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0 0 8px 0;
  display: flex;
  align-items: center;
  gap: 6px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.slot-list { display: flex; flex-direction: column; gap: 6px; }
/* 三列网格：时刻 + 风险档占第一行，里程与绕行另起一行，按钮竖跨两行钉在右侧。
   13px 字号下五段塞不进一行，硬挤会把「低风险」拆成两行、把里程截成省略号。 */
.slot-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  column-gap: 8px;
  row-gap: 2px;
  padding: 7px 9px;
  background: rgba(0,0,0,0.18);
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 13px;
}
.slot-time { grid-column: 1; grid-row: 1; white-space: nowrap; }
.slot-level { grid-column: 2; grid-row: 1; justify-self: start; font-weight: 700; white-space: nowrap; }
.seg-meta { grid-column: 1 / span 2; grid-row: 2; font-size: 12px; color: var(--text-secondary); }

/* 候选行：时刻后面挂「推荐」小标，行尾一个把该时刻送进功能一的按钮 */
.route-flag {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(56, 189, 248, 0.18);
  color: #38bdf8;
  font-size: 11px;
  font-style: normal;
}
.route-btn {
  grid-column: 3;
  grid-row: 1 / span 2;
  padding: 3px 7px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.route-btn:hover:not(:disabled) { background: rgba(255, 255, 255, 0.14); }
.route-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.slot-table { display: flex; flex-direction: column; gap: 4px; }
.slot-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 6px;
  font-size: 13px;
  background: rgba(0,0,0,0.14);
}
.slot-row.unsafe { background: rgba(239, 68, 68, 0.1); }
.slot-row .slot-time { flex: 0 0 42px; }
.slot-bar {
  flex: 1;
  height: 6px;
  background: rgba(255,255,255,0.1);
  border-radius: 9999px;
  overflow: hidden;
}
.slot-bar i { display: block; height: 100%; border-radius: 9999px; }
.slot-row .slot-level { flex: 0 0 46px; text-align: right; }
.slot-row .seg-meta { flex: 0 0 52px; text-align: right; }

.forecast-list { display: flex; flex-direction: column; gap: 6px; }
.forecast-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  background: rgba(0,0,0,0.18);
  border-radius: 8px;
  font-size: 13px;
}
.forecast-item.ok { background: rgba(34, 197, 94, 0.1); }
.fc-date { flex: 0 0 78px; }
.fc-window { flex: 1; }
.fc-window.muted { color: var(--text-secondary); }
.fc-level { font-weight: 700; }
</style>
