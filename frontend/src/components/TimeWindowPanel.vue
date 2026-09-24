<template>
  <!-- ============ 输入区：查找最佳窗口 ============ -->
  <section class="control-section">
    <p class="hint-text">
      在期望日期内扫描逐小时湍流强度，找出全程安全且湍流最低的起飞时段。
    </p>

    <div class="field-row">
      <label class="field-label">推演天数</label>
      <div class="stepper-group">
        <button
          v-for="opt in forecastOptions"
          :key="opt.value"
          class="chip-btn"
          :class="{ active: store.forecastDays === opt.value }"
          @click="store.forecastDays = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>
    <p class="hint-text small">
      当日无安全时段时向前推演的天数（0 = 只看当日）。
    </p>

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
          <div class="verdict-title">推荐起飞 {{ takeoffTime }}</div>
          <div class="verdict-sub">
            飞行时段 {{ r.recommended.window[0] }} – {{ r.recommended.window[1] }}，
            预计 {{ landingTime }} 降落
          </div>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">全程风险等级</span>
          <span class="kpi-value" :style="{ color: riskColor(r.recommended.level) }">
            {{ r.recommended.level }}
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">时段最大 TKE</span>
          <span class="kpi-value">
            {{ fmtTke(r.recommended.max_tke) }}<em>{{ TURB.tkeUnit }}</em>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">时段最大 EDR</span>
          <span class="kpi-value">
            {{ fmtEdr(r.recommended.max_edr) }}<em>{{ TURB.edrUnit }}</em>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">安全时段数</span>
          <span class="kpi-value">{{ safeSlotCount }}<em>个</em></span>
        </div>
      </div>

      <!-- 湍流强度变化曲线 -->
      <div v-if="r.curve.times.length" class="sub-block">
        <h4 class="sub-title"><TrendingUp :size="14" /> 湍流强度变化曲线</h4>
        <p class="hint-text small">起飞前 1 小时至降落后 1 小时</p>
        <div ref="chartRef" class="curve-chart"></div>
      </div>

      <!-- 备用时段 -->
      <div v-if="r.backup_windows.length" class="sub-block">
        <h4 class="sub-title"><Clock :size="14" /> 备用时段</h4>
        <div class="slot-list">
          <div v-for="(w, idx) in r.backup_windows" :key="idx" class="slot-item">
            <span class="seg-dot" :style="{ backgroundColor: riskColor(w.level) }"></span>
            <span class="slot-time">{{ w.start }} – {{ w.end }}</span>
            <span class="slot-level" :style="{ color: riskColor(w.level) }">{{ w.level }}</span>
            <span class="seg-meta">TKE {{ fmtTke(w.max_tke) }}</span>
          </div>
        </div>
      </div>

      <!-- 其他预警提示 -->
      <div v-if="r.warnings.length" class="sub-block">
        <h4 class="sub-title"><AlertTriangle :size="14" /> 其他预警提示</h4>
        <div class="warn-list">
          <div v-for="(w, idx) in r.warnings" :key="idx" class="warn-item" :class="w.level">
            <AlertTriangle :size="13" />
            <span>{{ w.text }}</span>
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

    <!-- AI 评估报告（流式生成） -->
    <ReportCard task="time_window" :payload="r" />

    <!-- 数据口径声明（两种情况都展示） -->
    <p class="hint-text small data-note">
      数据口径：500hPa 等压面，约 5km 空间分辨率、逐小时时间分辨率；
      TKE 为同层三维风场在 ±{{ TURB.windowHalfHours }} 小时窗口内的脉动方差，EDR 由其换算。
      时间为北京时间（UTC+8）。
    </p>
  </section>

  <section v-else class="control-section">
    <p class="hint-text">
      设置起点与终点后，点击「查找最佳窗口」获取推荐起飞时刻、湍流曲线与备用时段。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick, onBeforeUnmount, onMounted } from 'vue';
