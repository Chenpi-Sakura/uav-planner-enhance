import os
import math
import threading
import time
import xarray as xr
import io
from collections import OrderedDict
from typing import Dict, Optional, Sequence, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

from config import settings
from meteorology import pulsation_tke, edr_from_tke


# slab 读不到（数据集缺失 / bbox 落在域外）时逐点取数退回的占位常量。
# 全仓唯一一份，判定入口是下面的 is_placeholder_sample()。
# 注意它存在只是**历史兼容**：逐点取数的返回值会整批进响应，而剖面图要的是一条能画的
# 曲线。用 NaN 也能表达缺测（pydantic 会序列化成 JSON null，前端画断点），
# 但那样沿线每个点都是 null，图上什么都没有。所以这里保留常量 + 显式哨兵判定，
# 而不是把「没读到」伪装成一个能画出来的 0。
_PLACEHOLDER = {"u": 5.0, "v": 5.0, "w": 0.0, "tc": 280.0,
                "rh": 50.0, "qvapor": 0.005, "tke": 0.0, "edr": 0.0}


def is_placeholder_sample(u, v, w, tc) -> bool:
    """判断一组逐点取数结果是不是 :data:`_PLACEHOLDER`（即这一次 slab 没读到）。

    这不是杞人忧天：``_loaded`` 守卫挡不住这种情况（数据确实加载好了，
    只是这一次 slab 没读到），而占位值里的 ``tke=0.0`` 是一个**看起来最安全的数**，
    直接送进 ``classify_point`` 会把一次读盘失败说成「零湍流、低风险、可以飞」。

    真实格点不会同时精确等于这组常数，所以整体比对足够可靠；
    宁可误报「无数据」，也不能误报「安全」。
    """
    try:
        return (float(u) == _PLACEHOLDER["u"] and float(v) == _PLACEHOLDER["v"]
                and float(w) == _PLACEHOLDER["w"] and float(tc) == _PLACEHOLDER["tc"])
    except (TypeError, ValueError):
        return False


def nearest_time_index(times, target_time) -> int:
    """时间轴上最接近 target_time 的下标（数据标签为 UTC，逐小时）。"""
    tv = np.asarray(times, dtype='datetime64[ns]').astype('int64')
    tt = np.datetime64(target_time, 'ns').astype('int64')
    return int(np.abs(tv - tt).argmin())


def _nearest_time_indices(times, targets) -> np.ndarray:
    """时间轴上为每个目标时刻找最近下标（向量化版，供逐点时刻取数用）。"""
    tv = np.asarray(times, dtype='datetime64[ns]').astype('int64')
    tt = np.asarray(targets, dtype='datetime64[ns]').astype('int64')
    return np.abs(tv[None, :] - tt[:, None]).argmin(axis=1)


def turbulence_from_wind(u, v, w):
    """三方向风场 -> (TKE, EDR)。全仓库唯一出口，别处不要再写公式。

    输入形状 (hours, ...)，单位 m/s；脉动方差沿 hours 轴滑窗。
    """
    tke = pulsation_tke(u, v, w)
    return tke, edr_from_tke(tke)

def _merc_y(lat_deg: float) -> float:
    """纬度 -> Web Mercator 投影 y（弧度）。Leaflet 贴图用的就是这套投影。"""
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat_deg) / 2.0))


def _merc_to_lat(y_rad: float) -> float:
    """Web Mercator 投影 y -> 纬度（_merc_y 的逆）。"""
    return math.degrees(2.0 * math.atan(math.exp(y_rad)) - math.pi / 2.0)


def _mercator_row_latitudes(min_lat: float, max_lat: float, rows: int) -> np.ndarray:
    """覆盖层 PNG 的行纬度：在 Mercator y 上等距，第 0 行是北端 (max_lat)。

    L.imageOverlay 是把 PNG 线性贴到投影平面的 bounds 上的，所以第 j 行的纬度必须是
    φ_j = ym⁻¹(lerp(ym(max_lat), ym(min_lat), j/(rows-1)))，行代表什么纬度才和它贴在
    屏幕哪儿一致。此前按等纬度间隔排行，热图相对地图整体漂移：跨 25° 纬度时最大约
    0.9°（~100 km），上下边缘误差为 0、视口中纬度最大，只偏纬度不偏经度
    （Mercator 的 x 与经度线性，经度向从来没错）。

    rows < 2 或纬度退化（南>=北、超出投影定义域）时抛 ValueError，由调用方兜成全透明图。
    """
    rows = int(rows)
    y_top, y_bottom = _merc_y(max_lat), _merc_y(min_lat)
    if rows < 2 or not (math.isfinite(y_top) and math.isfinite(y_bottom)) \
            or not (y_top > y_bottom):
        raise ValueError(f'overlay box degenerate: lat {min_lat}..{max_lat}, rows={rows}')
    t = np.arange(rows, dtype='float64') / (rows - 1)
    y = y_top + (y_bottom - y_top) * t
    return np.degrees(2.0 * np.arctan(np.exp(y)) - math.pi / 2.0)


def _cell_edge_extents(axis_asc: np.ndarray) -> Tuple[float, float]:
    """轴极值外推到格点边缘（首/末点各 ± 半格步长），入参需单调。

    与 coverage_bbox 同一套「格点边缘」口径（那里用全轴步长的中位数）：热图着色区与
    数据覆盖框都停在最外圈格点的边缘。注意用中位数而非首/末局部间隔——真实数据轴
    轻微非均匀（纬度间隔 0.0435~0.0513°），用边界间隔会和覆盖框错开小半格。
    """
    a = np.asarray(axis_asc, dtype='float64')
    d = np.abs(np.diff(a))
    d = d[d > 0]
    half = (float(np.median(d)) if d.size else 0.0) / 2.0
    return float(a[0]) - half, float(a[-1]) + half


