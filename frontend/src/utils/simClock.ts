/**
 * 推演时钟：起飞时刻 + 已飞物理里程 -> 当前时刻/当前日期。
 *
 * 三个地方要用同一套口径 —— 游标文本、图表横轴标签、热图取哪一帧的日期。
 * 各算各的就会出现「无人机踩在第二天的天气上，坐标轴还在写第一天的钟」，
 * 所以这段单独成文件，可以用 node 直接跑断言（见 tests/simClock.test.mjs）。
 */

export interface SimClock {
  time: string;       // HH:MM，恒在一天之内
  date: string;       // YYYY-MM-DD，起飞日期 + dayOffset
  dayOffset: number;  // 跨过几个午夜
}

function fmtDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** 起飞日期 + 若干天（推演跨午夜时用，返回 YYYY-MM-DD）。 */
export function addDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(d.getTime())) return dateStr;
  d.setDate(d.getDate() + days);
  return fmtDate(d);
}

/** b - a 相差几天（配 addDays 用，把游标日期还原成"飞第几天"）。 */
export function daysBetween(a: string, b: string): number {
  const d1 = new Date(`${a}T00:00:00`).getTime();
  const d2 = new Date(`${b}T00:00:00`).getTime();
  if (Number.isNaN(d1) || Number.isNaN(d2)) return 0;
  return Math.round((d2 - d1) / 86400000);
}

/** 已飞 distKm 公里（速度 speedKmh）之后是几点、是哪一天。 */
export function clockAt(startDate: string, startTime: string, distKm: number, speedKmh: number): SimClock {
  const [h0, m0] = (startTime || '08:00').split(':');
  const speed = Number(speedKmh) || 60;
  const totalMinutes = (parseInt(h0 || '8') * 60 + (parseInt(m0 || '0') || 0))
    + Math.round((Number(distKm) || 0) / speed * 60);
  const dayOffset = Math.floor(totalMinutes / 1440);
  const inDay = ((totalMinutes % 1440) + 1440) % 1440;
  const time = `${String(Math.floor(inDay / 60)).padStart(2, '0')}:${String(inDay % 60).padStart(2, '0')}`;
  return { time, date: addDays(startDate, dayOffset), dayOffset };
}

/** 跨天前缀：'次日 '、'+3日 '；同一天不给前缀。 */
export function dayPrefix(dayOffset: number): string {
  if (dayOffset === 1) return '次日 ';
  if (dayOffset > 1) return `+${dayOffset}日 `;
  return '';
}

/** 游标读数：跨了午夜就带上"次日/+N日"，否则只有 HH:MM。 */
export function simClockText(clock: SimClock): string {
  return `${dayPrefix(clock.dayOffset)}${clock.time}`;
}
