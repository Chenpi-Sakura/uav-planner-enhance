<template>
  <div class="ai-sidebar-container" :class="{ 'collapsed': store.aiSidebarCollapsed, resizing }">
    <!-- 把手挂在左缘：折叠后整栏往右滑出屏幕，把手是唯一的入口 -->
    <button class="toggle-btn" @click="store.aiSidebarCollapsed = !store.aiSidebarCollapsed">
      <ChevronRight v-if="!store.aiSidebarCollapsed" :size="20" />
      <ChevronLeft v-else :size="20" />
    </button>

    <!-- 调宽把手：往左拖即变宽。和折叠把手上下错开，两者不会互相误触 -->
    <div class="resize-handle" role="separator" aria-orientation="vertical"
         :aria-valuenow="Math.round(panelWidth)" :aria-valuemin="MIN_W" :aria-valuemax="MAX_W"
         @pointerdown="startResize" @dblclick="resetWidth" title="左右拖动调宽，双击恢复默认"></div>

    <div class="ai-content glass-panel" :style="{ width: panelWidth + 'px' }">
      <div class="ai-head">
        <span class="ai-title"><Sparkles :size="16" /> 云枢Agent</span>
        <button v-if="turns.length" class="head-btn" :disabled="busy" @click="reset">
          <Eraser :size="12" /> 清空
        </button>
      </div>

      <!-- 唯一的对话流：AI 靠左、用户靠右，正文按 Markdown 渲染 -->
      <div class="ai-scroll" ref="logEl">
        <div v-if="!turns.length" class="chat-empty">
          <div class="chat-empty-title">云枢Agent</div>
          <div class="chat-empty-slogan">{{ emptySlogan }}</div>
        </div>
        <div v-for="(t, i) in turns" :key="i" class="msg" :class="t.role">
          <!-- 助手气泡分两个阶段：
               **吐字中** —— 全部内容摊开，不折叠、不压暗、不加任何边框，
               模型一边说一边做的样子原样留着；
               **答完之后** —— 过程收成一行可点的摘要，只把最后那段回答当正文摆出来。
               想回看中间发生了什么，点摘要行展开，而每一批操作**仍然是收起的**，
               要看某一批的细节再单独点开。 -->
          <div v-if="t.role === 'assistant'" class="bubble assistant-bubble">
            <!-- 过程摘要：只在答完后出现。点了才展开过程 -->
            <button v-if="!isLive(i) && hasProcess(t)" class="proc-bar"
                    :class="{ open: procOpenNow(i) }" @click="toggleProc(i)">
              <ChevronRight :size="12" class="trace-caret" />
              <span class="proc-title">共执行 {{ elapsedOf(t) }}</span>
            </button>

            <!-- 过程明细：按发生顺序排。铺垫（「我来查一下」）和它后面的操作是对应的，
                 搬到顶部再摆下来就对不上了，所以一律留在原位。
                 **整块**由摘要行控制：收起时一个字都不露，只剩那行摘要和正式回答 ——
                 之前漏了这道门禁，块内的各批虽然收着，头部却照样挂在下面。 -->
            <div v-if="procOpenNow(i)" class="proc">
            <template v-for="(p, pi) in processParts(i, t)" :key="pi">
              <!-- 一批操作。头部常驻，它说的是「做了几件事」，和下面的清单不重复 -->
              <div v-if="p.kind === 'steps'" class="trace"
                   :class="{ open: partOpenNow(i, pi), live: isLive(i) }">
                <button v-if="!isLive(i)" class="trace-head" @click="togglePart(i, pi)">
                  <ChevronRight :size="12" class="trace-caret" />
                  <span class="trace-title">已执行 {{ p.steps?.length ?? 0 }} 个操作</span>
                </button>
                <ul v-if="partOpenNow(i, pi) && p.steps?.length" class="trace-list">
                  <li v-for="(s, j) in p.steps" :key="j" :class="s.status">
                    <!-- 状态优先：出错/进行中盖过工具类别，否则一条「正在规划」和
                         一条「规划失败」看起来是同一类操作 -->
                    <component :is="stepIcon(s)" :size="12" class="trace-icon"
                               :class="{ spin: s.status === 'run' }" />
                    <span class="trace-text" :title="s.label">{{ s.label }}</span>
                    <!-- detail 可能很长（时间戳、路径、整句结论），不截断会把左边的
                         操作名挤成多行。硬截断 + 尾部渐隐表示「还有」，完整内容靠 title -->
                    <span v-if="s.detail" class="trace-detail" :title="s.detail">{{ s.detail }}</span>
                  </li>
                </ul>
                <!-- AI 实际看到的那张图也贴出来：不然用户只能相信「它看了」这句话 -->
                <img v-for="(img, ii) in (partOpenNow(i, pi) ? p.images : []) || []" :key="'img' + ii"
                     class="trace-image" :src="img" alt="AI 看到的图" @click="openImage(img)" />
              </div>

              <!-- 模型的自述文字。**完整摊开**：不折叠、不截断、不加引用线。
                   藏一半再让人点开，等于逼用户为读一句完整的话多点一次；
                   而它本来就是「当时说了什么」的如实记录，压暗了反而像在藏。 -->
              <div v-else class="preamble">
                <div class="preamble-body md" v-html="renderMarkdown(p.text || '')"></div>
              </div>
            </template>
            </div>

            <!-- 首 token 还没来：气泡是空的，转个圈告诉用户「它收到了，正在想」。
                 复用工具执行中那套 Loader + .spin，不再另画一个 —— 同一个面板里
                 同时存在两种「正在转」会让人以为是两回事。
                 一旦吐出任何文字或出现任何一批操作就让位：再转圈就成了假的忙碌。 -->
            <div v-if="isPending(i, t)" class="waiting">
              <Loader :size="14" class="spin" /> 正在处理…
            </div>

            <!-- 正式回答：这轮唯一不折叠的内容。上面确实摊着过程时才画分隔线 -->
            <div v-if="finalIdx(i, t) >= 0" class="md final"
                 :class="{ 'has-pre': hasProcess(t) && procOpenNow(i) }"
                 v-html="renderMarkdown(finalText(i, t))"></div>
          </div>
          <div v-else class="bubble">{{ t.text }}</div>

          <span v-if="t.source" class="msg-meta">
            {{ t.source === 'llm' ? clockOf(t.generatedAt) : '规则兜底' }}
          </span>
          <p v-if="t.error" class="msg-error"><AlertTriangle :size="12" /> {{ t.error }}</p>
        </div>
      </div>

      <div class="composer">
        <!-- 胶囊：简略 label 常显，完整问句靠悬浮/长按展开（触屏没有 hover）。
             生成期间是占位且不可点，但输入框不受影响 —— 建议入口不该锁住打字。 -->
        <div v-if="chips.length || chipsPending" class="chips" role="list">
          <template v-if="chipsPending">
            <span v-for="i in 3" :key="'pending' + i" class="chip pending">
              <Loader :size="11" class="spin" /> 正在想下一步…
            </span>
          </template>
          <button
            v-for="(c, i) in chipsPending ? [] : chips" :key="i" class="chip" :disabled="busy"
            :title="c.prompt"
            :aria-label="c.prompt"
            @click="useChip(c)"
            @contextmenu.prevent="expand(c, i)"
          >
            {{ c.label }}
          </button>
        </div>
        <p v-if="expanded" class="chip-full">{{ expanded }}</p>

        <!-- 附件缩略图：用户这次要发给 AI 看的图 -->
        <div v-if="pendingImages.length" class="attachments">
          <div v-for="(src, i) in pendingImages" :key="i" class="attachment">
            <img :src="src" :alt="`附件 ${i + 1}`" />
            <button class="attachment-x" title="移除" @click="dropImage(i)">
              <X :size="10" />
            </button>
          </div>
        </div>

        <!-- 「聊天框」= 文字区 + 底部按钮排，共用一圈边框。
             两个按钮都在框内、靠下、同一排、右对齐：附件在上（无框），
             发送在下（圆形实心）。放在框外会让人以为它们管的是整个会话。 -->
        <div class="composer-row">
          <div class="composer-input">
            <textarea v-model="draft" rows="2" :disabled="busy" :placeholder="draftPlaceholder"
                      @keydown.enter.exact.prevent="send(draft)"
                      @paste="onPaste"></textarea>
            <div class="composer-actions">
              <button class="attach-btn" :disabled="busy" title="附图给 AI 看（也可以直接 Ctrl+V 粘贴）"
                      @click="pickImage">
                <Paperclip :size="15" />
              </button>
              <button class="send-btn" :disabled="busy || !draft.trim()" @click="send(draft)">
                <Loader v-if="busy" :size="15" class="spin" />
                <Send v-else :size="15" />
              </button>
            </div>
          </div>
          <input ref="fileEl" type="file" accept="image/*" multiple hidden @change="onFiles" />
        </div>

        <!-- 免责提示常驻在输入框正下方：飞行安全是真要担责的事，
             这个系统的结论来自模型 + 再分析数据，不能拿它当放行凭证。 -->
        <p class="composer-note">内容由 AI 生成，重要信息请务必核查</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import {
  ChevronLeft, ChevronRight, Sparkles,
  Send, Loader, Eraser, AlertTriangle,
  Wrench, MapPin, Calculator, AlertCircle, Crosshair, Layers, PanelLeft,
  ChartLine, Play, SlidersHorizontal, Clock, Package, Plane, Users, Gauge,
  CalendarDays, Trash2, Thermometer, Search, Paperclip, X, Globe,
} from 'lucide-vue-next';
import {
  store, suggestChips, fetchSiteCatalog,
} from '../store';
import type { SuggestChip } from '../store';
import { invokeTool, buildAgentState, setAttachedImages } from '../agentTools';
import { API_BASE } from '../config';
import { renderMarkdown } from '../utils/markdown';
import { mergeChips, pickKeep } from '../utils/chips';