import {
  CalendarClock, Loader, CheckCircle2, XCircle, Clock,
  TrendingUp, AlertTriangle, BarChart3, CalendarDays,
} from 'lucide-vue-next';
import * as echarts from 'echarts';
import { store, RISK_COLORS } from '../store';
import { TIME_WINDOW_REASONS } from '../types/assessment';
import { API_BASE, TURB, fmtTke, fmtEdr } from '../config';
import ReportCard from './ReportCard.vue';

const forecastOptions = [
  { value: 0, label: '仅当日' },
  { value: 3, label: '3 天' },
  { value: 7, label: '7 天' },
];

const r = computed(() => store.timeWindow);
const chartRef = ref<HTMLElement | null>(null);
let chartInstance: echarts.ECharts | null = null;

const riskColor = (level: string) => RISK_COLORS[level] ?? '#94a3b8';

/** HH:MM 其中取时间部分（推荐卡用） */
const takeoffTime = computed(() => {
  const t = r.value?.recommended?.takeoff ?? '';
  return t.includes('T') ? t.split('T')[1] : t;
});
const landingTime = computed(() => {
  const t = r.value?.recommended?.landing ?? '';
  return t.includes('T') ? t.split('T')[1] : t;
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

const renderChart = () => {
  if (!chartRef.value || !r.value) return;
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value);
  }
  const c = r.value.curve;
  const takeoff = takeoffTime.value;
  const landing = landingTime.value;
  /** 起飞/降落标记线需落在图表已有刻度上（曲线为 30 分钟粒度，降落时刻可能不在刻度上，吸附到最近刻度）。 */
  const snapToTick = (hhmm: string) => {
    if (!hhmm || !c.times.length) return null;
    const toMin = (s: string) => {
      const [h, m] = s.split(':').map(Number);
      return (h || 0) * 60 + (m || 0);
    };
    const target = toMin(hhmm);
    return c.times.reduce((best, t) =>
      Math.abs(toMin(t) - target) < Math.abs(toMin(best) - target) ? t : best, c.times[0]);
  };
  const markLines = [takeoff, landing]
    .map(snapToTick)
    .filter((t, i, arr): t is string => !!t && arr.indexOf(t) === i)
    .map((t, i) => ({ xAxis: t, name: i === 0 ? '起飞' : '降落' }));

  chartInstance.setOption({
    grid: { left: 44, right: 52, top: 44, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderColor: 'rgba(148, 163, 184, 0.3)',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
    },
    legend: {
      show: true,
      top: 2,
      textStyle: { color: '#94a3b8', fontSize: 10 },
      itemWidth: 12,
      itemHeight: 8,
    },
    xAxis: {
      type: 'category',
      data: c.times,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.3)' } },
    },
    yAxis: [
      {
        type: 'value',
        name: 'TKE (m²/s²)',
        nameTextStyle: { color: '#94a3b8', fontSize: 10, padding: [0, 0, 4, 0] },
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.12)' } },
      },
      {
        type: 'value',
        name: 'EDR (m^(2/3)/s)',
        nameTextStyle: { color: '#94a3b8', fontSize: 10, padding: [0, 0, 4, 0] },
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => v.toFixed(2) },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '最大 TKE',
        type: 'line',
        smooth: true,
        symbolSize: 4,
        data: c.max_tke,
        itemStyle: { color: '#f97316' },
        lineStyle: { width: 2, color: '#f97316' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(249, 115, 22, 0.28)' },
            { offset: 1, color: 'rgba(249, 115, 22, 0.02)' },
          ]),
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(148, 163, 184, 0.6)', type: 'dashed' },
          label: { color: '#94a3b8', fontSize: 9, formatter: (p: { name?: string }) => p.name ?? '' },
          data: markLines,
        },
      },
      {
        name: '最大 EDR',
        type: 'line',
        smooth: true,
        symbolSize: 4,
        yAxisIndex: 1,
        data: c.max_edr.map(v => Number(Number(v).toFixed(4))),
        itemStyle: { color: '#38bdf8' },
        lineStyle: { width: 2, color: '#38bdf8' },
      },
    ],
  }, true);
  chartInstance.resize();
};

