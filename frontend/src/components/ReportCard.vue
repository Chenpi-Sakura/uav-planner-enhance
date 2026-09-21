<template>
  <div class="sub-block report-block">
    <div class="report-head">
      <h4 class="sub-title"><Sparkles :size="14" /> AI 评估报告</h4>
      <span v-if="sourceLabel" class="report-source" :class="source">{{ sourceLabel }}</span>
    </div>

    <!-- 生成中的骨架：仅在尚无任何正文时显示，避免与正文重复展示 -->
    <div v-if="loading && !text" class="report-skeleton">
      <span class="dot-loading"></span>
      <span>正在生成飞行建议...</span>
    </div>

    <div v-if="text" class="report-body" v-html="renderedHtml"></div>

    <p v-if="errorText" class="report-error">
      <AlertTriangle :size="13" /> {{ errorText }}
    </p>

    <div class="report-actions">
      <button class="ghost-btn" @click="generate" :disabled="loading">
        <Loader v-if="loading" :size="14" class="spin" />
        <RefreshCw v-else :size="14" />
        {{ loading ? '生成中...' : (text ? '重新生成' : '生成 AI 报告') }}
      </button>
      <button v-if="text" class="ghost-btn" @click="copyReport">
        <Copy :size="14" />
        {{ copied ? '已复制' : '复制报告' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { Sparkles, Loader, Copy, RefreshCw, AlertTriangle } from 'lucide-vue-next';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { API_BASE } from '../config';

DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node instanceof HTMLAnchorElement && node.target !== '_blank') {
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
  }
});

const props = defineProps<{
  /** 报告类型：功能一 / 功能二 */
  task: 'feasibility' | 'time_window';
  /** 结构化评估结果，直接透传给后端（后端只读取其中字段） */
  payload: unknown;
}>();

const text = ref('');
const loading = ref(false);
const errorText = ref('');
const copied = ref(false);
const source = ref<'llm' | 'rule' | ''>('');

const sourceLabel = computed(() => {
  if (!source.value) return '';
  return source.value === 'llm' ? 'LLM 生成' : '规则模板';
});

/**
 * 报告正文按 Markdown 渲染。
 *
 * 模型的小标题写法不稳定（`## 标题` 与整行 `**标题**` 混用），先归一再交给 marked；
 * 结果统一过 DOMPurify，避免把模型输出直接当 HTML 注入。
 */
const normalizeHeadings = (src: string) =>
  src.replace(/^([ \t]*)\*\*([^*\n]+)\*\*[ \t]*$/gm, '$1### $2');

const renderedHtml = computed(() =>
  DOMPurify.sanitize(
    marked.parse(normalizeHeadings(text.value), { gfm: true, breaks: true, async: false }),
  ),
);

const generate = async () => {
  if (loading.value) return;
  loading.value = true;
  errorText.value = '';
  text.value = '';
  source.value = '';

  try {
    const response = await fetch(`${API_BASE}/api/assess/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: props.task, payload: props.payload }),
    });
    if (!response.ok) throw new Error('报告生成失败');
    if (!response.body) throw new Error('当前环境不支持流式响应');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // 逐段读取 SSE：以空行分隔事件，逐条解析 data: 负载
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

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
        if (event.type === 'meta' && event.source) {
          source.value = event.source as 'llm' | 'rule';
        } else if (event.type === 'delta' && event.text) {
          text.value += event.text;
        } else if (event.type === 'error') {
          errorText.value = event.message ?? 'LLM 调用失败，已回退到规则模板版报告。';
        }
      }
    }
  } catch (error) {
    console.error('AI 报告请求失败:', error);
    errorText.value = '报告生成失败，请确认后端服务已启动。';
  } finally {
    loading.value = false;
  }
};

const copyReport = async () => {
  try {
    await navigator.clipboard.writeText(text.value);
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 1800);
  } catch {
    errorText.value = '复制失败，请手动选择文本复制。';
  }
};
</script>

<style scoped>
.report-block { margin-bottom: 16px; }

.report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.report-head .sub-title { margin: 0; }

.report-source {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 9999px;
  background: rgba(148, 163, 184, 0.18);
  color: var(--text-secondary);
  white-space: nowrap;
}
.report-source.llm {
  background: rgba(52, 152, 219, 0.18);
  color: #7dd3fc;
}

.report-skeleton {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-secondary);
  padding: 10px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.18);
}
.dot-loading {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-color);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}

.report-body {
  background: rgba(0, 0, 0, 0.18);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-primary);
}
.report-body :deep(h1),
.report-body :deep(h2),
.report-body :deep(h3),
.report-body :deep(h4),
.report-body :deep(h5),
.report-body :deep(h6) {
  font-size: 12px;
  color: var(--accent-color);
  margin: 10px 0 4px 0;
  font-weight: 600;
  line-height: 1.5;
}
.report-body :deep(:first-child) { margin-top: 0; }
.report-body :deep(p) { margin: 0 0 6px 0; }
.report-body :deep(p:last-child) { margin-bottom: 0; }
.report-body :deep(ul),
.report-body :deep(ol) { margin: 0 0 6px 0; padding-left: 18px; }
.report-body :deep(li) { margin: 0 0 3px 0; }
.report-body :deep(li::marker) { color: var(--text-secondary); }
.report-body :deep(strong) { font-weight: 700; color: var(--text-primary); }
.report-body :deep(em) { color: var(--text-secondary); }
.report-body :deep(a) { color: #7dd3fc; text-decoration: underline; }
.report-body :deep(code) {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.16);
}
.report-body :deep(pre) {
  margin: 0 0 6px 0;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.28);
  overflow-x: auto;
}
.report-body :deep(pre code) { padding: 0; background: none; }
.report-body :deep(blockquote) {
  margin: 0 0 6px 0;
  padding-left: 8px;
  border-left: 2px solid var(--accent-color);
  color: var(--text-secondary);
}
.report-body :deep(table) {
  width: 100%;
  margin: 0 0 6px 0;
  border-collapse: collapse;
  font-size: 11px;
}
.report-body :deep(th),
.report-body :deep(td) {
  padding: 3px 6px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  text-align: left;
}
.report-body :deep(th) { background: rgba(148, 163, 184, 0.12); }
.report-body :deep(hr) {
  margin: 8px 0;
  border: none;
  border-top: 1px solid rgba(148, 163, 184, 0.24);
}

.report-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 8px 0 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: #fca5a5;
}
.report-error svg { flex-shrink: 0; margin-top: 2px; }

.report-actions {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  margin-top: 10px;
}
.report-actions .ghost-btn {
  background: transparent;
  border: 1px dashed var(--panel-border);
  color: var(--text-secondary);
  border-radius: 9999px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  transition: all 0.2s;
}
.report-actions .ghost-btn:hover:not(:disabled) {
  border-color: var(--accent-color);
  color: var(--accent-color);
}
.report-actions .ghost-btn:disabled { opacity: 0.6; cursor: default; }
.spin { animation: spin 1.5s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }
</style>
