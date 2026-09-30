<template>
  <div class="ai-sidebar-container" :class="{ 'collapsed': store.aiSidebarCollapsed }">
    <!-- 把手挂在左缘：折叠后整栏往右滑出屏幕，把手是唯一的入口 -->
    <button class="toggle-btn" @click="store.aiSidebarCollapsed = !store.aiSidebarCollapsed">
      <ChevronRight v-if="!store.aiSidebarCollapsed" :size="20" />
      <ChevronLeft v-else :size="20" />
    </button>

    <div class="ai-content glass-panel">
      <div class="ai-head">
        <span class="ai-title"><Sparkles :size="16" /> AI 助手</span>
        <button v-if="turns.length" class="head-btn" :disabled="busy" @click="reset">
          <Eraser :size="12" /> 清空
        </button>
      </div>

      <!-- 唯一的对话流：AI 靠左、用户靠右，正文按 Markdown 渲染 -->
      <div class="ai-scroll" ref="logEl">
        <p v-if="!turns.length" class="chat-empty">{{ emptyHint }}</p>
        <div v-for="(t, i) in turns" :key="i" class="msg" :class="t.role">
          <!-- 助手气泡：动作轨迹在正文**上方**，且与正文同处一个框里。
               分成两个框会让人以为轨迹是「另一件事的旁白」，其实它就是这次回答的
               依据 —— 执行记录在前，结论在后。 -->
          <div v-if="t.role === 'assistant'" class="bubble assistant-bubble">
            <!-- 默认展开：轨迹是这次回答的依据，藏起来等于让人看不到它凭什么这么说。
                 用户主动折叠后就不再自动展开。 -->
            <div v-if="t.steps?.length" class="trace" :class="{ open: traceOpen[i] !== false }">
              <button class="trace-head" @click="toggleTrace(i)">
                <ChevronRight :size="12" class="trace-caret" />
                <span class="trace-title">已执行 {{ t.steps.length }} 个操作</span>
              </button>
              <ul v-if="traceOpen[i] !== false" class="trace-list">
                <li v-for="(s, j) in t.steps" :key="j" :class="s.status">
                  <!-- 状态优先：出错/进行中盖过工具类别，否则一条「正在规划」和
                       一条「规划失败」看起来是同一类操作 -->
                  <component
                    :is="stepIcon(s)"
                    :size="12"
                    class="trace-icon"
                    :class="{ spin: s.status === 'run' }"
                  />
                  <span class="trace-text" :title="s.label">{{ s.label }}</span>
                  <!-- detail 可能很长（时间戳、路径、整句结论），不截断会把左边的
                       操作名挤成多行。硬截断 + 尾部渐隐表示「还有」，完整内容靠 title -->
                  <span v-if="s.detail" class="trace-detail" :title="s.detail">{{ s.detail }}</span>
                </li>
              </ul>
            </div>
            <div class="md" v-html="renderMarkdown(t.text)"></div>
          </div>
          <div v-else class="bubble">{{ t.text }}</div>

          <span v-if="t.source" class="msg-meta">
            {{ t.source === 'llm' ? 'LLM 生成' : '规则兜底' }}
          </span>
          <p v-if="t.error" class="msg-error"><AlertTriangle :size="12" /> {{ t.error }}</p>
        </div>
      </div>

      <div class="composer">
        <!-- 胶囊：简略 label 常显，完整问句靠悬浮/长按展开（触屏没有 hover） -->
        <div v-if="chips.length" class="chips" role="list">
          <button
            v-for="(c, i) in chips" :key="i" class="chip" :disabled="busy"
            :title="c.prompt"
            :aria-label="c.prompt"
            @click="useChip(c)"
            @contextmenu.prevent="expand(c, i)"
          >
            {{ c.label }}
          </button>
        </div>
        <p v-if="expanded" class="chip-full">{{ expanded }}</p>

        <div class="composer-row">
          <textarea v-model="draft" rows="2" :disabled="busy" :placeholder="draftPlaceholder"
                    @keydown.enter.exact.prevent="send(draft)"></textarea>
          <button class="send-btn" :disabled="busy || !draft.trim()" @click="send(draft)">
            <Loader v-if="busy" :size="15" class="spin" />
            <Send v-else :size="15" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import {
  ChevronLeft, ChevronRight, Sparkles,
  Send, Loader, Eraser, AlertTriangle,
  Wrench, MapPin, Calculator, AlertCircle, Crosshair, Layers, PanelLeft,
  ChartLine, Play, SlidersHorizontal, Clock, Package, Plane, Users, Gauge,
  CalendarDays, Trash2, Thermometer, Search,
} from 'lucide-vue-next';
import {
  store, suggestChips, fetchSiteCatalog,
} from '../store';
import type { SuggestChip } from '../store';
import { invokeTool, buildAgentState } from '../agentTools';
import { API_BASE } from '../config';
import { renderMarkdown } from '../utils/markdown';

