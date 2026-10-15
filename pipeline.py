"""
Proceso de datos de Divy: genera la previsión real por spot para la app.

Fuentes (ver INVESTIGACION-DATOS.md):
  · Oleaje y viento hora a hora: Open-Meteo (Marine DWD/MFWAM y Forecast), por spot.
  · Perfil de temperatura por profundidad (diario): Copernicus MEDSEA 3D en el Mediterráneo, IBI 3D en el Atlántico
    ibérico y el GLOBAL (1/12°) en el resto del mundo.
  · Corriente a ~10 m (horaria): Copernicus IBI en toda España y MEDSEA en el Mediterráneo; fuera, el GLOBAL (cada 6 h,
    interpolado a horas). En la celda de mar más cercana.
  · Viento: ICON-EU en Europa; fuera, ECMWF Open Data (viento.py).
Catálogo mundial (14 oct): cada spot tiene una región de previsión y la salida se publica por regiones («actual-<región>»;
España sigue en «actual»). Con --muestra se prueba con la muestra mundial de la API sin publicar nada.
  · Avisos oficiales: AEMET (CAP con contornos), en cada ejecución (avisos.py).
  · Boyas: Copernicus In Situ global, la más cercana a cada spot (boyas.py), en cada ejecución.
  · Turbidez Kd490 por satélite (diaria, días nublados = sin dato), alrededor de cada spot: Copernicus OLCI 300 m en el
    Mediterráneo y multisensor 1 km en el Atlántico (NRT).

Los spots salen del catálogo de la app: los 6 de Cabo de Palos de mock.ts y el catálogo de toda España (spots.json,
generado por herramientas/spots/catalogo.py). Copernicus se descarga por ZONAS (grupos de spots cercanos).

Dos modos (PLAN-MVP.md, fase 2; se ejecutan en GitHub Actions, .github/workflows/prevision.yml):
  · completo (una vez al día): Copernicus + Open-Meteo. Guarda aparte la parte de Copernicus («copernicus»,
    con 24 h más de horizonte) para poder recombinarla después.
  · open-meteo (cada 6 h): solo oleaje y viento, recombinados con la última parte de Copernicus publicada.
Salida: la previsión completa («actual», 72 h hacia atrás y 72 h hacia delante, en UTC) en salida/prevision.json y,
con --publicar, en la API de Divy (PUT /api/interno/prevision/<clave>, con el token DIVY_FORECAST_TOKEN).
Uso:    .venv/bin/python -I pipeline.py [--modo completo|open-meteo] [--publicar https://joindivy.com ...]
Credenciales de Copernicus: las guardadas con `copernicusmarine login` o las variables
COPERNICUSMARINE_SERVICE_USERNAME y COPERNICUSMARINE_SERVICE_PASSWORD (en GitHub, como secretos).
Si una fuente falla, la previsión se genera igualmente con esos datos a null y un aviso en «warnings».
"""
import argparse
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'app/src/data/mock.ts'
CATALOG_ES = ROOT / 'app/src/data/spots.json'
CATALOG_WORLD = ROOT / 'herramientas/spots/mundo'  # <región>.json, generados con herramientas/spots/mundo.py
OUT_DIR = Path(__file__).resolve().parent / 'salida'
# Horas extra de Copernicus en el modo completo: la previsión de las 6 h siguientes reutiliza esta parte.
COP_EXTRA_H = 24
PAD = 0.15  # margen (°) alrededor de cada zona para encontrar la celda de mar
SAT_BOX = (0.09, 0.11)
HR_BOX = (0.009, 0.011)
WAVE_MAX_KM = 10  # celda de mar del modelo de oleaje como mucho a 10 km; si no, Open-Meteo para ese spot  # ~1 km alrededor del spot para la turbidez de 100 m  # medio lado (lat, lon) de la zona de satélite alrededor de cada spot (~20 km, como en el backtest)
warnings: list[str] = []
# Región de previsión de cada spot (catálogo mundial, 14 oct): la app descarga solo la región que mira. Los spots sin
# región (todo el catálogo de España de hoy) son «es», que también se publica como «actual» para la app anterior.
REGION: dict[str, str] = {}
GLOBAL_ZONE = 3.0  # lado (°) de las zonas de los modelos globales (rejilla de 9 km): menos peticiones que con 1°


def world_catalog():
    """Catálogo mundial (fuera de España) por regiones: {región: [spots]}."""
    return {f.stem: json.loads(f.read_text()) for f in sorted(CATALOG_WORLD.glob('*.json'))} if CATALOG_WORLD.exists() else {}


def spots_from_catalog(api=None, world=False):
    """Spots y coordenadas desde el catálogo de la app (una sola fuente de verdad). El proceso se ejecuta en un repositorio
    público aparte, sin la app (14 oct): ahí la lista se pide a la API de Divy, donde la sube el repositorio de la app."""
    if not CATALOG.exists():
        if not api:
            sys.exit('Sin el catálogo de la app: indica --publicar para leer los spots de la API de Divy')
        return from_list(json.loads(api_request(api, 'spots'))['spots'])
    src = CATALOG.read_text()
    found = re.findall(r"id: '([a-z0-9-]+)',(?:(?!id: ').)*?coord: \{ lat: ([-\d.]+), lng: ([-\d.]+) \}", src, flags=re.S)
    spots = {sid: (float(la), float(lo)) for sid, la, lo in found}
    if not spots:
        sys.exit('No se encontraron spots con coordenadas en mock.ts')
    for s in json.loads(CATALOG_ES.read_text()):
        spots[s['id']] = (s['coord']['lat'], s['coord']['lng'])
    if world:
        for region, items in world_catalog().items():
            for s in items:
                spots[s['id']] = (s['coord']['lat'], s['coord']['lng'])
                REGION[s['id']] = region
    return spots


def from_list(items):
    """Spots de una lista de la API ({id, lat, lng, region?}); guarda su región en REGION."""
    for s in items:
        REGION[s['id']] = s.get('region') or 'es'
    return {s['id']: (s['lat'], s['lng']) for s in items}


def region_of(sid):
    return REGION.get(sid, 'es')


