/**
 * 模型输出的 Markdown → 可安全注入的 HTML。
 *
 * 正文来自 LLM，直接当 HTML 注入等于把页面交给模型，因此统一过 DOMPurify。
 */
import { marked } from 'marked';
import DOMPurify from 'dompurify';

DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node instanceof HTMLAnchorElement && node.target !== '_blank') {
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
  }
});

// 模型的小标题写法不稳定（`## 标题` 与整行 `**标题**` 混用），先归一再交给 marked
const normalizeHeadings = (src: string) =>
  src.replace(/^([ \t]*)\*\*([^*\n]+)\*\*[ \t]*$/gm, '$1### $2');

export function renderMarkdown(src: string): string {
  return DOMPurify.sanitize(
    marked.parse(normalizeHeadings(src), { gfm: true, breaks: true, async: false }),
  ) as string;
}