interface TraceStep {
  tool: string;
  label: string;
  detail?: string;
  status: 'ok' | 'error' | 'run';
}

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  /** 正文来源：只有 AI 那一侧会有；rule = 后端降级，界面必须如实标出来 */
  source?: 'llm' | 'rule';
  /** 本轮真实发生过的工具调用，供轨迹区渲染。这是「Agent 在干嘛」的唯一真相 */
  steps?: TraceStep[];
  error?: string;
}

/** 空状态下的引导胶囊。刻意挑了**能体现多步能力**的句子，
 *  而不是「规划一条航线」这种一个工具就完事的 —— 用户第一眼就该知道这助手会动手。 */
const STARTER_CHIPS: SuggestChip[] = [
  { label: '拉萨送药到昌都', prompt: '从拉萨送 200 公斤兽药到昌都，请规划航线并评估风险' },
  { label: '开图层看曲松县', prompt: '打开 TKE 和 EDR 图层，把地图定位到曲松县，看看那边现在什么情况' },
  { label: '明天下午飞林芝', prompt: '评估明天下午拉萨到林芝的飞行风险' },
];

const turns = ref<Turn[]>([]);
const draft = ref('');
const logEl = ref<HTMLElement | null>(null);
const chips = ref<SuggestChip[]>(STARTER_CHIPS);
const expanded = ref('');
/** 每条助手消息的轨迹折叠与否。按下标存；undefined = 默认展开。
 *  用 `!== false` 判定而不是 truthy，是为了让「默认展开」和「用户手动折叠」能区分开。 */
const traceOpen = ref<Record<number, boolean>>({});
const toggleTrace = (i: number) => { traceOpen.value[i] = traceOpen.value[i] === false; };

/** 轨迹图标按工具类别分，让用户一眼看出这条是"动了界面"还是"算了数据"。 */
const TOOL_ICONS: Record<string, Component> = {
  add_waypoint: MapPin, update_waypoint: MapPin, remove_waypoint: MapPin,
  set_endpoints: MapPin, focus_region: Crosshair,
  run_plan: Calculator, run_site_plan: Calculator, run_time_window: Calculator,
  query_weather: Thermometer, search_sites: Search,
  toggle_overlay: Layers, switch_tab: PanelLeft, toggle_chart: ChartLine,
  play_simulation: Play, set_priority: SlidersHorizontal, set_time: Clock,
  set_payload_kg: Package, set_aircraft: Plane, set_mission: Users,
  set_speed: Gauge, set_forecast_days: CalendarDays, clear_route: Trash2,
};
const TOOL_VERBS: Record<string, string> = {
  toggle_overlay: '切换图层', focus_region: '移动地图', switch_tab: '切换页签',
  set_time: '修改时间', set_priority: '切换档位', set_payload_kg: '修改载重',
  set_aircraft: '切换机型', set_mission: '修改任务类型', set_speed: '修改速度',
  set_forecast_days: '修改推演天数', toggle_chart: '切换剖面图',
  add_waypoint: '添加途经点', update_waypoint: '修改途经点',
  remove_waypoint: '删除途经点', set_endpoints: '修改端点',
  clear_route: '清空航线',
};
/** 慢工具（要读气象场的）：先显示「正在…」，跑完再原地改写成结论 */
const SLOW_TOOLS = new Set(['run_plan', 'run_site_plan', 'run_time_window', 'query_weather']);
const SLOW_VERBS: Record<string, string> = {
  run_plan: '规划航线并评估三条候选',
  run_site_plan: '选站点并逐段复验',
  run_time_window: '扫描各时段气象条件',
  query_weather: '取各地点实况数据',
};

/** 状态盖过类别：出错与进行中用固定的告警/转圈图标，其余按工具类别取。 */
const stepIcon = (s: TraceStep) => {
  if (s.status === 'error') return AlertCircle;
  if (s.status === 'run') return Loader;
  return TOOL_ICONS[s.tool] ?? Wrench;
};

