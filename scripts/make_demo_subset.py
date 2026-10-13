"""抽取最小演示子集：以某个演示日为中心，从全年 NetCDF 切一小块出来。

用法（默认参数就是 2022-05-17 那一窗）：

    backend\\.python\\portable\\python.exe scripts\\make_demo_subset.py

产物落到 data_demo/，**文件名与源完全一致**（tibet_2022_upper_*.nc），后端用
仓库根 `.env` 里的 `UAV_DATA_DIR=data_demo` 指过去；不替换、不删除 data/ 下的全量数据。

三个裁剪维度都按实测来，不是估的：

- **lev 只留 500 hPa**（19 层 -> 1 层）。后端所有取数路径都把这一层钉死了：
  `data_loader.read_slab` 取 `levs == 500.0`、`_read_overlay_slice` 与
  `_read_overlay_window` 都是 `.sel(lev=500)`。留 19 层等于把 18 份从没读过的数据抄一遍。
- **时间取 72 帧**：演示日前一天 00Z 到后一天 00Z（不含），共 3 个 UTC 日。湍流是
  ±6h 滑动脉动方差（`TURB_WINDOW_HALF_HOURS`），而 `get_meteorology_grid` 读的是
  `date T08:00` 起 `24 + 6 + 1` 帧，时间窗口扫描又按**北京时间**整天往前挪 8h 才落到
  数据标签上，两头都必须留够；只 cut 24 帧会让演示日首尾的格点成片判成「窗口有效样本不足」。
  72 帧是这套余量算出来的结果，不是随手取的整数：演示日 D 真正需要的是
  `[D-14h, D+44h]`（见后端 `resolve_default_date`），前后各富余一天正好包住它。
- **区域 28.0–32.5N / 87.5–100.5E**：实测 91x228 格点，覆盖 `geo.CITIES` 的全部
  15 个城市并留出余量。收得更紧会让 `coverage_bbox` 的框比城市表还小，界面上
  一大半点位一点就出界。

写出来的块**不分块**（与源文件同为 contiguous 布局）：这个尺寸下分块只有坏处 —— 按后端
`load_data()` 请求的 `(24, 1, 80, 80)` 分块会让边缘块被补齐，6 MB 的数据涨到 11 MB，
还会多出一条 rechunk 告警。取数形态是「整块时间片 x 全区纬经」，连续布局正好是顺序读。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# 口径来自后端自己：窗口半宽与时区偏移改了这里要跟着改，所以直接 import。
from config import settings  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="抽取最小演示子集")
    p.add_argument("--date", default="2022-05-17",
                   help="演示日期（北京时间口径），子集 = 该日前一天 00Z 到后一天 00Z")
    p.add_argument("--bbox", default="28.0,32.5,87.5,100.5",
                   help="min_lat,max_lat,min_lon,max_lon")
    p.add_argument("--src", default=str(ROOT / "data"), help="全量数据目录")
    p.add_argument("--dst", default=str(ROOT / "data_demo"), help="子集输出目录")
    p.add_argument("--overwrite", action="store_true", help="覆盖已有子集")
    return p.parse_args()


def _vars() -> list:
    """[(输出文件名, 变量名), ...]，六个源文件按后端同一张表来。"""
    return [(settings.U_FILE, "u"), (settings.V_FILE, "v"), (settings.W_FILE, "w"),
            (settings.TC_FILE, "tc"), (settings.RH_FILE, "rh"), (settings.Q_FILE, "qvapor")]


def _cut_indices(ds, lev, t0, t1, lat_box, lon_box):
    """源文件上这段裁剪的整数下标（cut_one 与 verify 共用一套算法，免得两头口径不同）。"""
    tv = np.asarray(ds.time.values, dtype="datetime64[ns]")
    i0 = int(np.searchsorted(tv, np.datetime64(t0, "ns")))
    i1 = int(np.searchsorted(tv, np.datetime64(t1, "ns")))
    if i1 <= i0:
        raise ValueError(f"时间轴上没有 {t0}..{t1} 这段数据")
    la = np.asarray(ds.lat.values, dtype="float64")
    lo = np.asarray(ds.lon.values, dtype="float64")
    li = np.flatnonzero((la >= lat_box[0]) & (la <= lat_box[1]))
    oi = np.flatnonzero((lo >= lon_box[0]) & (lo <= lon_box[1]))
    if li.size == 0 or oi.size == 0:
        raise ValueError(f"区域 {lat_box} / {lon_box} 与数据轴不相交")
    lv = np.asarray(ds.lev.values, dtype="float64")
    lv_idx = int(np.argmin(np.abs(lv - lev)))
    if abs(float(lv[lv_idx]) - lev) > 1e-6:
        raise ValueError(f"数据里没有 {lev} hPa 层，最近的是 {float(lv[lv_idx])}")
    return i0, i1, li, oi, lv_idx


def cut_one(src_path: str, dst_path: str, var: str, lev: float,
            t0, t1, lat_box, lon_box) -> dict:
    """切一个变量并写盘，返回实测信息。"""
    ds = xr.open_dataset(src_path, chunks=None)
    try:
        i0, i1, li, oi, lv_idx = _cut_indices(ds, lev, t0, t1, lat_box, lon_box)
        tv = np.asarray(ds.time.values, dtype="datetime64[ns]")
        la = np.asarray(ds.lat.values, dtype="float64")
        lo = np.asarray(ds.lon.values, dtype="float64")
        lv_val = float(np.asarray(ds.lev.values, dtype="float64")[lv_idx])

        # lev 必须留成 size=1 的维度，不能用 isel(lev=idx) 降掉：
        # read_slab 要读 ds.lev.values 找 500 hPa，_read_overlay_slice 走 .sel(lev=500)，
        # 维一丢这两条路径都拿不到层，抽出来的子集就废了。
        sub = ds[var].isel(time=slice(i0, i1), lev=slice(lv_idx, lv_idx + 1),
                           lat=slice(int(li[0]), int(li[-1]) + 1),
                           lon=slice(int(oi[0]), int(oi[-1]) + 1))
        # 坐标与属性整体带过去，_FillValue / missing_value / units 都还在 attrs 里，
        # 所以写盘时 NaN 会重新落回 1e30，读回来又是 NaN —— 与全量文件同一行为。
        out = xr.Dataset({var: sub}, attrs=dict(ds.attrs))
        out.attrs["demo_subset"] = (
            f"cut from {os.path.basename(src_path)}: lev={lv_val:g} hPa, "
            f"time {tv[i0]}..{tv[i1 - 1]}, lat {float(la[li[0]]):.4f}..{float(la[li[-1]]):.4f}, "
            f"lon {float(lo[oi[0]]):.4f}..{float(lo[oi[-1]]):.4f}")
        # 不指定 chunksizes —— 与源文件一样写成 contiguous 布局。理由有两条实测：
        #   1) 分块到后端 load_data() 请求的 (24,1,80,80) 会让边缘块被补齐，72x91x228 的
        #      float32 本该 6 MB，分块后变 11 MB；而 contiguous 布局下 xarray 对请求的
        #      chunks 不发 "separate the stored chunks" 告警（源文件今天也是这样）。
        #   2) 本项目的取数形态是「整块时间片 x 全区纬经」，time-major 的连续布局正好是
        #      顺序读；分块只在 103 GB 的全量文件上才有意义，6 MB 的文件整份都在 OS 缓存里。
        # 源文件读一块 10x10 的 slab 实测 0.28 s，那是小跨度随机读的代价，与这里的形态无关。
        enc = {var: {"dtype": "float32", "zlib": False, "contiguous": True},
               "time": {"units": "hours since 2022-01-01 00:00:00", "calendar": "standard"}}
        t = time.time()
        out.to_netcdf(dst_path, engine="netcdf4", encoding=enc)
        return {"frames": i1 - i0, "lat": int(li.size), "lon": int(oi.size),
                "bytes": os.path.getsize(dst_path), "secs": time.time() - t,
                "lev": lv_val, "time_first": tv[i0], "time_last": tv[i1 - 1]}
    finally:
        ds.close()


def verify(dst_path: str, src_path: str, var: str, lev: float,
           t0, t1, lat_box, lon_box) -> None:
    """逐比特比子集与源数据的同一块切片，坐标也必须一致。

    这条是本脚本的唯一硬证据：抽错层、错一格、填充值走样，都会在这里炸掉，
    而不是等到演示那天在地图上看出对不上。
    """
    src = xr.open_dataset(src_path, chunks=None)
    sub = xr.open_dataset(dst_path, chunks=None)
    try:
        i0, i1, li, oi, lv_idx = _cut_indices(src, lev, t0, t1, lat_box, lon_box)
        ref = src[var].isel(time=slice(i0, i1), lev=slice(lv_idx, lv_idx + 1),
                            lat=slice(int(li[0]), int(li[-1]) + 1),
                            lon=slice(int(oi[0]), int(oi[-1]) + 1))
        va = np.asarray(sub[var].values, dtype="float64")
        vb = np.asarray(ref.values, dtype="float64")
        if va.shape != vb.shape:
            raise AssertionError(f"{var}: 形状不一致 {va.shape} != {vb.shape}")
        for name in ("time", "lev", "lat", "lon"):
            if not np.array_equal(np.asarray(sub[name].values), np.asarray(ref[name].values)):
                raise AssertionError(f"{var}: 坐标 {name} 与源不一致")
        # equal_nan 是必须的：源里 1e30 读回来是 NaN，写出去又落回 1e30，
        # 两边都成 NaN 才算过 —— 后端读到的正是这一份。
        if not np.array_equal(va, vb, equal_nan=True):
            bad = int(np.sum(~((va == vb) | (np.isnan(va) & np.isnan(vb)))))
            raise AssertionError(f"{var}: {bad} 个格点与源数据不同")
    finally:
        src.close()
        sub.close()


def main() -> int:
    args = _parse_args()
    min_lat, max_lat, min_lon, max_lon = (float(x) for x in args.bbox.split(","))
    day = np.datetime64(args.date)
    t0 = day - np.timedelta64(1, "D")          # 演示日前一天 00Z
    t1 = day + np.timedelta64(2, "D")          # 到后一天 00Z（不含）=> 72 帧
    os.makedirs(args.dst, exist_ok=True)

    print(f"演示日 {args.date}（北京口径），子集时间 {t0} .. {t1} 之前，"
          f"区域 {min_lat}..{max_lat} N / {min_lon}..{max_lon} E，只留 500 hPa")
    total = 0
    for fname, var in _vars():
        dst_path = os.path.join(args.dst, fname)
        if os.path.exists(dst_path) and not args.overwrite:
            print(f"  {fname}: 已存在，跳过（要重抽加 --overwrite）")
            total += os.path.getsize(dst_path)
            continue
        src_path = os.path.join(args.src, fname)
        if not os.path.exists(src_path):
            print(f"  {fname}: 找不到源文件 {src_path}")
            return 1
        info = cut_one(src_path, dst_path, var, 500.0, t0, t1,
                       (min_lat, max_lat), (min_lon, max_lon))
        verify(dst_path, src_path, var, 500.0, t0, t1,
               (min_lat, max_lat), (min_lon, max_lon))
        total += info["bytes"]
        print(f"  {var}: {info['frames']} 帧 x {info['lat']}x{info['lon']} "
              f"@ {info['lev']:g} hPa，{info['time_first']} .. {info['time_last']}，"
              f"{info['bytes'] / 1e6:.1f} MB，{info['secs']:.1f}s，逐比特一致")
    print(f"合计 {total / 1e6:.1f} MB -> {args.dst}")
    print(f"后端切过去：仓库根 .env 里写 UAV_DATA_DIR={args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
