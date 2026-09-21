<template>
  <!-- ============ 输入区：任务优先级 ============ -->
  <section class="control-section">
    <h3 class="section-title"><ShieldCheck :size="16" /> 任务优先级</h3>
    <p class="hint-text">优先级决定可接受的绕行成本（时间口径）上限</p>
    <div class="priority-group">
      <button
        v-for="opt in priorityOptions"
        :key="opt.value"
        class="priority-btn"
        :class="{ active: store.priority === opt.value }"
        @click="store.priority = opt.value"
      >
        <span class="priority-name">{{ opt.label }}</span>
        <span class="priority-limit">{{ opt.hint }}</span>
      </button>
    </div>

    <div class="action-row">
      <button
        class="capsule-btn full-width"
        @click="handleAssess"
        :disabled="store.assessLoading"
        :class="{ 'disabled-btn': store.assessLoading }"
      >
        <span v-if="store.assessLoading" class="loading-icon">
          <Loader :size="16" class="spin" /> 正在评估可行性...
        </span>
        <span v-else style="display:flex;align-items:center;gap:8px;">
          <ShieldCheck :size="16" /> 开始评估
        </span>
      </button>
    </div>
  </section>

  <!-- ============ 结果区 ============ -->
  <section v-if="r" class="control-section">
    <!-- 结论卡 -->
    <div class="verdict-card" :class="r.feasible ? 'ok' : 'bad'">
      <div class="verdict-icon">
        <CheckCircle2 v-if="r.feasible" :size="22" />
        <XCircle v-else :size="22" />
      </div>
      <div class="verdict-body">
        <div class="verdict-title">{{ r.conclusion }}</div>
        <div class="verdict-sub">{{ verdictSub }}</div>
      </div>
    </div>

    <!-- 起降点自身安全状态（不安全时明确点出是哪一端） -->
    <div v-if="unsafeEndpointText" class="endpoint-alert">
      <AlertTriangle :size="14" />
      <span>{{ unsafeEndpointText }}</span>
    </div>

    <!-- KPI（未规划出航线时，除直线距离外均为「不适用」） -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">航线总距离</span>
        <span v-if="r.distance.applicable" class="kpi-value">{{ r.distance.route_km }}<em>km</em></span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">直线距离</span>
        <span class="kpi-value">{{ r.distance.straight_km }}<em>km</em></span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">预计飞行时间</span>
        <span v-if="r.distance.applicable" class="kpi-value">{{ r.distance.flight_time_h }}<em>h</em></span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">绕行比</span>
        <span
          v-if="r.distance.applicable"
          class="kpi-value"
          :class="{ warn: r.distance.time_cost_h.exceeded }"
        >
          {{ r.distance.detour_ratio }}<em>×</em>
        </span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
    </div>

    <!-- 全程风险等级 -->
    <div v-if="r.risk" class="risk-banner" :style="{ borderColor: riskColor(r.risk.overall) }">
      <span class="risk-tag" :style="{ backgroundColor: riskColor(r.risk.overall) }">
        {{ r.risk.overall }}
      </span>
      <div class="risk-metrics">
        <span>最大 TKE <b>{{ r.risk.stats.max_tke }}</b></span>
        <span>最大 EDR <b>{{ r.risk.stats.max_edr }}</b></span>
        <span>中度以上占比 <b>{{ (r.risk.stats.moderate_ratio * 100).toFixed(1) }}%</b></span>
      </div>
    </div>

    <!-- 分段风险 -->
    <div v-if="r.risk && r.risk.segments.length" class="sub-block">
      <h4 class="sub-title"><Route :size="14" /> 区域飞行等级（以途经城市为间隔）</h4>
      <div class="segment-list">
        <div
          v-for="(seg, idx) in r.risk.segments"
          :key="idx"
          class="segment-item"
          :class="{ selected: store.selectedSegmentIndex === idx }"
          @click="toggleSegment(idx)"
          @mouseenter="store.hoveredSegmentIndex = idx"
          @mouseleave="store.hoveredSegmentIndex = null"
        >
          <span class="seg-dot" :style="{ backgroundColor: riskColor(seg.level) }"></span>
          <span class="seg-names">{{ seg.from_name }} → {{ seg.to_name }}</span>
          <span class="seg-level" :style="{ color: riskColor(seg.level) }">{{ seg.level }}</span>
          <span class="seg-meta">{{ seg.distance_km }}km · TKE {{ seg.max_tke }}</span>
        </div>
      </div>
    </div>

    <!-- 绕行成本分析 -->
    <div class="sub-block">
      <h4 class="sub-title"><Timer :size="14" /> 绕行成本分析（时间口径）</h4>
      <template v-if="r.distance.time_cost_h.applicable">
        <div class="cost-row">
          <span>额外耗时</span>
          <b :class="{ warn: r.distance.time_cost_h.exceeded }">
            +{{ r.distance.time_cost_h.extra }} h
          </b>
        </div>
        <div class="cost-row">
          <span>{{ priorityLabel }}任务允许</span>
          <b>+{{ r.distance.time_cost_h.limit }} h（{{ r.distance.time_cost_h.limit_ratio }}×）</b>
        </div>
        <div class="cost-bar">
          <div
            class="cost-bar-fill"
            :class="{ over: r.distance.time_cost_h.exceeded }"
            :style="{ width: costBarWidth }"
          ></div>
        </div>
        <p class="hint-text small" v-if="r.distance.time_cost_h.exceeded">
          绕行成本已超出当前优先级阈值，建议更换飞行时间或调整起降点。
        </p>
      </template>
      <p v-else class="hint-text small">
        尚未规划出安全航线，绕行成本无法计算。{{ priorityLabel }}任务允许额外耗时
        +{{ r.distance.time_cost_h.limit }} h（{{ r.distance.time_cost_h.limit_ratio }}×）。
      </p>
    </div>

    <!-- 阻塞原因 + 风险分布 -->
    <div v-if="r.blocking && r.blocking.regions.length" class="sub-block">
      <h4 class="sub-title"><AlertTriangle :size="14" /> 阻塞原因分析</h4>
      <p class="hint-text">
        主要阻塞区呈 <b>{{ r.blocking.dominant_shape }}</b> 分布，共
        {{ r.blocking.regions.length }} 处：
      </p>
      <div class="blocking-list">
        <div v-for="(reg, idx) in r.blocking.regions" :key="idx" class="blocking-item">
          <div class="blocking-head">
            <span class="blocking-shape">{{ reg.shape }}</span>
            <span class="blocking-area">{{ reg.area_km2 }} km²</span>
          </div>
          <div class="blocking-meta">
            中心 ({{ reg.center[0] }}, {{ reg.center[1] }}) · 等效半径 {{ reg.radius_km }}km
            · 长宽比 {{ reg.aspect_ratio }}
          </div>
          <div class="blocking-meta">
            峰值 TKE <b>{{ reg.max_tke }}</b> · EDR <b>{{ reg.max_edr }}</b>
            · {{ reg.crosses_corridor ? '横亘航线走廊' : '位于航线附近' }}
          </div>
        </div>
      </div>
      <button class="ghost-btn full-width" @click="focusBlocking">
        <MapPin :size="14" /> 在地图上查看风险分布
      </button>
    </div>

    <!-- 建议措施 -->
    <div class="sub-block">
      <h4 class="sub-title"><Lightbulb :size="14" /> 建议措施</h4>
      <p class="suggestion-text">{{ r.suggestions.text }}</p>

      <button class="ghost-btn full-width goto-window" @click="gotoTimeWindow">
        <CalendarClock :size="14" /> 查看替代时段
      </button>

      <div v-if="r.suggestions.alt_bases.length" class="alt-bases">
        <div class="alt-title">可用替代起降点：</div>
        <div
          v-for="(alt, idx) in r.suggestions.alt_bases"
          :key="idx"
          class="alt-item"
          @click="applyAltBase(alt)"
        >
          <span class="seg-dot" :style="{ backgroundColor: riskColor(alt.level) }"></span>
          <span class="alt-role">{{ alt.role_label || (alt.role === 'start' ? '替代起点' : '替代终点') }}</span>
          <span class="alt-desc">{{ alt.description }}</span>
          <span class="alt-meta">{{ alt.level }} · {{ alt.distance_km }}km</span>
        </div>
      </div>
    </div>

    <!-- AI 评估报告（流式生成） -->
    <ReportCard task="feasibility" :payload="r" />
  </section>

  <section v-else class="control-section">
    <p class="hint-text">
      设置起点与终点后，点击「开始评估」获取可飞性结论、风险等级与绕行成本分析。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  ShieldCheck, Loader, CheckCircle2, XCircle, Route, Timer,
  AlertTriangle, Lightbulb, MapPin, CalendarClock,
} from 'lucide-vue-next';
import { store, RISK_COLORS, PRIORITY_LABELS } from '../store';
import type { FeasibilityResult } from '../store';
import { API_BASE } from '../config';
import ReportCard from './ReportCard.vue';

