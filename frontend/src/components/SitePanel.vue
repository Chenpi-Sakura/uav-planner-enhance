<template>
  <!-- ============ 输入区：规划中继站点 ============ -->
  <section class="control-section">
    <!-- 机型：一次只算一台（双机对照已于 2026-09-28 撤销）。不预选 = 后端按默认那台出 -->
    <div class="field">
      <label class="field-label">机型</label>
      <div class="choice-row">
        <button class="choice-btn craft-btn" v-for="a in aircraftList" :key="a.key"
                :class="{ active: store.siteForm.aircraft === a.key }"
                @click="store.siteForm.aircraft = a.key">
          <span class="craft-name">
            <i class="craft-dot" :style="{ backgroundColor: colorOf(a.key) }"></i>{{ shortName(a) }}
          </span>
          <span class="craft-cap">商载上限 {{ a.max_payload_kg }} kg</span>
        </button>
      </div>
    </div>

    <div class="field-pair">
      <div class="field">
        <label class="field-label">任务载重 (kg)</label>
        <input type="number" class="capsule-input" v-model.number="store.siteForm.payload_kg" min="0" step="10" />
      </div>
      <div class="field">
        <label class="field-label">任务类型</label>
        <div class="choice-row">
          <button class="choice-btn" :class="{ active: store.siteForm.mission === 'cargo' }"
                  @click="store.siteForm.mission = 'cargo'">货运</button>
          <button class="choice-btn" :class="{ active: store.siteForm.mission === 'crew' }"
                  @click="store.siteForm.mission = 'crew'">载人</button>
        </div>
      </div>
    </div>

    <div class="action-row">
      <button class="capsule-btn full-width" @click="runSitePlan" :disabled="store.siteLoading || store.aiBusy"
              :class="{ 'disabled-btn': store.siteLoading || store.aiBusy }">
        <span v-if="store.siteLoading" class="loading-icon"><Loader :size="16" class="spin" /> 正在选站点并逐段复验…</span>
        <span v-else-if="store.aiBusy" class="loading-icon"><Loader :size="16" class="spin" /> AI 正在处理…</span>
        <span v-else class="btn-inner"><MapPin :size="16" /> 规划中继站点</span>
      </button>
    </div>
  </section>

  <!-- ============ 结果区：一次一台，被拒也照出结论与原因 ============ -->
  <section v-if="opt" class="control-section">
    <!-- 结论卡与路径规划页同一套四态：超预算黄、未算出灰，都不是「飞不了」 -->
    <div class="verdict-card" :class="tone">
      <div class="verdict-icon">
        <CheckCircle2 v-if="tone === 'ok'" :size="22" />
        <AlertTriangle v-else-if="tone === 'warn'" :size="22" />
        <HelpCircle v-else-if="tone === 'unknown'" :size="22" />
        <XCircle v-else :size="22" />
      </div>
      <div class="verdict-body">
        <div class="verdict-line">
          <span class="verdict-title">{{ opt.conclusion }}</span>
          <span v-if="opt.feasible && opt.worst_level" class="risk-tag"
                :style="{ backgroundColor: riskColor(opt.worst_level) }">{{ opt.worst_level }}</span>
        </div>
        <div v-if="!opt.feasible" class="verdict-sub">{{ verdictSub }}</div>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">落地次数</span>
        <span v-if="opt.feasible" class="kpi-value">{{ opt.relay_count }}<em>处</em></span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">总里程</span>
        <span v-if="opt.feasible" class="kpi-value">{{ opt.total_distance }}<em>km</em></span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">总航时</span>
        <span v-if="opt.feasible && opt.total_time_h != null" class="kpi-value">{{ opt.total_time_h }}<em>h</em></span>
        <span v-else class="kpi-value na">不适用</span>
      </div>
    </div>
    <div v-if="opt.legs.length" class="sub-block">
      <h4 class="sub-title"><Route :size="14" /> 中继航段</h4>
      <div class="segment-list">
        <div v-for="(l, idx) in opt.legs" :key="l.seq" class="segment-item"
             @mouseenter="store.hoveredSiteLegIndex = idx"
             @mouseleave="store.hoveredSiteLegIndex = null">
          <span class="seg-names">
            <span class="leg-seq">{{ l.seq }}</span>{{ l.from.name }} → {{ l.to.name }}
            <em v-if="!l.reverified" class="leg-flag">未复验</em>
            <em v-else-if="l.over_range" class="leg-flag warn">超段长</em>
          </span>
          <span class="seg-level" :style="{ color: riskColor(l.level ?? '') }">{{ l.level || '—' }}</span>
          <span class="seg-meta">{{ legMeta(l) }}</span>
        </div>
      </div>
    </div>

    <div v-if="opt.feasible" class="opt-actions">
      <button class="ghost-btn" @click="fillRoute(opt)">填入航线</button>
      <button class="capsule-btn" @click="planFrom(opt)">按此起飞重新规划</button>
    </div>
  </section>

  <section v-else class="control-section">
    <p class="hint-text">
      设置起点与终点后，点击「规划中继站点」获取中继链、逐段复验结论与最坏风险档。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue';
