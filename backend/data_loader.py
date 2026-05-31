import os
import xarray as xr
import io
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

    def _read_overlay_slice(self, file_path, var_name, target_time, lat_slice, lon_slice):
        """Read a 2D slice (time+lev resolved) without dask for fast overlay."""
        ds = xr.open_dataset(file_path, chunks=None)
        try:
            data = ds[var_name].sel(time=target_time, method='nearest').sel(lev=500)
            data = data.sel(lat=lat_slice, lon=lon_slice)
            return data.values.squeeze(), data.lat.values, data.lon.values
        finally:
            ds.close()

    def get_overlay_image(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer_type: str, date: str = '2022-05-30', time_slot: str = 'morning') -> bytes:
        """
        Generate a transparent PNG heatmap for the given bounding box and layer type.
        """
        fig = plt.figure(figsize=(8, 8), dpi=200)
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

        data = None
        Lons, Lats = None, None

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
            lat_step = max(1, lat_len // 60)
            lon_step = max(1, lon_len // 60)

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

            data = ndimage.gaussian_filter(data, sigma=1.0)
        except Exception as e:
            print(f"Error subsetting real data: {e}")
            import traceback
            traceback.print_exc()
            if Lons is None:
                lons = np.linspace(min_lon, max_lon, 200)
                lats = np.linspace(min_lat, max_lat, 200)
                Lons, Lats = np.meshgrid(lons, lats)
            data = np.zeros_like(Lons)

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
        
        levels = np.linspace(vmin, vmax, 50)
        cf = ax.contourf(Lons, Lats, data, levels=levels, cmap=cmap_name, alpha=0.55, extend='both')
        ax.contour(Lons, Lats, data, levels=15, colors='black', linewidths=0.3, alpha=0.8)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', transparent=True)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

# Singleton instance
data_loader = DataLoader()