interface TraceStep {
  tool: string;
  label: string;
  detail?: string;
  status: 'ok' | 'error' | 'run';
}

/**
 * 一条助手消息由若干「块」按**发生顺序**组成。
 *
 * 为什么不是「一个 text + 一个 steps」：模型常常先说一句「我来查一下」再调工具，
 * 调完工具接着再说话。文字与操作必须按时间排 —— 统一把轨迹堆在最上方或最下方，
 * 都会让先说的话出现在它发生之后，读起来像时空倒流。
 */
interface TurnPart {
  kind: 'text' | 'steps';
  text?: string;
  steps?: TraceStep[];
  /** 这一批工具里 AI 实际看到的图（data URL），贴出来让用户能核对 */
  images?: string[];
}

interface Turn {
  role: 'user' | 'assistant';
  /** 用户消息用这一条；助手消息用 parts（按顺序） */
  text: string;
  source?: 'llm' | 'rule';
  parts?: TurnPart[];
  error?: string;
  /** 本轮起止的墙钟时刻。摘要行上「共执行 X 分 X 秒」取自这里，答完即冻结 */
  startedAt?: number;
  endedAt?: number;
  /**
   * 生成时刻。来源标签上显示的就是它（「23:04 生成」）——
   * 比「LLM 生成」这种只说明来源、不说明时间的标签有用：用户是拿着这份报告去做决定的，
   * 「几点生成的」决定它现在还算不算数（数据是逐小时的，隔天就该重算）。
   * 和 startedAt 分开：startedAt 会连带算工具耗时，语义是「这轮跑了多久」；
   * 这个只认「正文是什么时候开始生成的」，两者不总是同一刻。
   */
  generatedAt?: number;
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
/** 胶囊正在后台生成：期间显示占位且不可点，但**不锁输入框** */
const chipsPending = ref(false);
/**
 * 本轮要**保留**在屏幕上的旧胶囊。
 *
 * 用户一次只点一条：点掉的那条已被消费，另外两条问的是别的角度、依然成立，
 * 所以留着不动，只补一条新的填坑。用户自己打字时上下文变了（可能已经覆盖了
 * 留着那两条的角度），整排重生成，这时它是空的。
 */
let keepChips: SuggestChip[] = [];
/** 新补出来的那条该插回第几位（用户点的是第几条）。-1 = 不指定，追加到末尾。 */
let keepAt = -1;
/** 胶囊请求的序号：只有最新一次的结果算数，早到的旧结果直接丢弃 */
let suggestSeq = 0;
const expanded = ref('');

/**
 * 一条助手消息里，**哪一段才是正式回答**。
 *
 * 判据是「最后一段有内容的文字」—— 它之前的所有东西（模型先说的「我来查一下」、
 * 每一批操作轨迹、AI 看到的图）都属于过程。以前这些和结论同字号排在一起，
 * 用户得读完整段才知道哪句才是答案。
 */
const finalIndex = (t: Turn): number => {
  const parts = t.parts ?? [];
  for (let k = parts.length - 1; k >= 0; k--) {
    if (parts[k].kind === 'text' && (parts[k].text || '').trim()) return k;
  }
  return -1;
};

/**
 * 正式回答的位置。**吐字期间一律返回 -1**。
 *
 * 之前只用「是不是最后一段非空文字」判，害得模型刚吐出的那句「我先扫一下全年
 * 时间窗口」在它孤零零占着末尾时，被当成结论用正常字号渲染 —— 要等下一段文字
 * 到了、它不再垫底，才降级成引用的样子。于是同一轮里前一句已经是引用、后一句
 * 还是正文，看着像两种语气。正式回答只在答完之后才存在，之前一律按过程处理。
 */
const finalIdx = (i: number, t: Turn) => (isLive(i) ? -1 : finalIndex(t));

const finalText = (i: number, t: Turn) => {
  const k = finalIdx(i, t);
  return k >= 0 ? t.parts![k].text || '' : '';
};

/**
 * 这一轮**要不要显出过程区**。
 *
 * 吐字中永远要 —— 用户得看着它一边说一边做；答完之后默认不要，只留一行摘要，
 * 结论单独摆出来。想回看中间发生了什么，点那行。
 */
const hasProcess = (t: Turn) => finalIndex(t) > 0;
const procOpenNow = (i: number) => isLive(i) || procOpen.value[`${i}`] === true;
const toggleProc = (i: number) => {
  if (isLive(i)) return;
  procOpen.value[`${i}`] = !procOpenNow(i);
};

/** 要摆出来的过程块：吐字中是全部，答完后就只剩正式回答之前的那几块 */
const processParts = (i: number, t: Turn): TurnPart[] => {
  const parts = t.parts ?? [];
  if (isLive(i)) return parts;
  return parts.slice(0, Math.max(0, finalIndex(t)));
};

/**
 * 这一轮花了多久，答完之后**冻结**。
 *
 * 冻结是必须的：不冻结的话那一行会随着时间一直变，用户刚读完它就变了。
 * 顺带一提，冻结的时机在 finally 里（Agent 循环完全结束），不是最后一次
 * delta —— 中间还要跑工具，那些时间也确实是它花的。
 */
const elapsedOf = (t: Turn): string => {
  if (!t.startedAt) return '不到 1 秒';
  const s = Math.max(1, Math.round(((t.endedAt ?? Date.now()) - t.startedAt) / 1000));
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
};

/**
 * 生成时刻格式化成 HH:MM（本地时区，跟用户对表盘的时间一致）。
 * 缺时刻时退回现在 —— 标签总得有个说法，不能空着或显示 NaN。
 */
const clockOf = (ms?: number): string => {
  const d = new Date(ms ?? Date.now());
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/**
 * 这一条是不是「正在回答中」。
 *
 * 收起是**答完之后**的整理动作：干活的时候藏起来，用户就只能盯着一个计数，
 * 看不到它一边说一边做。所以只有最后一条且仍在忙时才摊开。
 */
const isLive = (i: number) => busy.value && i === turns.value.length - 1;

/**
 * 正在回答，而且**一个字都还没吐出来**。
 *
 * 这段窗口不长但很关键：气泡先建好，首 token 要等一两秒。这期间气泡是条空壳，
 * 用户只看到自己那条提问孤零零地泡在下面，分不清「它没收到」还是「它正在想」。
 * 所以这里补一个转圈。
 *
 * 一旦有任何内容出现就撤掉：文字或操作任一有，都说明它已经在动了，
 * 这时候再转圈就是假的忙碌，反而像卡住了。
 */
const isPending = (i: number, t: Turn): boolean => {
  if (!isLive(i)) return false;
  if (t.text.trim()) return false;
  return !(t.parts ?? []).some(p => (p.text || '').trim() || (p.steps ?? []).length);
};

/**
 * 过程区里的每一块，在**原位**独立折叠 —— 铺垫和操作本来就是交错发生的，
 * 把它们搬到顶部再摆下来，文字和它对应的操作就对不上了。
 * key 用「轮次:块序号」，一个气泡里多块互不影响。
 *
 * 展开过程区时它们默认全是收起的：先给一层结构（做了几批、每批一句话），
 * 想钻哪一批再点开哪一批 —— 一口气摊开十几条操作，没人看得下去。
 */
const partOpen = ref<Record<string, boolean>>({});
/** 过程区（那一整块）展开与否，按轮次记。它管的是「看不看得出中间发生了什么」，
 *  partOpen 管的是「展开之后每批看不看得细」，两层别混。 */
const procOpen = ref<Record<string, boolean>>({});
const partOpenNow = (i: number, pi: number) =>
  isLive(i) || partOpen.value[`${i}:${pi}`] === true;
const togglePart = (i: number, pi: number) => {
  // 正在回答时全部摊开，点它不改变什么 —— 但也绝不能把状态写进去：
  // 那个键会一直留着，等这轮答完同序号的新块就莫名其妙是展开的。
  if (isLive(i)) return;
  partOpen.value[`${i}:${pi}`] = !partOpenNow(i, pi);
};

/** 轨迹图标按工具类别分，让用户一眼看出这条是"动了界面"还是"算了数据"。 */
const TOOL_ICONS: Record<string, Component> = {
  add_waypoint: MapPin, update_waypoint: MapPin, remove_waypoint: MapPin,
  set_endpoints: MapPin, focus_region: Crosshair,
  run_plan: Calculator, run_site_plan: Calculator, run_time_window: Calculator,
  query_weather: Thermometer, search_sites: Search,
  toggle_overlay: Layers, switch_tab: PanelLeft, toggle_chart: ChartLine,
  set_base_map: Globe,
  play_simulation: Play, set_priority: SlidersHorizontal, set_time: Clock,
  set_payload_kg: Package, set_aircraft: Plane, set_mission: Users,
  set_speed: Gauge, set_forecast_days: CalendarDays, clear_route: Trash2,
};
const TOOL_VERBS: Record<string, string> = {
  toggle_overlay: '切换图层', focus_region: '移动地图', switch_tab: '切换页签',
  set_base_map: '切换底图',
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

/**
 * 空态：名字 + 一句 slogan。
 *
 * 原来这里放的是一句「直接说要做什么，例如…我会自动切到…填好参数…算出来」——
 * 那是**说明书**，而且是最容易被记住的那一段说明书：用户读完它就知道该点哪、
 * 会看到什么，反而不用自己去试。真正该占住这个位置的是这个名字做什么用的，
 * 具体怎么用交给下面那排胶囊，它们本来就是例子。
 *
 * 标题与 slogan 都要**居中**：这是一块还没被用起来的空白，居中才像「等你开口」，
 * 顶格靠上会让人以为上面那块已经显示了内容、下面没东西了。
 */
const emptySlogan = '一句话进去，一份报告出来';

const draftPlaceholder = computed(() =>
  busy.value ? '正在处理…' : '说出你的任务，Enter 发送'
);

/** 推一条空的助手消息并返回它：整轮的正文与操作都写进这一条，不另起新气泡 */
const pushAssistant = () => {
  turns.value.push({ role: 'assistant', text: '', parts: [] });
  return turns.value[turns.value.length - 1];
};

/**
 * 追加一段正文。**接着上一段正文继续追加**，不新开一块 ——
 * 否则同一轮里模型分两次说的话之间会插进一段轨迹，读起来像两段独立发言。
 */
const appendText = (reply: Turn, s: string) => {
  const last = reply.parts![reply.parts!.length - 1];
  if (last && last.kind === 'text') { last.text = (last.text || '') + s; return; }
  reply.parts!.push({ kind: 'text', text: s });
  reply.text = reply.parts!.map(p => p.text ?? '').join('\n\n');
};

/** 追加一批操作记录。永远是新的一块 —— 操作与操作之间也按批次分开。 */
const appendSteps = (reply: Turn, steps: TraceStep[], images?: string[]) => {
  reply.parts!.push({ kind: 'steps', steps, images });
  reply.text = reply.parts!.map(p => p.text ?? '').join('\n\n');
};

/** 当前这段对话里最后一批操作的引用（就地改它，不要新建，否则会重复列出同一条） */
const lastSteps = (reply: Turn): TraceStep[] | null => {
  const last = reply.parts![reply.parts!.length - 1];
  return last && last.kind === 'steps' ? last.steps! : null;
};

/** 点开放大：缩略图看不清，而细节正是模型在描述的东西 */
const openImage = (src: string) => { window.open(src, '_blank', 'noopener'); };

// ---------------- 附件 ----------------
// 多模态按图算 token，尺寸先压一遍再送：手机照片原图动辄几 MB，
// 缩到长边 1024 足够看清内容，又不至于把一次对话的额度吃掉。

const pendingImages = ref<string[]>([]);
const fileEl = ref<HTMLInputElement | null>(null);
const pickImage = () => fileEl.value?.click();
const dropImage = (i: number) => { pendingImages.value.splice(i, 1); };

const MAX_ATTACH = 3;
const MAX_ATTACH_EDGE = 1024;

const onFiles = async (ev: Event) => {
  const input = ev.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = '';                       // 允许重复选同一个文件
  await addImages(files);
};

/**
 * 直接 Ctrl+V 粘贴图片。
 *
 * 从截图工具（微信/QQ/浏览器）复制过来时，用户手上就是这个动作 —— 让他再
 * 「存到桌面 → 点回形针 → 选文件」是凭空多出来的三步。这里不 preventDefault：
 * 粘的是文字时必须照常落进输入框，只有**确实带图**时才拦。
 */
const onPaste = async (ev: ClipboardEvent) => {
  const items = Array.from(ev.clipboardData?.items ?? []);
  const files = items
    .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
    .map(it => it.getAsFile())
    .filter((f): f is File => !!f);
  if (!files.length) return;              // 纯文字粘贴，放行
  ev.preventDefault();
  await addImages(files);
};

/** 统一入口：选文件与粘贴都走这里，条数与尺寸封顶也只在这里做一次 */
const addImages = async (files: File[]) => {
  for (const f of files) {
    if (pendingImages.value.length >= MAX_ATTACH) break;
    if (!f.type.startsWith('image/')) continue;
    try {
      pendingImages.value.push(await downscale(f));
    } catch (e) {
      console.error('图片读取失败:', e);
    }
  }
};

function downscale(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, MAX_ATTACH_EDGE / Math.max(img.width, img.height));
      const w = Math.max(1, Math.round(img.width * scale));
      const h = Math.max(1, Math.round(img.height * scale));
      const cv = document.createElement('canvas');
      cv.width = w; cv.height = h;
      const ctx = cv.getContext('2d');
      if (!ctx) return reject(new Error('canvas 不可用'));
      ctx.drawImage(img, 0, 0, w, h);
      resolve(cv.toDataURL('image/jpeg', 0.7));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('解码失败')); };
    img.src = url;
  });
}

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
    reply.source = 'llm';
    reply.startedAt = Date.now();
    reply.generatedAt = reply.startedAt;

    // 本轮随消息发出去的图：既随请求送给模型，也让 look_at_image(attachment) 能引用。
    // 发送后立即清空 —— 附件的生命周期只到本轮，不该跟着对话一直挂着。
    const sent = pendingImages.value.slice();
    setAttachedImages(sent);
    pendingImages.value = [];

    let toolResults: Record<string, any>[] = [];
    // 兜底：站表正常在 main.ts 启动时就取到了，这里只在「启动那次失败」时
    // 才真的产生等待。仍然要留着 —— 工具分不清「没加载」和「没有」，
    // 一次空表会让它回「站表里没有『曲松县』」，而曲松县确实在 220 条站表里
    // （CT-84）。用户会据此认为当地没有备降点，照着改任务区域就改错方向。
    // 前端与后端各有一道轮数闸（这里 6、agent.py 里 MAX_STEPS=5）。两边都留一道是
    // 有意的：任何一边单独失效都还收得住，不至于让模型无限兜圈。
    for (let round = 0; round < 6; round++) {
      if (round === 0) await fetchSiteCatalog();
      const step = await callAgent(history, text, toolResults, round, reply, sent);
      if (step.error) {
        reply.error = step.error;
        break;
      }
      if (!step.calls.length) break;   // 正文已经流进 reply 了

      // 这一批要真的执行。每调完一个就把结果记进轨迹，用户能看着它一步步动。
      //
      // 注意遍历的是**全部**调用而不是去重后的：回灌要按协议给每个 tool_call_id
      // 配一条结果，少一条模型那边就收不下整个请求。重复的那几个只复用结果、不重跑。
      const done = new Map<string, { ok: boolean; label: string; data?: unknown }>();
      for (const call of step.calls) {
        const tool = call.tool;
        const args = call.args || {};
        const key = `${tool}:${JSON.stringify(args)}`;
        let r = done.get(key);
        if (r) {
          // 同一批里完全相同的调用（模型偶尔会把 run_plan 连写两遍）只真跑一次，
          // 两次都跑就是白烧两轮 A* 拿回一样的数字。
          toolResults.push({ id: call.id, tool, args, ...r });
          continue;
        }

        const slow = SLOW_TOOLS.has(tool);
        // 慢工具先落一条「正在…」再原地改写：一次三档 A* 要十几秒，
        // 中间没有动静的话用户会以为卡死了。快工具不必多这一次闪烁。
        //
        // 「正在…」只存在于这里，**不再往正文里写一份占位**：两份状态同源时，
        // 清占位就会连带清掉模型这一轮真说出来的句子（表现为字吐出来又消失）。
        // 轨迹里那行转圈本身就是状态指示，正文里再写是重复。
        let steps = lastSteps(reply);
        if (!steps) { steps = []; appendSteps(reply, steps); }
        steps.push({
          tool,
          label: slow ? `正在${SLOW_VERBS[tool] ?? '处理'}…` : `${TOOL_VERBS[tool] ?? '处理'}中…`,
          status: 'run',
        });
        // 取**响应式数组里那一项**再改，而不是改 push 进去的原始对象：
        // Vue 的数组存的是代理，改原始对象不触发重渲染（之前看着能动是搭了
        // 别的更新的便车，这里改成不依赖巧合）。
        const row = steps[steps.length - 1];

        const full = await invokeTool(tool, args);
        row.label = full.label;
        row.detail = full.detail;
        row.status = full.ok ? 'ok' : 'error';
        // AI 实际看到的那张图贴进这一批：不然用户只能「相信」它看了
        if (full.image) {
          const owner = reply.parts![reply.parts!.length - 1];
          (owner.images ||= []).push(full.image);
        }
        // data 只放工具真拿到的结果；模型只能引用它，所以失败时这里是空的
        r = { ok: full.ok, label: full.label, data: full.data ?? null };
        done.set(key, r);
        // id/args 要带回去：后端据此重建 assistant(tool_calls) + role=tool 的配对
        toolResults.push({ id: call.id, tool, args, ...r });
      }
      // 下一步要按**工具改完之后的界面状态**来判断，所以每轮都重新取快照
      if (!toolResults.length) break;
    }

    if (!reply.text && !reply.error) {
      reply.text = '（没有可执行的操作，也没有可回答的内容）';
      reply.parts = [{ kind: 'text', text: reply.text }];
    }
  } catch (error) {
    console.error('Agent 循环失败:', error);
    const failed = turns.value[turns.value.length - 1];
    const msg = '没能完成这次操作。可以换个说法，或者直接在左栏手动操作。';
    if (failed?.role === 'assistant') {
      failed.text = msg;
      // parts 也要补上：助手气泡渲染的是 parts，光改 text 会剩一个空框，
      // 底下再挂一条错误提示 —— 用户看到的是「它答了，但什么都没说」。
      failed.parts = [{ kind: 'text', text: msg }];
      failed.source = 'rule';
    }
  } finally {
    // 计时在这里收口：Agent 循环（含中间那些秒级的工具执行）确实都算它的耗时。
    // 摘要是答完之后才露出来的，所以这一行读到的已经是终值，不会边读边跳。
    const last = turns.value[turns.value.length - 1];
    if (last?.role === 'assistant' && last.startedAt && !last.endedAt) {
      last.endedAt = Date.now();
    }
    // 输入框在这里就解锁 —— 答案已经出来了，用户可以立刻发下一条。
    // 胶囊慢两秒无所谓，它只是个建议入口，不该把整个输入区锁住。
    busy.value = false;
  }

  // 胶囊**不等**：在后台自己跑，跑完再替换。期间显示为「生成中」且不可点。
  // 用 seq 挡住竞态：用户在这两秒里又发了一条的话，上一轮的胶囊回来就是过期的，
  // 直接丢弃，不能覆盖新一轮的建议。
  //
  // count / keep 由调用方按「这轮是点胶囊还是自己打字」定：
  //   点胶囊 -> 只补被吃掉的那一条（keep 里的两条原样留着）
  //   自己打字 -> 上下文变了，3 条整排重生成（keep 为空）
  const seq = ++suggestSeq;
  chipsPending.value = true;
  // keep / at 必须在**发请求之前**取成本轮快照。下面紧接着就把模块级变量清空了，
  // 而合并发生在 .then 回调里 —— 回调再读 keepChips 读到的只会是那个空数组，
  // 留着的那两条就凭空没了，屏幕上只剩新补的那 1 条。
  const keep = keepChips;
  const at = keepAt;
  void suggestChips({
    task: store.timeWindow && store.sidebarTab === 'timeWindow' ? 'time_window' : 'feasibility',
    payload: (store.timeWindow && store.sidebarTab === 'timeWindow'
      ? store.timeWindow : store.feasibility) as any,
    // 完整对话：判断「哪些角度还没被问到」只有问答往复本身够用。
    // 不传工具轨迹与图片 —— 那些是「我干了什么」，与「用户接下来想问什么」无关。
    history: turns.value
      .filter(t => t.text)
      .map(t => ({ role: t.role, text: t.text })),
    keep,
    count: keep.length ? 1 : 3,
    state: {
      sidebar_tab: store.sidebarTab,
      has_result: !!(store.feasibility || store.timeWindow || store.siteResult),
      verdict: store.feasibility?.verdict,
      level: store.feasibility?.risk?.overall,
    },
  }).then(got => {
    if (seq !== suggestSeq) return;          // 已有更新的一轮，丢弃这份过期结果
    // 补出来的一条插回用户点的那个位置。一条都没补出来时优先留着那两条原样，
    // 而不是退回开场胶囊 —— 开场那三条里有一条正是用户刚点过的，再端上来
    // 等于让他点一下原地打转。
    const merged = mergeChips(keep, at, got.chips);
    chips.value = merged.length ? merged : (keep.length ? keep : STARTER_CHIPS);
    chipsPending.value = false;
  }).catch(() => {
    if (seq !== suggestSeq) return;
    chips.value = keep.length ? keep : STARTER_CHIPS;
    chipsPending.value = false;
  });
  keepChips = [];
  keepAt = -1;
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
  images: string[] = [],
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
        images,
        // 模型这一轮到目前说过的话。回灌时挂回 assistant 消息，
        // 否则它不知道自己刚讲过什么，同一段话换个说法再讲一遍。
        said: (reply.parts ?? []).map(p => p.text ?? '').join(''),
      }),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
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
          // 纯追加。这里曾经有一段「首个 delta 先清空正文」的逻辑，用来清掉
          // 「正在规划…」占位 —— 但模型常常先说一句「我来查一下」再调工具，
          // 那句话是真流式给用户看了的，下一轮却被清掉，表现为**字吐出来又消失**。
          // 占位已改由轨迹里的转圈行承担，正文不再需要清空。
          appendText(reply, event.text);
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