def _resample_overlay_grid(values: np.ndarray, lat_asc: np.ndarray,
                           lon_asc: np.ndarray, phi_rows: np.ndarray,
                           lam_cols: np.ndarray) -> np.ndarray:
    """把 (lat, lon) 场双线性重采样到任意目标格点（行可在纬度上不等距）。

    lat_asc/lon_asc 必须严格递增；phi_rows 降序（第 0 行 = 北端）、lam_cols 升序。
    超出源范围的值按边缘常量延拓（np.interp 默认），只用来铺满最外圈格点半步长的
    边缘带；再往外由调用方按覆盖边缘裁剪，不会多刷假色。
    """
    inter = np.empty((phi_rows.size, values.shape[1]), dtype='float64')
    for c in range(values.shape[1]):
        inter[:, c] = np.interp(phi_rows, lat_asc, values[:, c])
    out = np.empty((phi_rows.size, lam_cols.size), dtype='float64')
    for r in range(phi_rows.size):
        out[r] = np.interp(lam_cols, lon_asc, inter[r])
    return out


class DataLoader:
    def __init__(self):
        self.ds_u = None
        self.ds_v = None
        self.ds_w = None
        self.ds_tc = None
        self.ds_rh = None
        self.ds_q = None
        self._loaded = False
        # 功能二：按天 slab 的 LRU 缓存（键 = 变量+起始时刻+小时数+量化 bbox）
        self._slab_cache = OrderedDict()
        self._slab_bytes = 0
        # A* 用的「整块湍流场」缓存（键 = 时间+bbox）。三档规划是同一个键，
        # 没有这层就要把同一块数据从 USB 盘上捞三遍。
        self._grid_cache = OrderedDict()
        self._axis_bounds_cache = None
        # 冷启动互斥。6 个 NetCDF 各约 102 GB，并发 open 是纯粹的 IO 放大：
        # 实测 4 线程冷启动把 open_dataset 调了 48 次（应为 6）。
        # 更糟的是重入本身有破坏性 —— 一个线程重开会先把**别的线程正在用的**
        # dataset 置 None，那边读到的就是占位常量（tke=0）且不抛异常。
        #
        # 必须是 **RLock**：_ensure_loaded() 持锁调用 load_data()，而 load_data()
        # 自己还要再拿一次锁。普通 Lock 不可重入，同线程二次获取直接自死锁
        # ——而现有用例大多预置 _loaded=True 走早退分支，恰好绕过了这条路径。
        self._load_lock = threading.RLock()
        # 失败退避：加载失败后不要每个请求都重试一遍 6 个 102 GB 的 open。
        self._load_failed_at = 0.0

    _DS_ATTRS = ("ds_u", "ds_v", "ds_w", "ds_tc", "ds_rh", "ds_q")

    def _close_datasets(self) -> None:
        """显式关掉已打开的 dataset。

        xarray 的 dataset 持有底层文件句柄。靠 GC 回收既不及时也不确定 —— 尤其在
        「重开覆盖旧对象」这条路上，句柄会随着请求数累积到系统句柄上限。
        """
        for attr in self._DS_ATTRS:
            ds = getattr(self, attr, None)
            if ds is not None:
                try:
                    ds.close()
                except Exception:      # 关闭失败不该挡住后续流程
                    pass
            setattr(self, attr, None)

    # 加载失败后的重试冷却（秒）。数据缺失/损坏不会自己好起来，
    # 每个请求都重试 6 个 102 GB 的 open 是个现成的 IO 放大器。
    _LOAD_RETRY_COOLDOWN_S = 30.0

    def _ensure_loaded(self) -> bool:
        """确保数据已加载，**并发安全**。返回是否可用。

        双检 + 互斥：6 个 NetCDF 各约 102 GB，并发 open 是纯粹的 IO 放大
        （实测 4 线程冷启动 open_dataset 被调 48 次，应为 6）。
        失败后退避 ``_LOAD_RETRY_COOLDOWN_S`` 秒，避免每个请求都重试一遍。
        """
        if self._loaded:
            return True
        if (self._load_failed_at
                and time.monotonic() - self._load_failed_at < self._LOAD_RETRY_COOLDOWN_S):
            return False
        with self._load_lock:
            if self._loaded:          # 双检：等锁期间别的线程可能已经加载完
                return True
            if (self._load_failed_at
                    and time.monotonic() - self._load_failed_at < self._LOAD_RETRY_COOLDOWN_S):
                return False
            self.load_data()
        return self._loaded

    def load_data(self):
        """打开 6 个数据集。**调用方应走 :meth:`_ensure_loaded`，不要直接调这里。**

        直接调用本身是安全的（open 全程在锁内），但会绕过「已加载就返回」
        和失败退避这两道判断。
        """
        u_path = os.path.join(settings.DATA_DIR, settings.U_FILE)
        v_path = os.path.join(settings.DATA_DIR, settings.V_FILE)
        w_path = os.path.join(settings.DATA_DIR, settings.W_FILE)
        tc_path = os.path.join(settings.DATA_DIR, settings.TC_FILE)
        rh_path = os.path.join(settings.DATA_DIR, settings.RH_FILE)

        with self._load_lock:
            if self._loaded:          # 已有可用句柄就不重开
                return
            try:
                self.ds_u = xr.open_dataset(u_path, chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  U (wind) OK", flush=True)
                self.ds_v = xr.open_dataset(v_path, chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  V (wind) OK", flush=True)
                self.ds_w = xr.open_dataset(w_path, chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  W (wind) OK", flush=True)
                self.ds_tc = xr.open_dataset(tc_path, chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  TC (temp) OK", flush=True)
                self.ds_rh = xr.open_dataset(rh_path, chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  RH (humidity) OK", flush=True)
                self.ds_q = xr.open_dataset(os.path.join(settings.DATA_DIR, settings.Q_FILE), chunks={'time': 24, 'lev': 1, 'lat': 80, 'lon': 80})
                print("  Q (qvapor) OK", flush=True)
                self._loaded = True
                self._load_failed_at = 0.0
                print("All data loaded successfully.")
            except FileNotFoundError as e:
                # 缺文件时把已经开好的几个也关掉，别留半开的句柄
                self._close_datasets()
                self._load_failed_at = time.monotonic()
                print(f"Warning: Data file not found. {e}")
            except Exception as e:
                # 权限、文件损坏等不是 FileNotFoundError。原来它直接冒泡成 500，
                # 这里给出可读信息并保证句柄回收。
                self._close_datasets()
                self._load_failed_at = time.monotonic()
                print(f"Error: failed to load meteorological datasets: "
                      f"{type(e).__name__}: {e}", flush=True)
                raise

    def _gather_slab(self, var: str, la, lo, tt, t0, hours, bbox):
        """读一块 slab 并算好每个采样点最近的 (时, 纬, 经) 下标。

        返回 (values, ilat, ilon, itime)；取不到时返回 None。
        """
        vals, sl, so, ts = self.read_slab(var, t0, hours, bbox)
        if vals is None:
            return None
        ilat = np.abs(sl[:, None] - la[None, :]).argmin(axis=0)
        ilon = np.abs(so[:, None] - lo[None, :]).argmin(axis=0)
        itime = _nearest_time_indices(ts, tt)
        return vals, ilat, ilon, itime

    def get_meteorology_at_points(self, lats, lons, times):
        """沿程批量取数：**每个采样点用它自己的时刻**查（4D 口径）。

        参数
        ----
        lats, lons : 等长的采样点坐标
        times : 与 lats 等长的时刻序列（str 或 np.datetime64，数据时间 = UTC 标签）

        为什么不再是"整条线共用一个时刻"：规划时每个点是按【到达时刻】判定的，
        剖面若仍用起飞时刻那一帧，同一条线上就会出现"规划说这里有值、剖面取到缺测"
        （或反之）的错位，折线图与结论对不上。

        性能：只读 6 块 slab（u/v/w/tc/rh/qvapor 各一块，覆盖全线的时空包围盒），
        再逐点在自己的 ±TURB_WINDOW_HALF_HOURS 窗内算脉动方差，不按点次读盘。

        缺测点是 NaN（不是 0）：NaN 经 pydantic 序列化为 JSON null，前端画成断点。
        数据文件缺失时退回 ``_PLACEHOLDER``（与旧实现一致，tke/edr 为 0）——
        **这组常量是「没读到」而不是「读到了零」，任何拿它做安全判定的调用方
        都必须先用 ``is_placeholder_sample`` 挡一道**，否则一次读盘失败会被
        ``classify_point`` 判成「零湍流、低风险、可以飞」。
        """
        if not self._ensure_loaded():
            return out

        la = np.asarray(lats, dtype=float).ravel()
        lo = np.asarray(lons, dtype=float).ravel()
        n = la.size
        placeholder = _PLACEHOLDER
        out = {k: np.full(n, v, dtype=np.float64) for k, v in placeholder.items()}
        if n == 0 or n != lo.size or self.ds_u is None:
            return out

        try:
            tt = np.array([np.datetime64(x, 'ns') for x in times], dtype='datetime64[ns]')
            if tt.size != n:
                raise ValueError("times 必须与 lats 等长")

            half = int(settings.TURB_WINDOW_HALF_HOURS)
            pad = 0.1
            bbox = (float(la.min()) - pad, float(la.max()) + pad,
                    float(lo.min()) - pad, float(lo.max()) + pad)
            t0 = tt.min() - np.timedelta64(half, 'h')
            span = int((tt.max() - tt.min()) / np.timedelta64(1, 'h')) + 2 * half + 1

            # 越界的时刻必须在读之前判掉。``read_slab`` 用 searchsorted 定位起始帧
            # 再 clamp 到 [0, len-1]，于是 2022-01-01 **之前**的日期会被静默钳到
            # 首帧：返回真实数值却标着用户请求的日期，``available=True`` 且无 note。
            # 实测 2021-06-15 / 2021-01-01 / 1990-01-01 返回的 tke/tc 与
            # 2022-01-01T00:00 逐位相同。数据**之后**的方向本来就是 NaN
            # （下面 inside 掩码负责），这里把前面这个方向补齐成一致行为。
            #
            # 判据是**中心时刻**在不在数据集时间轴上，不是「±half 窗是否与数据相交」：
            # 后者会把边缘处只覆盖了几小时的点也算成有效，那正是脉动方差
            # 样本不足该给 NaN 的情形，交给下面的 pulsation_tke 判。
            # 与 get_meteorology_grid 的越界守卫同一口径。
            b = self._axis_bounds()
            in_range = (tt >= b['time'][0]) & (tt <= b['time'][1])
            if not in_range.all():
                # 全越界：直接给 NaN，不要让 searchsorted 去钳
                if not in_range.any():
                    return {k: np.full(n, np.nan) for k in placeholder}
                tt = np.where(in_range, tt, tt.max())   # 越界点不参与窗内计算

            cols = []
            itime = None
            for var in ('u', 'v', 'w'):
                got = self._gather_slab(var, la, lo, tt, t0, span, bbox)
                if got is None:
                    return out
                vals, ilat, ilon, itime = got
                cols.append(vals[:, ilat, ilon])          # (T, n)
            frames = cols[0].shape[0]

            # 逐点各自的 ±half 小时窗：(n, W) 的时间下标 -> 转置成 (W, n) 喂给 pulsation_tke。
            # 越界的帧补 NaN 而不是复制边缘帧 —— 数据真的到头时那是缺测，
            # 样本不足 TURB_MIN_WINDOW_SAMPLES 会让 pulsation_tke 给出 NaN。
            raw = itime[:, None] + np.arange(-half, half + 1)[None, :]
            inside = (raw >= 0) & (raw <= frames - 1)
            rows = np.clip(raw, 0, frames - 1)
            cols_idx = np.arange(n)[:, None]
            win = [np.where(inside.T, c[rows, cols_idx].T, np.nan) for c in cols]
            tke_all = pulsation_tke(win[0], win[1], win[2])
            out.update({
                "u": win[0][half], "v": win[1][half], "w": win[2][half],
                "tke": tke_all[half], "edr": edr_from_tke(tke_all[half]),
            })

            for var, key in (('tc', 'tc'), ('rh', 'rh'), ('qvapor', 'qvapor')):
                got = self._gather_slab(var, la, lo, tt, t0, span, bbox)
                if got is not None:
                    vals, ilat, ilon, it = got
                    out[key] = vals[it, ilat, ilon]

            # 越界的点最终一律 NaN。上面把越界时刻挪到 tt.max() 只是为了让
            # searchsorted 有个合法落点、不至于钳到首帧，值到这里必须被抹掉。
            if not in_range.all():
                for key, arr in out.items():
                    out[key] = np.where(in_range, arr, np.nan)
        except Exception:
            # 这里已经出错了，我们对这条航线**什么都不知道**。返回占位常量等于
            # 报「零湍流、零风、280K」——那是一份凭空的安全证明，比报错更糟。
            # 所以整批给 NaN：前端画断点，上层按 isfinite 判成「未算出」。
            return {k: np.full(n, np.nan) for k in placeholder}

        return out

    def _axis_bounds(self):
        """数据集 lat / lon / time 三轴的取值范围与格点步长（进程内只算一次）。"""
        if self._axis_bounds_cache is None:
            la = np.asarray(self.ds_u.lat.values, dtype=float)
            lo = np.asarray(self.ds_u.lon.values, dtype=float)
            tv = np.asarray(self.ds_u.time.values, dtype='datetime64[ns]')

            def _step(a):
                d = np.abs(np.diff(a))
                d = d[d > 0]
                return float(np.median(d)) if d.size else 0.0

            self._axis_bounds_cache = {
                "lat": (float(la.min()), float(la.max())),
                "lon": (float(lo.min()), float(lo.max())),
                "lat_step": _step(la),
                "lon_step": _step(lo),
                "time": (tv[0], tv[-1]),
            }
        return self._axis_bounds_cache

    @staticmethod
    def _empty_grid():
        """覆盖不足时的返回值，必须是空数组而不是 None。

        planner 靠 ``times.size == 0 / lats.size == 0`` 区分「日期或区域在数据之外
        （no_data）」与「被湍流封死（blocked）」，返回 None 会把前者伪装成后者。
        """
        return (np.empty((0, 0, 0)), np.empty((0, 0, 0)),
                np.empty((0,)), np.empty((0,)),
                np.empty((0,), dtype='datetime64[ns]'))

    def get_meteorology_grid(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float, date: str = '2022-07-01'):
        """A* 用的整块 TKE / EDR 场，时间轴 [date, date+30h] 逐小时。

        帧数 = 24 + TURB_WINDOW_HALF_HOURS + 1：旧实现用 label 化
        ``.sel(time=slice(t-6h, t+24h+6h))``（两端含），裁掉前 pad 后尾巴自然
        多出 6 帧。这里刻意保持同一条时间轴，不给下游对帧数的隐含假设留破口；
        A* 实际查到的时刻不会超过 date + 飞行时长。

        取数走 read_slab 的 isel 路径（不再经 dask 的 .sel），并对同键结果做场级
        缓存：三档规划的 bbox 只由起终点决定、date 也相同，是逐比特一样的三次读；
        带途经点的航线原本每段各读一次，现在也收敛成一次。
        """
        if not self._ensure_loaded():
            return self._empty_grid()

        if self.ds_u is None:
            return None, None, None, None, None

        target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
        target_time = np.datetime64(target_time, 'ns')
        frames = 24 + int(settings.TURB_WINDOW_HALF_HOURS) + 1
        bbox = (float(min_lat), float(max_lat), float(min_lon), float(max_lon))

        key = (str(target_time), round(bbox[0], 4), round(bbox[1], 4),
               round(bbox[2], 4), round(bbox[3], 4))
        cached = self._grid_cache.get(key)
        if cached is not None:
            self._grid_cache.move_to_end(key)
            return cached

        # 越界要显式判：read_slab 的 searchsorted 会把越界时刻静默钳到数据集
        # 最后一帧，拿边界值冒充真实数值（同一个坑见 time_window.py:239 的注释）。
        b = self._axis_bounds()
        end_time = target_time + np.timedelta64(24, 'h')
        if (bbox[0] > b['lat'][1] or bbox[1] < b['lat'][0]
                or bbox[2] > b['lon'][1] or bbox[3] < b['lon'][0]
                or target_time > b['time'][1] or end_time < b['time'][0]):
            return self._empty_grid()

        tke, edr, lats, lons, times = self.read_turbulence_field(target_time, frames, bbox)
        if tke is None:
            return self._empty_grid()

        result = (tke, edr, lats, lons, times)
        self._grid_cache[key] = result
        while len(self._grid_cache) > int(settings.TW_GRID_CACHE_SIZE):
            self._grid_cache.popitem(last=False)
        return result

    # ==================== 功能二：时间窗口规划的批量读取 ====================

    _VAR_FILES = {
        "u": "U_FILE", "v": "V_FILE", "w": "W_FILE",
        "tc": "TC_FILE", "rh": "RH_FILE", "qvapor": "Q_FILE",
    }

    def _var_path(self, var: str) -> str:
        """返回变量对应的 NetCDF 文件绝对路径。"""
        attr = self._VAR_FILES.get(var)
        if attr is None:
            raise ValueError(f"unknown variable: {var}")
        return os.path.join(settings.DATA_DIR, getattr(settings, attr))

    def read_slab(self, var: str, start_time, hours: int, bbox):
        """读取某个变量在 500hPa 上的 (hours, lat, lon) 数据块。

        参数
        ----
        var : 'u' / 'v' / 'w' / 'tc' / 'rh' / 'qvapor'
        start_time : 数据时间轴上的起始时刻（np.datetime64 或字符串）
        hours : 读取的小时数
        bbox : (min_lat, max_lat, min_lon, max_lon)

        返回
        ----
        (values, lats, lons, times) —— 失败时 values 为 None。

        实现要点（依据实测性能结论）：
          - 用 isel + 预计算整数索引定位，避免 label 化 .sel() 的开销；
          - 只打开该变量自己的文件，绝不顺带读取其它变量；
          - 结果进 LRU 缓存，供扫描 / 曲线 / 预警复用。
        """
        try:
            min_lat, max_lat, min_lon, max_lon = (float(v) for v in bbox)
        except Exception:
            return None, None, None, None

        hours = int(max(1, hours))
        key = (var, str(np.datetime64(start_time)),
               hours,
               round(min_lat, 3), round(max_lat, 3),
               round(min_lon, 3), round(max_lon, 3))

        cached = self._slab_cache.get(key)
        if cached is not None:
            self._slab_cache.move_to_end(key)
            return cached

        try:
            path = self._var_path(var)
            ds = xr.open_dataset(path, chunks=None)
            try:
                tv = ds.time.values
                i0 = int(np.searchsorted(tv, np.datetime64(start_time)))
                i0 = max(0, min(i0, len(tv) - 1))
                i1 = min(len(tv), i0 + hours)
                if i1 <= i0:
                    return None, None, None, None

                la = ds.lat.values
                lo = ds.lon.values
                li = np.where((la >= min_lat) & (la <= max_lat))[0]
                loi = np.where((lo >= min_lon) & (lo <= max_lon))[0]
                if len(li) == 0 or len(loi) == 0:
                    return None, None, None, None

                levs = ds.lev.values
                lv_idx = int(np.where(levs == 500.0)[0][0]) if (levs == 500.0).any() else int(
                    np.abs(levs - 500.0).argmin())

                data = ds[var].isel(
                    time=slice(i0, i1), lev=lv_idx,
                    lat=slice(int(li[0]), int(li[-1]) + 1),
                    lon=slice(int(loi[0]), int(loi[-1]) + 1),
                ).values
                lats = la[li[0]:li[-1] + 1]
                lons = lo[loi[0]:loi[-1] + 1]
                times = tv[i0:i1]
            finally:
                ds.close()
        except Exception as e:
            print(f"read_slab({var}) failed: {e}")
            return None, None, None, None

        result = (np.asarray(data), np.asarray(lats), np.asarray(lons), np.asarray(times))

        self._cache_slab(key, result)
        return result

    def _cache_slab(self, key, result) -> None:
        """把一块 slab 放进 LRU，按「条数 + 字节」双上限淘汰。

        只按条数不够：功能二一次 8 天扫描的 u/v/w 每块约 20 MB，
        而沿程取数（±6h 窗）的小块只有约 1 MB，同一个数字上限对两者差一个量级；
        反过来只按字节会让一堆小块把键空间撑爆。两条都要。
        """
        self._slab_cache[key] = result
        self._slab_bytes += int(result[0].nbytes)
        max_items = int(settings.TW_SLAB_CACHE_SIZE)
        max_bytes = int(settings.TW_SLAB_CACHE_MAX_BYTES)
        while self._slab_cache and (len(self._slab_cache) > max_items
                                    or self._slab_bytes > max_bytes):
            _, dropped = self._slab_cache.popitem(last=False)
            self._slab_bytes -= int(dropped[0].nbytes)

    def read_turbulence_field(self, start_time, hours: int, bbox):
        """读取 [start_time, +hours) 的 TKE / EDR 场。

        返回 (tke, edr, lats, lons, times)，失败时 tke 为 None。
        内部多读前后各 TURB_WINDOW_HALF_HOURS 小时用于扣脉动窗口均值，
        返回前裁回请求区间，时间轴语义与 read_slab 一致。
        """
        half = int(settings.TURB_WINDOW_HALF_HOURS)
        hours = int(max(1, hours))
        t0 = np.datetime64(start_time, 'ns') - np.timedelta64(half, 'h')
        arrs, times, lats, lons = [], None, None, None
        for var in ('u', 'v', 'w'):
            vals, la, lo, ts = self.read_slab(var, t0, hours + 2 * half, bbox)
            if vals is None:
                return None, None, None, None, None
            arrs.append(np.asarray(vals))
            times, lats, lons = ts, la, lo
        tke, edr = turbulence_from_wind(arrs[0], arrs[1], arrs[2])
        idx = np.flatnonzero(np.asarray(times, dtype='datetime64[ns]')
                             >= np.datetime64(start_time, 'ns'))[:hours]
        return tke[idx], edr[idx], lats, lons, times[idx]

    def check_coverage(self, lat: float, lon: float, tol: float = 0.25) -> bool:
        """判断坐标是否落在气象数据覆盖范围内（带容差）。全仓唯一实现。

        数据未加载时返回 True —— 这里只回答「坐标是否在数据域内」，不回答
        「数据是否可用」。放行之后真正的取数失败会由上游按「未算出」处理
        （compute_path_turbulence 返回 NaN、端点判为不安全），不会退化成
        「查不到就算安全」。

        真正读轴失败（如 dataset 已损坏）不能与「确实在范围内」混为一谈，
        所以记一条告警而不是静默吞掉。注意本方法**仍然返回 True**：
        它只回答「坐标是否在数据域内」，不回答「数据是否可用」——
        轴读不出来与「坐标在域内」是两件事，把后者说成前者会让一次
        数据集损坏冒充成一次正常的覆盖检查。取数失败由上游按「未算出」处理。
        """
        try:
            ds = self.ds_u
            if ds is None:
                return True
            lats = ds.lat.values
            lons = ds.lon.values
            lat_lo, lat_hi = float(np.min(lats)), float(np.max(lats))
            lon_lo, lon_hi = float(np.min(lons)), float(np.max(lons))
            return bool((lat_lo - tol <= lat <= lat_hi + tol)
                        and (lon_lo - tol <= lon <= lon_hi + tol))
        except Exception as e:
            print(f"Warning: coverage check failed for ({lat}, {lon}): "
                  f"{type(e).__name__}: {e}", flush=True)
            return True

    def coverage_bbox(self) -> Optional[Dict[str, float]]:
        """气象数据在经纬度上的覆盖框（按格点边缘，不是格点中心），读不到时返回 None。

        给地图用：没有这条边界，「框外=没数据」和「框内=安全」在屏幕上长得一样，
        用户会把缺测区读成可飞区。

        取格点边缘（轴极值 ± 半步长）而不是格点中心：热图的着色区本来就铺到最外圈
        格点的边缘，用中心值画框会让框比热图窄半格，看上去"框和数据对不齐"。
        """
        try:
            if self.ds_u is None:
                return None
            b = self._axis_bounds()
            lat_lo, lat_hi = b["lat"]
            lon_lo, lon_hi = b["lon"]
            dlat = b.get("lat_step", 0.0) / 2.0
            dlon = b.get("lon_step", 0.0) / 2.0
            return {
                "lat_min": lat_lo - dlat, "lat_max": lat_hi + dlat,
                "lon_min": lon_lo - dlon, "lon_max": lon_hi + dlon,
            }
        except Exception:
            return None

    def _read_overlay_slice(self, file_path, var_name, target_time, lat_slice, lon_slice):
        """Read a 2D slice (time+lev resolved) without dask for fast overlay."""
        ds = xr.open_dataset(file_path, chunks=None)
        try:
            data = ds[var_name].sel(time=target_time, method='nearest').sel(lev=500)
            data = data.sel(lat=lat_slice, lon=lon_slice)
            return data.values.squeeze(), data.lat.values, data.lon.values
        finally:
            ds.close()

    def _read_overlay_window(self, file_path, var_name, target_time, lat_slice, lon_slice):
        """读取 target_time 前后各 TURB_WINDOW_HALF_HOURS 小时的 (time, lat, lon) 块。

        脉动方差需要窗口内的多帧数据，单小时切片不够；调用方算完后按时间下标取中心帧。
        """
        half = np.timedelta64(int(settings.TURB_WINDOW_HALF_HOURS), 'h')
        t = np.datetime64(target_time, 'ns')
        ds = xr.open_dataset(file_path, chunks=None)
        try:
            data = ds[var_name].sel(time=slice(t - half, t + half), lev=500,
                                    lat=lat_slice, lon=lon_slice)
            return data.values, data.lat.values, data.lon.values, data.time.values
        finally:
            ds.close()

    @staticmethod
    def _overlay_canvas(min_lat: float, max_lat: float, min_lon: float, max_lon: float,
                        dpi: int, max_px: int):
        """按 bbox 在 Web Mercator 下的实际宽高比给出画布英寸数。

        Leaflet 是把整张图线性贴到 bounds 上的，此前固定正方形画布对扁平视野框
        有近六成像素落在拉伸出来的空白上；宽高比对齐后同样的像素预算才真用在数据上。
        """
        def merc_y(lat):
            return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))

        try:
            dy = merc_y(max_lat) - merc_y(min_lat)
            aspect = math.radians(max_lon - min_lon) / dy if dy > 0 else 1.0
        except (ValueError, OverflowError):
            aspect = 1.0
        if not math.isfinite(aspect) or aspect <= 0:
            aspect = 1.0
        aspect = min(max(aspect, 0.2), 6.0)

        if aspect >= 1.0:
            w_px, h_px = int(max_px), int(round(max_px / aspect))
        else:
            w_px, h_px = int(round(max_px * aspect)), int(max_px)
        return (max(w_px, 1) / dpi, max(h_px, 1) / dpi), (w_px, h_px)

    @staticmethod
    def _encode_overlay_png(fig, colors: int) -> bytes:
        """把画布导出成 PNG；colors>=2 时转成 8-bit 调色板。

        直接取 Agg 的像素缓冲，绕开「先编 PNG 再解码回来临时量化」这一趟往返
        （实测省约 0.36s/张）。contourf 本就是离散色阶，量化后可见色差约 2/255，
        alpha 走 tRNS 保留；Pillow 异常时退回 matplotlib 自己编码，宁可大图也别开不出图。
        """
        buf = io.BytesIO()
        try:
            from PIL import Image
            fig.canvas.draw()
            w, h = fig.canvas.get_width_height()
            img = Image.frombytes('RGBA', (w, h), bytes(fig.canvas.buffer_rgba()))
            if colors >= 2:
                img = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                                   dither=Image.Dither.NONE)
            img.save(buf, format='PNG', optimize=True)
            return buf.getvalue()
        except Exception as e:
            print(f"Overlay png export via Pillow failed ({e}), falling back to savefig")
            buf.close()
            buf = io.BytesIO()
            fig.savefig(buf, format='png', transparent=True)
            return buf.getvalue()

    def get_overlay_image(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer_type: str, date: str = '2022-05-30', time_slot: str = 'morning') -> bytes:
        """
        Generate a transparent PNG heatmap for the given bounding box and layer type.
        """
        dpi = int(settings.OVERLAY_DPI)
        figsize, (px_w, px_h) = self._overlay_canvas(min_lat, max_lat, min_lon, max_lon,
                                                     dpi, int(settings.OVERLAY_MAX_PX))
        fig = plt.figure(figsize=figsize, dpi=dpi)
        # 等效于 savefig(transparent=True)：底板不透明度清零，贴图区域才不会被填成白底
        fig.patch.set_alpha(0.0)
        ax = plt.Axes(fig, [0., 0., 1., 1.])
        ax.set_axis_off()
        fig.add_axes(ax)
        # 画布尺寸按 bbox 在 Mercator 下的真实宽高比分配；绘制坐标系稍后设为索引坐标
        # （0..cols-1, 0..rows-1），不再用经纬度当轴——PNG 像素是按投影平面线性铺的，
        # 只有行在索引上等距、且每行纬度按 Mercator 反插值，贴图才不漂。

        u_path = os.path.join(settings.DATA_DIR, settings.U_FILE)
        v_path = os.path.join(settings.DATA_DIR, settings.V_FILE)
        w_path = os.path.join(settings.DATA_DIR, settings.W_FILE)
        tc_path = os.path.join(settings.DATA_DIR, settings.TC_FILE)
        rh_path = os.path.join(settings.DATA_DIR, settings.RH_FILE)
        q_path = os.path.join(settings.DATA_DIR, settings.Q_FILE)

        try:
            target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
            pad = 0.5

            # Need ds_u for direction check only — use the chunked instance
            if self.ds_u.lat[0] > self.ds_u.lat[-1]:
                lat_slice_ol = slice(max_lat + pad, min_lat - pad)
            else:
                lat_slice_ol = slice(min_lat - pad, max_lat + pad)
            if self.ds_u.lon[0] > self.ds_u.lon[-1]:
                lon_slice_ol = slice(max_lon + pad, min_lon - pad)
            else:
                lon_slice_ol = slice(min_lon - pad, max_lon + pad)

            # Read directly without dask (fast I/O, only the needed sub-region)
            turb_layer = layer_type in ('tke', 'edr')
            if turb_layer:
                # 湍流是脉动方差，单小时切片算不出来：整段多读 ±half 小时
                u_win, u_lats, u_lons, u_times = self._read_overlay_window(
                    u_path, 'u', target_time, lat_slice_ol, lon_slice_ol)
                v_win = self._read_overlay_window(
                    v_path, 'v', target_time, lat_slice_ol, lon_slice_ol)[0]
                w_win = self._read_overlay_window(
                    w_path, 'w', target_time, lat_slice_ol, lon_slice_ol)[0]
            else:
                u_vals, u_lats, u_lons = self._read_overlay_slice(u_path, 'u', target_time, lat_slice_ol, lon_slice_ol)
                v_vals, _, _ = self._read_overlay_slice(v_path, 'v', target_time, lat_slice_ol, lon_slice_ol)

            lat_len = len(u_lats)
            lon_len = len(u_lons)
            if lat_len < 2 or lon_len < 2:
                # 包围框整体落在数据覆盖范围外时 sel() 给的是空数组而不是 None，
                # 放任它走到 contourf 会抛在 try 外面变成 500，这里先判成无数据。
                raise ValueError(f'overlay grid degenerate: {lat_len} x {lon_len}')
            grid_points = int(settings.OVERLAY_GRID_POINTS)
            lat_step = max(1, lat_len // grid_points)
            lon_step = max(1, lon_len // grid_points)
            # 抽稀显式保留两端点：源网格必定铺满整个数据窗口（旧版靠
            # _pad_edge_coords 事后补边，这里改为重采样+裁剪，见下）。
            ilat = np.unique(np.concatenate([np.arange(0, lat_len, lat_step), [lat_len - 1]]))
            ilon = np.unique(np.concatenate([np.arange(0, lon_len, lon_step), [lon_len - 1]]))

            # 轴可能是降序的（数据轴向不定）；重采样前统一成「升序轴 + 升序下标」
            src_lats = np.asarray(u_lats, dtype='float64')
            src_lons = np.asarray(u_lons, dtype='float64')
            lat_rev = bool(src_lats[0] > src_lats[-1])
            lon_rev = bool(src_lons[0] > src_lons[-1])
            if lat_rev:
                src_lats = src_lats[::-1]
                ilat = (lat_len - 1) - ilat[::-1]
            if lon_rev:
                src_lons = src_lons[::-1]
                ilon = (lon_len - 1) - ilon[::-1]

            def _asc_take(a, axis, idx, rev):
                """把源数组翻成升序、再按抽稀下标取——轴和数据必须同步过。"""
                if rev:
                    a = np.flip(a, axis=axis)
                return np.take(a, idx, axis=axis)

            if turb_layer:
                # 方差逐格点独立，先抽稀再算，结果与全分辨率算完再抽稀一致
                u_thin = _asc_take(_asc_take(u_win, 1, ilat, lat_rev), 2, ilon, lon_rev)
                v_thin = _asc_take(_asc_take(v_win, 1, ilat, lat_rev), 2, ilon, lon_rev)
                w_thin = _asc_take(_asc_take(w_win, 1, ilat, lat_rev), 2, ilon, lon_rev)
                tke_all, edr_all = turbulence_from_wind(u_thin, v_thin, w_thin)
                ci = nearest_time_index(u_times, target_time)
                data_src = (tke_all if layer_type == 'tke' else edr_all)[ci]
            elif layer_type == 'temp':
                tc_vals, _, _ = self._read_overlay_slice(tc_path, 'tc', target_time, lat_slice_ol, lon_slice_ol)
                data_src = _asc_take(_asc_take(tc_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)
            elif layer_type == 'humidity':
                rh_vals, _, _ = self._read_overlay_slice(rh_path, 'rh', target_time, lat_slice_ol, lon_slice_ol)
                data_src = _asc_take(_asc_take(rh_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)
            elif layer_type == 'qvapor':
                q_vals, _, _ = self._read_overlay_slice(q_path, 'qvapor', target_time, lat_slice_ol, lon_slice_ol)
                data_src = _asc_take(_asc_take(q_vals, 0, ilat, lat_rev), 1, ilon, lon_rev) * 1000.0
            elif layer_type == 'flow':
                w_vals, _, _ = self._read_overlay_slice(w_path, 'w', target_time, lat_slice_ol, lon_slice_ol)
                # 三个分量先抽到同一套格点再开模（旧版只抽 w，步长>1 时形状对不上会静默崩图）
                u_src = _asc_take(_asc_take(u_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)
                v_src = _asc_take(_asc_take(v_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)
                w_src = _asc_take(_asc_take(w_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)
                data_src = np.sqrt(u_src**2 + v_src**2 + w_src**2)
            else:
                data_src = _asc_take(_asc_take(u_vals, 0, ilat, lat_rev), 1, ilon, lon_rev)

            data_src = np.asarray(data_src, dtype='float64')
            if data_src.shape != (ilat.size, ilon.size):
                raise ValueError(f'overlay grid degenerate: {data_src.shape} != '
                                 f'({ilat.size}, {ilon.size})')

            # ---- 目标格点：PNG 行在 Mercator y 上等距（Leaflet 就是这么贴图的）----
            y_top, y_bottom = _merc_y(max_lat), _merc_y(min_lat)
            span = y_top - y_bottom
            if not (span > 0.0) or max_lon <= min_lon:
                raise ValueError(f'overlay box degenerate: {min_lat}..{max_lat} / '
                                 f'{min_lon}..{max_lon}')
            # 视野框大于数据覆盖时按覆盖占比加密行列，数据分辨率不随框变大被稀释；
            # 上限：像素数（比像素还细没意义）与 4x 源格点（纯插值不出新信息）
            cov_lo = min(max(_merc_y(float(src_lats[0])), y_bottom), y_top)
            cov_hi = min(max(_merc_y(float(src_lats[-1])), y_bottom), y_top)
            f_lat = max((cov_hi - cov_lo) / span, 1e-6)
            f_lon = max(min(max_lon, float(src_lons[-1])) - max(min_lon, float(src_lons[0])),
                        1e-9) / (max_lon - min_lon)
            rows = int(max(2, min(round((ilat.size - 1) / f_lat) + 1, px_h, 4 * ilat.size)))
            cols = int(max(2, min(round((ilon.size - 1) / f_lon) + 1, px_w, 4 * ilon.size)))
            phi_rows = _mercator_row_latitudes(min_lat, max_lat, rows)  # 降序，第 0 行 = max_lat
            lam_cols = np.linspace(min_lon, max_lon, cols)              # 升序，第 0 列 = min_lon
            # 重采样基准轴 = 抽稀后的轴（与 data_src 同形）；抽稀保两端，覆盖窗口不变
            data_plot = _resample_overlay_grid(data_src, src_lats[ilat], src_lons[ilon],
                                               phi_rows, lam_cols)

            # ---- 索引坐标下绘制：行 j（0=北端）放到 y=rows-1-j，PNG 顶行才是 max_lat ----
            Xg, Yg = np.meshgrid(np.arange(cols, dtype='float64'),
                                 (rows - 1.0) - np.arange(rows, dtype='float64'))
            ax.set_xlim(0.0, cols - 1.0)
            ax.set_ylim(0.0, rows - 1.0)

            # 覆盖边缘（格点中心 ± 半步长）换算到索引坐标：着色到此为止，再往外透明。
            # 边缘落在请求框之外时矩形自然超出坐标轴，等同不裁剪。
            edge_lat_lo, edge_lat_hi = _cell_edge_extents(src_lats)
            edge_lon_lo, edge_lon_hi = _cell_edge_extents(src_lons)
            fx = (cols - 1) / (max_lon - min_lon)
            fy = (rows - 1) / span
            y_e_lo = _merc_y(max(edge_lat_lo, -89.0))
            y_e_hi = _merc_y(min(edge_lat_hi, 89.0))
            clip_rect = Rectangle(((edge_lon_lo - min_lon) * fx, (y_e_lo - y_bottom) * fy),
                                  (edge_lon_hi - edge_lon_lo) * fx, (y_e_hi - y_e_lo) * fy,
                                  transform=ax.transData)
        except Exception as e:
            print(f"Error subsetting real data: {e}")
            import traceback
            traceback.print_exc()
            # 取不到数据（多数是包围框整体越界）就出全透明图：
            # 拿 0 当实测值去 contourf 会在地图上糊一层假色。
            png = self._encode_overlay_png(fig, int(settings.OVERLAY_COLORS))
            plt.close(fig)
            return png

        cmap_name = 'jet'
        vmin, vmax = 0.0, 30.0
        if layer_type == 'tke': 
            cmap_name = 'YlOrRd'
            # 上沿取硬阈值 26 之上一点：满红即「完全没法飞」，不再按平均风上色
            vmin, vmax = 0.0, 30.0
        elif layer_type == 'edr': 
            cmap_name = 'magma'
            vmin, vmax = 0.0, 0.40
        elif layer_type == 'temp': 
            cmap_name = 'coolwarm'
            vmin, vmax = -15.0, 10.0
        elif layer_type == 'humidity':
            cmap_name = 'GnBu'
            vmin, vmax = 0.0, 100.0
        elif layer_type == 'qvapor':
            cmap_name = 'PuBu'
            vmin, vmax = 0.0, 10.0
        
        # 填充/等值线画在索引坐标格点上，再按覆盖边缘裁剪：着色区与覆盖框同口径（格点边缘），
        # 行距几何由 _mercator_row_latitudes 保证（旧 _pad_edge_coords 补边法在 Mercator 行距下已废弃）
        levels = np.linspace(vmin, vmax, int(settings.OVERLAY_CONTOUR_LEVELS))
        cf = ax.contourf(Xg, Yg, data_plot, levels=levels, cmap=cmap_name,
                         alpha=0.55, extend='both')
        cf.set_clip_path(clip_rect)
        line_levels = int(settings.OVERLAY_LINE_LEVELS)
        if line_levels > 0:
            cl = ax.contour(Xg, Yg, data_plot, levels=line_levels, colors='black',
                            linewidths=0.3, alpha=0.8)
            cl.set_clip_path(clip_rect)

        png = self._encode_overlay_png(fig, int(settings.OVERLAY_COLORS))
        plt.close(fig)
        return png

# Singleton instance
data_loader = DataLoader()