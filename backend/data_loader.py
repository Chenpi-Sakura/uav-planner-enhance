import os
import math
import xarray as xr
import io
from collections import OrderedDict
from typing import Optional, Sequence, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import scipy.ndimage as ndimage

from config import settings
from meteorology import pulsation_tke, edr_from_tke


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

    def load_data(self):
        u_path = os.path.join(settings.DATA_DIR, settings.U_FILE)
        v_path = os.path.join(settings.DATA_DIR, settings.V_FILE)
        w_path = os.path.join(settings.DATA_DIR, settings.W_FILE)
        tc_path = os.path.join(settings.DATA_DIR, settings.TC_FILE)
        rh_path = os.path.join(settings.DATA_DIR, settings.RH_FILE)

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
            print("All data loaded successfully.")
        except FileNotFoundError as e:
            print(f"Warning: Data file not found. {e}")

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
        数据文件缺失时退回占位常量（与旧实现一致，tke/edr 为 0）。
        """
        if not self._loaded:
            self.load_data()

        la = np.asarray(lats, dtype=float).ravel()
        lo = np.asarray(lons, dtype=float).ravel()
        n = la.size
        placeholder = {"u": 5.0, "v": 5.0, "w": 0.0, "tc": 280.0,
                       "rh": 50.0, "qvapor": 0.005, "tke": 0.0, "edr": 0.0}
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
        except Exception:
            pass

        return out

    def get_meteorology_grid(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float, date: str = '2022-07-01'):
        """
        Pre-fetch a 2D grid of meteorology data for fast A* lookup.
        """
        if not self._loaded:
            self.load_data()
            
        if self.ds_u is None:
            return None, None, None, None, None
            
        target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
        end_time = target_time + np.timedelta64(24, 'h')
        # 脉动量要扣窗口均值，因此前后各多读 half 小时；多读的部分只用于算窗口，
        # 返回前按时间裁掉，保持与旧实现完全相同的帧数与时间轴。
        half = np.timedelta64(int(settings.TURB_WINDOW_HALF_HOURS), 'h')
        time_slice = slice(target_time - half, end_time + half)

        if self.ds_u.lat[0] > self.ds_u.lat[-1]:
            lat_slice = slice(max_lat, min_lat)
        else:
            lat_slice = slice(min_lat, max_lat)
        if self.ds_u.lon[0] > self.ds_u.lon[-1]:
            lon_slice = slice(max_lon, min_lon)
        else:
            lon_slice = slice(min_lon, max_lon)

        try:
            u_grid = self.ds_u.u.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            v_grid = self.ds_v.v.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            w_grid = self.ds_w.w.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            
            lats = self.ds_u.lat.sel(lat=lat_slice).values
            lons = self.ds_u.lon.sel(lon=lon_slice).values
            times = self.ds_u.time.sel(time=time_slice).values
            
            tke_grid, edr_grid = turbulence_from_wind(u_grid, v_grid, w_grid)
            keep = np.asarray(times, dtype='datetime64[ns]') >= np.datetime64(target_time, 'ns')
            tke_grid, edr_grid, times = tke_grid[keep], edr_grid[keep], times[keep]
            
            return tke_grid, edr_grid, lats, lons, times
        except Exception as e:
            print(f"Error fetching meteorology grid: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None, None, None

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

        self._slab_cache[key] = result
        while len(self._slab_cache) > int(settings.TW_SLAB_CACHE_SIZE):
            self._slab_cache.popitem(last=False)
        return result

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
        """判断坐标是否落在气象数据覆盖范围内（带容差）。

        数据未加载时返回 True（不阻塞评估流程，与功能一口径一致）。
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
        except Exception:
            return True

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
        figsize, _ = self._overlay_canvas(min_lat, max_lat, min_lon, max_lon,
                                          dpi, int(settings.OVERLAY_MAX_PX))
        fig = plt.figure(figsize=figsize, dpi=dpi)
        # 等效于 savefig(transparent=True)：底板不透明度清零，贴图区域才不会被填成白底
        fig.patch.set_alpha(0.0)
        ax = plt.Axes(fig, [0., 0., 1., 1.])
        ax.set_axis_off()
        fig.add_axes(ax)
        
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)

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
            grid_points = int(settings.OVERLAY_GRID_POINTS)
            lat_step = max(1, lat_len // grid_points)
            lon_step = max(1, lon_len // grid_points)

            lats_sub = u_lats[::lat_step]
            lons_sub = u_lons[::lon_step]
            Lons, Lats = np.meshgrid(lons_sub, lats_sub)

            if turb_layer:
                # 方差逐格点独立，先抽稀再算，结果与全分辨率算完再抽稀一致
                thin = (slice(None), slice(None, None, lat_step), slice(None, None, lon_step))
                tke_all, edr_all = turbulence_from_wind(u_win[thin], v_win[thin], w_win[thin])
                ci = nearest_time_index(u_times, target_time)
                data = (tke_all if layer_type == 'tke' else edr_all)[ci]
            elif layer_type == 'temp':
                tc_vals, _, _ = self._read_overlay_slice(tc_path, 'tc', target_time, lat_slice_ol, lon_slice_ol)
                data = tc_vals[::lat_step, ::lon_step]
            elif layer_type == 'humidity':
                rh_vals, _, _ = self._read_overlay_slice(rh_path, 'rh', target_time, lat_slice_ol, lon_slice_ol)
                data = rh_vals[::lat_step, ::lon_step]
            elif layer_type == 'qvapor':
                q_vals, _, _ = self._read_overlay_slice(q_path, 'qvapor', target_time, lat_slice_ol, lon_slice_ol)
                data = q_vals[::lat_step, ::lon_step] * 1000.0
            elif layer_type == 'flow':
                w_vals, _, _ = self._read_overlay_slice(w_path, 'w', target_time, lat_slice_ol, lon_slice_ol)
                w_vals = w_vals[::lat_step, ::lon_step]
                data = np.sqrt(u_vals**2 + v_vals**2 + w_vals**2)
            else:
                data = u_vals[::lat_step, ::lon_step]

            if Lons.size < 4 or data.size < 4 or Lons.shape != data.shape:
                # 包围框整体落在数据覆盖范围外时 sel() 给的是空数组而不是 None，
                # 放任它走到 contourf 会抛在 try 外面变成 500，这里先判成无数据。
                raise ValueError(f'overlay grid degenerate: {Lons.shape} / {data.shape}')

            data = ndimage.gaussian_filter(data, sigma=0.0)
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
        
        levels = np.linspace(vmin, vmax, int(settings.OVERLAY_CONTOUR_LEVELS))
        cf = ax.contourf(Lons, Lats, data, levels=levels, cmap=cmap_name, alpha=0.55, extend='both')
        line_levels = int(settings.OVERLAY_LINE_LEVELS)
        if line_levels > 0:
            ax.contour(Lons, Lats, data, levels=line_levels, colors='black', linewidths=0.3, alpha=0.8)

        png = self._encode_overlay_png(fig, int(settings.OVERLAY_COLORS))
        plt.close(fig)
        return png

# Singleton instance
data_loader = DataLoader()