import {
  Loader, MapPin, Route, CheckCircle2, XCircle, AlertTriangle, HelpCircle,
} from 'lucide-vue-next';
import {
  store, runSitePlan, applySiteOption, fetchSiteCatalog,
  runPlan, SITE_AIRCRAFT_COLORS, SITE_REASON_TEXT, RISK_COLORS,
  effortText, verdictTone,
} from '../store';
import type { SiteLeg, SiteOption, Verdict } from '../store';

const options = computed<SiteOption[]>(() => store.siteResult?.options ?? []);
/** 一次只算一台：结果区不再用卡片并排做多机对照 */
const opt = computed<SiteOption | null>(() => options.value[0] ?? null);
const aircraftList = computed(() => store.siteCatalog?.aircraft ?? []);

const colorOf = (key: string) => SITE_AIRCRAFT_COLORS[key] ?? '#94a3b8';
const shortName = (a: { label: string }) => a.label.split(' ')[0];
const reasonText = (r?: string | null) => SITE_REASON_TEXT[r ?? ''] ?? (r || '');
const riskColor = (level: string) => RISK_COLORS[level] ?? '#94a3b8';

const tone = computed(() => verdictTone(opt.value?.verdict as Verdict | undefined));

/** 被拒时后端给的是 message（能力包/走廊那几条硬门限），没有就退回 reason 码的译名 */
const verdictSub = computed(() => {
  const o = opt.value;
  if (!o) return '';
  const base = o.message || reasonText(o.reason) || '当前条件下得不出可行的中继链';
  const effort = o.verdict === 'undetermined' ? effortText(o.search_effort) : '';
  return effort ? `${base}（${effort}）` : base;
});

/** 航段行的次要信息：里程（未复验时只能给直线）· 气象判据 · 备注 */
const legMeta = (l: SiteLeg) => [
  l.route_km != null ? `${l.route_km} km` : `直线 ${l.straight_km} km`,
  l.weather?.label,
  l.note,
].filter(Boolean).join(' · ');

/**
 * 选中哪台 = 顺手把速度框换成该机能力口径（自然语言那条走 store 的 parseSiteTask）。
 *
 * 段长本来就是「航时锚点 × 巡航速度」折出来的，框里显示的数必须就是推算用的数，
 * 否则距离按 100 说飞得完、时刻表按 60 说飞多久。多航点这一页不吃框里的值——
 * 速度由能力包定，框是留给另外两个标签页的。
 */
watch([() => store.siteForm.aircraft, aircraftList], ([key]) => {
  const a = aircraftList.value.find(x => x.key === key);
  if (a) store.uavSpeed = a.cruise_kmh;
});

/** 填入航线 = 只改 waypoints/终点，不立刻重规划：让用户自己决定什么时候跑 A* */
const fillRoute = (o: SiteOption) => {
  applySiteOption(o);
};

/** 按此起飞重新规划 = 填入后立刻走现有的三档规划，和「开始规划」同一条链路 */
const planFrom = async (o: SiteOption) => {
  applySiteOption(o);
  store.sidebarTab = 'plan';
  await runPlan();
};

// 站表在 main.ts 里就随应用启动发出去了；这里是启动期那次失败后的重试入口。
onMounted(() => {
  if (!store.siteCatalog) void fetchSiteCatalog();
});

// 切走标签页时组件直接消失，不会补一发 mouseleave；索引留着会让地图一直压暗着其余段
onBeforeUnmount(() => {
  store.hoveredSiteLegIndex = null;
});
</script>

<style scoped>
/* 结论卡 / KPI / 分区标题 / 航段行骨架 / 次要按钮 走 style.css 的「结果区共用词汇」，
   这里只留本页独有的输入区与航段角标。 */
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.field-pair { display: flex; gap: 10px; }
.field-pair .field { flex: 1; }
/* 同一行里输入框要和选项按钮等高：列宽已由 flex:1 拉平，让输入框吃掉
   列内剩余高度，就不必给两边写死同一个 px（字号一改就对不上）。 */
.field-pair .field > .capsule-input { flex: 1; }
.action-row { margin-top: 16px; }

.btn-inner { display: flex; align-items: center; gap: 8px; }
.loading-icon { display: flex; align-items: center; gap: 8px; }
.spin { animation: spin 1.5s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }

/* 机型按钮两行：名字前挂机型色圆点（地图上那条中继链就是这个色），第二行给商载上限。
   只作用在这一排，任务类型那排仍是单行。 */
.craft-btn { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.craft-name { display: flex; align-items: center; gap: 6px; }
.craft-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.craft-cap { font-size: 11px; font-weight: 400; color: var(--text-secondary); }

.leg-seq { color: var(--text-secondary); margin-right: 6px; }
.leg-flag {
  margin-left: 6px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  font-size: 11px;
  font-style: normal;
}
.leg-flag.warn { background: rgba(251, 191, 36, 0.18); color: #fbbf24; }

/* 悬浮行 = 地图聚焦该段（见 MapContainer.renderSitePlan）。行本身只提亮底色，
   不改行高也不描边，否则鼠标划过一列会把整块内容顶得上下跳。 */
.segment-item:hover { background: rgba(255, 255, 255, 0.07); }

/* 两个动作一主一次：填入航线只是改输入，重新规划才跑 A* */
.opt-actions { display: flex; gap: 8px; }
.opt-actions button {
  flex: 1;
  padding: 7px 10px;
  font-size: 13px;
  white-space: nowrap;
}
</style>
