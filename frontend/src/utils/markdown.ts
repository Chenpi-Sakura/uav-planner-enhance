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

const TS = /\t/;

/**
 * 把制表符分隔的「伪表格」归一成 Markdown 管道表。
 *
 * 模型写表格时并不稳定：有时给 `| a | b |`，有时直接用制表符对齐。后者
 * 经 marked 会退化成几行普通段落，**根本不会出现 `<table>`** —— 界面上就是
 * 一堆挤在一起的文字，看起来像模型格式化了、其实什么表格都没有。
 *
 * 只在「连续两行以上都含制表符」时才认定是表格：单行出现制表符多半是排版对齐，
 * 硬转反而会造出一张假表。列数按最宽的那行补齐，缺格留空。
 */
function tabTableToPipe(src: string): string {
  const lines = src.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    if (!TS.test(lines[i])) { out.push(lines[i]); i++; continue; }

    const run: string[] = [];
    while (i < lines.length && TS.test(lines[i])) { run.push(lines[i]); i++; }
    if (run.length < 2) { out.push(...run); continue; }

    const rows = run.map(l =>
      l.split(/\t+/).map(c => c.trim()).filter((c, k, a) => !(k === a.length - 1 && c === '')),
    );
    const width = Math.max(...rows.map(r => r.length));
    const pad = (r: string[]) => Array.from({ length: width }, (_, k) => r[k] ?? '');

    out.push(`| ${pad(rows[0]).join(' | ')} |`);
    out.push(`| ${Array.from({ length: width }, () => '---').join(' | ')} |`);
    for (const r of rows.slice(1)) out.push(`| ${pad(r).join(' | ')} |`);
  }
  return out.join('\n');
}

export function renderMarkdown(src: string): string {
  return DOMPurify.sanitize(
    marked.parse(tabTableToPipe(normalizeHeadings(src)), {
      gfm: true, breaks: true, async: false,
    }),
  ) as string;
}
