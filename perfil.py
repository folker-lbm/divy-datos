"""Perfil vertical de temperatura del modelo mediterráneo de Copernicus en Cabo de Palos."""
import sys
import numpy as np
import xarray as xr

ds = xr.open_dataset(sys.argv[1])
t = ds['thetao']
print('malla lat', ds.latitude.values.round(3), 'lon', ds.longitude.values.round(3))
print('niveles (m)', ds.depth.values.round(1))
print('horas', ds.time.size, str(ds.time.values[0])[:16], '→', str(ds.time.values[-1])[:16])
# Celda más cercana a los Bajos (aprox. 37.649, -0.660)
p = t.sel(latitude=37.649, longitude=-0.660, method='nearest')
print('celda usada', float(p.latitude), float(p.longitude))
for hora in [0, 12, 24, 48, 72]:
    if hora >= p.time.size: continue
    perfil = p.isel(time=hora)
    vals = [(float(d), float(v)) for d, v in zip(perfil.depth.values, perfil.values) if not np.isnan(v)]
    linea = '  '.join(f'{d:.0f}m:{v:.1f}' for d, v in vals)
    # termoclina: mayor gradiente vertical (°C por metro)
    g = [((vals[i][0] + vals[i + 1][0]) / 2, (vals[i][1] - vals[i + 1][1]) / (vals[i + 1][0] - vals[i][0])) for i in range(len(vals) - 1)]
    zmax, gmax = max(g, key=lambda x: x[1]) if g else (None, None)
    print(f"{str(perfil.time.values)[:16]} | {linea}")
    print(f"    fondo en la celda ≈ {vals[-1][0]:.0f} m · gradiente máx {gmax:.2f} °C/m a ~{zmax:.0f} m")
