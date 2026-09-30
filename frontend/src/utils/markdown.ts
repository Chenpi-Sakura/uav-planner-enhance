/**
 * 模型输出的 Markdown → 可安全注入的 HTML。
 *
 * 正文来自 LLM，直接当 HTML 注入等于把页面交给模型，因此统一过 DOMPurify。
 */
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import katex from 'katex';
import 'katex/dist/katex.min.css';

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

/**
 * 把 `$...$` / `$$...$$` 里的数学式摘出来，交给 KaTeX 排版。
 *
 * **必须在 marked 之前摘**。LaTeX 里 `_`、`^`、`*` 全是 Markdown 的强调/下标标记：
 * `$x_i$` 经 marked 会变成斜体的 `x`，上下标直接消失。留到最后再处理就晚了——
 * 到那时公式已经被吃成普通文本，只能干瞪眼。
 *
 * 摘出来的片段先换成占位符，等 sanitize 完再把 KaTeX 的 HTML 填回去。
 * 不能在 sanitize **之前**填：KaTeX 输出里的 `<math>` / `<annotation>` 会被
 * DOMPurify 按 HTML profile 剥掉（它默认不认 MathML），公式就只剩个空壳。
 * 反过来填在之后则完全绕过了净化 —— 所以 KaTeX 必须关掉一切危险能力
 * （`trust: false` 且 `strict: 'ignore'`），让它只做排版、不解释任何外部资源。
 */
const MATH_TOKEN = (i: number) => `xMATHOx${i}xENDx`;

const MATH_BLOCK = /\$\$([\s\S]+?)\$\$/g;
// 行内式：$ 后面不能紧跟空白，前面不能紧跟空白，否则「$5 和 $10」这种货币写法会被误吞
const MATH_INLINE = /(?<!\$)\$(?![\s$])([^$\n]+?)(?<![\s$])\$(?!\$)/g;

function extractMath(src: string) {
  const rendered: string[] = [];

  const put = (tex: string, display: boolean) => {
    let html: string;
    try {
      html = katex.renderToString(tex, {
        displayMode: display,
        throwOnError: false,   // 公式写错时原样显示而不是整块吞掉
        strict: 'ignore',      // 不因 \frac{1}{2} 之外的写法报错打断渲染
        trust: false,          // 禁止 \href/\includegraphics 之类引入外部资源
        output: 'html',        // 只要 HTML 输出：MathML 会被净化剥掉，留着也没用
      });
    } catch {
      html = '';               // 真出不了就留空，至少不把整段正文带走
    }
    rendered.push(html);
    return MATH_TOKEN(rendered.length - 1);
  };

  let text = src.replace(MATH_BLOCK, (_, tex: string) => put(tex, true));
  text = text.replace(MATH_INLINE, (_, tex: string) => put(tex, false));
  return { text, rendered };
}

function restoreMath(html: string, rendered: string[]): string {
  if (!rendered.length) return html;
  return html.replace(/xMATHOx(\d+)xENDx/g, (_, i: string) => rendered[Number(i)] ?? '');
}

export function renderMarkdown(src: string): string {
  const { text, rendered } = extractMath(src);
  const safe = DOMPurify.sanitize(
    marked.parse(tabTableToPipe(normalizeHeadings(text)), {
      gfm: true, breaks: true, async: false,
    }),
  ) as string;
  return restoreMath(safe, rendered);
}
