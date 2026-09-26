<template>
  <div v-if="notice" class="notice-backdrop" @click.self="close">
    <div class="notice-card glass-panel" role="dialog" aria-modal="true" :aria-label="notice.title">
      <div class="notice-head">
        <TriangleAlert :size="20" class="notice-icon" />
        <h3 class="notice-title">{{ notice.title }}</h3>
        <button class="notice-close" @click="close" aria-label="关闭">
          <X :size="16" />
        </button>
      </div>

      <p class="notice-body">{{ notice.body }}</p>

      <ul class="notice-actions" v-if="notice.actions.length">
        <li v-for="(item, i) in notice.actions" :key="i">{{ item }}</li>
      </ul>

      <p class="notice-foot">地图与折线图已清掉上一次结果，不会保留旧航线。</p>

      <button class="notice-btn" @click="close">知道了</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue';
import { TriangleAlert, X } from 'lucide-vue-next';
import { store } from '../store';
import { TURB } from '../config';
import type { PlanNoticeReason } from '../store';

interface NoticeCopy {
  title: string;
  body: string;
  actions: string[];
}

/**
 * 几类结论的后端语义互斥，文案不能混用：
 * no_data 是「查不到」，blocked 是「确实被封死」，over_budget 是「有线但太贵」，
 * iteration_limit 是「没搜完」—— 后两种都不能说成「没有航线」。
 */
const COPY: Record<PlanNoticeReason, NoticeCopy> = {
  blocked: {
    title: '未找到安全航线',
    body: `当前时刻的起降点或沿途湍流超过硬阈值（TKE ≥ ${TURB.tkeUnflyable} ${TURB.tkeUnit} 或 EDR ≥ ${TURB.edrUnflyable} ${TURB.edrUnit}），航线被高危区封死。`,
    actions: [
      '换一个起飞时刻，或用「时间窗口」标签找出当天的可飞时段',
      '调整起降点位置',
      '到「可行性评估」查看具体是哪片湍流区挡住了路',
    ],
  },
  over_budget: {
    title: '这一档有安全航线，只是绕行超出预算',
    body: '寻路找到了避开高危湍流区的航线，但绕行比例超过该档任务接受的时间上限。这是成本不达标，不代表飞不了。',
    actions: [
      '换一个湍流更弱的起飞时刻，绕行距离通常会明显缩短',
      '接受这份额外耗时，按绕行比最小的那一档执行',
      '调整起降点，缩短需要绕开的走廊',
    ],
  },
  no_data: {
    title: '该日期或区域没有气象数据',
    body: '数据集只覆盖 2022 年全年、青藏高原一带。当前日期或起降点落在范围外，属于查不到数据，不是没有航线。',
    actions: [
      '把日期改到 2022 年以内',
      '把起降点移回数据覆盖范围内',
    ],
  },
  iteration_limit: {
    title: '搜索未收敛，不代表没有航线',
    body: '寻路在本次任务的展开预算内没能找到满足当前阈值的路径。预算按航程伸缩（短线的下限就是过去的固定值），所以航程很长、或沿途中等以上湍流很多时可能撞上。',
    actions: [
      '在设置里适当放宽 TKE / EDR 阈值',
      '减少途经点，或把长航线拆成几段分别验证',
      '换一个湍流更弱的起飞时刻',
    ],
  },
  request_failed: {
    title: '规划请求失败',
    body: '后端没有正常响应。请确认后端服务已启动（端口见根目录 config.json）。',
    actions: [],
  },
};

const notice = computed<NoticeCopy | null>(() => {
  const n = store.planNotice;
  if (!n) return null;
  const copy = COPY[n.reason] ?? COPY.request_failed;
  // 「没算完」得说清花了多少力气，否则它和「没路」读起来是同一句话
  if (n.reason === 'iteration_limit' && n.effort) {
    return { ...copy, body: `${copy.body}（${n.effort}）` };
  }
  return copy;
});

const close = () => {
  store.planNotice = null;
};

const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && store.planNotice) close();
};

onMounted(() => window.addEventListener('keydown', onKeydown));
onUnmounted(() => window.removeEventListener('keydown', onKeydown));
</script>

<style scoped>
.notice-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(2, 6, 23, 0.55);
  backdrop-filter: blur(2px);
}

.notice-card {
  width: min(420px, calc(100vw - 48px));
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.notice-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.notice-icon {
  color: #fbbf24;
  flex: 0 0 auto;
}

.notice-title {
  margin: 0;
  flex: 1;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}

.notice-close {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  padding: 4px;
  border-radius: 6px;
}

.notice-close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: var(--text-primary);
}

.notice-body {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
}

.notice-actions {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-secondary);
}

.notice-foot {
  margin: 0;
  padding-top: 10px;
  border-top: 1px solid var(--panel-border);
  font-size: 11.5px;
  color: var(--text-secondary);
}

.notice-btn {
  align-self: flex-end;
  padding: 7px 18px;
  border: none;
  border-radius: 8px;
  background: var(--accent-color);
  color: #06121f;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.notice-btn:hover {
  filter: brightness(1.08);
}
</style>
