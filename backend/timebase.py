"""统一时间基准换算（UI 北京时间 <-> 数据标签时间）。

背景：NetCDF 时间标签实为 UTC，而前端输入与展示均为北京时间，
两者相差 `settings.DATA_TZ_OFFSET_HOURS` 小时。

约定：
  - 后端**对外契约**（API 入参 / 响应 / UI 文案）统一使用北京时间；
  - `data_loader` 等数据层接口只接受「数据标签时间」；
  - 二者之间必须经过本模块换算，禁止直接拼接 `date + start_time` 查表。

历史教训：M2 之前只在时间窗口链路做了换算，功能一与规划器仍把
北京时间的字符串直接当数据标签使用，导致取到偏早 8 小时的气象场，
窗口与曲线整体偏移（计划书 §12 头号风险）。
"""
from typing import Union

import numpy as np

from config import settings

TimeLike = Union[str, np.datetime64]


def to_data_time(user_time: TimeLike) -> np.datetime64:
    """把 UI 输入的北京时间换算为数据时间轴上的时刻（UTC 标签）。"""
    t = np.datetime64(user_time)
    if not settings.DATA_LABEL_IS_UTC:
        return t
    return t - np.timedelta64(int(settings.DATA_TZ_OFFSET_HOURS), 'h')


def to_user_time(data_time: TimeLike) -> np.datetime64:
    """把数据时间轴上的时刻换算回北京时间（用于展示/对外输出）。"""
    t = np.datetime64(data_time)
    if not settings.DATA_LABEL_IS_UTC:
        return t
    return t + np.timedelta64(int(settings.DATA_TZ_OFFSET_HOURS), 'h')


def to_data_time_str(user_time: TimeLike) -> str:
    """北京时间 -> 数据标签字符串（形如 2022-07-01T00:00）。"""
    return str(to_data_time(user_time).astype('datetime64[m]'))


def to_user_time_str(data_time: TimeLike) -> str:
    """数据标签 -> 北京时间字符串（形如 2022-07-01T08:00）。"""
    return str(to_user_time(data_time).astype('datetime64[m]'))


def data_timezone_meta() -> dict:
    """返回时区口径的元信息，供响应体透出（前端/报告可据此标注）。"""
    return {
        "data_label_is_utc": bool(settings.DATA_LABEL_IS_UTC),
        "tz_offset_hours": int(settings.DATA_TZ_OFFSET_HOURS),
        "user_time_label": "北京时间（UTC+8）",
    }