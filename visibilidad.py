"""Kd490: modelo (MED e IBI, análisis + previsión) frente a satélite (OLCI 300 m) en Cabo de Palos."""
import numpy as np
import xarray as xr

sat = xr.open_dataset('salida/kd490.nc')['KD490']
med = xr.open_dataset('salida/kd_modelo_med.nc')['kd490']
ibi = xr.open_dataset('salida/kd_modelo_ibi.nc')['kd']
for name, da in (('MED', med), ('IBI', ibi)):
    print(name, 'dims', dict(da.sizes))
def zone(da):
    d = da
    if 'depth' in d.dims: d = d.isel(depth=0)
    return {str(t)[:10]: float(np.nanmedian(d.sel(time=t).values)) for t in d.time.values}
s, m, i = zone(sat), zone(med), zone(ibi)
print(f"{'día':10s} {'satélite':>9s} {'MED':>7s} {'IBI':>7s}   (Kd490 en 1/m; mayor = agua más turbia)")
pairs_m, pairs_i = [], []
for day in sorted(set(s) | set(m) | set(i)):
    sv, mv, iv = s.get(day, np.nan), m.get(day, np.nan), i.get(day, np.nan)
    print(f"{day:10s} {sv:9.3f} {mv:7.3f} {iv:7.3f}" + ('   ← previsión' if day > '2026-10-13' else ''))
    if not np.isnan(sv) and not np.isnan(mv): pairs_m.append((sv, mv))
    if not np.isnan(sv) and not np.isnan(iv): pairs_i.append((sv, iv))
for name, p in (('MED', pairs_m), ('IBI', pairs_i)):
    a = np.array(p)
    if len(a) > 2:
        print(f'{name}: días comparables {len(a)} · sesgo medio {np.mean(a[:,1]-a[:,0]):+.3f} · correlación {np.corrcoef(a[:,0], a[:,1])[0,1]:.2f}')
