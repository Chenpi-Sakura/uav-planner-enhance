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
from meteorology import calculate_tke, calculate_edr

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

    def get_meteorology_at_point(self, lat: float, lon: float, date: str = '2022-07-01', time_slot: str = 'morning'):
        """
        Interpolate data to a specific lat/lon point.
        """
        if not self._loaded:
            self.load_data()
            
        if self.ds_u is None:
            u, v, w, tc = 5.0, 5.0, 0.0, 280.0
        else:
            try:
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                u = float(self.ds_u.sel(lat=lat, lon=lon, method='nearest').u.sel(time=target_time, method='nearest').sel(lev=500).values)
                v = float(self.ds_v.sel(lat=lat, lon=lon, method='nearest').v.sel(time=target_time, method='nearest').sel(lev=500).values)
                w = float(self.ds_w.sel(lat=lat, lon=lon, method='nearest').w.sel(time=target_time, method='nearest').sel(lev=500).values)
                tc = float(self.ds_tc.sel(lat=lat, lon=lon, method='nearest').tc.sel(time=target_time, method='nearest').sel(lev=500).values)
                rh = float(self.ds_rh.sel(lat=lat, lon=lon, method='nearest').rh.sel(time=target_time, method='nearest').sel(lev=500).values)
                qvapor = float(self.ds_q.sel(lat=lat, lon=lon, method='nearest').qvapor.sel(time=target_time, method='nearest').sel(lev=500).values)
            except Exception as e:
                u, v, w, tc, rh, qvapor = 5.0, 5.0, 0.0, 280.0, 50.0, 0.005

        tke = calculate_tke(u, v, w)
        edr = calculate_edr(tke)

        return {
            "u": u,
            "v": v,
            "w": w,
            "tc": tc,
            "rh": rh,
            "qvapor": qvapor,
            "tke": tke,
            "edr": edr
        }

    def get_meteorology_at_points(self, lats, lons, date: str = '2022-07-01'):
        """
        Vectorized batch query for multiple points. Uses direct reads (no dask).
        """
        if not self._loaded:
            self.load_data()

        n = len(lats)
        u = np.full(n, 5.0, dtype=np.float64)
        v = np.full(n, 5.0, dtype=np.float64)
        w = np.full(n, 0.0, dtype=np.float64)
        tc = np.full(n, 280.0, dtype=np.float64)
        rh = np.full(n, 50.0, dtype=np.float64)
        qvapor = np.full(n, 0.005, dtype=np.float64)

        if self.ds_u is not None:
            try:
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                da_lat = xr.DataArray(np.asarray(lats), dims='z')
                da_lon = xr.DataArray(np.asarray(lons), dims='z')

                u_path = os.path.join(settings.DATA_DIR, settings.U_FILE)
                v_path = os.path.join(settings.DATA_DIR, settings.V_FILE)
                w_path = os.path.join(settings.DATA_DIR, settings.W_FILE)
                tc_path = os.path.join(settings.DATA_DIR, settings.TC_FILE)
                rh_path = os.path.join(settings.DATA_DIR, settings.RH_FILE)
                q_path = os.path.join(settings.DATA_DIR, settings.Q_FILE)

                with xr.open_dataset(u_path, chunks=None) as ds:
                    u = ds.u.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
                with xr.open_dataset(v_path, chunks=None) as ds:
                    v = ds.v.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
                with xr.open_dataset(w_path, chunks=None) as ds:
                    w = ds.w.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
                with xr.open_dataset(tc_path, chunks=None) as ds:
                    tc = ds.tc.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
                with xr.open_dataset(rh_path, chunks=None) as ds:
                    rh = ds.rh.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
                with xr.open_dataset(q_path, chunks=None) as ds:
                    qvapor = ds.qvapor.sel(time=target_time, method='nearest').sel(lev=500).sel(lat=da_lat, lon=da_lon, method='nearest').values
            except Exception:
                pass

        wind_speed_sq = u**2 + v**2 + w**2
        tke = 0.01 * 0.5 * wind_speed_sq
        edr = 0.5 * (tke ** 1.5) / 100.0

        return {
            "u": u,
            "v": v,
            "w": w,
            "tc": tc,
            "rh": rh,
            "qvapor": qvapor,
            "tke": tke,
            "edr": edr
        }

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
        time_slice = slice(target_time, end_time)

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
            
            wind_speed_sq = u_grid**2 + v_grid**2 + w_grid**2
            tke_grid = 0.01 * 0.5 * wind_speed_sq
            edr_grid = 0.5 * (tke_grid ** 1.5) / 100.0
            
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
            u_vals, u_lats, u_lons = self._read_overlay_slice(u_path, 'u', target_time, lat_slice_ol, lon_slice_ol)
            v_vals, v_lats, v_lons = self._read_overlay_slice(v_path, 'v', target_time, lat_slice_ol, lon_slice_ol)

            lat_len = len(u_lats)
            lon_len = len(u_lons)
            grid_points = int(settings.OVERLAY_GRID_POINTS)
            lat_step = max(1, lat_len // grid_points)
            lon_step = max(1, lon_len // grid_points)

            u_vals = u_vals[::lat_step, ::lon_step]
            v_vals = v_vals[::lat_step, ::lon_step]
            lats_sub = u_lats[::lat_step]
            lons_sub = u_lons[::lon_step]
            Lons, Lats = np.meshgrid(lons_sub, lats_sub)

            if layer_type == 'tke':
                w_vals, _, _ = self._read_overlay_slice(w_path, 'w', target_time, lat_slice_ol, lon_slice_ol)
                w_vals = w_vals[::lat_step, ::lon_step]
                data = 0.01 * 0.5 * (u_vals**2 + v_vals**2 + w_vals**2)
            elif layer_type == 'edr':
                w_vals, _, _ = self._read_overlay_slice(w_path, 'w', target_time, lat_slice_ol, lon_slice_ol)
                w_vals = w_vals[::lat_step, ::lon_step]
                tke = 0.01 * 0.5 * (u_vals**2 + v_vals**2 + w_vals**2)
                data = 0.5 * (tke ** 1.5) / 100.0
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
                data = u_vals

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
            vmin, vmax = 0.0, 1.5
        elif layer_type == 'edr': 
            cmap_name = 'magma'
            vmin, vmax = 0.0, 0.015
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