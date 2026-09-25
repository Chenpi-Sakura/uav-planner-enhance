<template>
  <!-- 三档横向并排：点击=切为当前航线，悬停=地图上临时预览该档 -->
  <section class="tier-cards-section">
    <div class="tier-cards">
      <button
        v-for="opt in cards"
        :key="opt.priority"
        class="tier-card"
        :class="[opt.tone, { selected: store.priority === opt.priority }]"
        :style="{ borderLeftColor: TIER_COLORS[opt.priority] }"
        :title="opt.title"
        @click="choose(opt.priority)"
        @mouseenter="store.hoveredTier = opt.priority"
        @mouseleave="store.hoveredTier = null"
      >
        <span class="card-tier">{{ PRIORITY_LABELS[opt.priority] ?? opt.priority }}</span>
        <span class="card-verdict">{{ opt.conclusion }}</span>
        <span class="card-metric">{{ opt.routeKm ?? '—' }}<i v-if="opt.routeKm"> km</i></span>
        <span class="card-metric">绕行 {{ opt.detour ?? '—' }}</span>
        <span class="card-metric">{{ opt.level || '—' }}</span>
      </button>
    </div>
    <p v-if="meta?.all_identical" class="card-note">
      该时段沿程湍流未超过中风险下沿，三档算出的是同一条线
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { store, FEAS_REASON_TEXT, PRIORITY_LABELS, TIER_COLORS, verdictTone } from '../store';
import { activateRouteTier } from '../store';
import type { FeasibilityResult } from '../store';

const meta = computed(() => store.routeOptions?.meta ?? null);

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
  return `${label}：${FEAS_REASON_TEXT[r.reason ?? ''] ?? r.conclusion}`;
};

const cards = computed(() =>
  (store.routeOptions?.results ?? []).map(r => {
    const d = r.distance;
    return {
      priority: r.priority,
      tone: verdictTone(r.verdict),
      conclusion: r.conclusion,
      routeKm: d.applicable ? d.route_km : null,
      detour: d.applicable && d.detour_ratio != null ? `${d.detour_ratio}×` : null,
      level: r.risk?.overall ?? null,
      title: tipFor(r),
    };
  })
);

const choose = (priority: string) => {
  activateRouteTier(priority);
};
</script>

<style scoped>
.tier-cards-section {
  margin-bottom: 14px;
}

.tier-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.tier-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 10px 8px;
  border-radius: 10px;
  border: 1px solid var(--panel-border);
  border-left: 3px solid;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, opacity 0.15s, box-shadow 0.15s;
}

.tier-card.ok .card-verdict { color: #4ade80; }
.tier-card.bad .card-verdict { color: #f87171; }
/* 超预算=黄（线存在，只是贵），未算出=灰（还没结论）：都不是红色那种「飞不了」 */
.tier-card.warn .card-verdict { color: #fbbf24; }
.tier-card.unknown .card-verdict { color: #94a3b8; }

.tier-card:hover {
  background: rgba(255, 255, 255, 0.09);
}

/* 未选中的卡片整体压暗，和地图上「选中 100% / 其余 30%」的视觉语言对齐 */
.tier-card:not(.selected) { opacity: 0.62; }

.tier-card.selected {
  background: rgba(56, 189, 248, 0.12);
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) inset;
  opacity: 1;
}

.card-tier {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary, #e2e8f0);
}

.card-verdict {
  font-size: 11px;
  font-weight: 600;
}

.card-metric {
  font-size: 11px;
  line-height: 1.45;
  white-space: nowrap;
}

.card-metric i {
  font-style: normal;
  opacity: 0.7;
}

.card-note {
  margin: 8px 2px 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
}
</style>
