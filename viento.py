"""
Viento por spot sin límite de llamadas (Luka, 14 oct: «escalar al mundo»): mosaico de modelos abiertos con prioridad
por región. Los modelos se descargan como ficheros (GRIB) enteros: el coste no depende del número de spots.

  1. ICON-EU (DWD, servicio meteorológico alemán), ~7 km, cada 3 h, horario hasta 78 h. Europa (29,5–70,5° N).
  2. Open-Meteo (por spot) para los spots fuera de ICON-EU (p. ej. Canarias): mejor que un global de 28 km.
  3. ECMWF Open Data (IFS, 0,25°, cada 3 h) si Open-Meteo falla. Global; pasará a 9 km cuando ECMWF lo publique.

Más regiones (HRRR en EE. UU., HRDPS en Canadá, AROME en Francia…) se añaden aquí cuando se abran spots allí.
Salida: {spot_id: {'YYYY-MM-DDTHH:00': {'wind': km/h, 'gust': km/h, 'wdir': ° de procedencia}}}.
"""
import bz2
import math
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone

import requests

ICON_EU = 'https://opendata.dwd.de/weather/nwp/icon-eu/grib'
ICON_EU_BOX = (29.5, 70.5, -23.5, 62.5)  # lat mín, lat máx, lon mín, lon máx
ICON_MAX_STEP = 78  # horario hasta aquí


def in_icon_eu(pos):
    return ICON_EU_BOX[0] <= pos[0] <= ICON_EU_BOX[1] and ICON_EU_BOX[2] <= pos[1] <= ICON_EU_BOX[3]


def _grib_fields(path):
    """[(shortName, step, valores 2D, (lat0, dlat, lon0, dlon))] de un fichero GRIB en rejilla regular."""
    import eccodes

    out = []
    with open(path, 'rb') as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                ni, nj = eccodes.codes_get(h, 'Ni'), eccodes.codes_get(h, 'Nj')
                lat0 = eccodes.codes_get(h, 'latitudeOfFirstGridPointInDegrees')
                lon0 = eccodes.codes_get(h, 'longitudeOfFirstGridPointInDegrees')
                dlat = eccodes.codes_get(h, 'jDirectionIncrementInDegrees') * (1 if eccodes.codes_get(h, 'jScansPositively') else -1)
                dlon = eccodes.codes_get(h, 'iDirectionIncrementInDegrees')
                vals = eccodes.codes_get_values(h).reshape(nj, ni)
                out.append((eccodes.codes_get(h, 'shortName'), int(eccodes.codes_get(h, 'endStep', ktype=int)), vals, (lat0, dlat, lon0, dlon)))
            finally:
                eccodes.codes_release(h)
    return out


def _at(vals, grid, pos):
    lat0, dlat, lon0, dlon = grid
    j = int(round((pos[0] - lat0) / dlat))
    lon = pos[1] if lon0 < 0 or pos[1] >= 0 else pos[1] + 360
    i = int(round(((lon - lon0) % 360) / dlon))
    if 0 <= j < vals.shape[0] and 0 <= i < vals.shape[1]:
        v = float(vals[j, i])
        return None if math.isnan(v) or abs(v) > 1e10 else v
    return None


def _wind(u, v):
    """km/h y dirección de procedencia (°)."""
    return round(math.hypot(u, v) * 3.6), round((math.degrees(math.atan2(-u, -v)) + 360) % 360)


def _latest_icon_run(session, now):
    """Última pasada de ICON-EU ya publicada entera (se comprueba que exista el último paso que usamos)."""
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % 3)
    for back in range(0, 8):
        run = t - timedelta(hours=3 * back)
        url = f'{ICON_EU}/{run:%H}/u_10m/icon-eu_europe_regular-lat-lon_single-level_{run:%Y%m%d%H}_{ICON_MAX_STEP:03d}_U_10M.grib2.bz2'
        for attempt in range(3):
            try:
                if session.head(url, timeout=30).status_code == 200:
                    return run
                break
            except requests.RequestException:  # cortes TLS puntuales del servidor del DWD
                time.sleep(3 * (attempt + 1))
    return None


