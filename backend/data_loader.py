import os
import xarray as xr
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

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

    def get_meteorology_at_point(self, lat: float, lon: float):
        """
        Interpolate data to a specific lat/lon point.
        """
        if self.ds_u10 is None:
            u10, v10, t2 = 5.0, 5.0, 280.0
        else:
            try:
                u10 = float(self.ds_u10.sel(lat=lat, lon=lon, method='nearest').u10.isel(time=0).values)
                v10 = float(self.ds_v10.sel(lat=lat, lon=lon, method='nearest').v10.isel(time=0).values)
                t2 = float(self.ds_t2.sel(lat=lat, lon=lon, method='nearest').t2.isel(time=0).values)
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

    def get_overlay_image(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer_type: str) -> bytes:
        """
        Generate a transparent PNG heatmap for the given bounding box and layer type.
        """
        # Increase DPI to 200 for higher resolution (e.g. 1600x1600 px image)
        fig = plt.figure(figsize=(8, 8), dpi=200)
        # Create axes spanning the entire figure to avoid borders
        ax = plt.Axes(fig, [0., 0., 1., 1.])
        ax.set_axis_off()
        fig.add_axes(ax)
        
        # Enforce the axes limits to exactly the map bounds so the image precisely aligns with Leaflet ImageOverlay bounds
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)
        
        if self.ds_u10 is None:
            lons = np.linspace(min_lon, max_lon, 200)
            lats = np.linspace(min_lat, max_lat, 200)
            Lons, Lats = np.meshgrid(lons, lats)
            freq1, freq2 = 20.0, 30.0
            phase = 0.0
            if layer_type == 'edr':
                freq1, freq2 = 40.0, 50.0
                phase = 1.0
            elif layer_type == 'temp':
                freq1, freq2 = 10.0, 15.0
                phase = 2.0
            data = (np.sin((Lons + phase) * freq1) * np.cos((Lats + phase) * freq2) + 
                   np.sin(Lons * freq2 * 0.5) + np.cos(Lats * freq1 * 0.5)) * 10
            data = data - np.min(data)
        else:
            try:
                # Expand the slice by a small margin (e.g. 0.5 degrees) so the data extends BEYOND the screen
                # This prevents the contour/pcolormesh from stopping before the edge of the image bounds
                pad = 0.5
                u10_subset = self.ds_u10.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                v10_subset = self.ds_v10.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                
                u10_vals = u10_subset.u10.isel(time=0).values.squeeze()
                v10_vals = v10_subset.v10.isel(time=0).values.squeeze()
                Lons, Lats = np.meshgrid(u10_subset.lon.values, u10_subset.lat.values)
                
                if layer_type == 'tke':
                    data = 0.1 * 0.5 * (u10_vals**2 + v10_vals**2)
                elif layer_type == 'edr':
                    tke = 0.1 * 0.5 * (u10_vals**2 + v10_vals**2)
                    data = 0.5 * (tke ** 1.5) / 100.0
                elif layer_type == 'temp':
                    t2_subset = self.ds_t2.sel(lat=slice(min_lat - pad, max_lat + pad), lon=slice(min_lon - pad, max_lon + pad))
                    data = t2_subset.t2.isel(time=0).values.squeeze() - 273.15
                else:
                    data = u10_vals
            except Exception as e:
                print(f"Error subsetting real data: {e}")
                lons = np.linspace(min_lon, max_lon, 200)
                lats = np.linspace(min_lat, max_lat, 200)
                Lons, Lats = np.meshgrid(lons, lats)
                data = (np.sin(Lons * 20.0) * np.cos(Lats * 30.0)) * 10
                data = data - np.min(data)
                
        # Set colormap
        cmap_name = 'jet'
        if layer_type == 'tke': cmap_name = 'YlOrRd'
        elif layer_type == 'edr': cmap_name = 'magma'
        elif layer_type == 'temp': cmap_name = 'coolwarm'
        
        # Plot filled contours (high resolution, 50 levels for smooth gradient)
        # alpha=0.55 makes it transparent
        cf = ax.contourf(Lons, Lats, data, levels=50, cmap=cmap_name, alpha=0.55)
        
        # Also plot the actual isolines (等值线) on top for clarity
        ax.contour(Lons, Lats, data, levels=15, colors='black', linewidths=0.3, alpha=0.3)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', transparent=True)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

# Singleton instance
data_loader = DataLoader()