// busy 放在 store 上：左栏的规划按钮也要禁用，否则用户能在 AI 联动期间
// 从左边再点一次「开始规划」，两份结果互相覆盖，AI 讲的和面板显示的对不上。
const busy = computed({
  get: () => store.aiBusy,
  set: (v: boolean) => { store.aiBusy = v; },
});

const emptyHint = computed(() =>
  '直接说要做什么，例如「从拉萨送 200 公斤兽药到昌都」。我会自动切到对应模式、填好参数并把结果算出来。'
);

const draftPlaceholder = computed(() =>
  busy.value ? '正在处理…' : '说出你的任务，Enter 发送'
);

/** 推一条空的助手消息并返回它：整轮的正文都写进这一条，不另起新气泡 */
const pushAssistant = () => {
  turns.value.push({ role: 'assistant', text: '' });
  return turns.value[turns.value.length - 1];
};

/**
 * 一轮对话 = **Agent 循环**。
 *
 * 流程：问后端「该调哪些工具」→ 逐个真的执行（每一步都渲染进轨迹）→ 带着执行
 * 结果再问后端 → 直到它不再要工具，吐出最终正文。
 *
 * 为什么要做成「后端吐一批、前端执行、再回灌」的往返，而不是后端一口气出答案：
 * 模型必须**看到工具结果**才能接着判断。「查了 50 km 内没有备降点」它才知道该
 * 如实说没有；「定位曲松县」失败了它才知道要换名字。一步到位就等于让它闭眼编。
 *
 * 往返也带来一个附带好处：每一步都是独立请求，断在哪都能从那一步重试。
 */