watch(
  () => r.value?.curve,
  async () => {
    await nextTick();
    renderChart();
  },
  { deep: true },
);

watch(
  () => store.sidebarTab,
  async () => {
    if (store.sidebarTab === 'timeWindow') {
      await nextTick();
      renderChart();
    }
  },
);

// 从功能一「查看替代时段」跳转过来时自动执行一次扫描
onMounted(() => {
  if (store.pendingTimeWindowScan) {
    store.pendingTimeWindowScan = false;
    nextTick(() => { void handleScan(); });
  }
});

onBeforeUnmount(() => {
  chartInstance?.dispose();
  chartInstance = null;
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

    if (!response.ok) throw new Error('时间窗口扫描失败');

    store.timeWindow = await response.json();
    await nextTick();
    renderChart();
  } catch (error) {
    console.error('时间窗口扫描请求失败:', error);
    alert('时间窗口扫描失败，请确保后端服务已启动');
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
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 12px 0;
}
.hint-text.small { font-size: 11px; opacity: 0.75; }
.data-note {
  margin: 14px 0 0 0;
  padding-top: 12px;
  border-top: 1px solid var(--panel-border);
  line-height: 1.7;
}

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
  font-size: 12px;
  color: var(--text-secondary);
}
.stepper-group {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.chip-btn {
  background: rgba(255,255,255,0.05);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  padding: 8px 6px;
  font-size: 12px;
  color: var(--text-primary);
  cursor: pointer;
  transition: all 0.2s;
}
.chip-btn:hover { background: rgba(255,255,255,0.12); }
.chip-btn.active {
  background: rgba(52, 152, 219, 0.22);
  border-color: var(--accent-color);
}

.action-row { margin-top: 16px; }
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
.verdict-title { font-size: 18px; font-weight: 700; }
.verdict-sub { font-size: 11px; color: var(--text-secondary); margin-top: 2px; line-height: 1.6; }

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
.kpi-label { font-size: 10px; color: var(--text-secondary); }
.kpi-value { font-size: 16px; font-weight: 700; }
.kpi-value em { font-size: 10px; font-weight: 400; font-style: normal; opacity: 0.7; margin-left: 2px; }

.sub-block {
  margin-bottom: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--panel-border);
}
.sub-title {
  font-size: 12px;
  color: var(--text-secondary);
  margin: 0 0 8px 0;
  display: flex;
  align-items: center;
  gap: 6px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.curve-chart {
  width: 100%;
  height: 200px;
  margin-top: 4px;
}

.slot-list { display: flex; flex-direction: column; gap: 6px; }
.slot-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  background: rgba(0,0,0,0.18);
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 11px;
}
.slot-time { flex: 1; }
.slot-level { font-weight: 700; }
.seg-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.seg-meta { font-size: 10px; color: var(--text-secondary); }

.slot-table { display: flex; flex-direction: column; gap: 4px; }
.slot-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 6px;
  font-size: 11px;
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
  font-size: 11px;
}
.forecast-item.ok { background: rgba(34, 197, 94, 0.1); }
.fc-date { flex: 0 0 78px; }
.fc-window { flex: 1; }
.fc-window.muted { color: var(--text-secondary); }
.fc-level { font-weight: 700; }

.warn-list { display: flex; flex-direction: column; gap: 6px; }
.warn-item {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 7px 9px;
  border-radius: 8px;
  font-size: 11px;
  line-height: 1.6;
  background: rgba(234, 179, 8, 0.1);
  border: 1px solid rgba(234, 179, 8, 0.3);
  color: #fde047;
}
.warn-item.critical {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}
.warn-item svg { flex-shrink: 0; margin-top: 2px; }
</style>
