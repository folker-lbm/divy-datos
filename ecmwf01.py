"""
T127 (21 nov 2026): viento y oleaje de ECMWF Open Data a 0,1° (~9 km; IFS y su modelo de oleaje WAM), abiertos desde el
20 oct 2026 con licencia CC BY 4.0 (atribución: «Contiene datos de ECMWF Open Data»). Interruptor: DIVY_ECMWF01=1
(variable del repositorio con el mismo nombre en prevision.yml). Sin la variable, nada cambia.

Validado contra 140 boyas a 25 km o menos de un spot (12-18 nov, ver docs del informe de T127 en Divy-cache/T127-ecmwf):
  · Viento: error típico de 1,99 m/s frente a 2,46 de ICON (2,16 frente a 2,81 a 5 km o menos de costa); también en Europa
    (2,17 frente a 2,51 de ICON-EU), incluso a 3 días. Con el interruptor, es el viento de todo el mundo; ICON y lo demás
    quedan de respaldo.
  · Oleaje: mejor que Open-Meteo Marine (0,35 m frente a 0,43) y igual o mejor que el global de Copernicus (periodo, mejor
    en todas partes); peor que los regionales europeos de Copernicus (0,35 frente a 0,26). Con el interruptor, va después
    de los regionales y antes del global de Copernicus (si su celda de mar está a 10 km o menos), y sustituye a Open-Meteo
    como respaldo.

Se baja el campo del mundo entero, solo las variables que hacen falta, por rangos de bytes con el `.index` de cada paso
(unos 17 MB por paso: 10u, 10v, 10fg, swh, mwd, pp1d), cada 3 h hasta cubrir el final de la ventana, de la copia de Google
Cloud (la más rápida) y, si falla, de la de ECMWF y de la de AWS. Viento en el punto de rejilla más cercano (como en la
validación); oleaje en la celda de mar más cercana al spot (con altura, dirección y periodo; si no, sin periodo), hasta 3
celdas (~33 km). Interpolado a horas como ecmwf() de viento.py. La racha a 0,1° es la máxima de la hora anterior a cada
paso y solo llega hasta +90 h. Un spot solo sale si sus pasos cubren toda la ventana sin huecos de más de HUECO_MAX h:
si faltan pasos, va entero al respaldo (ICON, Open-Meteo).
Salida: (viento {spot: {hora: {wind, gust, wdir}}}, oleaje {spot: {hora: [hs, dir, tp]}}, distancia {spot: km a la celda
de mar}).
"""
import json
import math
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

MIRRORS = (  # la de AWS, la última: devuelve 503 con 0,1° (revisión del 21 nov)
    'https://storage.googleapis.com/ecmwf-open-data',
    'https://data.ecmwf.int/forecasts',
    'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com',
)
OPER = ('10u', '10v', '10fg')
WAVE = ('swh', 'mwd', 'pp1d')
MAX_STEP = 144  # cada 3 h hasta aquí en las cuatro pasadas
ANILLOS = 3  # celdas alrededor para buscar mar (oleaje)
HUECO_MAX = 6  # h: un spot con un hueco mayor entre pasos (pasos perdidos) no cuenta como cubierto y va al respaldo


def _get(url, rng=None, tries=4):
    headers = {'User-Agent': 'divy-pipeline'}
    if rng:
        headers['Range'] = f'bytes={rng[0]}-{rng[1]}'
    err = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120).read()
        except Exception as e:  # noqa: BLE001
            err = e
            if getattr(e, 'code', None) == 404:
                break
            time.sleep(3 * 2 ** i)
    raise err


def _name(run, step, stream):
    return f'{run:%Y%m%d}/{run:%H}z/ifs/0p1/{stream}/{run:%Y%m%d%H}0000-{step}h-{stream}-fc-sfc'


def _latest_run(now, last_step_for, warnings):
    """Última pasada publicada entera hasta el paso que hace falta (se mira el `.index` de ese paso en las dos corrientes)."""
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % 6)
    for back in range(0, 5):
        run = t - timedelta(hours=6 * back)
        step = last_step_for(run)
        for base in MIRRORS:
            try:
                for stream in ('oper', 'wave'):
                    _get(f'{base}/{_name(run, step, stream)}.index', tries=2)
                return run, base
            except Exception:  # noqa: BLE001
                continue
    warnings.append('ECMWF 0,1°: no hay ninguna pasada reciente completa')
    return None, None