const priorityOptions = [
  { value: 'urgent' as const, label: '紧急', hint: '≤ +25% 时间' },
  { value: 'normal' as const, label: '常规', hint: '≤ +50% 时间' },
  { value: 'economy' as const, label: '经济', hint: '≤ +100% 时间' },
];

const r = computed<FeasibilityResult | null>(() => store.feasibility);

const priorityLabel = computed(() => PRIORITY_LABELS[store.priority] ?? '常规');

const verdictSub = computed(() => {
  if (!r.value) return '';
  if (r.value.feasible) {
    return `存在安全可行航线，可在 ${r.value.distance.flight_time_h} 小时内完成飞行`;
  }
  const map: Record<string, string> = {
    detour_exceeds_threshold: '存在安全航线，但绕行成本超出阈值',
    no_safe_path: '航路被高强度湍流区封死，无法规划安全航线',
    data_coverage_insufficient: '起降点超出气象数据覆盖范围',
    search_iteration_limit: '搜索达到迭代上限，未能确认安全航线',
  };
  return map[r.value.reason ?? ''] ?? '当前条件下不建议执行飞行任务';
});

const riskColor = (level: string) => RISK_COLORS[level] ?? '#94a3b8';

/** 起降点自身不安全时的提示文案（含实际 TKE 值） */
const unsafeEndpointText = computed(() => {
  const eps = r.value?.meta?.endpoints;
  const bad = r.value?.meta?.unsafe_endpoint;
  if (!eps || !bad) return '';
  const parts: string[] = [];
  if (eps.start && !eps.start.safe) {
    parts.push(`起点 ${eps.start.describe} 处 TKE ${eps.start.tke}（超过安全阈值）`);
  }
  if (eps.end && !eps.end.safe) {
    parts.push(`终点 ${eps.end.describe} 处 TKE ${eps.end.tke}（超过安全阈值）`);
  }
  if (!parts.length) return '';
  return `${bad}自身不满足安全条件：${parts.join('；')}`;
});

