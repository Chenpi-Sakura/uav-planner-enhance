<template>
  <section class="site-panel">
    <!-- 机型：一次只算一台（双机对照已于 2026-09-28 撤销）。不预选 = 后端按默认那台出 -->
    <div class="field">
      <label class="field-label">机型</label>
      <div class="choice-row">
        <button class="choice-btn" v-for="a in aircraftList" :key="a.key"
                :class="{ active: store.siteForm.aircraft === a.key }"
                @click="store.siteForm.aircraft = a.key">{{ shortName(a) }}</button>
      </div>
    </div>

    <div class="field-row">
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

    <!-- 起降点与途经点在左栏顶部的共用区编辑，这里不再回显 -->
    <p class="hint">途经点仅用于圈定走廊，「填入航线」会整批替换。一句话任务在右侧 AI 助手栏。</p>

    <button class="capsule-btn full-width" @click="runSitePlan" :disabled="store.siteLoading"
            :class="{ 'disabled-btn': store.siteLoading }">
      <span v-if="store.siteLoading" class="loading-icon"><Loader :size="16" class="spin" /> 正在选站点并逐段复验…</span>
      <span v-else style="display:flex; align-items:center; gap:8px;"><MapPin :size="16" /> 规划中继站点</span>
    </button>

    <!-- 结果卡：一次一台，被拒也照出结论与原因 -->
    <div class="options" v-if="options.length">
      <div class="opt-card" v-for="o in options" :key="o.aircraft"
           :style="{ borderColor: colorOf(o.aircraft) }">
        <div class="opt-head">
          <span class="dot-color" :style="{ background: colorOf(o.aircraft) }"></span>
          <span class="opt-title">{{ o.label }}</span>
        </div>
        <div class="opt-line">
          <span>结论</span>
          <b :class="verdictClass(o)">{{ o.conclusion }}</b>
        </div>
        <div class="opt-line" v-if="o.leg_limit_km">
          <span>段长上限</span><b>{{ o.leg_limit_km }} km</b>
        </div>
        <div class="opt-line" v-if="o.feasible">
          <span>落地次数</span><b>{{ o.relay_count }} 处</b>
        </div>
        <div class="opt-line" v-if="o.feasible">
          <span>总里程</span><b>{{ o.total_distance }} km</b>
        </div>
        <div class="opt-line" v-if="o.feasible && o.total_time_h != null">
          <span>总航时</span><b>{{ o.total_time_h }} h</b>
        </div>
        <div class="opt-line" v-if="o.feasible && o.worst_level">
          <span>最坏风险</span><b>{{ o.worst_level }}</b>
        </div>
        <div class="opt-line">
          <span>可用站点</span><b>{{ o.usable_sites }}</b>
        </div>
        <p class="opt-msg" v-if="o.message">{{ o.message }}</p>
        <p class="opt-msg muted" v-else-if="o.reason">{{ reasonText(o.reason) }}</p>

        <div class="opt-actions" v-if="o.feasible">
          <button class="capsule-btn tiny" @click.stop="fillRoute(o)">填入航线</button>
          <button class="capsule-btn tiny" @click.stop="planFrom(o)">按此起飞重新规划</button>
        </div>

        <ol class="legs" v-if="o.feasible">
          <li v-for="l in o.legs" :key="l.seq">
            <span class="leg-seq">{{ l.seq }}</span>
            <span class="leg-name">{{ l.from.name }} → {{ l.to.name }}</span>
            <span class="leg-km">{{ l.route_km != null ? l.route_km + ' km' : '直线 ' + l.straight_km + ' km' }}</span>
            <span class="leg-flag" v-if="!l.reverified">未复验</span>
            <span class="leg-flag warn" v-else-if="l.over_range">超段长</span>
            <div class="leg-sub">
              <span v-if="l.level">风险 {{ l.level }}</span>
              <span v-if="l.weather?.label">{{ l.weather.label }}</span>
              <span v-if="l.note">{{ l.note }}</span>
            </div>
          </li>
        </ol>

        <ul class="notes" v-if="o.notes?.length">
          <li v-for="(n, i) in o.notes" :key="i">{{ n }}</li>
        </ul>
      </div>
    </div>

    <p class="hint" v-if="!options.length">尚未规划。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import { Loader, MapPin } from 'lucide-vue-next';