IBI_BOX = (26.0, 56.0, -19.0, 5.0)  # lat mín, lat máx, lon mín, lon máx del modelo IBI (incluye Canarias)


def kind(pos):
    """Modelo físico de la zona: MEDSEA (Mediterráneo), IBI (Atlántico ibérico y Canarias) o el global (resto)."""
    if is_med(pos):
        return 'med'
    if IBI_BOX[0] <= pos[0] <= IBI_BOX[1] and IBI_BOX[2] <= pos[1] <= IBI_BOX[3]:
        return 'ibi'
    return 'glo'


def is_med(pos):
    """Mediterráneo (MEDSEA y satélite MED): al este del Estrecho y al oeste de Oriente Próximo (36,3° E), sin Canarias,
    el Cantábrico ni el mar Negro. Antes del catálogo mundial bastaba con la latitud: Japón caía «en el Mediterráneo»."""
    la, lo = pos
    if not (-5.4 < lo < 36.3 and 30.2 < la < 45.9):
        return False
    return not (la > 40.9 and lo > 26.9)  # mar Negro y mar de Mármara


def zones(spots, size=1.0):
    """Agrupa spots cercanos para descargar Copernicus por zonas: cajas de `size` grados con los modelos regionales y de
    GLOBAL_ZONE con el global (sus celdas son más grandes). Cada zona es de un solo modelo (`kind`)."""
    out = []
    for sid, (la, lo) in sorted(spots.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        k = kind((la, lo))
        lim = GLOBAL_ZONE if k == 'glo' else size
        for z in out:
            b = z['box']
            nb = (min(b[0], la), max(b[1], la), min(b[2], lo), max(b[3], lo))
            if nb[1] - nb[0] <= lim and nb[3] - nb[2] <= lim and z['kind'] == k:
                z['box'], z['ids'] = nb, z['ids'] + [sid]
                break
        else:
            out.append({'box': (la, la, lo, lo), 'ids': [sid], 'kind': k})
    for z in out:
        b = z['box']
        z['bounds'] = dict(minimum_latitude=b[0] - PAD, maximum_latitude=b[1] + PAD, minimum_longitude=b[2] - PAD, maximum_longitude=b[3] + PAD)
        z['med'] = z['kind'] == 'med'
        # ¿Toda la zona dentro de IBI? (en el Mediterráneo, IBI solo llega a Baleares; antes se pedía en todas)
        z['inIbi'] = all(IBI_BOX[0] <= spots[s][0] <= IBI_BOX[1] and IBI_BOX[2] <= spots[s][1] <= IBI_BOX[3] for s in z['ids'])
    return out


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(d))


def get_json(url, tries=4):
    """GET JSON con reintentos: ante «demasiadas peticiones» (429) o un fallo de red, espera y vuelve a probar."""
    import time
    for n in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if n == tries - 1:
                raise
            wait = 65 if '429' in str(e) else 10 * (n + 1)
            print(f'    reintento en {wait} s ({str(e)[:60]})')
            time.sleep(wait)


def clean(v, nd=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)


OM_BATCH = 100  # spots por petición a Open-Meteo (una URL con 500 coordenadas es demasiado larga)
# Plan gratuito de Open-Meteo: 600 llamadas/min, 5.000/h y 10.000/día, y cada coordenada cuenta como una llamada. Un lote
# de 100 spots en dos APIs son ~200: se espera entre lotes para quedar por debajo de 600/min.
OM_PAUSE_S = 25
# Spots que se pueden pedir a Open-Meteo en cada ejecución (10.000 llamadas/día entre 8 ejecuciones, con margen).
OM_MAX_PER_RUN = 1100