def icon_eu(spots, now, end, warnings):
    out = {sid: {} for sid, pos in spots.items() if in_icon_eu(pos)}
    if not out:
        return {}
    session = requests.Session()
    run = _latest_icon_run(session, now)
    if run is None:
        warnings.append('ICON-EU: no hay ninguna pasada reciente completa')
        return {}
    steps = [s for s in range(0, ICON_MAX_STEP + 1) if run + timedelta(hours=s) <= end]
    var_names = {'u_10m': 'U_10M', 'v_10m': 'V_10M', 'vmax_10m': 'VMAX_10M'}

    def fetch(args):
        var, step = args
        url = f'{ICON_EU}/{run:%H}/{var}/icon-eu_europe_regular-lat-lon_single-level_{run:%Y%m%d%H}_{step:03d}_{var_names[var]}.grib2.bz2'
        for attempt in range(3):
            try:
                r = session.get(url, timeout=120)
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix='.grib2') as tmp:
                    tmp.write(bz2.decompress(r.content))
                    tmp.flush()
                    _, _, vals, grid = _grib_fields(tmp.name)[0]
                return var, step, {sid: _at(vals, grid, spots[sid]) for sid in out}
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    warnings.append(f'ICON-EU {var} +{step} h: {str(e)[:80]}')
                time.sleep(3 * (attempt + 1))
        return var, step, {}

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(fetch, [(v, s) for v in var_names for s in steps]))
    by = {(v, s): vals for v, s, vals in results}
    for s in steps:
        key = (run + timedelta(hours=s)).strftime('%Y-%m-%dT%H:00')
        for sid in out:
            u, v, g = by.get(('u_10m', s), {}).get(sid), by.get(('v_10m', s), {}).get(sid), by.get(('vmax_10m', s), {}).get(sid)
            if u is None or v is None:
                continue
            speed, wdir = _wind(u, v)
            out[sid][key] = {'wind': speed, 'wdir': wdir, 'gust': round(g * 3.6) if g is not None else None}
    print(f'  · viento ICON-EU (pasada {run:%d %H} UTC): {len(out)} spots, {len(steps)} horas')
    return out


def ecmwf(spots, now, end, warnings):
    """Respaldo global: ECMWF Open Data, cada 3 h, interpolado a horas."""
    if not spots:
        return {}
    try:
        from ecmwf.opendata import Client

        steps = list(range(0, 145, 3))
        with tempfile.NamedTemporaryFile(suffix='.grib2') as tmp:
            res = Client(source='ecmwf').retrieve(type='fc', stream='oper', step=steps, param=['10u', '10v', '10fg'], target=tmp.name)
            base = res.datetime.replace(tzinfo=timezone.utc)
            fields = _grib_fields(tmp.name)
    except Exception as e:  # noqa: BLE001
        warnings.append(f'ECMWF Open Data: {str(e)[:120]}')
        return {}
    by = {}
    for name, step, vals, grid in fields:
        name = '10fg' if name.startswith('10fg') or name == 'fg10' else name
        by[(name, step)] = (vals, grid)
    out = {sid: {} for sid in spots}
    for sid, pos in spots.items():
        series = {}
        for s in steps:
            u, v = by.get(('10u', s)), by.get(('10v', s))
            if not u or not v:
                continue
            uu, vv = _at(*u, pos), _at(*v, pos)
            g = by.get(('10fg', s))
            gg = _at(*g, pos) if g else None
            if uu is not None and vv is not None:
                series[s] = (uu, vv, gg)
        ks = sorted(series)
        for a, b in zip(ks, ks[1:]):
            for h in range(a, b):
                w = (h - a) / (b - a)
                u = series[a][0] * (1 - w) + series[b][0] * w
                v = series[a][1] * (1 - w) + series[b][1] * w
                gust = series[b][2]
                t = base + timedelta(hours=h)
                if t > end:
                    break
                speed, wdir = _wind(u, v)
                out[sid][t.strftime('%Y-%m-%dT%H:00')] = {'wind': speed, 'wdir': wdir, 'gust': round(gust * 3.6) if gust is not None else None}
    print(f'  · viento ECMWF (pasada {base:%d %H} UTC): {len(spots)} spots')
    return out
