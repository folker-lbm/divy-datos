"""
T139, fase en sombra: oleaje por trenes con la exposición de T126, calculado sin publicar nada.

Para cada spot con rayos de T126 (datos/t139-rayos.json.gz):
1. Descarga las particiones del oleaje de Copernicus (mar de viento y dos mares de fondo, con altura, dirección y
   periodo medio) en la MISMA celda de mar y con la MISMA prioridad de modelos que la previsión (MEDSEA > IBI >
   regional > global, celda a 10 km o menos; pipeline.py).
2. Lee la previsión publicada de producción de su región (GET /api/prevision/<región>, la que ve la app) para tener el
   viento de cada hora y comparar.
3. Calcula con trenes.py el mar en la entrada, el periodo del tren dominante y si cuenta el viento.

Escribe en salida/ (artefacto del flujo «Experimento», nunca en KV):
- `sombra-<región>.json`: por spot y hora (las horas de la previsión publicada): `hsE` (mar en la entrada), `tm`
  (periodo medio del tren dominante), `k` (factor del periodo en la entrada desde costa), `wexp` (1 si cuenta el viento),
  `tot` (oleaje total de la misma descarga: altura, dirección y periodo de pico, para comprobar que es la misma pasada
  del modelo y comparar con el motor de hoy sobre los mismos datos) y los trenes en la celda.
- `prev-<región>.json` y `catalogo-<región>.json`: la previsión y el catálogo publicados que se han usado, para
  comparar el color con el motor real en local (sin tocar la app ni la API).
- `t139-resumen.json`: tiempos, avisos y concordancia con la previsión publicada.

Uso: flujo «Experimento» con `script: experimento_t139.py` (credenciales de Copernicus y DIVY_FORECAST_TOKEN para leer la
lista de spots; no publica). En local: `.venv/bin/python -I experimento_t139.py --directo [--solo id,id] [--regiones r]`
(lectura directa de la nube de Copernicus, sin credenciales; la lista de spots sale de los catálogos públicos).
"""
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402
import trenes as T  # noqa: E402

API = 'https://joindivy.com'
OUT = Path(__file__).resolve().parent / 'salida'
REGIONES = ['es', 'africa', 'caribe', 'eu', 'filipinas', 'med', 'mrojo', 'norteamerica', 'oceania', 'sudamerica', 'sudeste']
VARS = ['VHM0', 'VMDR', 'VTPK', 'VHM0_WW', 'VMDR_WW', 'VTM01_WW', 'VHM0_SW1', 'VMDR_SW1', 'VTM01_SW1', 'VHM0_SW2', 'VMDR_SW2', 'VTM01_SW2']
TRENES = {'tot': ('VHM0', 'VMDR', 'VTPK'), 'ww': ('VHM0_WW', 'VMDR_WW', 'VTM01_WW'), 'sw1': ('VHM0_SW1', 'VMDR_SW1', 'VTM01_SW1'), 'sw2': ('VHM0_SW2', 'VMDR_SW2', 'VTM01_SW2')}
# Las del formato compacto de la API (app/src/data/forecast-compacta.ts)
ESCALA = {'hs': 100, 'dir': 10, 'tp': 10, 'wind': 1, 'gust': 1, 'wdir': 1, 'cMed': 100, 'cIbi': 100, 'cReg': 100, 'cGlo': 100, 'cDir': 10, 'cOm': 10, 'sst': 10}
avisos = []


def get(path):
    req = urllib.request.Request(f'{API}{path}', headers={'User-Agent': 'divy-t139', 'Accept-Encoding': 'identity'})
    for intento in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            if intento == 3:
                raise
            time.sleep(5 * (intento + 1))


def descompactar(s):
    """Formato 5 de la API: columnas en diferencias enteras (forecast-compacta.ts)."""
    if s.get('version') != 5:
        return s
    for sp in s['spots'].values():
        for c, serie in (sp.pop('z', None) or {}).items():
            acc, out = 0, []
            for d in serie:
                if d is None:
                    out.append(None)
                else:
                    acc += d
                    out.append(acc / ESCALA[c])
            sp[c] = out
        for c, n in (sp.pop('vacias', None) or {}).items():
            sp[c] = [None] * n
    return s


def limpio(v, nd):
    v = float(v)
    return None if not math.isfinite(v) else round(v, nd)


