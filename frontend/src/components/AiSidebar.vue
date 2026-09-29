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
        <button v-if="turns.length" class="head-btn" :disabled="streaming" @click="turns = []">
          <Eraser :size="12" /> 清空
        </button>
      </div>

      <div class="ai-scroll" ref="logEl">
        <!-- 自然语言任务：一期只做到「解析 → 回填表单 → 我确认」，不直接触发规划 -->
        <section class="ai-block">
          <h4 class="block-title"><MessageSquareText :size="14" /> 一句话任务</h4>
          <textarea class="capsule-input task-text" rows="2"
                    v-model="store.siteForm.text"
                    placeholder="例：从拉萨送 200 公斤兽药到昌都"></textarea>
          <div class="parse-row">
            <button class="capsule-btn tiny" @click="doParse" :disabled="parsing">
              <Loader v-if="parsing" :size="13" class="spin" />
              {{ parsing ? '解析中…' : '解析任务' }}
            </button>
            <span class="badge" v-if="store.siteForm.parseSource === 'llm'">AI 抽取</span>
            <span class="badge muted" v-else-if="store.siteForm.parseSource === 'rule'">关键词兜底</span>
          </div>
        </section>

        <!-- 对话流：AI 靠左、用户靠右，正文按 Markdown 渲染 -->
        <section class="chat">
          <p v-if="!turns.length" class="chat-empty">{{ emptyHint }}</p>
          <div v-for="(t, i) in turns" :key="i" class="msg" :class="t.role">
            <div v-if="t.role === 'assistant'" class="bubble md" v-html="renderMarkdown(t.text)"></div>
            <div v-else class="bubble">{{ t.text }}</div>
            <span v-if="t.source" class="msg-meta">{{ t.source === 'llm' ? 'LLM 生成' : '规则兜底' }}</span>
            <p v-if="t.error" class="msg-error"><AlertTriangle :size="12" /> {{ t.error }}</p>
          </div>
        </section>
      </div>

      <!-- 输入区钉在栏底，不随消息流滚走 -->
      <div class="composer">
        <button class="chip-btn" :disabled="!canSend || streaming" @click="askReport">
          <FileText :size="13" /> 生成飞行建议报告
        </button>
        <div class="composer-row">
          <textarea v-model="draft" rows="2" :disabled="!canSend" :placeholder="draftPlaceholder"
                    @keydown.enter.exact.prevent="send(draft)"></textarea>
          <button class="send-btn" :disabled="!canSend || streaming || !draft.trim()" @click="send(draft)">
            <Loader v-if="streaming" :size="15" class="spin" />
            <Send v-else :size="15" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import {
  ChevronLeft, ChevronRight, Sparkles, MessageSquareText, FileText,
  Send, Loader, Eraser, AlertTriangle,
} from 'lucide-vue-next';
import { store, parseSiteTask, fetchSiteCatalog } from '../store';
import { API_BASE } from '../config';
import { renderMarkdown } from '../utils/markdown';

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  /** 正文来源：只有 AI 那一侧会有；rule = 后端降级，界面必须如实标出来 */
  source?: 'llm' | 'rule';
  error?: string;
}

const turns = ref<Turn[]>([]);
const draft = ref('');
const parsing = ref(false);
const streaming = ref(false);
const logEl = ref<HTMLElement | null>(null);

const doParse = async () => {
  parsing.value = true;
  try { await parseSiteTask(); } finally { parsing.value = false; }
};

/**
 * 对话的对象跟随左栏当前那一页：报告与问答都只引用这一页的结构化结果，
 * 后端不额外取数（与旧版 AI 报告同一防幻觉口径）。
 */
const chatTarget = computed<{ task: string; payload: Record<string, any> | null }>(() => {
  if (store.sidebarTab === 'timeWindow') return { task: 'time_window', payload: store.timeWindow as any };
  if (store.sidebarTab === 'site') return { task: '', payload: null };
  return { task: 'feasibility', payload: store.feasibility as any };
});

const canSend = computed(() => !!chatTarget.value.payload);