/**
 * 点胶囊 = 填入完整问句并直接发出，保持「对话即操作」的连贯感。
 *
 * 被点的这条算「消费掉了」，另外两条原样留着：用户一次只点一条，
 * 留着那两条问的是别的角度、依然成立，没必要连坐重生成。
 * 新生成的那一条会接在它们后面，凑回三条。
 */
const useChip = (chip: SuggestChip) => {
  if (busy.value) return;
  // 留哪些、新补的插回第几位：交给纯算子算（见 utils/chips.ts），
  // 位置是相对**留着的那几条**算的 —— 点掉一条之后后面会往前挪，
  // 直接拿原列表的下标会插错一格。
  const { keep, at } = pickKeep(chips.value, chip);
  keepChips = keep;
  // 补出来的那条插回用户点的那个位置，而不是甩到末尾：位置本身就是信息，
  // 三条胶囊的排序本来就表达了推荐优先级，挪位等于悄悄改了推荐。
  keepAt = at;
  void send(chip.prompt);
};

/** 触屏没有 hover：长按/右键把完整问句显示出来，否则用户看不到自己将发出什么 */
const expand = (chip: SuggestChip, _i: number) => {
  expanded.value = expanded.value === chip.prompt ? '' : chip.prompt;
};

// ---------------- 手动规划完 → 自动出一份评估报告 ----------------