const send = async (raw: string) => {
  const text = raw.trim();
  if (!text || busy.value) return;
  draft.value = '';
  expanded.value = '';
  busy.value = true;
  // 本轮之前的历史快照：工具回灌时要带上，但不能把本轮的占位气泡算进去
  const history = turns.value.filter(t => t.text)
    .map(t => ({ role: t.role, content: t.text }));
  try {
    turns.value.push({ role: 'user', text });
    const reply = pushAssistant();
    reply.steps = [];
    reply.source = 'llm';

    let toolResults: Record<string, any>[] = [];
    // 前端与后端各有一道轮数闸（这里 6、agent.py 里 MAX_STEPS=5）。两边都留一道是
    // 有意的：任何一边单独失效都还收得住，不至于让模型无限兜圈。
    for (let round = 0; round < 6; round++) {
      // 只有最后一轮（不再要工具）才让正文占住气泡：前几轮正文是空的，
      // 气泡里显示的是轨迹。用户看到的是「一步步在动 → 得出结论」。
      const step = await callAgent(history, text, toolResults, round, reply);
      if (step.error) {
        reply.error = step.error;
        break;
      }
      if (!step.calls.length) break;   // 正文已经流进 reply 了

      // 同一批里完全相同的调用只执行一次。模型偶尔会把 run_plan 连写两遍
      // （用户切档时尤其容易），两次都真跑就是白烧两轮 A* 拿回一样的数字。
      const seen = new Set<string>();
      const batch = step.calls.filter(c => {
        const key = `${c.tool}:${JSON.stringify(c.args ?? {})}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });

      // 这一批要真的执行。每调完一个就把结果记进轨迹，用户能看着它一步步动。
      for (const call of batch) {
        const tool = call.tool;
        const slow = SLOW_TOOLS.has(tool);
        // 慢工具先落一条「正在…」再原地改写：一次三档 A* 要十几秒，
        // 中间没有动静的话用户会以为卡死了。快工具不必多这一次闪烁。
        reply.steps!.push({
          tool,
          label: slow ? `正在${SLOW_VERBS[tool] ?? '处理'}…` : `${TOOL_VERBS[tool] ?? '处理'}中…`,
          status: 'run',
        });
        // 取**响应式数组里那一项**再改，而不是改 push 进去的原始对象：
        // Vue 的数组存的是代理，改原始对象不触发重渲染（之前看着能动是搭了
        // 别的更新的便车，这里改成不依赖巧合）。
        const row = reply.steps![reply.steps!.length - 1];

        const r = await invokeTool(tool, call.args || {});
        row.label = r.label;
        row.detail = r.detail;
        row.status = r.ok ? 'ok' : 'error';
        // data 只放工具真拿到的结果；模型只能引用它，所以失败时这里是空的
        toolResults.push({ tool, ok: r.ok, label: r.label, data: r.data ?? null });
      }
      // 下一步要按**工具改完之后的界面状态**来判断，所以每轮都重新取快照
      if (!toolResults.length) break;
    }

    if (!reply.text && !reply.error) {
      reply.text = '（没有可执行的操作，也没有可回答的内容）';
    }

    // 胶囊只在拿到回答后刷，避免在工具执行途中就把建议摆出来
    const last = reply.text;
    const recent = turns.value.filter(t => t.role === 'user' && t.text).map(t => t.text).slice(-3);
    const got = await suggestChips({
      task: store.timeWindow && store.sidebarTab === 'timeWindow' ? 'time_window' : 'feasibility',
      payload: (store.timeWindow && store.sidebarTab === 'timeWindow'
        ? store.timeWindow : store.feasibility) as any,
      last_assistant: last,
      recent,
      state: {
        sidebar_tab: store.sidebarTab,
        has_result: !!(store.feasibility || store.timeWindow || store.siteResult),
        verdict: store.feasibility?.verdict,
        level: store.feasibility?.risk?.overall,
      },
    });
    chips.value = got.chips?.length ? got.chips : STARTER_CHIPS;
  } catch (error) {
    console.error('Agent 循环失败:', error);
    const failed = turns.value[turns.value.length - 1];
    const msg = '没能完成这次操作。可以换个说法，或者直接在左栏手动操作。';
    if (failed?.role === 'assistant') { failed.text = msg; failed.source = 'rule'; }
  } finally {
    busy.value = false;
  }
};

/**
 * 问后端一步。返回要么是要调的工具，要么是最终正文，不会两者都有。
 *
 * ``reply`` 是本轮那条**响应式**助手消息：delta 直接累加到它身上，每个 chunk 都会
 * 触发一次重渲染，正文才是真流式出来的。攒在局部变量里等读完再一次性赋值看着
 * "结果一样"，实际体感是卡十几秒后整段蹦出来 —— 一次三档 A* 十几秒，这个差别很大。
 */
const callAgent = async (
  history: { role: string; content: string }[],
  text: string,
  toolResults: Record<string, any>[],
  roundIndex: number,
  reply: Turn,
): Promise<{ calls: any[]; error?: string }> => {
  const out: { calls: any[]; error?: string } = { calls: [] };
  try {
    const response = await fetch(`${API_BASE}/api/assess/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [...history, { role: 'user', content: text }],
        state: buildAgentState(),
        tool_results: toolResults,
        round_index: roundIndex,
      }),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    // 工具轮里气泡上写的是「正在规划…」这类中间态；正文轮第一个 delta 到达时
    // 必须把它清掉，否则最终回答会接在中间态后面，变成
    // 「正在规划…这是结论」。delta 只会在正文轮出现，所以清一次就够。
    let clearedPlaceholder = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let event: any;
        try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (event.type === 'tool_calls') out.calls = event.calls ?? [];
        else if (event.type === 'delta' && event.text) {
          if (!clearedPlaceholder) { reply.text = ''; clearedPlaceholder = true; }
          reply.text += event.text;
        }
        else if (event.type === 'error') out.error = event.message ?? '生成失败';
      }
    }
  } catch (error) {
    console.error('Agent 请求失败:', error);
    out.error = 'AI 服务不可用，请确认后端已启动。';
  }
  return out;
};

/** 点胶囊 = 填入完整问句并直接发出，保持「对话即操作」的连贯感 */
const useChip = (chip: SuggestChip) => {
  if (busy.value) return;
  void send(chip.prompt);
};

/** 触屏没有 hover：长按/右键把完整问句显示出来，否则用户看不到自己将发出什么 */
const expand = (chip: SuggestChip, _i: number) => {
  expanded.value = expanded.value === chip.prompt ? '' : chip.prompt;
};

const reset = () => {
  turns.value = [];
  chips.value = STARTER_CHIPS;
  expanded.value = '';
  traceOpen.value = {};
};

// 新消息与流式增量都要把日志拉到底部
watch(
  () => [turns.value.length, turns.value[turns.value.length - 1]?.text.length ?? 0] as const,
  async () => {
    await nextTick();
    const el = logEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  }
);

// 意图识别要用站表把「曲松县」这类地名解成坐标，站表没取过就先取一次。
// 重复调用没有代价（store 里已按 null 判空）。
onMounted(() => {
  if (!store.siteCatalog) void fetchSiteCatalog();
});
</script>

