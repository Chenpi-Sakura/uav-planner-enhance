/**
 * 推演时钟的跨天行为（backlog ⑤：之前只有代码推导，没有可重跑的凭据）。
 *
 * 跑法（node >= 22.18，直接吃 .ts）：
 *     cd frontend && node tests/simClock.test.mjs
 *
 * 这里钉住的是三处共用同一把尺子：游标读数、折线图横轴标签、热图取帧用的日期。
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { addDays, clockAt, dayPrefix, daysBetween, simClockText } from '../src/utils/simClock.ts';

const D = '2022-07-01';
const T = '08:00';

test('没跨午夜：时刻按里程推进，日期不动，不带前缀', () => {
  const c = clockAt(D, T, 100, 60);            // 1h40m
  assert.equal(c.time, '09:40');
  assert.equal(c.date, D);
  assert.equal(c.dayOffset, 0);
  assert.equal(simClockText(c), '09:40');
});

test('正好飞到午夜：算作次日 00:00，不是当天 24:00', () => {
  const c = clockAt(D, T, 960, 60);            // 起飞后整 16 小时
  assert.equal(c.time, '00:00');
  assert.equal(c.date, '2022-07-02');
  assert.equal(dayPrefix(c.dayOffset), '次日 ');
  assert.equal(simClockText(c), '次日 00:00');
});

test('1600 km 的长途线（27 小时）落在次日白天', () => {
  const c = clockAt(D, T, 1600, 60);
  assert.equal(c.time, '10:40');
  assert.equal(c.date, '2022-07-02');
  assert.equal(simClockText(c), '次日 10:40');
});

test('飞满两天以上用 +N日，日期跟着走', () => {
  const c = clockAt(D, T, 3000, 60);           // 50 小时
  assert.equal(c.dayOffset, 2);
  assert.equal(c.date, '2022-07-03');
  assert.equal(simClockText(c), '+2日 10:00');
});

test('起飞时刻贴近午夜时，1 分钟就跨天', () => {
  const c = clockAt(D, '23:59', 2, 60);        // 2 km @60 = 2 分钟
  assert.equal(c.time, '00:01');
  assert.equal(c.date, '2022-07-02');
});

test('addDays 跨年/跨月与 daysBetween 互为逆运算', () => {
  assert.equal(addDays('2022-12-31', 1), '2023-01-01');
  assert.equal(addDays('2024-02-27', 2), '2024-02-29');   // 闰年
  assert.equal(daysBetween(D, addDays(D, 3)), 3);
  assert.equal(daysBetween(D, '2022-06-30'), -1);
  assert.equal(addDays('不是日期', 5), '不是日期');            // 坏输入原样返回
});

test('速度缺失按 60 兜底，不炸成 Infinity', () => {
  for (const sp of [0, NaN, undefined]) {
    const c = clockAt(D, T, 120, sp);
    assert.equal(c.time, '10:00');
  }
});

test('横轴与游标同一个函数：同里程必须同读数', () => {
  const dist = 1234.5;
  const axis = clockAt(D, T, dist, 60);
  const cursor = clockAt(D, T, dist, 60);
  assert.deepEqual(axis, cursor);
  assert.equal(`${dayPrefix(axis.dayOffset)}${axis.time}`, simClockText(cursor));
});