def descargar(spots, t0, t1, directo):
    """Trenes por spot y hora: {sid: {'tag': ..., 'km': ..., 'h': {'2026-11-27T10:00': {'tot': [hs, dir, tp], 'ww': ...}}}}."""
    import xarray as xr
    from arco import open_arco

    if directo:
        abrir = open_arco
    else:
        import copernicusmarine as cm

        abrir = cm.open_dataset
    res = {sid: {} for sid in spots}  # sid -> tag -> {'km', 'h'}
    zs = P.zones(spots)
    modelos = [
        ('med', 'cmems_mod_med_wav_anfc_4.2km_PT1H-i', lambda z: z['kind'] == 'med'),
        ('ibi', 'cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i', lambda z: z['inIbi']),
        ('reg', 'cmems_mod_nws_wav_anfc_1.5km_PT1H-i', lambda z: z['kind'] == 'nws'),
        ('reg', 'cmems_mod_bal_wav_anfc_PT1H-i', lambda z: z['kind'] == 'bal'),
        ('reg', 'cmems_mod_blk_wav_anfc_2.5km_PT1H-i', lambda z: z['kind'] == 'blk'),
    ]

    def una(tag, ds_id, z):
        try:
            try:
                ds = abrir(dataset_id=ds_id, variables=VARS, start_datetime=t0, end_datetime=t1, **z['bounds'])
            except Exception as e:  # noqa: BLE001
                if directo:
                    raise
                avisos.append(f'{tag}: el acceso normal falla ({type(e).__name__}); lectura directa')
                ds = open_arco(dataset_id=ds_id, variables=VARS, start_datetime=t0, end_datetime=t1, **z['bounds'])
            arr = {v: ds[v].load() for v in VARS}
            tiempos = arr['VHM0'].time.values
            vals = {v: arr[v].values for v in VARS}
            if tag == 'glo':
                # Cada 3 h: a horas, como pipeline.py (altura y periodo lineales; dirección por sus componentes).
                t_in = tiempos.astype('datetime64[s]').astype(np.int64)
                horas = np.arange(tiempos[0], tiempos[-1] + np.timedelta64(1, 'h'), np.timedelta64(1, 'h'))
                t_out = horas.astype('datetime64[s]').astype(np.int64)
                lerp = lambda a: np.apply_along_axis(lambda col: np.interp(t_out, t_in, col), 0, a)  # noqa: E731
                for v in VARS:
                    if v.startswith('VMDR'):
                        rad = np.radians(vals[v])
                        vals[v] = (np.degrees(np.arctan2(lerp(np.sin(rad)), lerp(np.cos(rad)))) + 360) % 360
                    else:
                        vals[v] = lerp(vals[v])
                tiempos = horas
            for sid in z['ids']:
                if tag == 'glo' and any(t in res[sid] for t in ('med', 'ibi', 'reg')):
                    continue
                cell = P.nearest_sea_cell(arr['VHM0'].isel(time=0), spots[sid])
                if cell is None or cell[2] > P.WAVE_MAX_KM:
                    continue
                i, j, d = cell
                h = {}
                for n, t in enumerate(tiempos):
                    fila = {}
                    for k, (a, b, c) in TRENES.items():
                        hs = limpio(vals[a][n, i, j], 2)
                        fila[k] = None if hs is None else [hs, limpio(vals[b][n, i, j], 0), limpio(vals[c][n, i, j], 1)]
                    h[str(t)[:13] + ':00'] = fila
                res[sid][tag] = {'km': round(d, 1), 'h': h}
        except Exception as e:  # noqa: BLE001
            avisos.append(f'{tag} {ds_id} (zona {", ".join(z["ids"][:2])}…): {type(e).__name__} {str(e)[:160]}')

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda a: una(*a), [(tag, ds_id, z) for tag, ds_id, ok in modelos for z in zs if ok(z)]))
    print(f'  · regionales: {sum(1 for v in res.values() if v)} spots', flush=True)
    # El global, en las zonas con algún spot aún sin oleaje regional (como pipeline.py).
    falta = [z for z in zs if any(not res[sid] for sid in z['ids'])]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda z: una('glo', 'cmems_mod_glo_wav_anfc_0.083deg_PT3H-i', z), falta))
    print(f'  · con global: {sum(1 for v in res.values() if v)} spots', flush=True)
    out = {}
    for sid, por_tag in res.items():
        for tag in ('med', 'ibi', 'reg', 'glo'):  # la prioridad de merge() en pipeline.py
            if tag in por_tag:
                out[sid] = {'tag': tag, **por_tag[tag]}
                break
    return out


