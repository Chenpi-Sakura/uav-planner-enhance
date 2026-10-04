/**
 * 胶囊（建议入口）的两段纯算子：点掉一条之后留哪些，新补的那条插回第几位。
 *
 * 抽出来不是为了好看。这段逻辑原先长在 AiSidebar.vue 的 Promise 回调里，读的是
 * 模块级变量 `keepChips` —— 而那个变量在**发出请求那一刻**就被清空了，回调跑到时
 * 读到的是空数组，留着的那两条就这么凭空消失：初始状态点一条胶囊，问完之后屏幕上
 * 只剩新生成的那 1 条，而不是「保留 2 + 补 1」。
 *
 * 纯函数 + 单测能把这句契约钉死：**发请求之前先取快照，回调里只认快照**。
 * 改回去时把 chips.test.mjs 里那条「旧写法」的断言对一下就知道行为变了。
 */

export interface Chip {
  label: string;
  prompt: string;
}

/**
 * 用户点掉了一条胶囊：返回屏幕上原样留着的那几条，以及新补的那条该插回第几位。
 *
 * `at` 是**留着那条数组**里的下标，不是原列表里的下标 —— 点掉一条之后后面会往前挪，
 * 直接拿原下标会插错位置（点第 1 条却插到第 2 位）。越界的一律夹到边界。
 */
export function pickKeep(chips: Chip[], clicked: Chip): { keep: Chip[]; at: number } {
  const i = chips.findIndex(c => c.prompt === clicked.prompt);
  const keep = chips.filter(c => c.prompt !== clicked.prompt);
  const at = i < 0 ? keep.length : Math.min(i, keep.length);
  return { keep, at };
}

/**
 * 把新生成的胶囊插回 `keep` 的第 `at` 位。
 *
 * 一条都没补出来时返回**空数组**（而不是回落值）：屏幕留白、还是退回开场胶囊，
 * 是调用方按当时场景该做的判断，不该由这个算子替它决定。
 */
export function mergeChips(
  keep: Chip[],
  at: number,
  fresh: Chip[] | null | undefined,
): Chip[] {
  if (!fresh?.length) return [];
  const k = Math.max(0, Math.min(at, keep.length));
  return [...keep.slice(0, k), ...fresh, ...keep.slice(k)];
}
