"""
Viento de modelos locales de alta resolución donde existen (FUENTES-GLOBALES.md, paso 5; 15 oct), sobre el de ICON
global (~13 km): sustituyen el viento medio y su dirección; las rachas siguen siendo las de ICON (estos modelos no las
dan), nunca por debajo del viento medio. Se leen por OPeNDAP de servidores ERDDAP, sin clave; licencias de uso y
redistribución libres (sin uso legal).
  · CariCOOS WRF-NMM 2 km: Puerto Rico e Islas Vírgenes.
  · PacIOOS WRF: Hawái (~6 km), Guam (~3 km) y Samoa (~3 km). El de la Commonwealth de las Marianas (~12 km) no mejora
    a ICON y no se usa.
"""
import math

import numpy as np

from caricoos import _hourly

PACIOOS = 'https://pae-paha.pacioos.hawaii.edu/erddap/griddap/'
CARICOOS = 'https://dm3.caricoos.org/erddap/griddap/'
# Con su zona aproximada (lat0, lat1, lon0, lon1): los grupos sin spots allí no llaman al servidor.
MODELS = [
    ('CariCOOS WRF 2 km', CARICOOS + 'wrf_nmm_2km_best_agg', 'U10', 'V10', (16.5, 19.5, -68.5, -64)),
    ('PacIOOS WRF Hawái', PACIOOS + 'wrf_hi', 'Uwind', 'Vwind', (15, 26, -164, -150)),
    ('PacIOOS WRF Guam', PACIOOS + 'wrf_guam', 'Uwind', 'Vwind', (11, 16, 142, 147)),
    ('PacIOOS WRF Samoa', PACIOOS + 'wrf_samoa', 'Uwind', 'Vwind', (-17, -11, -175, -167)),
]


def local_wind(spots, wind, now, end, warnings):
    """Mejora `wind` ({spot: {hora: {wind, wdir, gust}}}) con los modelos locales que cubren a cada spot."""
    import xarray as xr

    from pacioos import near

    for label, url, uvar, vvar, rough in MODELS:
        if not near(spots, rough):
            continue
        try:
            ds = xr.open_dataset(url)
            la0, la1 = float(ds.latitude.min()), float(ds.latitude.max())
            lo0, lo1 = float(ds.longitude.min()), float(ds.longitude.max())
            ids = [s for s, (la, lo) in spots.items() if la0 <= la <= la1 and lo0 <= lo <= lo1 and wind.get(s)]
            if not ids:
                continue
            lats, lons = [spots[s][0] for s in ids], [spots[s][1] for s in ids]
            sub = ds.sel(time=slice(np.datetime64(now.replace(tzinfo=None)), np.datetime64(end.replace(tzinfo=None))),
                         latitude=slice(min(lats) - 0.1, max(lats) + 0.1), longitude=slice(min(lons) - 0.1, max(lons) + 0.1))
            u, v = sub[uvar].load(), sub[vvar].load()
            if u.time.size < 2:
                continue
            for sid in ids:
                la, lo = spots[sid]
                i = int(np.abs(u.latitude.values - la).argmin())
                j = int(np.abs(u.longitude.values - lo).argmin())
                hourly, uu = _hourly(u.time.values, u.values[:, i, j])
                _, vv = _hourly(v.time.values, v.values[:, i, j])
                for t, a, b in zip(hourly, uu, vv):
                    row = wind[sid].get(str(t)[:13] + ':00')
                    if row is None or not np.isfinite(a) or not np.isfinite(b):
                        continue
                    row['wind'] = round(math.hypot(a, b) * 3.6)
                    row['wdir'] = round((math.degrees(math.atan2(-a, -b)) + 360) % 360)
                    if row.get('gust') is not None:
                        row['gust'] = max(row['gust'], row['wind'])
            print(f'  · viento {label}: {len(ids)} spots')
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Viento {label}: {type(e).__name__} {str(e)[:120]}')