<style scoped>
.ai-sidebar-container {
  position: absolute;
  top: 50%;
  right: var(--rail-gap);
  transform: translateY(-50%);
  z-index: 1000;
  display: flex;
  height: calc(100vh - var(--rail-gap) * 2);
  max-height: 900px;
  transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.ai-sidebar-container.collapsed {
  transform: translate(calc(100% + var(--rail-gap)), -50%);
}

/* 宽度、高度与折叠方式都和左栏同一套，两栏只是分居左右 */
.ai-content {
  width: var(--rail-w);
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.toggle-btn {
  position: absolute;
  left: -32px;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 60px;
  border-radius: 16px 0 0 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: var(--panel-bg);
  -webkit-backdrop-filter: blur(20px);
  backdrop-filter: blur(20px);
  border: 1px solid var(--panel-border);
  border-right: none;
  color: var(--text-secondary);
  box-shadow: -4px 0 12px rgba(0, 0, 0, 0.2);
  transition: color 0.3s ease;
  z-index: -1;
}

.toggle-btn:hover { color: var(--text-primary); }

.ai-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--panel-border);
  background: rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
}

.ai-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.5px;
}

.ai-title svg { color: var(--accent-color); }

.head-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  font-size: 11px;
  border-radius: 9999px;
  border: 1px solid var(--panel-border);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}
.head-btn:hover:not(:disabled) { color: var(--text-primary); }
.head-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.ai-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.chat-empty {
  margin: 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.msg { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; }
.msg.user { align-items: flex-end; }

.bubble {
  max-width: 92%;
  padding: 8px 11px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 1.7;
  word-break: break-word;
}

.msg.assistant .bubble {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--panel-border);
  border-bottom-left-radius: 4px;
  color: var(--text-primary);
}

/* 助手气泡里塞了轨迹，宽度要放开 —— 轨迹里的地名/数字比正文的长 */
.assistant-bubble {
  max-width: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}

.msg.user .bubble {
  background: rgba(52, 152, 219, 0.18);
  border: 1px solid rgba(52, 152, 219, 0.42);
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}

/* 模型给的 Markdown 结构要在窄栏里读得下去：段落间距压到最小 */
.md :deep(p) { margin: 0 0 6px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(h3), .md :deep(h4) { font-size: 12px; margin: 8px 0 4px; color: var(--text-primary); }
.md :deep(ul), .md :deep(ol) { margin: 0 0 6px; padding-left: 18px; }
.md :deep(li) { margin: 0 0 2px; }
.md :deep(code) { font-size: 11px; background: rgba(0, 0, 0, 0.3); padding: 1px 4px; border-radius: 4px; }

/* 表格：撑满气泡 + 圆角 + 按内容分配列宽 + 双向居中。
   - 圆角必须用 `border-collapse: separate`：collapse 会把圆角和裁剪一起吃掉，
     单元格的底色会从圆角处溢出去。
   - 列宽比例交给 `table-layout: auto`（默认行为）：浏览器按各列**内容宽度**分配，
     再把富余宽度按同样比例摊回去。所以不能给某列加 nowrap —— 那会把它钉死成
     一个宽度，比例分配就失效了。
   - 居中用 th/td 的 text-align + vertical-align：Markdown 表格没有对齐语义，
     交给模型写对齐符号不如统一居中好看。 */
.md :deep(table) {
  width: 100%;
  table-layout: auto;
  border-collapse: separate;
  border-spacing: 0;
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 11px;
  line-height: 1.55;
  margin: 6px 0 8px;
}

.md :deep(th) {
  background: rgba(0, 0, 0, 0.3);
  color: var(--text-secondary);
  font-weight: 600;
  text-align: center;
  vertical-align: middle;
  padding: 6px 10px;
  border-bottom: 1px solid var(--panel-border);
}

.md :deep(td) {
  text-align: center;
  vertical-align: middle;
  padding: 6px 10px;
  border-bottom: 1px solid var(--panel-border);
  word-break: break-word;   /* 长地名/长结论换行，而不是把整张表顶出气泡 */
}

.md :deep(tr:last-child td) { border-bottom: none; }
.md :deep(td:first-child) { color: var(--text-secondary); }
.md :deep(td + td) { color: var(--text-primary); }

.md :deep(a) { color: var(--accent-color); }

.msg-meta { font-size: 10px; color: var(--text-secondary); }

/* ---- 动作轨迹 ----
   这是「让 Agent 的思考可见」的落点：每一条都对应一次真实的工具调用。
   它是**助手气泡的一部分**（在正文上方），不是外挂的旁白框 —— 执行记录在前、
   结论在后，同一个框里读完。 */
.trace {
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.22);
  overflow: hidden;
  flex-shrink: 0;
}