/** 报告正在生成。用来防重复：busy 变 false 可能连着触发两次（报告结束 + 新一轮开始） */
let reportRunning = false;

/**
 * 消费 store.reportRequest：把 /api/assess/report 的流式正文落成一条助手消息。
 *
 * 刻意**不**另做一套「报告卡片」：报告与 AI 回答是同一种内容（对这次规划的说法），
 * 多一种呈现就多一份要跟着改的样式。报告整段只有一个 text 块，所以气泡里的
 * 过程折叠天然不触发，看起来就是一段正常回答。
 */
const runReport = async () => {
  const req = store.reportRequest;
  if (!req || busy.value || reportRunning) return;   // AI 正在说话就先欠着
  store.reportRequest = null;
  reportRunning = true;
  // 报告是用户主动等的东西，侧栏收着就等于没出
  store.aiSidebarCollapsed = false;

  const turn = pushAssistant();
  turn.source = 'llm';
  // 打生成时刻：标签上显示的就是它。放在发请求之前，且**只打一次**——
  // 让它跟着流式增量走的话，同一条报告在十几秒里会被刷成一串不同的时间。
  turn.generatedAt = Date.now();
  try {
    const response = await fetch(`${API_BASE}/api/assess/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: req.task, payload: req.payload }),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() ?? '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let event: any;
        try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }
        // LLM 没配时后端会降级成规则模板，标签要说准，不能一律写「LLM 生成」
        if (event.type === 'meta') turn.source = event.source === 'rule' ? 'rule' : 'llm';
        else if (event.type === 'delta' && event.text) appendText(turn, event.text);
        else if (event.type === 'error') turn.error = event.message;
      }
    }
  } catch (error) {
    console.error('报告生成失败:', error);
    turn.error = '报告生成失败，可以稍后重试。';
  } finally {
    if (!turn.text && !turn.error) {
      turn.text = '（这次没有生成出报告）';
      turn.parts = [{ kind: 'text', text: turn.text }];
    }
    reportRunning = false;
  }
};

watch(() => store.reportRequest?.nonce, () => { void runReport(); });
// AI 说完话那一刻也看一眼：规划可能是在 AI 回答期间算完的，那次请求要等它让出
watch(busy, (b) => { if (!b) void runReport(); });

const reset = () => {
  turns.value = [];
  chips.value = STARTER_CHIPS;
  chipsPending.value = false;
  keepChips = [];                // 不清会漏进下一轮：清空对话后残留的旧胶囊不该被保留
  keepAt = -1;
  suggestSeq++;              // 作废还在路上的那次请求，否则它回来会把占位换回旧胶囊
  expanded.value = '';
  partOpen.value = {};
  procOpen.value = {};
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

// 站表在 main.ts 里就随应用启动发出去了，正常到这里早已到位。
// 这里保留只是**启动期那次失败后的重试入口**（后端当时还没起来），
// 重复调用没有代价：store 里按 null 判空，store.ts 里共用同一个 in-flight 请求。
onMounted(() => {
  if (!store.siteCatalog) void fetchSiteCatalog();
});

// ---------------- 横向调宽 ----------------
// 报告要放结论、地图要并排看时，380px 那个默认宽度偏窄；但宽度是个人偏好，
// 刷新后回到旧值等于每次都要重拖一遍，所以落盘。
const W_KEY = 'aiSidebarWidth';
const MIN_W = 320;
// 上限跟着视口走：比两侧栏加起来还宽的话，地图就没法看了。
const MAX_W = Math.min(720, Math.round(window.innerWidth * 0.45));

const DEFAULT_W = 380;
const panelWidth = ref(readStoredWidth());
const resizing = ref(false);

function readStoredWidth(): number {
  const raw = Number(localStorage.getItem(W_KEY));
  // 存的值可能来自更宽/更窄的屏幕（比如换过显示器），一律重新夹一遍再用
  return Number.isFinite(raw) && raw > 0 ? Math.min(MAX_W, Math.max(MIN_W, raw)) : DEFAULT_W;
}

/** 宽度是内联样式，容差给 0.5px：拖拽时会出现 379.5 这类小数，存进 localStorage 太难看 */
const commitWidth = () => localStorage.setItem(W_KEY, String(Math.round(panelWidth.value)));

const resetWidth = () => {
  panelWidth.value = DEFAULT_W;
  commitWidth();
};

let dragStartX = 0;
let dragStartW = 0;

const startResize = (ev: PointerEvent) => {
  // 只认左键：右键/中键在某些环境会弹出别的菜单，接管了反而添乱
  if (ev.button !== 0) return;
  ev.preventDefault();
  dragStartX = ev.clientX;
  dragStartW = panelWidth.value;
  resizing.value = true;
  window.addEventListener('pointermove', onResizeMove);
  window.addEventListener('pointerup', endResize);
  window.addEventListener('pointercancel', endResize);
};

const onResizeMove = (ev: PointerEvent) => {
  // 往左拖（X 变小）就是变宽，所以要反过来减
  const next = dragStartW - (ev.clientX - dragStartX);
  panelWidth.value = Math.min(MAX_W, Math.max(MIN_W, next));
};

const endResize = () => {
  if (!resizing.value) return;
  resizing.value = false;
  window.removeEventListener('pointermove', onResizeMove);
  window.removeEventListener('pointerup', endResize);
  window.removeEventListener('pointercancel', endResize);
  commitWidth();
};

// 拖拽期间别让浏览器选中正文（否则一拖整篇报告变蓝），也顺手禁掉拖图
watch(resizing, on => {
  document.body.style.userSelect = on ? 'none' : '';
  document.body.style.cursor = on ? 'ew-resize' : '';
});

onBeforeUnmount(() => {
  endResize();
  document.body.style.userSelect = '';
  document.body.style.cursor = '';
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

/* 拖拽时把过渡关掉：0.4s 的 transform 过渡会让宽度看起来「追不上」鼠标 */
.ai-sidebar-container.resizing { transition: none; }

.ai-sidebar-container.collapsed {
  transform: translate(calc(100% + var(--rail-gap)), -50%);
}

/* 宽度、高度与折叠方式都和左栏同一套，两栏只是分居左右 */
.ai-content {
  width: var(--rail-w);
  height: 100%;
  /* 拖宽时只让颗粒平铺、不跟着拉伸，粗糙度才和左栏对得上 */
  background-size: var(--panel-noise-tile) var(--panel-noise-tile);
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

/* 调宽把手：贴在左缘的一条细热区。默认只留 6px 视觉宽度、实际热区 10px，
   悬停时才显出那条竖线 —— 常驻一条线会让面板左缘看着像多了个控件。 */
.resize-handle {
  position: absolute;
  left: -5px;
  top: 0;
  bottom: 0;
  width: 10px;
  cursor: ew-resize;
  z-index: 1;
  touch-action: none;   /* 否则触屏上拖动会被当成滚动页面 */
}

.resize-handle::after {
  content: '';
  position: absolute;
  left: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 40px;
  border-radius: 1px;
  background: var(--text-secondary);
  opacity: 0;
  transition: opacity 0.2s ease;
}

.resize-handle:hover::after { opacity: 0.7; }
.resizing .resize-handle::after { opacity: 1; }

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
  font-size: 15px;
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
  font-size: 12px;
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

/* 空态：标题 + slogan 占住整块空白。垂直居中，让它像「等你开口」而不是「已显示内容」 */
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 0 20px;
  text-align: center;
}

.chat-empty-title {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: 0.5px;
  color: var(--text-primary);
}

.chat-empty-slogan {
  font-size: 13px;
  line-height: 1.7;
  letter-spacing: 1px;
  color: var(--text-secondary);
  opacity: 0.8;
}

.msg { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; }
.msg.user { align-items: flex-end; }

.bubble {
  max-width: 92%;
  padding: 8px 11px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.msg.assistant .bubble {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--panel-border);
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
  white-space: pre-wrap;
}

/* 模型给的 Markdown 结构要在窄栏里读得下去：段落间距压到最小 */
.md :deep(p) { margin: 0 0 6px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(h3), .md :deep(h4) { font-size: 14px; margin: 8px 0 4px; color: var(--text-primary); }
.md :deep(ul), .md :deep(ol) { margin: 0 0 6px; padding-left: 18px; }
.md :deep(li) { margin: 0 0 2px; }
.md :deep(code) { font-size: 12px; background: rgba(0, 0, 0, 0.3); padding: 1px 4px; border-radius: 4px; }

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
  font-size: 12px;
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

.msg-meta { font-size: 11px; color: var(--text-secondary); }

/* ---- 等待首 token ---- */
/* 单行状态：沿用正文 1.7 的行高会让这一行上下各空出小半行，看着空，收紧到 1.4。
   下边距比 CSS 上的值再多补 3px：中文没有下伸部，字形全贴在基线上，
   同样的行高 + 同样的 padding，上边看着宽、下边看着窄。补一点才视觉对齐。 */
.waiting {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 0 3px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-secondary);
}

/* ---- 过程摘要行 ----
   答完之后，「它干了什么」压成这一行。它是这一轮**唯一**的过程入口：
   收起时点它展开整段过程，展开时点它又收回去。 */
.proc-bar {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 2px 0;
  margin-bottom: 8px;
  font-size: 11px;
  font-family: inherit;
  /* 刻意不加任何容器：它就是一行可点的文字。
     套个边框底色的话，这行会比下面真正要读的正文还抢眼。 */
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}
.proc-bar:hover { color: var(--text-primary); }
.proc-bar.open .trace-caret { transform: rotate(90deg); }

.proc-title {
  font-weight: 600;
  letter-spacing: 0.3px;
  /* 下划线：这行看着像正文的一部分，不是浮在外面的一个控件 */
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(148, 163, 184, 0.4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 展开后的过程区。它只是**分组用的壳**，间距交给 gap ——
   .trace 自带 margin 的话会和 gap 叠加，同一个 gap 下块与块的距离还不一样。 */
.proc {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.proc > .trace, .proc > .preamble { margin: 0; }

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

/* 吐字过程中一律「素面」：不套卡片、不压暗、不加引用线。
   那些装饰是用来把过程和结论**区分开**的，可答案还没出来，这会儿还没有结论
   要区分 —— 提前把它压暗，用户看着就像自己的提问被敷衍了。 */
.trace.live {
  border: none;
  background: transparent;
  margin: 0 0 4px;
}

.trace.live .trace-list {
  border-top: none;
  padding: 0;
}

.trace-head {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 5px 9px;
  font-size: 11px;
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

/* ---- 过程中的自述文字 ----
   只有壳，正文样式全部继承气泡：不加引用线、不压暗、不给折叠头。
   之前那套「左边一条竖线 + 62% 透明度」是把它当引用处理，实际它只是
   模型当时说的话，藏头露尾地摆出来反而像在遮遮掩掩。 */
.preamble {
  flex-shrink: 0;
}

.preamble-body {
  font-size: 14px;
  line-height: 1.7;
}

/* 正式回答上方的分隔线：把「过程」与「结论」在视觉上切开。
   只在前面确实有过程时才画，否则顶部会挂一条无来由的横线。 */
.md.final.has-pre {
  border-top: 1px solid rgba(148, 163, 184, 0.22);
  padding-top: 9px;
  margin-top: 4px;
}

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
  font-size: 12px;
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
  font-size: 11px;
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

.msg-error {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  font-size: 12px;
  color: #fca5a5;
}

.composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px 20px 12px;
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
  font-size: 12px;
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

/* 生成中的占位：不可点、不可聚焦，只是告诉用户「建议还在来」 */
.chip.pending {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
  background: transparent;
  border-style: dashed;
  cursor: progress;
}

/* 触屏没有 hover，长按/右键后把完整问句摊在这里 */
.chip-full {
  margin: 0;
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1.6;
  border-radius: 8px;
  border: 1px dashed var(--panel-border);
  background: rgba(0, 0, 0, 0.2);
  color: var(--text-primary);
}

.attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.attachment {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--panel-border);
}

.attachment img { width: 100%; height: 100%; object-fit: cover; display: block; }

.attachment-x {
  position: absolute;
  top: 1px; right: 1px;
  width: 15px; height: 15px;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 3px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  cursor: pointer;
}
.attachment-x:hover { background: #ef4444; }

/* 「聊天框」是**一个**框：文字区和按钮区共用一圈边框。
   之前是 textarea 自己带框、按钮在框外，于是附件和发送看着像两个不相干的控件。 */
.composer-row { display: flex; align-items: stretch; gap: 8px; }

.composer-input {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.25);
  transition: border-color 0.2s;
}
.composer-input:focus-within { border-color: var(--accent-color); }

.composer-input textarea {
  width: 100%;
  min-height: 46px;
  resize: none;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  padding: 8px 12px 2px;
  color: var(--text-primary);
}
.composer-input textarea:focus { outline: none; }
.composer-input textarea:disabled { opacity: 0.6; }

/* 按钮排：靠下、右对齐、同一排。放在框内而不是绝对定位盖在文字上，
   文字就不会被压到按钮底下 —— 那一行会长得很难看。 */
.composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: flex-end;
  padding: 0 10px 8px 12px;
}

/* 附件键不带框：它是个辅助入口，套个圈会和旁边的主按钮抢注意力 */
.attach-btn {
  width: 28px;
  height: 28px;
  display: flex; align-items: center; justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: color 0.2s, background 0.2s;
}
.attach-btn:hover:not(:disabled) { color: var(--text-primary); background: rgba(255, 255, 255, 0.08); }
.attach-btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* AI 实际看到的那张图：贴出来是为了让用户能核对「它看的到底是哪张」，
   点开放大到新标签 —— 缩略图看不清细节，而细节正是模型在描述的东西。 */
.trace-image {
  display: block;
  width: 100%;
  margin-top: 6px;
  border-radius: 6px;
  border: 1px solid var(--panel-border);
  cursor: zoom-in;
}

.send-btn {
  width: 30px;
  height: 30px;
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

/* 免责提示。压得比正文小一号、灰一档 —— 它的作用是「在」，不是「抢」。
   不另加 margin：父级 .composer 已经有 gap，两者叠加会凭空多出一截空白。 */
.composer-note {
  margin: 0;
  text-align: center;
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.8;
}

/* 面板里所有「正在转」共用这一处：工具执行中、胶囊占位、发送键、等待首 token。
   同一面板里出现两种转法会让人以为是两回事，所以只保留这一条定义。
   刻意不加 prefers-reduced-motion 降级：降级成静止的圈看着就像卡住了，
   而这里每一处转圈都在表示「还没结束」，停掉反而传达了错误的状态。 */
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
