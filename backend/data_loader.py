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
        self._loaded = False

    def load_data(self):
        u_path = os.path.join(settings.DATA_DIR, settings.U_FILE)
        v_path = os.path.join(settings.DATA_DIR, settings.V_FILE)
        w_path = os.path.join(settings.DATA_DIR, settings.W_FILE)
        tc_path = os.path.join(settings.DATA_DIR, settings.TC_FILE)

        try:
            self.ds_u = xr.open_dataset(u_path)
            self.ds_v = xr.open_dataset(v_path)
            self.ds_w = xr.open_dataset(w_path)
            self.ds_tc = xr.open_dataset(tc_path)
            self._loaded = True
            print("Data loaded successfully.")
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
                # Time string format YYYY-MM-DDTHH:MM:SS
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                u = float(self.ds_u.sel(lat=lat, lon=lon, method='nearest').u.sel(time=target_time, method='nearest').sel(lev=500).values)
                v = float(self.ds_v.sel(lat=lat, lon=lon, method='nearest').v.sel(time=target_time, method='nearest').sel(lev=500).values)
                w = float(self.ds_w.sel(lat=lat, lon=lon, method='nearest').w.sel(time=target_time, method='nearest').sel(lev=500).values)
                tc = float(self.ds_tc.sel(lat=lat, lon=lon, method='nearest').tc.sel(time=target_time, method='nearest').sel(lev=500).values)
            except Exception as e:
                u, v, w, tc = 5.0, 5.0, 0.0, 280.0

        tke = calculate_tke(u, v, w)
        edr = calculate_edr(tke)

        return {
            "u": u,
            "v": v,
            "w": w,
            "tc": tc,
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
            return None, None, None, None
            
        target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
        end_time = target_time + np.timedelta64(24, 'h')
        time_slice = slice(target_time, end_time)
        
        # Ensure lat slice bounds are in correct order for monotonic coordinates
        lat_slice = slice(min_lat, max_lat)
        lon_slice = slice(min_lon, max_lon)
        
        # We use .sel() for time, lat, and lon slicing
        try:
            # Slicing the sub-region and extracting raw numpy arrays
            u_grid = self.ds_u.u.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            v_grid = self.ds_v.v.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            w_grid = self.ds_w.w.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
            
            lats = self.ds_u.lat.sel(lat=lat_slice).values
            lons = self.ds_u.lon.sel(lon=lon_slice).values
            times = self.ds_u.time.sel(time=time_slice).values
            
            # Vectorized calculation for the entire grid instantly
            wind_speed_sq = u_grid**2 + v_grid**2 + w_grid**2
            tke_grid = 0.01 * 0.5 * wind_speed_sq
            edr_grid = 0.5 * (tke_grid ** 1.5) / 100.0
            
            return tke_grid, edr_grid, lats, lons, times
        except Exception as e:
            print(f"Error fetching meteorology grid: {e}")
            return None, None, None, None, None


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
        
        if not self._loaded:
            self.load_data()
            
        if self.ds_u is None:
            lons = np.linspace(min_lon, max_lon, 200)
            lats = np.linspace(min_lat, max_lat, 200)
            Lons, Lats = np.meshgrid(lons, lats)
            data = np.zeros_like(Lons)
        else:
            try:
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                pad = 0.5
                u_sub_raw = self.ds_u.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                v_sub_raw = self.ds_v.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                
                # Dynamic stride to limit grid size for < 300ms rendering
                lat_len = len(u_sub_raw.lat)
                lon_len = len(u_sub_raw.lon)
                lat_step = max(1, lat_len // 60)
                lon_step = max(1, lon_len // 60)
                
                u_subset = u_sub_raw.isel(lat=slice(None, None, lat_step), lon=slice(None, None, lon_step))
                v_subset = v_sub_raw.isel(lat=slice(None, None, lat_step), lon=slice(None, None, lon_step))
                
                u_vals = u_subset.u.sel(time=target_time, method='nearest').sel(lev=500).values.squeeze()
                v_vals = v_subset.v.sel(time=target_time, method='nearest').sel(lev=500).values.squeeze()
                Lons, Lats = np.meshgrid(u_subset.lon.values, u_subset.lat.values)
                
                if layer_type == 'tke':
                    w_sub_raw = self.ds_w.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                    w_subset = w_sub_raw.isel(lat=slice(None, None, lat_step), lon=slice(None, None, lon_step))
                    w_vals = w_subset.w.sel(time=target_time, method='nearest').sel(lev=500).values.squeeze()
                    data = 0.1 * 0.5 * (u_vals**2 + v_vals**2 + w_vals**2)
                elif layer_type == 'edr':
                    w_sub_raw = self.ds_w.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                    w_subset = w_sub_raw.isel(lat=slice(None, None, lat_step), lon=slice(None, None, lon_step))
                    w_vals = w_subset.w.sel(time=target_time, method='nearest').sel(lev=500).values.squeeze()
                    tke = 0.1 * 0.5 * (u_vals**2 + v_vals**2 + w_vals**2)
                    data = 0.5 * (tke ** 1.5) / 100.0
                elif layer_type == 'temp':
                    tc_sub_raw = self.ds_tc.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                    tc_subset = tc_sub_raw.isel(lat=slice(None, None, lat_step), lon=slice(None, None, lon_step))
                    data = tc_subset.tc.sel(time=target_time, method='nearest').sel(lev=500).values.squeeze() - 273.15
                else:
                    data = u_vals
                
                # Apply Gaussian filter to smooth the contour lines (avoid jagged polygons)
                data = ndimage.gaussian_filter(data, sigma=1.2)
            except Exception as e:
                print(f"Error subsetting real data: {e}")
                lons = np.linspace(min_lon, max_lon, 200)
                lats = np.linspace(min_lat, max_lat, 200)
                Lons, Lats = np.meshgrid(lons, lats)
                data = np.zeros_like(Lons)
                
        cmap_name = 'jet'
        if layer_type == 'tke': cmap_name = 'YlOrRd'
        elif layer_type == 'edr': cmap_name = 'magma'
        elif layer_type == 'temp': cmap_name = 'coolwarm'
        
        cf = ax.contourf(Lons, Lats, data, levels=50, cmap=cmap_name, alpha=0.55)
        ax.contour(Lons, Lats, data, levels=15, colors='black', linewidths=0.3, alpha=0.3)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', transparent=True)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

# Singleton instance
data_loader = DataLoader()