.trace-head {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 5px 9px;
  font-size: 10px;
  font-family: inherit;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}
.trace-head:hover { color: var(--text-primary); }

.trace-caret { transition: transform 0.2s; flex-shrink: 0; }
.trace.open .trace-caret { transform: rotate(90deg); }

.trace-title { font-weight: 600; letter-spacing: 0.3px; }

.trace-list {
  margin: 0;
  padding: 1px 9px 7px 9px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-top: 1px dashed rgba(148, 163, 184, 0.18);
  padding-top: 6px;
}

.trace-list li {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-primary);
  /* 两列都不许换行：换行会把一行操作撑成三行，轨迹整体节奏就散了 */
  flex-wrap: nowrap;
  min-width: 0;
}

.trace-icon {
  flex-shrink: 0;
  align-self: center;
  color: var(--text-secondary);
  opacity: 0.85;
}
.trace-list li.error .trace-icon { color: #fca5a5; }
.trace-list li.error .trace-text { color: #fca5a5; }
.trace-list li.run .trace-text { color: var(--text-secondary); }

/* 操作名：优先保住自己的宽度，实在放不下才截（它比 detail 更能说明做了什么） */
.trace-text {
  flex: 0 1 auto;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 补充说明：硬截断 + 尾部渐隐。
   右侧这列天然是长的（时间戳、整句结论、路径），不限宽就会把左边操作名挤没。
   padding-right 留出的空白正好落在 mask 的渐隐区里，所以省略号不会被一起淡掉 ——
   省略号留在可见区，渐隐只吃掉「还有更多」的那段空白。 */
.trace-detail {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 48%;
  /* margin-left:auto 把它顶到行尾：补充信息是「结果」而不是「过程」，
     贴右对齐后整列读起来是一条竖线，扫视时不用逐行去找。
     渐隐也因此总出现在同一个位置（行的最右端），而不是跟着文字长度飘。 */
  margin-left: auto;
  padding-right: 16px;
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.85;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 18px), transparent 100%);
  mask-image: linear-gradient(to right, #000 calc(100% - 18px), transparent 100%);
}

/* 没有 title 提示的场合（截断时也不该让鼠标停在死字上） */
.trace-detail:hover { color: var(--text-primary); }

.spin { animation: spin 1s linear infinite; }

.msg-error {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  font-size: 11px;
  color: #fca5a5;
}

.composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 20px 16px;
  border-top: 1px solid var(--panel-border);
  background: rgba(0, 0, 0, 0.15);
}

/* 胶囊横排可换行：窄栏里三条一行放不下，挤成两行比横向滚动好点得多 */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  padding: 5px 10px;
  font-size: 11px;
  font-family: inherit;
  border-radius: 9999px;
  border: 1px solid var(--panel-border);
  background: rgba(56, 189, 248, 0.10);
  color: var(--text-primary);
  cursor: pointer;
  white-space: nowrap;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: border-color 0.2s, background 0.2s;
}
.chip:hover:not(:disabled) { border-color: var(--accent-color); background: rgba(56, 189, 248, 0.18); }
.chip:disabled { opacity: 0.45; cursor: not-allowed; }

/* 触屏没有 hover，长按/右键后把完整问句摊在这里 */
.chip-full {
  margin: 0;
  padding: 7px 10px;
  font-size: 11px;
  line-height: 1.6;
  border-radius: 8px;
  border: 1px dashed var(--panel-border);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
}

.composer-row { display: flex; align-items: flex-end; gap: 8px; }

.composer-row textarea {
  flex: 1;
  min-width: 0;
  resize: none;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.6;
  padding: 8px 12px;
  border-radius: 12px;
  border: 1px solid var(--panel-border);
  background: rgba(0, 0, 0, 0.25);
  color: var(--text-primary);
}
.composer-row textarea:focus { outline: none; border-color: var(--accent-color); }

.send-btn {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: var(--accent-color);
  color: #fff;
  cursor: pointer;
  transition: background 0.2s, opacity 0.2s;
}
.send-btn:hover:not(:disabled) { background: var(--accent-hover); }
.send-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