/** 成本进度条：以阈值对应的时间增量为满格基准 */
const costBarWidth = computed(() => {
  if (!r.value) return '0%';
  const { applicable, extra, limit } = r.value.distance.time_cost_h;
  // 未规划出航线时 extra 为 null，进度条不适用
  if (!applicable || extra == null) return '0%';
  const base = Math.max(limit, 0.01);
  const pct = Math.min(100, (extra / base) * 100);
  return `${pct.toFixed(1)}%`;
});

const toggleSegment = (idx: number) => {
  store.selectedSegmentIndex = store.selectedSegmentIndex === idx ? null : idx;
};

/** 跳转到时间窗口标签页并自动执行一次扫描（用于「更换飞行时间」建议）。 */
const gotoTimeWindow = () => {
  store.sidebarTab = 'timeWindow';
  store.pendingTimeWindowScan = true;
};

const focusBlocking = () => {
  if (r.value?.blocking?.regions?.length) {
    store.highlightRegions = r.value.blocking.regions;
    if (!store.overlays.includes('tke')) {
      store.overlays.push('tke');
    }
  }
};

const applyAltBase = (alt: { role?: string; end: { lat: number; lon: number } }) => {
  // 替代点可能是起点也可能是终点，按 role 写回对应输入框
  const target = alt.role === 'start' ? store.start : store.end;
  target.lat = String(alt.end.lat);
  target.lng = String(alt.end.lon);
};

const handleAssess = async () => {
  if (!store.start.lat || !store.start.lng || !store.end.lat || !store.end.lng) {
    alert('请先设置起点和终点！');
    return;
  }

  store.assessLoading = true;
  store.highlightRegions = [];
  try {
    const response = await fetch(`${API_BASE}/api/assess/feasibility`, {
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

    if (!response.ok) throw new Error(`评估失败 (${response.status})`);

    const result: FeasibilityResult = await response.json();
    store.feasibility = result;
    store.selectedSegmentIndex = null;

    // 把评估路径同步到地图
    if (result.path && result.path.length > 0) {
      const startAlt = Number(store.start.alt || 600);
      const endAlt = Number(store.end.alt || 600);
      const n = result.path.length;
      store.plannedPath = result.path.map((p, i) => ({
        lat: p.lat,
        lng: p.lon,
        alt: n > 1 ? startAlt + (endAlt - startAlt) * (i / (n - 1)) : startAlt,
      }));
      store.simulationIndex = 0;
      store.isSimulating = false;
    }

    // 不可飞时自动展示风险分布
    if (!result.feasible && result.blocking?.regions?.length) {
      store.highlightRegions = result.blocking.regions;
      if (!store.overlays.includes('tke')) store.overlays.push('tke');
    }
  } catch (error) {
    console.error('可行性评估请求失败:', error);
    alert('评估失败，请确保后端服务已启动');
  } finally {
    store.assessLoading = false;
  }
};
</script>

<style scoped>
/* 面板内的分区间距：本组件使用 scoped 样式，父组件 Sidebar.vue 的
   .control-section 规则无法穿透选择器作用域，需要在此单独声明，
   否则「开始评估」按钮会与下方结论/提示文字贴死。 */
.control-section {
  margin-bottom: 16px;
}
.hint-text {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 10px 0;
}
.hint-text.small { font-size: 11px; opacity: 0.75; }

.kpi-value.na {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

/* 起降点自身不安全提示 */
.endpoint-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 8px 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.45);
  color: #fca5a5;
  font-size: 12px;
  line-height: 1.6;
}
.endpoint-alert svg { flex-shrink: 0; margin-top: 2px; }

/* 替代起降点角色标签 */
.alt-role {
  flex-shrink: 0;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.18);
  color: var(--text-secondary);
}

