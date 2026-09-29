<template>
  <!-- 三档胶囊：只报档位名与可飞状态，点击=切为当前航线，悬停=地图上临时预览该档 -->
  <section class="tier-pills-section">
    <div class="tier-pills">
      <button
        v-for="opt in cards"
        :key="opt.priority"
        class="tier-pill"
        :class="{ selected: store.priority === opt.priority }"
        :style="{ borderColor: TIER_COLORS[opt.priority] }"
        :title="opt.title"
        @click="choose(opt.priority)"
        @mouseenter="store.hoveredTier = opt.priority"
        @mouseleave="store.hoveredTier = null"
      >
        <span class="pill-tier">{{ PRIORITY_LABELS[opt.priority] ?? opt.priority }}</span>
        <span class="pill-verdict">{{ TONE_GLYPH[opt.tone] }} {{ opt.conclusion }}</span>
      </button>
    </div>
    <p v-if="meta?.all_identical" class="card-note">
      该时段湍流未超中风险下沿，三档为同一条线
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { store, FEAS_REASON_TEXT, PRIORITY_LABELS, TIER_COLORS, effortText, verdictTone } from '../store';
import { activateRouteTier } from '../store';
import type { FeasibilityResult } from '../store';

const meta = computed(() => store.routeOptions?.meta ?? null);

/**
 * 四态靠字形而不是靠颜色：描边已经是档位色，再给状态字叠一套语义色，
 * 一枚胶囊上就有两套颜色在抢注意力。✓ 可飞 / ⚠ 超预算 / ✕ 不可飞 / ? 未算出。
 */
const TONE_GLYPH: Record<string, string> = { ok: '✓', warn: '⚠', bad: '✕', unknown: '?' };

/**
 * 悬停提示按结论四态各说各的话：三档只差湍流软惩罚倍率，越保守的档越容易
 * 撞上绕行预算或迭代上限，把这两种也写成「不可飞」正好把对比读反。
 */
const tipFor = (r: FeasibilityResult) => {
  const label = PRIORITY_LABELS[r.priority] ?? r.priority;
  const d = r.distance;
  if (r.feasible) return `${label}：飞行 ${(d.flight_time_h ?? 0).toFixed(2)} h`;
  if (r.verdict === 'over_budget') {
    return `${label}：存在安全航线，绕行 ${d.detour_ratio}× 超出本档上限 ${d.time_cost_h.limit_ratio}×`;
  }
  const base = `${label}：${FEAS_REASON_TEXT[r.reason ?? ''] ?? r.conclusion}`;
  const effort = r.verdict === 'undetermined' ? effortText(r.search_effort) : '';
  return effort ? `${base}（${effort}）` : base;
};

const cards = computed(() =>
  (store.routeOptions?.results ?? []).map(r => ({
    priority: r.priority,
    tone: verdictTone(r.verdict),
    conclusion: r.conclusion,
    title: tipFor(r),
  }))
);

const choose = (priority: string) => {
  activateRouteTier(priority);
};
</script>

<style scoped>
.tier-pills-section {
  margin-bottom: 14px;
}

.tier-pills {
  display: flex;
  gap: 8px;
}

/* 整圈描边，颜色=该档在地图上的线色，胶囊即图例。
   用 2px 而不是 1px：新调色板（#0081a7 / #6a994e）明度偏低，1px 压在深色面板上几乎看不见。 */
.tier-pill {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 6px;
  border-radius: 9999px;
  border: 2px solid var(--panel-border);
  background: rgba(255, 255, 255, 0.04);
  color: #ffffff;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, opacity 0.15s;
}

.pill-tier {
  font-size: 14px;
  font-weight: 600;
}

.pill-verdict {
  font-size: 13px;
}

.tier-pill:hover { background: rgba(255, 255, 255, 0.09); }

/* 未选中的压暗，和地图上「选中 100% / 其余 30%」的视觉语言对齐 */
.tier-pill:not(.selected) { opacity: 0.62; }

.tier-pill.selected {
  background: rgba(255, 255, 255, 0.12);
  opacity: 1;
}

.card-note {
  margin: 8px 2px 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-secondary);
}
</style>