import {
  store, runSitePlan, applySiteOption, fetchSiteCatalog,
  runPlan, SITE_AIRCRAFT_COLORS, SITE_REASON_TEXT,
} from '../store';
import type { SiteOption } from '../store';

const options = computed<SiteOption[]>(() => store.siteResult?.options ?? []);
const aircraftList = computed(() => store.siteCatalog?.aircraft ?? []);

const colorOf = (key: string) => SITE_AIRCRAFT_COLORS[key] ?? '#94a3b8';
const shortName = (a: { label: string }) => a.label.split(' ')[0];
const reasonText = (r?: string | null) => SITE_REASON_TEXT[r ?? ''] ?? (r || '');

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

const verdictClass = (o: SiteOption) => ({
  ok: o.feasible,
  warn: !o.feasible && o.verdict === 'undetermined',
  bad: !o.feasible && o.verdict !== 'undetermined',
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

onMounted(() => {
  if (!store.siteCatalog) void fetchSiteCatalog();
});
</script>

<style scoped>
.site-panel { display: flex; flex-direction: column; gap: 10px; padding: 4px 0 12px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field-row { display: flex; gap: 10px; }
.field-row .field { flex: 1; }
/* 同一行里输入框要和选项按钮等高：列宽已由 flex:1 拉平，让输入框吃掉
   列内剩余高度，就不必给两边写死同一个 px（字号一改就对不上）。 */
.field-row .field > .capsule-input { flex: 1; }
/* 字号与另两个页签同一档：标签/选项 14、正文 13、次要 12、角标 11 */
.field-label { font-size: 14px; color: var(--text-secondary); }
.hint { font-size: 13px; color: var(--text-secondary); margin: 0; line-height: 1.5; }
.capsule-btn.tiny { padding: 5px 10px; font-size: 12px; width: auto; }
.options { display: flex; flex-direction: column; gap: 8px; }
.opt-card {
  border: 1px solid transparent; border-radius: 8px; padding: 8px 10px;
  background: rgba(255,255,255,0.04);
}
.opt-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.opt-title { font-size: 14px; color: var(--text-primary); }
.dot-color { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.opt-line { display: flex; justify-content: space-between; font-size: 13px; color: var(--text-secondary); }
.opt-line b { font-weight: 500; }
.opt-line b.ok { color: #4ade80; }
.opt-line b.warn { color: #fbbf24; }
.opt-line b.bad { color: #f87171; }
.opt-msg { font-size: 13px; color: #fca5a5; margin: 6px 0 0; line-height: 1.5; }
.opt-msg.muted { color: var(--text-secondary); }
.opt-actions { display: flex; gap: 6px; margin-top: 8px; }
.legs { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.legs li { display: grid; grid-template-columns: 18px 1fr auto; gap: 6px; align-items: center; font-size: 13px; }
.leg-seq { color: var(--text-secondary); }
.leg-name { color: var(--text-primary); }
.leg-km { color: var(--text-secondary); }
.leg-flag { font-size: 11px; color: var(--text-secondary); border: 1px solid rgba(255,255,255,0.15);
            border-radius: 4px; padding: 1px 4px; white-space: nowrap; }
.leg-flag.warn { color: #fbbf24; border-color: rgba(251,191,36,0.4); }
.leg-sub { grid-column: 2 / span 2; font-size: 12px; color: var(--text-secondary); display: flex; gap: 8px; }
.notes { margin: 8px 0 0; padding-left: 16px; font-size: 12px; color: var(--text-secondary); line-height: 1.5; }
.full-width { width: 100%; }
.loading-icon { display: flex; align-items: center; gap: 8px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
