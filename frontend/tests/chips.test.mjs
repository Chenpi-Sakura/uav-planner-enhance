/**
 * 胶囊合并算子的单元测试（utils/chips.ts）。
 *
 * 跑法（node >= 22.18，直接跑 .ts）：
 *     cd frontend && npm test
 *
 * 这里最要紧的是最后一条「快照」用例：它守的正是 2026-10-04 那个真 bug
 * —— 初始状态点一条胶囊，问完只剩 1 条而不是 3 条。
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeChips, pickKeep } from '../src/utils/chips.ts';

/** 与 AiSidebar.vue 里的 STARTER_CHIPS 同形（值不重要，形状和条数重要） */
const STARTER = [
  { label: '拉萨送药到昌都', prompt: '从拉萨送 200 公斤兽药到昌都，请规划航线并评估风险' },
  { label: '开图层看曲松县', prompt: '打开 TKE 和 EDR 图层，把地图定位到曲松县，看看那边现在什么情况' },
  { label: '明天下午飞林芝', prompt: '评估明天下午拉萨到林芝的飞行风险' },
];
const FRESH = [{ label: '哪段最颠', prompt: '帮我看看哪一段最颠？' }];

const labels = (chips) => chips.map(c => c.label);

test('点掉第一条：留着 2 条，at 落在开头', () => {
  const { keep, at } = pickKeep(STARTER, STARTER[0]);
  assert.deepEqual(labels(keep), ['开图层看曲松县', '明天下午飞林芝']);
  assert.equal(at, 0);
});

test('点掉中间那条：at 相对「留着的那两条」算，不是原列表下标', () => {
  // 原下标是 1，但点掉之后第 3 条往前挪成第 2 位，补出来的新胶囊要插在它前面
  const { keep, at } = pickKeep(STARTER, STARTER[1]);
  assert.deepEqual(labels(keep), ['拉萨送药到昌都', '明天下午飞林芝']);
  assert.equal(at, 1);
});

test('点掉最后一条：at = 保留条数，新胶囊接在末尾', () => {
  const { keep, at } = pickKeep(STARTER, STARTER[2]);
  assert.deepEqual(labels(keep), ['拉萨送药到昌都', '开图层看曲松县']);
  assert.equal(at, 2);
});

test('点的不在当前列表里：原样全留，新胶囊接末尾', () => {
  const ghost = { label: '幽灵', prompt: '不在列表里的一条' };
  const { keep, at } = pickKeep(STARTER, ghost);
  assert.deepEqual(labels(keep), labels(STARTER));
  assert.equal(at, 3);
});

test('prompt 相同才算同一条：标签不同不影响', () => {
  const same = { label: '换个说法', prompt: STARTER[0].prompt };
  const { keep } = pickKeep(STARTER, same);
  assert.equal(keep.length, 2, '按 prompt 匹配，不按 label');
});

test('合并：3 条变 3 条（留 2 + 补 1），新胶囊回到原位', () => {
  for (const idx of [0, 1, 2]) {
    const { keep, at } = pickKeep(STARTER, STARTER[idx]);
    const out = mergeChips(keep, at, FRESH);
    assert.equal(out.length, 3, `点第 ${idx + 1} 条后应还是 3 条`);
    assert.equal(labels(out)[at], '哪段最颠', `点第 ${idx + 1} 条，新胶囊应插回第 ${at} 位`);
    // 留着的那两条一条都不能少
    for (const c of keep) assert.ok(labels(out).includes(c.label));
  }
});

test('合并：新胶囊多条时按顺序整体插入', () => {
  const fresh = [FRESH[0], { label: '备用时段', prompt: '除了这个时段还有哪些备用时段？' }];
  const { keep, at } = pickKeep(STARTER, STARTER[0]);
  const out = mergeChips(keep, at, fresh);
  assert.deepEqual(labels(out), ['哪段最颠', '备用时段', '开图层看曲松县', '明天下午飞林芝']);
});

test('一条都没补出来时返回空数组：留白还是回落由调用方决定', () => {
  const { keep, at } = pickKeep(STARTER, STARTER[0]);
  assert.deepEqual(mergeChips(keep, at, []), []);
  assert.deepEqual(mergeChips(keep, at, null), []);
  assert.deepEqual(mergeChips(keep, at, undefined), []);
});

test('at 越界夹到边界，不越界不抛', () => {
  // 负数夹到 0（新胶囊排最前），超长夹到末尾。pickKeep 本身不会给出这两种值，
  // 这里守的是「外部传进来的 at 不至于把 slice 弄出空洞或抛异常」。
  assert.deepEqual(labels(mergeChips([{ label: 'a', prompt: 'a' }], -1, FRESH)), ['哪段最颠', 'a']);
  assert.deepEqual(labels(mergeChips([{ label: 'a', prompt: 'a' }], 99, FRESH)), ['a', '哪段最颠']);
  assert.deepEqual(mergeChips([], 5, FRESH), FRESH, '没有可留的：新胶囊独立成排');
});

/**
 * 回归：模块级 keep 变量在**发请求那一刻**就被清空了，回调里现读只会读到空数组。
 * 这条把旧写法（读变量）和新写法（用快照）的结果并排钉住。
 */
test('回归：发请求后清空 keep 变量，回调合并仍须用发请求时的快照', () => {
  // 模拟 AiSidebar.vue 的结构：模块级变量 -> 发请求 -> 立刻清空 -> 回调里合并
  let moduleKeep = pickKeep(STARTER, STARTER[0]).keep;
  const moduleAt = 0;

  // 修复点：请求体与回调**共用同一份快照**
  const snapshot = moduleKeep;
  const request = { keep: snapshot, count: snapshot.length ? 1 : 3 };
  moduleKeep = [];                       // ← 旧代码在这里清空

  assert.equal(request.keep.length, 2, '请求体里带的是那 2 条');
  assert.equal(request.count, 1, '点胶囊只补 1 条');

  // 新写法：合并用快照 -> 3 条
  assert.equal(mergeChips(snapshot, moduleAt, FRESH).length, 3);
  // 旧写法：合并现读模块变量 -> 只剩 1 条（用户看到的现象）
  assert.equal(mergeChips(moduleKeep, moduleAt, FRESH).length, 1);
});
