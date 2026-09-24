<template>
  <!-- 三档横向并排：点击=切为当前航线，悬停=地图上临时预览该档 -->
  <section class="tier-cards-section">
    <div class="tier-cards">
      <button
        v-for="opt in cards"
        :key="opt.priority"
        class="tier-card"
        :class="[opt.feasible ? 'ok' : 'bad', { selected: store.priority === opt.priority }]"
        :style="{ borderLeftColor: TIER_COLORS[opt.priority] }"
        :title="opt.title"
        @click="choose(opt.priority)"
        @mouseenter="store.hoveredTier = opt.priority"
        @mouseleave="store.hoveredTier = null"
      >
        <span class="card-tier">{{ PRIORITY_LABELS[opt.priority] ?? opt.priority }}</span>
        <span class="card-verdict">{{ opt.feasible ? '可飞' : '不可飞' }}</span>
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
import { store, PRIORITY_LABELS, TIER_COLORS } from '../store';
import { activateRouteTier } from '../store';

const meta = computed(() => store.routeOptions?.meta ?? null);

const cards = computed(() =>
  (store.routeOptions?.results ?? []).map(r => {
    const d = r.distance;
    return {
      priority: r.priority,
      feasible: r.feasible,
      routeKm: d.applicable ? d.route_km : null,
      detour: d.applicable && d.detour_ratio != null ? `${d.detour_ratio}×` : null,
      level: r.risk?.overall ?? null,
      title: r.feasible
        ? `${PRIORITY_LABELS[r.priority] ?? r.priority}：飞行 ${(d.flight_time_h ?? 0).toFixed(2)} h`
        : `不可飞：${r.reason ?? ''}`,
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
