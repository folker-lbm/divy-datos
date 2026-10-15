"""
Modelos locales de CariCOOS (Puerto Rico e Islas Vírgenes; FUENTES-GLOBALES.md, paso 5; 15 oct):
  · Oleaje SWAN costero de 100–180 m (Puerto Rico, Islas Vírgenes de EE. UU., Saint Croix), frente a los 9 km del global.
  · El viento WRF-NMM de 2 km está en viento_local.py, con los de PacIOOS.
Se leen por OPeNDAP del ERDDAP de CariCOOS, sin clave. Licencia: uso y redistribución libres (sin uso legal).
"""
import math

import numpy as np

ERDDAP = 'https://dm3.caricoos.org/erddap/griddap/'
SWAN = ('SWAN_HighRes_PR', 'SWAN_HighRes_USVI', 'SWAN_HighRes_StCroix')
ROUGH = (16.5, 19.5, -68.5, -64)  # Puerto Rico e Islas Vírgenes, para no llamar al servidor sin spots allí
WIN = 0.02  # ~2 km alrededor del spot para buscar la celda de mar más cercana del SWAN (celdas de ~100 m)


def _hourly(times, values):
    """Valores (tiempo, ...) repartidos hora a hora con interpolación lineal."""
    t_in = times.astype('datetime64[s]').astype(np.int64)
    hourly = np.arange(times[0], times[-1] + np.timedelta64(1, 'h'), np.timedelta64(1, 'h'))
    t_out = hourly.astype('datetime64[s]').astype(np.int64)
    return hourly, np.interp(t_out, t_in, values)


def caricoos_waves(spots, res, start, end, nearest_sea_cell, clean, warnings):
    """Añade el oleaje SWAN (columna «reg») a los spots dentro de sus mallas."""
    import xarray as xr

    t0, t1 = np.datetime64(start.replace(tzinfo=None)), np.datetime64(end.replace(tzinfo=None))
    from pacioos import near

    if not near(spots, ROUGH):
        return
    done = set()
    for name in SWAN:
        try:
            ds = xr.open_dataset(ERDDAP + name)
            la0, la1 = float(ds.latitude.min()), float(ds.latitude.max())
            lo0, lo1 = float(ds.longitude.min()), float(ds.longitude.max())
            ids = [s for s, (la, lo) in spots.items() if s not in done and la0 <= la <= la1 and lo0 <= lo <= lo1]
            n = 0
            for sid in ids:
                la, lo = spots[sid]
                sub = ds.sel(time=slice(t0 - np.timedelta64(3, 'h'), t1), latitude=slice(la - WIN, la + WIN), longitude=slice(lo - WIN, lo + WIN))
                hs, dr, tp = sub.hs.load(), sub.dir.load(), sub.tp.load()
                if hs.time.size < 2 or hs.latitude.size == 0:
                    continue
                cell = nearest_sea_cell(hs.isel(time=0), (la, lo))
                if cell is None:
                    continue
                i, j, d = cell
                times = hs.time.values
                hourly, h = _hourly(times, hs.values[:, i, j])
                tpv = tp.values[:, i, j]
                if np.issubdtype(tpv.dtype, np.timedelta64):  # el periodo llega como duración: a segundos
                    tpv = tpv / np.timedelta64(1, 's')
                _, p = _hourly(times, tpv.astype(float))
                rad = np.radians(dr.values[:, i, j])
                _, sn = _hourly(times, np.sin(rad))
                _, cs = _hourly(times, np.cos(rad))
                res[sid]['seaCell']['wavReg'] = round(d, 2)
                for t, a, b, c, e in zip(hourly, h, sn, cs, p):
                    res[sid]['waves'].setdefault(str(t)[:13] + ':00', {})['reg'] = [clean(a), clean((math.degrees(math.atan2(b, c)) + 360) % 360, 0), clean(e, 1)]
                done.add(sid)
                n += 1
            if ids:
                print(f'  · CariCOOS {name}: oleaje para {n} de {len(ids)} spots')
        except Exception as e:  # noqa: BLE001
            warnings.append(f'CariCOOS {name}: {type(e).__name__} {str(e)[:120]}')