const emptyHint = computed(() => {
  if (!canSend.value) {
    return store.sidebarTab === 'site'
      ? '多航点模式暂无可对话的结果。切到「路径规划」或「时间窗口」页后再来问。'
      : '先在左栏跑一次计算，这里才有可对话的评估结果。';
  }
  return '可以问这条航线的任何细节，例如「为什么建议这个起飞时刻」「哪一段最危险」。';
});

const draftPlaceholder = computed(() =>
  canSend.value ? '就当前结果提问，Enter 发送' : '暂无可对话的评估结果'
);

/** 消费 SSE：事件类型与后端 generate_report_stream / generate_chat_stream 一致 */
const readStream = async (url: string, body: unknown, reply: Turn) => {
  streaming.value = true;
  try {
    const response = await fetch(`${API_BASE}${url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 事件以空行分隔；末尾不完整的一段留在 buffer 里等下一块
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let event: { type: string; source?: string; text?: string; message?: string };
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (event.type === 'meta' && event.source) reply.source = event.source as 'llm' | 'rule';
        else if (event.type === 'delta' && event.text) reply.text += event.text;
        else if (event.type === 'error') reply.error = event.message ?? '生成失败';
      }
    }
  } catch (error) {
    console.error('AI 请求失败:', error);
    reply.error = 'AI 服务不可用，请确认后端已启动。';
  } finally {
    streaming.value = false;
  }
};

/**
 * 追加一轮对话并流式写入答复。
 *
 * 拿的是 push 之后数组里的**代理对象**：直接改推进去的那个原始字面量不会触发重渲染。
 */
const ask = async (question: string, preset: { url: string; body: unknown }) => {
  if (streaming.value || !canSend.value) return;
  const history = turns.value.map(t => ({ role: t.role, content: t.text }));
  turns.value.push({ role: 'user', text: question });
  turns.value.push({ role: 'assistant', text: '' });
  const reply = turns.value[turns.value.length - 1];
  const { task, payload } = chatTarget.value;
  const body = preset.body ?? { task, payload, messages: [...history, { role: 'user', content: question }] };
  await readStream(preset.url, body, reply);
};

const send = async (raw: string) => {
  const text = raw.trim();
  if (!text) return;
  draft.value = '';
  await ask(text, { url: '/api/assess/chat', body: null });
};

/** 一键报告仍走 /api/assess/report：它不需要历史，产出的是成篇的建议书 */
const askReport = () =>
  ask('请针对当前评估结果生成完整的飞行建议报告。', {
    url: '/api/assess/report',
    body: { task: chatTarget.value.task, payload: chatTarget.value.payload },
  });

// 新消息与流式增量都要把日志拉到底部
watch(
  () => [turns.value.length, turns.value[turns.value.length - 1]?.text.length ?? 0] as const,
  async () => {
    await nextTick();
    const el = logEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  }
);

// 解析入口常驻右栏，而机型能力包原本由多航点页自己拉；不补齐的话，
// 解析回填机型后速度框会停在旧值。store 里已按 null 判空，重复调用没有代价。
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

.ai-block { display: flex; flex-direction: column; gap: 8px; }

.block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.block-title svg { color: var(--accent-color); }

.task-text { resize: vertical; font-family: inherit; font-size: 12px; }
.parse-row { display: flex; align-items: center; gap: 8px; }
.capsule-btn.tiny { padding: 5px 10px; font-size: 11px; width: auto; }
.badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(56, 189, 248, 0.18);
  color: #7dd3fc;
}
.badge.muted { background: rgba(148, 163, 184, 0.18); color: var(--text-secondary); }

.chat { display: flex; flex-direction: column; gap: 10px; }

.chat-empty {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
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
.md :deep(table) { border-collapse: collapse; font-size: 11px; }
.md :deep(th), .md :deep(td) { border: 1px solid var(--panel-border); padding: 2px 6px; }
.md :deep(a) { color: var(--accent-color); }

.msg-meta { font-size: 10px; color: var(--text-secondary); }

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

.chip-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  align-self: flex-start;
  padding: 5px 11px;
  font-size: 11px;
  border-radius: 9999px;
  border: 1px solid var(--panel-border);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
}
.chip-btn:hover:not(:disabled) { color: var(--text-primary); border-color: var(--accent-color); }
.chip-btn:disabled { opacity: 0.45; cursor: not-allowed; }

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
