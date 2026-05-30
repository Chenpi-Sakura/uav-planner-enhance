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
        self.ds_u10 = None
        self.ds_v10 = None
        self.ds_t2 = None
        self.load_data()

    def load_data(self):
        u10_path = os.path.join(settings.DATA_DIR, settings.U10_FILE)
        v10_path = os.path.join(settings.DATA_DIR, settings.V10_FILE)
        t2_path = os.path.join(settings.DATA_DIR, settings.T2_FILE)

        try:
            self.ds_u10 = xr.open_dataset(u10_path)
            self.ds_v10 = xr.open_dataset(v10_path)
            self.ds_t2 = xr.open_dataset(t2_path)
            print("Data loaded successfully.")
        except FileNotFoundError as e:
            print(f"Warning: Data file not found. {e}")

    def get_meteorology_at_point(self, lat: float, lon: float, date: str = '2022-05-30', time_slot: str = 'morning'):
        """
        Interpolate data to a specific lat/lon point.
        """
        if self.ds_u10 is None:
            u10, v10, t2 = 5.0, 5.0, 280.0
        else:
            try:
                # Time string format YYYY-MM-DDTHH:MM:SS
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                u10 = float(self.ds_u10.sel(lat=lat, lon=lon, method='nearest').u10.sel(time=target_time, method='nearest').values)
                v10 = float(self.ds_v10.sel(lat=lat, lon=lon, method='nearest').v10.sel(time=target_time, method='nearest').values)
                t2 = float(self.ds_t2.sel(lat=lat, lon=lon, method='nearest').t2.sel(time=target_time, method='nearest').values)
            except Exception as e:
                u10, v10, t2 = 5.0, 5.0, 280.0

        tke = calculate_tke(u10, v10)
        edr = calculate_edr(tke)

        return {
            "u10": u10,
            "v10": v10,
            "t2": t2,
            "tke": tke,
            "edr": edr
        }

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
        
        if self.ds_u10 is None:
            lons = np.linspace(min_lon, max_lon, 200)
            lats = np.linspace(min_lat, max_lat, 200)
            Lons, Lats = np.meshgrid(lons, lats)
            data = np.zeros_like(Lons)
        else:
            try:
                target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
                pad = 0.5
                u10_subset = self.ds_u10.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                v10_subset = self.ds_v10.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                
                u10_vals = u10_subset.u10.sel(time=target_time, method='nearest').values.squeeze()
                v10_vals = v10_subset.v10.sel(time=target_time, method='nearest').values.squeeze()
                Lons, Lats = np.meshgrid(u10_subset.lon.values, u10_subset.lat.values)
                
                if layer_type == 'tke':
                    data = 0.1 * 0.5 * (u10_vals**2 + v10_vals**2)
                elif layer_type == 'edr':
                    tke = 0.1 * 0.5 * (u10_vals**2 + v10_vals**2)
                    data = 0.5 * (tke ** 1.5) / 100.0
                elif layer_type == 'temp':
                    t2_subset = self.ds_t2.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                    data = t2_subset.t2.sel(time=target_time, method='nearest').values.squeeze() - 273.15
                else:
                    data = u10_vals
                
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