def open_meteo(spots, start, end, marine_for=None, wind_for=None):
    """Viento (y oleaje y corriente de respaldo) hora a hora por spot (UTC), por lotes. `marine_for`: spots que necesitan
    la API marina (los que no tienen oleaje o corriente de Copernicus); `wind_for`: los que necesitan viento (fuera de
    ICON-EU, viento.py); None = todos."""
    out = {sid: {} for sid in spots}
    ids = list(spots)
    import time
    for k in range(0, len(ids), OM_BATCH):
        batch = ids[k:k + OM_BATCH]
        if not any((marine_for is None or sid in marine_for) or (wind_for is None or sid in wind_for) for sid in batch):
            continue
        if k:
            time.sleep(OM_PAUSE_S)
        mbatch = [sid for sid in batch if marine_for is None or sid in marine_for]
        mlats = ','.join(str(spots[sid][0]) for sid in mbatch)
        mlons = ','.join(str(spots[sid][1]) for sid in mbatch)
        mcommon = f'latitude={mlats}&longitude={mlons}&timezone=GMT&start_hour={start:%Y-%m-%dT%H:00}&end_hour={end:%Y-%m-%dT%H:00}'
        try:
            if not mbatch:
                raise StopIteration
            # Además del oleaje, la corriente superficial (tercera vía de corriente, sin cuenta: si Copernicus faltara del
            # todo) y la temperatura del agua en superficie (14 oct).
            marine = get_json(f'https://marine-api.open-meteo.com/v1/marine?{mcommon}&hourly=wave_height,wave_direction,wave_period,ocean_current_velocity,ocean_current_direction,sea_surface_temperature&cell_selection=sea')
            for sid, m in zip(mbatch, marine if isinstance(marine, list) else [marine]):
                h = m['hourly']
                for t, hs, d, tp, cv, cd, sst in zip(h['time'], h['wave_height'], h['wave_direction'], h['wave_period'],
                                                      h['ocean_current_velocity'], h['ocean_current_direction'], h['sea_surface_temperature']):
                    out[sid].setdefault(t, {}).update(hs=clean(hs), dir=clean(d, 0), tp=clean(tp, 1), cOm=clean(cv, 2), cOmDir=clean(cd, 0), sst=clean(sst, 1))
        except StopIteration:
            pass
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Open-Meteo Marine (lote {k // OM_BATCH + 1}): {e}')
        wbatch = [sid for sid in batch if wind_for is None or sid in wind_for]
        common = f'latitude={",".join(str(spots[sid][0]) for sid in wbatch)}&longitude={",".join(str(spots[sid][1]) for sid in wbatch)}&timezone=GMT&start_hour={start:%Y-%m-%dT%H:00}&end_hour={end:%Y-%m-%dT%H:00}'
        if not wbatch:
            continue
        try:
            weather = get_json(f'https://api.open-meteo.com/v1/forecast?{common}&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m&wind_speed_unit=kmh')
            for sid, w in zip(wbatch, weather if isinstance(weather, list) else [weather]):
                h = w['hourly']
                for t, sp_, g, d in zip(h['time'], h['wind_speed_10m'], h['wind_gusts_10m'], h['wind_direction_10m']):
                    out[sid].setdefault(t, {}).update(wind=clean(sp_, 0), gust=clean(g, 0), wdir=clean(d, 0))
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Open-Meteo Forecast (lote {k // OM_BATCH + 1}): {e}')
    return out


def nearest_sea_cell(da, pos):
    """(índice lat, índice lon, distancia km) de la celda con dato más cercana a `pos`."""
    sea = np.isfinite(da.values)
    best = None
    for i, la in enumerate(da.latitude.values):
        for j, lo in enumerate(da.longitude.values):
            if sea[i, j]:
                d = km(pos, (float(la), float(lo)))
                if best is None or d < best[2]:
                    best = (i, j, d)
    return best


COP_AUTH = 'https://auth.marine.copernicus.eu/realms/MIS/.well-known/openid-configuration'


def copernicus_up():
    """¿Responde el servidor de acceso de Copernicus? Si no, no tiene sentido intentar cada zona (13 oct: caída)."""
    try:
        with urllib.request.urlopen(COP_AUTH, timeout=20) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001
        warnings.append(f'Copernicus: el servidor de acceso no responde ({str(e)[:80]})')
        return False


def copernicus(spots, start, end, direct=False):
    """Corriente, perfiles y satélite por zonas. `direct`: leer de la nube de Copernicus sin su servidor de acceso (arco.py)."""
    import copernicusmarine as cm

    import xarray as xr

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from arco import open_arco

    opener = open_arco if direct else cm.open_dataset
    if direct:
        print('Copernicus: lectura directa (servidor de acceso caído)')

    res = {sid: {'currents': {}, 'waves': {}, 'profiles': [], 'seaCell': {}, 'satellite': [], 'hr': []} for sid in spots}
    t0, t1 = start.strftime('%Y-%m-%dT%H:00:00'), end.strftime('%Y-%m-%dT%H:00:00')
    s0 = (start - timedelta(days=1)).strftime('%Y-%m-%dT00:00:00')
    zs = zones(spots)
    print(f'{len(spots)} spots en {len(zs)} zonas')

    def each_zone(label, ds_id, kinds, fn, **kw):
        """Descarga `ds_id` en las zonas cuyo modelo está en `kinds` ({'med', 'ibi', 'glo'}; None = todas; o una
        función que recibe la zona)."""
        # Las zonas se leen en paralelo (son descargas): la lectura directa tardaba 75 min de una en una (14 oct).
        from concurrent.futures import ThreadPoolExecutor

        def one(n, z):
            if not direct and any('CouldNotConnectToAuthenticationSystem' in w for w in warnings):
                return  # el acceso ha fallado a mitad: no se reintenta zona por zona
            try:
                ds = opener(dataset_id=ds_id, **kw, **z['bounds'])
                fn(ds, z)
            except Exception as e:  # noqa: BLE001
                warnings.append(f'Copernicus {label} (zona {n + 1}: {", ".join(z["ids"][:2])}…): {type(e).__name__} {str(e)[:160]}')

        ok = kinds if callable(kinds) else (lambda z: kinds is None or z['kind'] in kinds)
        todo = [(n, z) for n, z in enumerate(zs) if ok(z)]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda a: one(*a), todo))
        print('  ·', label)

    # Corriente horaria a ~10 m: IBI en todas partes, MEDSEA en el Mediterráneo. Fuera, el global (cada 6 h, que se
    # reparte hora a hora interpolando u y v).
    def currents(tag):
        def fn(ds, z):
            u, v = ds.uo.isel(depth=0).load(), ds.vo.isel(depth=0).load()
            if tag == 'glo':
                # Interpolación lineal a horas con numpy (xarray.interp necesita scipy, que no está en el entorno).
                t_in = u.time.values.astype('datetime64[s]').astype(np.int64)
                hourly = np.arange(u.time.values[0], u.time.values[-1] + np.timedelta64(1, 'h'), np.timedelta64(1, 'h'))
                t_out = hourly.astype('datetime64[s]').astype(np.int64)
                lerp = lambda a: np.apply_along_axis(lambda col: np.interp(t_out, t_in, col), 0, a)  # noqa: E731
                u = xr.DataArray(lerp(u.values), coords={'time': hourly, 'latitude': u.latitude, 'longitude': u.longitude}, dims=('time', 'latitude', 'longitude'))
                v = xr.DataArray(lerp(v.values), coords={'time': hourly, 'latitude': v.latitude, 'longitude': v.longitude}, dims=('time', 'latitude', 'longitude'))
            sp = np.hypot(u, v) * 3.6  # km/h
            # Dirección hacia la que va la corriente (grados desde el norte, como en la brújula de la app).
            dr = (np.degrees(np.arctan2(u, v)) + 360) % 360
            for sid in z['ids']:
                cell = nearest_sea_cell(sp.isel(time=0), spots[sid])
                if cell is None:
                    continue
                i, j, d = cell
                res[sid]['seaCell'][tag] = round(d, 1)
                for t, val, ang in zip(sp.time.values, sp.isel(latitude=i, longitude=j).values, dr.isel(latitude=i, longitude=j).values):
                    h = res[sid]['currents'].setdefault(str(t)[:13] + ':00', {})
                    h[tag] = clean(val)
                    h[tag + 'Dir'] = clean(ang, 0)
        return fn

    cur = dict(variables=['uo', 'vo'], start_datetime=t0, end_datetime=t1, minimum_depth=8, maximum_depth=12)
    each_zone('corrientes med', 'cmems_mod_med_phy-cur_anfc_4.2km-3D_PT1H-m', {'med'}, currents('med'), **cur)
    each_zone('corrientes ibi', 'cmems_mod_ibi_phy_anfc_0.027deg-3D_PT1H-m', lambda z: z['inIbi'], currents('ibi'), **cur)
    each_zone('corrientes global', 'cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i', {'glo'}, currents('glo'), **cur)

    # Oleaje horario (14 oct, Luka: sin límite de llamadas y de la misma fuente oficial): MEDSEA (4,2 km) en el
    # Mediterráneo, IBI (2,8 km, también Canarias) y el global (9 km, cada 3 h) para lo que quede fuera.
    def waves(tag):
        def fn(ds, z):
            hs, dr, tp = ds.VHM0.load(), ds.VMDR.load(), ds.VTPK.load()
            if tag == 'glo':
                # El global es cada 3 h: se reparte hora a hora (15 oct: sin esto, 2 de cada 3 horas quedaban sin oleaje y
                # la app daba «sin datos»). Altura y periodo, lineales; la dirección, por sus componentes.
                t_in = hs.time.values.astype('datetime64[s]').astype(np.int64)
                hourly = np.arange(hs.time.values[0], hs.time.values[-1] + np.timedelta64(1, 'h'), np.timedelta64(1, 'h'))
                t_out = hourly.astype('datetime64[s]').astype(np.int64)
                lerp = lambda a: np.apply_along_axis(lambda col: np.interp(t_out, t_in, col), 0, a)  # noqa: E731
                like = lambda a: xr.DataArray(a, coords={'time': hourly, 'latitude': hs.latitude, 'longitude': hs.longitude}, dims=('time', 'latitude', 'longitude'))  # noqa: E731
                rad = np.radians(dr.values)
                dr = like((np.degrees(np.arctan2(lerp(np.sin(rad)), lerp(np.cos(rad)))) + 360) % 360)
                hs, tp = like(lerp(hs.values)), like(lerp(tp.values))
            for sid in z['ids']:
                cell = nearest_sea_cell(hs.isel(time=0), spots[sid])
                if cell is None or cell[2] > WAVE_MAX_KM:
                    continue
                i, j, d = cell
                res[sid]['seaCell']['wav' + tag.capitalize()] = round(d, 1)
                for t, a, b, c in zip(hs.time.values, hs.isel(latitude=i, longitude=j).values, dr.isel(latitude=i, longitude=j).values, tp.isel(latitude=i, longitude=j).values):
                    res[sid]['waves'].setdefault(str(t)[:13] + ':00', {})[tag] = [clean(a), clean(b, 0), clean(c, 1)]
        return fn

    wav = dict(variables=['VHM0', 'VMDR', 'VTPK'], start_datetime=t0, end_datetime=t1)
    each_zone('oleaje med', 'cmems_mod_med_wav_anfc_4.2km_PT1H-i', {'med'}, waves('med'), **wav)
    each_zone('oleaje ibi', 'cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i', lambda z: z['inIbi'], waves('ibi'), **wav)
    # El global, solo en las zonas con algún spot aún sin oleaje (fuera de MEDSEA/IBI, o lejos de su costa).
    each_zone('oleaje global', 'cmems_mod_glo_wav_anfc_0.083deg_PT3H-i', lambda z: any(not res[sid]['waves'] for sid in z['ids']), waves('glo'), **wav)

    # Perfil diario de temperatura hasta 60 m: MEDSEA en el Mediterráneo, IBI en el Atlántico.
    def profiles(ds, z):
        th = ds.thetao.load()
        for sid in z['ids']:
            cell = nearest_sea_cell(th.isel(time=0, depth=0), spots[sid])
            if cell is None:
                continue
            i, j, d = cell
            res[sid]['seaCell']['temp'] = round(d, 1)
            for k, t in enumerate(th.time.values):
                col = th.isel(time=k, latitude=i, longitude=j).values
                levels = [[round(float(zz), 1), round(float(v), 1)] for zz, v in zip(th.depth.values, col) if np.isfinite(v)]
                res[sid]['profiles'].append({'date': str(t)[:10], 'levels': levels})

    tem = dict(variables=['thetao'], start_datetime=t0, end_datetime=t1, minimum_depth=0, maximum_depth=60)
    each_zone('temperatura med', 'cmems_mod_med_phy-tem_anfc_4.2km_P1D-m', {'med'}, profiles, **tem)
    each_zone('temperatura ibi', 'cmems_mod_ibi_phy_anfc_0.027deg-3D_P1D-m', {'ibi'}, profiles, **tem)
    each_zone('temperatura global', 'cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m', {'glo'}, profiles, **tem)

    # Satélite: mediana de Kd490 alrededor de cada spot por día (al menos el 20 % de la zona sin nubes).
    def satellite(ds, z):
        kd = ds.KD490.load()
        for sid in z['ids']:
            la, lo = spots[sid]
            box = kd.sel(latitude=slice(la - SAT_BOX[0], la + SAT_BOX[0]), longitude=slice(lo - SAT_BOX[1], lo + SAT_BOX[1]))
            if box.latitude.size == 0:  # latitudes en orden descendente
                box = kd.sel(latitude=slice(la + SAT_BOX[0], la - SAT_BOX[0]), longitude=slice(lo - SAT_BOX[1], lo + SAT_BOX[1]))
            for k, t in enumerate(box.time.values):
                vals = box.isel(time=k).values
                valid = float(np.isfinite(vals).mean()) if vals.size else 0.0
                res[sid]['satellite'].append({'date': str(t)[:10], 'kd': clean(np.nanmedian(vals), 3) if valid >= 0.2 else None, 'validFraction': round(valid, 2)})

    # Turbidez de alta resolución (Sentinel-2, 100 m, franja de 20 km de costa europea): mediana en ~1 km alrededor del
    # spot, más fina que el satélite regional (FUENTES-DATOS.md, paso 1).
    def turbidity_hr(ds, z):
        tur = ds.TUR
        for sid in z['ids']:
            la, lo = spots[sid]
            box = tur.sel(latitude=slice(la - HR_BOX[0], la + HR_BOX[0]), longitude=slice(lo - HR_BOX[1], lo + HR_BOX[1]))
            if box.latitude.size == 0:
                box = tur.sel(latitude=slice(la + HR_BOX[0], la - HR_BOX[0]), longitude=slice(lo - HR_BOX[1], lo + HR_BOX[1]))
            box = box.load()
            for k, t in enumerate(box.time.values):
                vals = box.isel(time=k).values
                valid = float(np.isfinite(vals).mean()) if vals.size else 0.0
                if valid >= 0.1:
                    res[sid]['hr'].append({'date': str(t)[:10], 'tur': clean(np.nanmedian(vals), 2), 'validFraction': round(valid, 2)})

    hr_kw = dict(variables=['TUR'], start_datetime=s0, end_datetime=t1)
    each_zone('turbidez HR med', 'cmems_obs_oc_med_bgc_tur-spm-chl_nrt_l3-hr-mosaic_P1D-m', {'med'}, turbidity_hr, **hr_kw)
    # La alta resolución de IBI no llega al norte de Europa (la prueba mundial del 14 oct falló por encima de ~49° N).
    each_zone('turbidez HR ibi', 'cmems_obs_oc_ibi_bgc_tur-spm-chl_nrt_l3-hr-mosaic_P1D-m', lambda z: z['kind'] == 'ibi' and z['box'][1] <= 49, turbidity_hr, **hr_kw)

    sat = dict(variables=['KD490'], start_datetime=s0, end_datetime=t1)
    each_zone('satélite med', 'cmems_obs-oc_med_bgc-transp_nrt_l3-olci-300m_P1D', {'med'}, satellite, **sat)
    each_zone('satélite atl', 'cmems_obs-oc_atl_bgc-transp_nrt_l3-multi-1km_P1D', {'ibi'}, satellite, **sat)
    each_zone('satélite global', 'cmems_obs-oc_glo_bgc-transp_nrt_l3-multi-4km_P1D', {'glo'}, satellite, **sat)
    return res


def iso(t):
    return t.isoformat().replace('+00:00', 'Z')


SOURCES = {
    'oleaje': 'Copernicus Marine MEDSEA_ANALYSISFORECAST_WAV_006_017, IBI_ANALYSISFORECAST_WAV_005_005 y GLOBAL_ANALYSISFORECAST_WAV_001_027; respaldo: Open-Meteo Marine',
    'viento': 'DWD ICON-EU (Europa, ~7 km); fuera de Europa: ECMWF Open Data (IFS 0,25°); Canarias y huecos: Open-Meteo Forecast',
    'temperatura': 'Copernicus Marine MEDSEA_ANALYSISFORECAST_PHY_006_013 (Mediterráneo), IBI_ANALYSISFORECAST_PHY_005_001 (Atlántico ibérico) y GLOBAL_ANALYSISFORECAST_PHY_001_024 (resto del mundo, 1/12°)',
    'corriente': 'Copernicus Marine MEDSEA, IBI_ANALYSISFORECAST_PHY_005_001 y GLOBAL_ANALYSISFORECAST_PHY_001_024 (~10 m; el global, cada 6 h interpolado); respaldo: Open-Meteo Marine (superficie)',
    'satélite': 'Copernicus Marine OCEANCOLOUR_MED_BGC_L3_NRT_009_141 (OLCI 300 m), OCEANCOLOUR_ATL_BGC_L3_NRT_009_111 (multisensor 1 km) y OCEANCOLOUR_GLO_BGC_L3_NRT_009_101 (multisensor 4 km, resto del mundo)',
    'satéliteHR': 'Copernicus Marine OCEANCOLOUR_MED/IBI_BGC_HR_L3_NRT_009_205/204 (Sentinel-2, 100 m)',
    'satéliteGlobal': 'NASA PACE OCI PACE_OCI_L3M_KD_NRT (4 km)',
    'avisos': 'AEMET (CAP, Plan Meteoalerta)',
    'boyas': 'Copernicus Marine In Situ INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030',
}


# Columnas por spot, alineadas al eje horario común (window.from + i horas). Formato v4: unas 7 veces más pequeño que
# un objeto por hora, que con 500 spots no se podía descargar al abrir la app.
COLUMNS = ('hs', 'dir', 'tp', 'wind', 'gust', 'wdir', 'cMed', 'cIbi', 'cGlo', 'cDir', 'cOm', 'sst')


# Visibilidad estimada (m) a partir de cada medida. Provisional: se calibra con los reportes (backtest, REGLAS v0.5).
#   · Kd490 (m⁻¹): visibilidad horizontal ≈ 0,6 / Kd (0,03 → 20 m; 0,045 → 13 m; 0,064 → 9 m), coherente con los
#     umbrales de REGLAS-CONDICIONES (clara < 0,045 ≤ normal < 0,064 ≤ turbia).
#   · Turbidez (FNU): ≈ 7 / TUR^0,7 (0,3 → 16 m; 1 → 7 m; 2 → 4 m; 5 → 2 m).
def vis_from_kd(kd):
    return None if kd is None or kd <= 0 else round(min(30.0, 0.6 / kd), 1)


def vis_from_tur(tur):
    return None if tur is None or tur <= 0 else round(min(30.0, 7.0 / tur ** 0.7), 1)


# Prioridad de fuentes de visibilidad (Luka, 13 oct: «que cada spot tenga la información más fiable»): por día, la de
# mayor resolución cerca de la costa. Los reportes de buceadores van por encima de todas, pero se combinan en la app.
CLARITY_PRIORITY = ('hr', 'olci', 'pace')


def clarity(c):
    """Por día, la mejor medida de transparencia disponible con su fuente y su visibilidad estimada."""
    by_day = {}
    for row in c.get('hr', []):
        by_day.setdefault(row['date'], {})['hr'] = {'source': 'hr', 'tur': row['tur'], 'vis': vis_from_tur(row['tur'])}
    for row in c.get('satellite', []):
        if row.get('kd') is not None:
            by_day.setdefault(row['date'], {})['olci'] = {'source': 'olci', 'kd': row['kd'], 'vis': vis_from_kd(row['kd'])}
    for row in c.get('pace', []):
        if row.get('kd') is not None:
            by_day.setdefault(row['date'], {})['pace'] = {'source': 'pace', 'kd': row['kd'], 'vis': vis_from_kd(row['kd'])}
    out = []
    for day in sorted(by_day):
        best = next((by_day[day][k] for k in CLARITY_PRIORITY if k in by_day[day] and by_day[day][k]['vis'] is not None), None)
        if best:
            out.append({'date': day, **best})
    return out


def merge(spots, om, cop, now, start, end, alerts=None, buoy=None, wind=None, prev_wind=None):
    """Previsión que usa la app: columnas horarias (Open-Meteo + corriente de Copernicus), perfiles y satélite."""
    n = int((end - start).total_seconds() // 3600) + 1
    keys = [(start + timedelta(hours=i)).strftime('%Y-%m-%dT%H:00') for i in range(n)]
    out_spots = {}
    for sid in spots:
        c = cop.get('spots', {}).get(sid, {})
        cols = {k: [] for k in COLUMNS}
        for t in keys:
            row = dict(om[sid].get(t, {}))
            # Viento: ICON-EU o ECMWF (viento.py) > Open-Meteo > el de la previsión anterior (horas pasadas, que los
            # modelos ya no publican).
            w = (wind or {}).get(sid, {}).get(t)
            if w:
                row.update(w)
            elif row.get('wind') is None and (prev_wind or {}).get(sid, {}).get(t):
                row.update(prev_wind[sid][t])
            # Oleaje: Copernicus (MEDSEA > IBI > global) si llega al spot; si no, Open-Meteo.
            wv = c.get('waves', {}).get(t, {})
            best = next((wv[k] for k in ('med', 'ibi', 'glo') if k in wv and wv[k][0] is not None), None)
            if best:
                row.update(hs=best[0], dir=best[1], tp=best[2])
            cur = c.get('currents', {}).get(t, {})
            # Dirección: la del modelo más fino disponible (MEDSEA en el Mediterráneo, si no IBI, si no el global).
            c_dir = next((cur.get(k + 'Dir') for k in ('med', 'ibi', 'glo') if cur.get(k) is not None), row.get('cOmDir'))
            vals = {**row, 'cMed': cur.get('med'), 'cIbi': cur.get('ibi'), 'cGlo': cur.get('glo'), 'cDir': c_dir}
            for k in COLUMNS:
                cols[k].append(vals.get(k))
        out_spots[sid] = {'seaCellKm': c.get('seaCell', {}), **cols, 'profiles': c.get('profiles', []), 'satellite': c.get('satellite', []), 'clarity': clarity(c)}
        if alerts and alerts.get(sid):
            out_spots[sid]['alerts'] = alerts[sid]
        if buoy and buoy.get(sid):
            out_spots[sid]['buoy'] = buoy[sid]
    return {
        'version': 4,
        'alertsSource': 'AEMET (CAP, Plan Meteoalerta)',
        'generatedAt': iso(now),
        'copernicusAt': cop.get('generatedAt'),
        'window': {'from': iso(start), 'to': iso(end), 'hours': n},
        'sources': SOURCES,
        'warnings': warnings + [w for w in cop.get('warnings', []) if w not in warnings],
        'spots': out_spots,
    }


def out_name(region):
    """Archivo de salida de una región (España conserva el nombre de siempre)."""
    return 'prevision.json' if region == 'es' else f'prevision-{region}.json'


def summary(snapshots, seconds):
    """Cobertura por región de la prueba con la muestra: porcentaje de spots con cada dato y tamaño de la salida."""
    out = {'minutos': round(seconds / 60, 1), 'regiones': {}}
    for r, snap in snapshots.items():
        ss = list(snap['spots'].values())
        n = len(ss) or 1
        has = lambda f: round(100 * sum(1 for s in ss if f(s)) / n)  # noqa: E731
        size = len(json.dumps(snap, ensure_ascii=False, separators=(',', ':')).encode())
        out['regiones'][r] = {
            'spots': len(ss),
            'oleaje %': has(lambda s: any(v is not None for v in s['hs'])),
            'viento %': has(lambda s: any(v is not None for v in s['wind'])),
            'corriente %': has(lambda s: any(v is not None for k in ('cMed', 'cIbi', 'cGlo') for v in s[k])),
            'temperatura %': has(lambda s: bool(s['profiles'])),
            'visibilidad %': has(lambda s: bool(s['clarity'])),
            'KB': size // 1024,
        }
    out['avisos'] = warnings[:40]
    return out


def api_request(base, key, method='GET', body=None):
    token = os.environ.get('DIVY_FORECAST_TOKEN')
    if not token:
        sys.exit('Falta DIVY_FORECAST_TOKEN para hablar con la API de Divy')
    req = urllib.request.Request(f'{base.rstrip("/")}/api/interno/prevision/{key}', method=method, data=body,
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'User-Agent': 'divy-pipeline'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def publish(urls, key, data):
    body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    for base in urls:
        try:
            api_request(base, key, 'PUT', body)
            print(f'  · publicado «{key}» en {base} ({len(body) // 1024} KB)')
        except Exception as e:  # noqa: BLE001
            sys.exit(f'No se pudo publicar «{key}» en {base}: {e}')


def main():
    ap = argparse.ArgumentParser(description='Previsión marina de Divy')
    ap.add_argument('--modo', choices=['completo', 'open-meteo'], default='completo')
    ap.add_argument('--publicar', nargs='*', default=[], metavar='URL', help='API de Divy donde publicar (p. ej. https://joindivy.com)')
    ap.add_argument('--forzar', action='store_true', help='generar aunque la previsión publicada sea reciente')
    ap.add_argument('--subir-spots', nargs='+', metavar='URL', help='solo subir la lista de spots del catálogo a la API (repositorio de la app)')
    ap.add_argument('--con-mundo', action='store_true', help='con --subir-spots: incluir el catálogo mundial y subir sus spots por región')
    ap.add_argument('--muestra', metavar='URL', help='probar con la muestra mundial de la API («spots-muestra»): genera y mide, sin publicar nada')
    args = ap.parse_args()
    import time
    t_start = time.time()

    if args.subir_spots:
        # Con --con-mundo, el proceso también genera la previsión del resto del mundo y la app recibe sus spots por región.
        cat = [{'id': k, 'lat': v[0], 'lng': v[1], 'region': region_of(k)} for k, v in spots_from_catalog(world=args.con_mundo).items()]
        stamp = iso(datetime.now(timezone.utc))
        body = json.dumps({'generatedAt': stamp, 'spots': cat}).encode()
        for base in args.subir_spots:
            api_request(base, 'spots', 'PUT', body)
            print(f'  · {len(body) // 1024} KB de spots subidos a {base}')
            if args.con_mundo:
                for region, items in world_catalog().items():
                    api_request(base, f'catalogo-{region}', 'PUT', json.dumps({'generatedAt': stamp, 'region': region, 'spots': items}, ensure_ascii=False).encode())
                    print(f'  · catálogo «{region}» ({len(items)} spots) subido a {base}')
        return
    if args.muestra:
        spots = from_list(json.loads(api_request(args.muestra, 'spots-muestra'))['spots'])
        args.publicar, args.modo = [], 'completo'
    else:
        spots = spots_from_catalog(args.publicar[0] if args.publicar else None)
    regions = sorted({region_of(sid) for sid in spots})
    print(f'{len(spots)} spots en {len(regions)} regiones: {", ".join(regions)}')
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Sin duplicados: lo pueden lanzar el temporizador del Worker y el de GitHub. Si la previsión publicada ya es de esta
    # misma franja, no se repite (ahorra llamadas a Open-Meteo y a Copernicus).
    if args.publicar and not args.forzar:
        try:
            pub = json.loads(urllib.request.urlopen(urllib.request.Request(f'{args.publicar[0].rstrip("/")}/api/prevision', headers={'User-Agent': 'divy-pipeline'}), timeout=60).read())
            gen = datetime.fromisoformat(pub['generatedAt'].replace('Z', '+00:00'))
            cop_at = pub.get('copernicusAt')
            fresh_om = now - gen < timedelta(minutes=100)
            fresh_cop = cop_at and now - datetime.fromisoformat(cop_at.replace('Z', '+00:00')) < timedelta(hours=3)
            if fresh_om and (args.modo == 'open-meteo' or fresh_cop):
                print(f'La previsión publicada ya es reciente ({pub["generatedAt"]}, Copernicus {cop_at}): no se repite')
                return
        except Exception as e:  # noqa: BLE001
            print(f'No se pudo comprobar la previsión publicada ({e}); se genera igualmente')
    start, end = now - timedelta(hours=72), now + timedelta(hours=72)
    OUT_DIR.mkdir(exist_ok=True)
    cop_file = OUT_DIR / 'copernicus.json'

    def previous_cop():
        """Última parte de Copernicus publicada, juntando la de cada región (API en GitHub Actions; en local, la del
        último modo completo). España también puede estar aún en la clave antigua «copernicus»."""
        try:
            if not args.publicar:
                return json.loads(cop_file.read_text()) if cop_file.exists() else None
            out = None
            for r in regions:
                part = None
                for key in (f'copernicus-{r}', 'copernicus') if r == 'es' else (f'copernicus-{r}',):
                    try:
                        part = json.loads(api_request(args.publicar[0], key))
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if part is None:
                    warnings.append(f'Sin parte de Copernicus anterior de la región {r}')
                    continue
                if out is None:
                    out = {'generatedAt': part.get('generatedAt'), 'warnings': [], 'spots': {}}
                out['spots'].update(part.get('spots', {}))
            return out
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Sin parte de Copernicus anterior ({e})')
        return None

    def previous_wind():
        """Viento de las horas pasadas de la última previsión publicada (ICON-EU y ECMWF solo dan horas futuras)."""
        try:
            pubs = []
            for r in regions:
                try:
                    if args.publicar:
                        path = '/api/prevision' if r == 'es' else f'/api/prevision/{r}'
                        req = urllib.request.Request(f'{args.publicar[0].rstrip("/")}{path}', headers={'User-Agent': 'divy-pipeline'})
                        pubs.append(json.loads(urllib.request.urlopen(req, timeout=60).read()))
                    elif (OUT_DIR / out_name(r)).exists():
                        pubs.append(json.loads((OUT_DIR / out_name(r)).read_text()))
                except Exception:  # noqa: BLE001
                    continue  # región nueva: aún sin previsión anterior
            out = {}
            for pub in pubs:
                t0 = datetime.fromisoformat(pub['window']['from'].replace('Z', '+00:00'))
                for sid, s in pub.get('spots', {}).items():
                    rows = {}
                    for i, v in enumerate(s.get('wind', [])):
                        t = t0 + timedelta(hours=i)
                        if v is not None and t < now:
                            rows[t.strftime('%Y-%m-%dT%H:00')] = {'wind': v, 'gust': s['gust'][i], 'wdir': s['wdir'][i]}
                    out[sid] = rows
            return out
        except Exception as e:  # noqa: BLE001
            warnings.append(f'Sin viento anterior para las horas pasadas ({str(e)[:80]})')
            return {}

    fresh_cop = False
    if args.modo == 'completo':
        # Si su servidor de acceso no responde, se leen los mismos datos directamente de su nube (arco.py, 14 oct).
        cop_spots = copernicus(spots, start, end + timedelta(hours=COP_EXTRA_H), direct=not copernicus_up())
        with_data = sum(1 for v in cop_spots.values() if v['currents'] or v['profiles'])  # 0 si no se pudo ni empezar
        if with_data >= 0.5 * len(spots):
            cop = {'generatedAt': iso(now), 'warnings': list(warnings), 'spots': {sid: {
                'seaCell': v['seaCell'], 'currents': v['currents'], 'waves': v['waves'], 'profiles': v['profiles'], 'satellite': v['satellite'], 'hr': v['hr']} for sid, v in cop_spots.items()}}
            fresh_cop = True
        else:
            # Fiabilidad (Luka, 13 oct): si Copernicus falla (p. ej. caída de su servidor de acceso), no se publica una
            # previsión vacía: se mantiene la última parte buena y se avisa.
            cop = previous_cop() or {'generatedAt': None, 'warnings': [], 'spots': {}}
            warnings.append(f'Copernicus no disponible ({with_data} de {len(spots)} spots con datos): se mantiene la parte de {cop.get("generatedAt")}')
            print(warnings[-1])
        # NASA PACE (global, 4 km): relleno de los días sin satélite de Copernicus. Va aparte: se descarga aunque Copernicus
        # esté caído y se guarda con su parte para las ejecuciones de Open-Meteo.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from pace import pace_kd
        pace = pace_kd(spots, now, 4, SAT_BOX, warnings)
        for sid, rows in pace.items():
            cop['spots'].setdefault(sid, {})['pace'] = rows
        if pace:
            fresh_cop = True  # hay algo nuevo que guardar, aunque la parte de Copernicus sea la anterior
        if fresh_cop:
            cop_file.write_text(json.dumps(cop, ensure_ascii=False, separators=(',', ':')))
    else:
        # Última parte de Copernicus: de la API (en GitHub Actions) o, en local, del último modo completo.
        cop = previous_cop()
        if cop is None:
            # Sin modo completo previo: se publica oleaje y viento; temperatura, corriente y satélite quedan «sin datos».
            cop = {'generatedAt': None, 'warnings': [], 'spots': {}}
            if not warnings:
                warnings.append('Sin parte de Copernicus: solo oleaje y viento')
        print(f'Copernicus de {cop.get("generatedAt")}')

    # La API marina de Open-Meteo solo para los spots sin oleaje o sin corriente regional de Copernicus (14 oct).
    cs = cop.get('spots', {})
    marine_for = {sid for sid in spots if not cs.get(sid, {}).get('waves') or not cs.get(sid, {}).get('currents')}
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # con -I, la carpeta del script no está en el path
    # Viento por modelos descargados enteros (viento.py): ICON-EU en Europa y, fuera, ECMWF (global, sin límite de
    # llamadas; catálogo mundial, 14 oct). Open-Meteo, más fino que ECMWF, solo para España fuera de ICON-EU (Canarias)
    # y los huecos.
    from viento import ecmwf, icon_eu
    wind = icon_eu(spots, now, end, warnings)
    world = {sid: spots[sid] for sid in spots if not wind.get(sid) and region_of(sid) != 'es'}
    if world:
        wind.update(ecmwf(world, now, end, warnings))
        print(f'ECMWF: viento para {sum(1 for sid in world if wind.get(sid))} de {len(world)} spots fuera de Europa')
    wind_for = {sid for sid in spots if not wind.get(sid)}
    # Plan gratuito de Open-Meteo: 10.000 llamadas al día y 8 ejecuciones diarias. Si Copernicus o ECMWF fallan en todo
    # el mundo, no se puede pedir todo a Open-Meteo: primero España y, del resto, hasta completar el cupo.
    cupo = OM_MAX_PER_RUN - len(marine_for & {s for s in spots if region_of(s) == 'es'}) - len(wind_for & {s for s in spots if region_of(s) == 'es'})
    for name, group in (('Marine', marine_for), ('Forecast', wind_for)):
        rest = sorted(sid for sid in group if region_of(sid) != 'es')
        if len(rest) > max(0, cupo):
            group.difference_update(rest[max(0, cupo):])
            warnings.append(f'Open-Meteo {name}: {len(rest) - max(0, cupo)} spots fuera de España sin respaldo (cupo diario)')
        cupo -= min(len(rest), max(0, cupo))
    print(f'Open-Meteo Marine para {len(marine_for)} y Forecast para {len(wind_for)} de {len(spots)} spots')
    om = open_meteo(spots, start, end, marine_for, wind_for)
    no_wind = {sid: spots[sid] for sid in wind_for if not any(r.get('wind') is not None for r in om[sid].values())}
    wind.update(ecmwf(no_wind, now, end, warnings))
    from avisos import aemet  # avisos oficiales (FUENTES-DATOS.md, paso 2)
    alerts = aemet(spots, now, warnings)
    from boyas import buoys  # medidas reales de boyas (FUENTES-DATOS.md, paso 3)
    buoy = buoys(spots, now, warnings) if copernicus_up() else {}
    prev_wind = previous_wind()
    snapshots = {}
    for r in regions:
        ids = [sid for sid in spots if region_of(sid) == r]
        snapshots[r] = merge({sid: spots[sid] for sid in ids}, om, cop, now, start, end, alerts, buoy, wind, prev_wind)
        snapshots[r]['region'] = r
        (OUT_DIR / out_name(r)).write_text(json.dumps(snapshots[r], ensure_ascii=False, separators=(',', ':')))
    n_hours = sum(sum(v is not None for v in s['hs']) for snap in snapshots.values() for s in snap['spots'].values())
    print(f'OK salida/ · {len(spots)} spots · {n_hours} horas · avisos: {warnings or "ninguno"}')
    if args.muestra:
        resumen = summary(snapshots, time.time() - t_start)
        (OUT_DIR / 'resumen.json').write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
        print(json.dumps(resumen, ensure_ascii=False, indent=1))
        return
    # Sin oleaje ni viento la previsión no sirve: no se publica para no tapar la anterior.
    if n_hours == 0:
        sys.exit('Open-Meteo no devolvió horas: no se publica')
    if args.publicar:
        if args.modo == 'completo' and fresh_cop:
            # La parte de Copernicus, por regiones (con el mundo entero no cabría en una sola clave de KV).
            for r in regions:
                ids = {sid for sid in spots if region_of(sid) == r}
                publish(args.publicar, f'copernicus-{r}', {**cop, 'spots': {k: v for k, v in cop['spots'].items() if k in ids}})
        for r in regions:
            # España sigue en «actual» (la app anterior la lee de /api/prevision).
            publish(args.publicar, 'actual' if r == 'es' else f'actual-{r}', snapshots[r])
    print(f'Tiempo total: {(time.time() - t_start) / 60:.1f} min')


if __name__ == '__main__':
    main()