def _decode(raw):
    """[(shortName, valores 2D con NaN donde no hay dato, (lat0, lon0, d))] de unos mensajes GRIB en memoria."""
    import eccodes
    import numpy as np

    out = []
    pos = 0
    while pos < len(raw):
        h = eccodes.codes_new_from_message(raw[pos:])
        try:
            size = eccodes.codes_get(h, 'totalLength')
            ni, nj = eccodes.codes_get(h, 'Ni'), eccodes.codes_get(h, 'Nj')
            v = eccodes.codes_get_values(h).reshape(nj, ni)
            if eccodes.codes_get(h, 'bitmapPresent'):
                v = np.where(v == eccodes.codes_get(h, 'missingValue'), np.nan, v)
            grid = (eccodes.codes_get(h, 'latitudeOfFirstGridPointInDegrees'), eccodes.codes_get(h, 'longitudeOfFirstGridPointInDegrees'),
                    eccodes.codes_get(h, 'iDirectionIncrementInDegrees'))
            out.append((eccodes.codes_get(h, 'shortName'), v, grid))
        finally:
            eccodes.codes_release(h)
        pos += size
    return out


def _cells(ids, spots, shape, grid):
    """Índices (j, i) del punto de rejilla más cercano a cada spot (rejilla de norte a sur, longitudes de 0 a 360 o -180)."""
    import numpy as np

    la0, lo0, d = grid
    la = np.array([spots[s][0] for s in ids])
    lo = np.array([spots[s][1] for s in ids])
    j = np.clip(np.rint((la0 - la) / d).astype(int), 0, shape[0] - 1)
    i = np.rint(((lo - lo0) % 360) / d).astype(int) % shape[1]
    return j, i


def _km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1.0, a)))


def _sea_cells(masks, j, i, pts, grid):
    """Celda de mar más cercana al spot (distancia real, desde sus coordenadas) en la ventana de (2·ANILLOS+1)² celdas,
    con la primera máscara que tenga alguna: [(j, i, km, con_periodo) o None]."""
    la0, lo0, d = grid
    nj, ni = masks[0].shape
    out = []
    for jj, ii, (la, lo) in zip(j.tolist(), i.tolist(), pts):
        best = None
        for m, mask in enumerate(masks):
            for dj in range(-ANILLOS, ANILLOS + 1):
                y = jj + dj
                if not 0 <= y < nj:
                    continue
                for di in range(-ANILLOS, ANILLOS + 1):
                    x = (ii + di) % ni
                    if not mask[y, x]:
                        continue
                    km = _km(la, lo, la0 - y * d, lo0 + x * d)
                    if best is None or km < best[2]:
                        best = (y, x, km, m == 0)
            if best is not None:
                break
        out.append(best)
    return out


def _cubre(ks, steps):
    """Los pasos con dato cubren toda la ventana sin huecos de más de HUECO_MAX h."""
    return bool(ks) and ks[0] == steps[0] and ks[-1] == steps[-1] and all(b - a <= HUECO_MAX for a, b in zip(ks, ks[1:]))


