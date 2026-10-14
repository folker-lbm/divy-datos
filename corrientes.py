"""Corrientes de IBI (~3 km) y MEDSEA (~4 km) en spots de Cabo de Palos, usando la celda de mar más cercana."""
import math
import numpy as np
import xarray as xr

SPOTS = {'Bajo de Dentro': (37.649, -0.660), 'Bajo de Fuera': (37.667, -0.632), 'Cala Reona': (37.620, -0.712), 'Cueva de la Virgen': (37.626, -0.702)}
km = lambda a, b: 6371 * math.acos(min(1, math.sin(math.radians(a[0])) * math.sin(math.radians(b[0])) + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(a[1] - b[1]))))
for tag in ('ibi', 'med'):
    ds = xr.open_dataset(f'salida/corrientes_{tag}.nc')
    sp = np.hypot(ds.uo, ds.vo) * 3.6  # km/h
    sea = ~np.isnan(sp.isel(time=0, depth=0).values)
    print(f'== {tag.upper()} ({ds.time.size} h)')
    for name, pos in SPOTS.items():
        best = min(((km(pos, (float(la), float(lo))), i, j) for i, la in enumerate(ds.latitude.values) for j, lo in enumerate(ds.longitude.values) if sea[i, j]))
        d, i, j = best
        cell = sp.isel(latitude=i, longitude=j)
        s0 = cell.isel(depth=0).values; s10 = cell.sel(depth=10, method='nearest').values
        u = ds.uo.isel(latitude=i, longitude=j).sel(depth=10, method='nearest').values; v = ds.vo.isel(latitude=i, longitude=j).sel(depth=10, method='nearest').values
        dirs = (np.degrees(np.arctan2(u, v)) + 360) % 360  # hacia dónde va
        print(f'   {name:20s} celda de mar a {d:.1f} km · sup {np.nanmin(s0):.2f}–{np.nanmax(s0):.2f} km/h · 10 m {np.nanmin(s10):.2f}–{np.nanmax(s10):.2f} km/h (media {np.nanmean(s10):.2f}) · rumbo medio a 10 m {np.nanmedian(dirs):.0f}°')
# Correlación entre modelos en la celda de los Bajos a 10 m
a = xr.open_dataset('salida/corrientes_ibi.nc'); b = xr.open_dataset('salida/corrientes_med.nc')
sa = (np.hypot(a.uo, a.vo) * 3.6).sel(latitude=37.666, longitude=-0.638, method='nearest').sel(depth=10, method='nearest').values
sb = (np.hypot(b.uo, b.vo) * 3.6).sel(latitude=37.646, longitude=-0.667, method='nearest').sel(depth=10, method='nearest').values
n = min(len(sa), len(sb)); ok = ~np.isnan(sa[:n]) & ~np.isnan(sb[:n])
print('Correlación IBI–MEDSEA a 10 m en los Bajos:', round(float(np.corrcoef(sa[:n][ok], sb[:n][ok])[0, 1]), 2))