def main():
    t_ini = time.time()
    directo = '--directo' in sys.argv
    solo = set(sys.argv[sys.argv.index('--solo') + 1].split(',')) if '--solo' in sys.argv else None
    regiones = sys.argv[sys.argv.index('--regiones') + 1].split(',') if '--regiones' in sys.argv else REGIONES
    OUT.mkdir(exist_ok=True)
    rayos = T.cargar_rayos()
    print(f'{len(rayos)} spots con rayos de T126', flush=True)
    prevs, cats, region_de = {}, {}, {}
    for r in regiones:
        prevs[r] = descompactar(get('/api/prevision' if r == 'es' else f'/api/prevision/{r}'))
        cats[r] = get(f'/api/catalogo/{r}')
        for s in cats[r]['spots']:
            region_de[s['id']] = r
        (OUT / f'prev-{r}.json').write_text(json.dumps(prevs[r], separators=(',', ':')))
        (OUT / f'catalogo-{r}.json').write_text(json.dumps(cats[r], ensure_ascii=False, separators=(',', ':')))
        print(f'  · {r}: previsión {prevs[r]["generatedAt"]} (Copernicus {prevs[r].get("copernicusAt")}), {len(cats[r]["spots"])} spots', flush=True)
    spots = {s['id']: (s['coord']['lat'], s['coord']['lng']) for r in regiones for s in cats[r]['spots'] if s['id'] in rayos and (not solo or s['id'] in solo)}
    sin_rayos = sum(1 for r in regiones for s in cats[r]['spots'] if s['id'] not in rayos)
    print(f'{len(spots)} spots con rayos en estas regiones ({sin_rayos} sin rayos, que siguen como hoy)', flush=True)
    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t0 = (ahora - timedelta(hours=3)).strftime('%Y-%m-%dT%H:00:00')
    t1 = (ahora + timedelta(hours=78)).strftime('%Y-%m-%dT%H:00:00')
    t_desc = time.time()
    tr = descargar(spots, t0, t1, directo)
    t_desc = time.time() - t_desc
    resumen = {'generatedAt': P.iso(datetime.now(timezone.utc)), 'ventana': [t0, t1], 'directo': directo, 'regiones': {}}
    for r in regiones:
        prev = prevs[r]
        w = prev['window']
        desde = datetime.fromisoformat(w['from'].replace('Z', '+00:00'))
        claves = [(desde + timedelta(hours=i)).strftime('%Y-%m-%dT%H:00') for i in range(w['hours'])]
        barco = {s['id']: s.get('entry', {}).get('label') == 'Barco' for s in cats[r]['spots']}
        out, dif = {}, []
        for s in cats[r]['spots']:
            sid = s['id']
            if sid not in tr or sid not in prev['spots']:
                continue
            p, x, ray = prev['spots'][sid], tr[sid], rayos[sid]
            col = {k: [] for k in ('hsE', 'tm', 'k', 'wexp', 'tot', 'ww', 'sw1', 'sw2')}
            for n, c in enumerate(claves):
                fila = x['h'].get(c)
                wind, wdir = p.get('wind', [None] * len(claves))[n], p.get('wdir', [None] * len(claves))[n]
                col['wexp'].append(None if wdir is None else int(T.viento_cuenta(ray, wdir)))
                if not fila or not fila.get('tot'):
                    for k in ('hsE', 'tm', 'k', 'tot', 'ww', 'sw1', 'sw2'):
                        col[k].append(None)
                    continue
                h, tm, _ = T.entrada(ray, {k: fila.get(k) for k in ('ww', 'sw1', 'sw2')}, (wind, wdir))
                col['hsE'].append(round(h, 2))
                col['tm'].append(tm)
                col['k'].append(round(T.factor_periodo(tm, barco.get(sid, False)), 3))
                for k in ('tot', 'ww', 'sw1', 'sw2'):
                    col[k].append(fila.get(k))
                hp = p.get('hs', [None] * len(claves))[n]
                if hp is not None and fila['tot'][0] is not None and c >= ahora.strftime('%Y-%m-%dT%H:00'):
                    dif.append(abs(hp - fila['tot'][0]))
            out[sid] = {'tag': x['tag'], 'km': x['km'], **col}
        (OUT / f'sombra-{r}.json').write_text(json.dumps({'region': r, 'generatedAt': resumen['generatedAt'], 'prevGeneratedAt': prev['generatedAt'], 'window': w, 'spots': out}, separators=(',', ':')))
        dif.sort()
        resumen['regiones'][r] = {
            'spots': len(cats[r]['spots']), 'conTrenes': len(out), 'prevGeneratedAt': prev['generatedAt'], 'copernicusAt': prev.get('copernicusAt'),
            'horasComparadas': len(dif), 'difHsMediana': dif[len(dif) // 2] if dif else None,
            'difHsP90': dif[int(len(dif) * 0.9)] if dif else None, 'igualA5cm': round(sum(d <= 0.05 for d in dif) / len(dif), 3) if dif else None,
        }
        print(f'  · {r}: {len(out)} spots con trenes; |hs publicado − hs de esta descarga| mediana {resumen["regiones"][r]["difHsMediana"]}', flush=True)
    resumen.update(segundosDescarga=round(t_desc), segundos=round(time.time() - t_ini), avisos=avisos[:200], nAvisos=len(avisos))
    (OUT / 't139-resumen.json').write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in resumen.items() if k != 'avisos'}, ensure_ascii=False, indent=1))
    print(f'{len(avisos)} avisos', *avisos[:20], sep='\n')


if __name__ == '__main__':
    main()