def ecmwf01(spots, now, end, warnings, olas=True):
    """Viento y oleaje de ECMWF 0,1° para `spots` ({id: (lat, lng)}) hasta `end`. Ver el comentario del módulo."""
    if not spots:
        return {}, {}, {}
    import numpy as np

    def last_step(run):
        h = int((end - run).total_seconds() // 3600)
        return min(MAX_STEP, max(0, -(-h // 3) * 3))

    run, base = _latest_run(now, last_step, warnings)
    if run is None:
        return {}, {}, {}
    steps = list(range(0, last_step(run) + 1, 3))
    streams = (('oper', OPER),) + ((('wave', WAVE),) if olas else ())
    ids = list(spots)
    pts = [spots[s] for s in ids]
    pos = {}  # por rejilla: índices de los spots
    sea = {}  # por rejilla: celda de mar de cada spot (del primer paso de oleaje que llega, con sus tres campos)
    lock = threading.Lock()

    def cells(shape, grid):
        with lock:
            if (shape, grid) not in pos:
                pos[(shape, grid)] = _cells(ids, spots, shape, grid)
            return pos[(shape, grid)]

    def sample(stream, fields):
        """Valores de los campos de un paso en los spots (dentro del hilo: el campo entero no se guarda)."""
        out = {}
        if stream == 'wave':
            shape, grid = fields['swh'][0].shape, fields['swh'][1]
            key = (shape, grid)
            with lock:
                hay = key in sea
            if not hay:
                fin = {p: np.isfinite(fields[p][0]) for p in WAVE}
                # Con los tres campos; si no hay ninguna celda así, sin el periodo (pp1d falta en ~1.600 celdas de costa).
                c = _sea_cells([fin['swh'] & fin['mwd'] & fin['pp1d'], fin['swh'] & fin['mwd']], *cells(shape, grid), pts, grid)
                with lock:
                    sea.setdefault(key, c)
            for p, (v, _) in fields.items():
                out[p] = [None if c is None or not np.isfinite(v[c[0], c[1]]) else float(v[c[0], c[1]]) for c in sea[key]]
            return out
        for p, (v, grid) in fields.items():
            j, i = cells(v.shape, grid)
            out[p] = [None if not np.isfinite(x) else float(x) for x in v[j, i]]
        return out

    def fetch(args):
        stream, params, step = args
        name = _name(run, step, stream)
        err = None
        for b in (base,) + tuple(m for m in MIRRORS if m != base):
            try:
                tries = 4 if b == base else 2
                idx = [json.loads(line) for line in _get(f'{b}/{name}.index', tries=tries).decode().splitlines() if line.strip()]
                sel = sorted((x for x in idx if x['param'] in params and x.get('levtype', 'sfc') == 'sfc'), key=lambda x: x['_offset'])
                raw = b''.join(_get(f'{b}/{name}.grib2', (x['_offset'], x['_offset'] + x['_length'] - 1), tries=tries) for x in sel)
                fields = _decode(raw)
                if len(fields) != len(sel):
                    raise ValueError(f'{len(fields)} campos de {len(sel)}')
                if stream == 'wave' and {x['param'] for x in sel} != set(WAVE):
                    raise ValueError(f'faltan campos de oleaje: {sorted(set(WAVE) - {x["param"] for x in sel})}')
                # Con el nombre del `.index` (el shortName de eccodes de la racha cambia según la versión: 10fg o fg10).
                return step, sample(stream, {x['param']: (v, grid) for x, (_, v, grid) in zip(sel, fields)})
            except Exception as e:  # noqa: BLE001
                err = e
        warnings.append(f'ECMWF 0,1° {stream} +{step} h: {str(err)[:80]}')
        return step, {}

    vals = {}  # (param, step) -> lista por spot
    with ThreadPoolExecutor(max_workers=6) as pool:
        for step, got in pool.map(fetch, [(s, p, st) for s, p in streams for st in steps]):
            for p, lst in got.items():
                vals[(p, step)] = lst
    km = {}
    for cs in sea.values():
        km = {ids[n]: round(c[2], 1) for n, c in enumerate(cs) if c is not None}

    def lerp(a, b, w):
        return a * (1 - w) + b * w

    def at(p, s, n):
        lst = vals.get((p, s))
        return lst[n] if lst else None

    wind, waves = {}, {}
    sin_w = sin_o = 0
    for n, sid in enumerate(ids):
        w_rows, o_rows = {}, {}
        ks = [s for s in steps if at('10u', s, n) is not None and at('10v', s, n) is not None]
        if _cubre(ks, steps):
            for a, b in zip(ks, ks[1:]):
                for h in range(a, b + (1 if b == ks[-1] else 0)):
                    t = run + timedelta(hours=h)
                    if t > end:
                        break
                    w = (h - a) / (b - a)
                    u = lerp(at('10u', a, n), at('10u', b, n), w)
                    v = lerp(at('10v', a, n), at('10v', b, n), w)
                    # Racha: la del paso en las horas de paso; entre pasos, la mayor de las dos (ninguna, más allá de +90 h).
                    ga, gb = at('10fg', a, n), at('10fg', b, n)
                    g = ga if h == a else gb if h == b else (max(ga, gb) if ga is not None and gb is not None else None)
                    w_rows[t.strftime('%Y-%m-%dT%H:00')] = {'wind': round(math.hypot(u, v) * 3.6), 'wdir': round((math.degrees(math.atan2(-u, -v)) + 360) % 360),
                                                           'gust': round(g * 3.6) if g is not None else None}
        elif ks:
            sin_w += 1
        ko = [s for s in steps if at('swh', s, n) is not None and at('mwd', s, n) is not None]
        if _cubre(ko, steps):
            for a, b in zip(ko, ko[1:]):
                for h in range(a, b + (1 if b == ko[-1] else 0)):
                    t = run + timedelta(hours=h)
                    if t > end:
                        break
                    w = (h - a) / (b - a)
                    ra, rb = math.radians(at('mwd', a, n)), math.radians(at('mwd', b, n))
                    d = (math.degrees(math.atan2(lerp(math.sin(ra), math.sin(rb), w), lerp(math.cos(ra), math.cos(rb), w))) + 360) % 360
                    pa, pb = at('pp1d', a, n), at('pp1d', b, n)
                    o_rows[t.strftime('%Y-%m-%dT%H:00')] = [round(lerp(at('swh', a, n), at('swh', b, n), w), 2), round(d),
                                                             round(lerp(pa, pb, w), 1) if pa is not None and pb is not None else None]
        elif ko:
            sin_o += 1
        if w_rows:
            wind[sid] = w_rows
        if o_rows:
            waves[sid] = o_rows
    if sin_w or sin_o:
        warnings.append(f'ECMWF 0,1°: pasos perdidos; {sin_w} spots sin su viento y {sin_o} sin su oleaje (van al respaldo)')
    print(f'  · ECMWF 0,1° (pasada {run:%d %H} UTC, {len(steps)} pasos, {base.split("/")[2]}): viento de {len(wind)} y oleaje de {len(waves)} de {len(ids)} spots')
    return wind, waves, km