.priority-group {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 14px;
}
.priority-btn {
  background: rgba(255,255,255,0.05);
  border: 1px solid var(--panel-border);
  border-radius: 10px;
  padding: 8px 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  transition: all 0.2s;
  /* button 元素默认使用 UA 的 buttontext 颜色（深色面板上会渲染成黑色），
     这里显式继承主文字色，保证优先级选项可读。 */
  color: var(--text-primary);
}
.priority-btn:hover { background: rgba(255,255,255,0.12); }
.priority-btn.active {
  background: rgba(52, 152, 219, 0.22);
  border-color: var(--accent-color);
}
.priority-name { font-size: 13px; font-weight: 600; }
.priority-limit { font-size: 10px; color: var(--text-secondary); }

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
.verdict-sub { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }

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
.kpi-value.warn { color: #f97316; }

.risk-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: 10px;
  border-left: 4px solid;
  background: rgba(0,0,0,0.2);
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.risk-tag {
  padding: 2px 10px;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 700;
  color: #0b1220;
}
.risk-metrics {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}
.risk-metrics b { color: var(--text-primary); }

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

.segment-list { display: flex; flex-direction: column; gap: 6px; }
.segment-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  background: rgba(0,0,0,0.18);
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.segment-item:hover { background: rgba(255,255,255,0.07); }
.segment-item.selected { border-color: var(--accent-color); background: rgba(52,152,219,0.15); }
.seg-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.seg-names { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seg-level { font-weight: 700; }
.seg-meta { font-size: 10px; color: var(--text-secondary); }

.cost-row { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 6px; }
.cost-row b.warn { color: #f97316; }
.cost-bar {
  height: 6px;
  background: rgba(255,255,255,0.1);
  border-radius: 9999px;
  overflow: hidden;
  margin: 6px 0;
}
.cost-bar-fill {
  height: 100%;
  background: #22c55e;
  border-radius: 9999px;
  transition: width 0.4s ease;
}
.cost-bar-fill.over { background: #ef4444; }

.blocking-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.blocking-item {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  padding: 8px 10px;
}
.blocking-head { display: flex; justify-content: space-between; margin-bottom: 4px; }
.blocking-shape { font-size: 12px; font-weight: 700; color: #f87171; }
.blocking-area { font-size: 11px; color: var(--text-secondary); }
.blocking-meta { font-size: 10px; color: var(--text-secondary); line-height: 1.55; }
.blocking-meta b { color: var(--text-primary); }

.ghost-btn {
  background: transparent;
  border: 1px dashed var(--panel-border);
  color: var(--text-secondary);
  border-radius: 9999px;
  padding: 7px 18px;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  transition: all 0.2s;
}
.ghost-btn:hover { border-color: var(--accent-color); color: var(--accent-color); }
.goto-window { margin-top: 16px; margin-bottom: 10px; }

.suggestion-text {
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-primary);
  background: rgba(0,0,0,0.18);
  border-radius: 8px;
  padding: 10px;
  margin: 0 0 10px 0;
}

.alt-bases { display: flex; flex-direction: column; gap: 6px; }
.alt-title { font-size: 11px; color: var(--text-secondary); margin-bottom: 2px; }
.alt-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  background: rgba(0,0,0,0.18);
  border-radius: 8px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.alt-item:hover { background: rgba(52,152,219,0.15); }
.alt-desc { flex: 1; }
.alt-meta { font-size: 10px; color: var(--text-secondary); }

.action-row { margin-top: 16px; }
.loading-icon { display: flex; align-items: center; gap: 8px; justify-content: center; }
.spin { animation: spin 1.5s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }
</style>
