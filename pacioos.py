"""
Modelos oceánicos locales de PacIOOS (Universidad de Hawái; FUENTES-GLOBALES.md, paso 5; 15 oct): ROMS de Hawái
(~4 km), Guam y Samoa, más finos que el global de Copernicus (9 km). Corriente a ~10 m (cada 3 h, repartida por horas)
y perfil diario de temperatura hasta 60 m, en la celda de mar más cercana a cada spot.
Se leen por OPeNDAP del servidor ERDDAP de PacIOOS, sin clave. Licencia: uso y redistribución libres (sin uso legal).
Su corriente va en la columna «reg» (regional) y su perfil sustituye al global en los spots de su zona.
"""
import numpy as np

ERDDAP = 'https://pae-paha.pacioos.hawaii.edu/erddap/griddap/'
DATASETS = ('roms_hiig', 'roms_marig', 'roms_samoa')
PAD = 0.2
# Zona aproximada de cada modelo: con la previsión en paralelo (15 oct), los grupos sin spots allí no llaman al servidor.
ROUGH = {'roms_hiig': (15, 26, -164, -150), 'roms_marig': (11, 22, 141, 149), 'roms_samoa': (-17, -11, -175, -167)}


def near(spots, box):
    la0, la1, lo0, lo1 = box
    return any(la0 <= la <= la1 and lo0 <= lo <= lo1 for la, lo in spots.values())


def pacioos(spots, res, start, end, nearest_sea_cell, clean, warnings):
    """Añade a `res` (la parte de Copernicus del proceso) la corriente y los perfiles de PacIOOS donde haya modelo."""
    import xarray as xr

    t0, t1 = np.datetime64(start.replace(tzinfo=None)), np.datetime64(end.replace(tzinfo=None))
    for name in DATASETS:
        if not near(spots, ROUGH[name]):
            continue
        try:
            ds = xr.open_dataset(ERDDAP + name)
            la0, la1 = float(ds.latitude.min()), float(ds.latitude.max())
            lo0, lo1 = float(ds.longitude.min()), float(ds.longitude.max())
            ids = [sid for sid, (la, lo) in spots.items() if la0 <= la <= la1 and lo0 <= lo <= lo1]
            if not ids:
                continue
            lats = [spots[s][0] for s in ids]
            lons = [spots[s][1] for s in ids]
            box = dict(latitude=slice(min(lats) - PAD, max(lats) + PAD), longitude=slice(min(lons) - PAD, max(lons) + PAD))
            sub = ds.sel(time=slice(t0 - np.timedelta64(3, 'h'), t1), **box)
            u = sub.u.sel(depth=10, method='nearest').load()
            v = sub.v.sel(depth=10, method='nearest').load()
            # Temperatura: solo las 12 UTC de cada día (lo que se usa); bajar todas las horas en 3D tardaba minutos.
            noon = [k for k, t in enumerate(sub.time.values) if str(t)[11:13] == '12']
            temp = sub.temp.sel(depth=slice(0, 60)).isel(time=noon).load()
            # Corriente: cada 3 h → horas, interpolando u y v.
            t_in = u.time.values.astype('datetime64[s]').astype(np.int64)
            hourly = np.arange(u.time.values[0], u.time.values[-1] + np.timedelta64(1, 'h'), np.timedelta64(1, 'h'))
            t_out = hourly.astype('datetime64[s]').astype(np.int64)
            for sid in ids:
                cell = nearest_sea_cell(u.isel(time=0), spots[sid])
                if cell is None:
                    continue
                i, j, d = cell
                uu = np.interp(t_out, t_in, u.values[:, i, j])
                vv = np.interp(t_out, t_in, v.values[:, i, j])
                res[sid]['seaCell']['reg'] = round(d, 1)
                for t, a, b in zip(hourly, uu, vv):
                    h = res[sid]['currents'].setdefault(str(t)[:13] + ':00', {})
                    h['reg'] = clean(float(np.hypot(a, b)) * 3.6)
                    h['regDir'] = clean((np.degrees(np.arctan2(a, b)) + 360) % 360, 0)
                # Perfil de temperatura: el de las 12 UTC de cada día.
                profiles = []
                for k, t in enumerate(temp.time.values):
                    col = temp.isel(time=k, latitude=i, longitude=j).values
                    levels = [[round(float(z), 1), round(float(x), 1)] for z, x in zip(temp.depth.values, col) if np.isfinite(x)]
                    if levels:
                        profiles.append({'date': str(t)[:10], 'levels': levels})
                if profiles:
                    res[sid]['profiles'] = profiles
                    res[sid]['seaCell']['temp'] = round(d, 1)
            print(f'  · PacIOOS {name}: {len(ids)} spots')
        except Exception as e:  # noqa: BLE001
            warnings.append(f'PacIOOS {name}: {type(e).__name__} {str(e)[:120]}')
