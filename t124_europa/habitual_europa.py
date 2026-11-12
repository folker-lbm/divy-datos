"""
T124 en Europa (10 nov 2026): lo habitual de la corriente de cada spot europeo con los mismos modelos regionales que usa
la previsión (pipeline.py de divy-datos), para que el motor compare con lo habitual también en Europa.

Mismo tratamiento que la previsión:
  · el mismo producto de previsión de Copernicus (su archivo de análisis de un año, que sigue en línea): MEDSEA, IBI,
    noroeste europeo, Báltico y mar Negro, hora a hora;
  · la misma profundidad: el primer nivel del modelo desde 8 m (pipeline.py pide 8–12 m y toma el primero);
  · la misma celda: la celda de mar más cercana al spot (nearest_sea_cell), y solo si está a 3 km o menos, como la app
    (MAX_CELL_KM de forecast-data.ts: si está más lejos, la app no usa la corriente de ese modelo);
  · la misma media: si el spot tiene dos modelos cerca (MEDSEA e IBI en el Mediterráneo español), la media de las dos
    velocidades, como `current.mean` del motor.

Por spot: percentiles 50, 90 y 98 del año (km/h), la celda y su distancia, y las medidas para la confianza en la malla
regional (parte de mar de las 9 celdas alrededor, distancia a la celda, diferencia entre celdas vecinas).

Uso: python -I habitual_europa.py  [--desde 2025-11-01 --hasta 2026-11-01]
Sale en salida/habitual-europa.json. Los trozos descargados se guardan en chunks/ (no se vuelven a pedir).
"""
import argparse
import hashlib
import json
import math
import sys
import time
import uuid
from collections.abc import MutableMapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

AQUI = Path('/Volumes/MacExt/Divy-cache/T124/europa')  # carpeta de trabajo: catálogos, previsiones, trozos y salidas (pesados, fuera del repositorio)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline.py y arco.py de divy-datos
import pipeline  # noqa: E402
from arco import _arco_url, _HttpStore  # noqa: E402

DATASETS = {
    'med': 'cmems_mod_med_phy-cur_anfc_4.2km-3D_PT1H-m',
    'ibi': 'cmems_mod_ibi_phy_anfc_0.027deg-3D_PT1H-m',
    'nws': 'cmems_mod_nws_phy-cur_anfc_1.5km-3D_PT1H-i',
    'bal': 'cmems_mod_bal_phy_anfc_PT1H-i',
    'blk': 'cmems_mod_blk_phy-cur_anfc_2.5km_PT1H-m',
}
MAX_CELL_KM = 3.0  # forecast-data.ts
PCTS = (50, 75, 90, 95, 98)
VEC = 2  # celdas a cada lado para las vecinas (5 × 5)
CACHE = AQUI / 'chunks2'


class Cache(MutableMapping):
    """Almacén Zarr de solo lectura con copia en disco de cada trozo. Solo un 403 o 404 es «trozo que no existe» (tierra);
    cualquier otro fallo se reintenta y, si sigue, para el cálculo (nunca se rellena un hueco sin saberlo)."""

    def __init__(self, url):
        import requests

        self.url = url.rstrip('/')
        self.s = requests.Session()
        self.dir = CACHE / hashlib.sha1(url.encode()).hexdigest()[:12]
        self.dir.mkdir(parents=True, exist_ok=True)

    def _get(self, key):
        f = self.dir / key.replace('/', '__')
        if f.exists():
            return f.read_bytes() if f.stat().st_size else None
        for n in range(8):
            try:
                r = self.s.get(f'{self.url}/{key}', timeout=180)
                if r.status_code in (403, 404):  # S3 de Copernicus responde 403 a lo que no existe (como arco.py)
                    b = None
                    break
                r.raise_for_status()
                b = r.content
                if 'Content-Length' in r.headers and len(b) != int(r.headers['Content-Length']):
                    raise IOError(f'descarga incompleta de {key}')
                break
            except Exception:  # noqa: BLE001
                if n == 7:
                    raise
                time.sleep(3 * (n + 1))
        tmp = self.dir / f'.{f.name}.{uuid.uuid4().hex}.tmp'
        tmp.write_bytes(b or b'')
        tmp.replace(f)
        return b

    def __getitem__(self, key):
        b = self._get(key)
        if b is None:
            raise KeyError(key)
        return b

    def __contains__(self, key):
        return self._get(key) is not None

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __setitem__(self, k, v):
        raise NotImplementedError

    def __delitem__(self, k):
        raise NotImplementedError


def abrir(ds_id):
    import xarray as xr
    import zarr

    url = _arco_url(ds_id, service='arco-time-series')
    ds = xr.open_zarr(zarr.storage.KVStore(Cache(url)), consolidated=True)
    if 'elevation' in ds.dims:
        prof = -ds.elevation.values
        cand = [k for k, z in enumerate(prof) if 8 <= z <= 12]
        k = min(cand, key=lambda k: prof[k])
        ds = ds.isel(elevation=k)
        nivel = float(prof[k])
    else:
        prof = ds.depth.values
        cand = [k for k, z in enumerate(prof) if 8 <= z <= 12]
        k = min(cand, key=lambda k: prof[k])
        ds = ds.isel(depth=k)
        nivel = float(prof[k])
    return ds[['uo', 'vo']], nivel, url


def modelo_reg(pos):
    k = pipeline.kind(pos)
    return k if k in ('nws', 'bal', 'blk') else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--desde', default='2025-11-01')
    ap.add_argument('--hasta', default='2026-11-01')
    ap.add_argument('--hilos', type=int, default=16)
    ap.add_argument('--solo', default=None, help='solo estos modelos (med,ibi,…)')
    a = ap.parse_args()
    import dask
    import xarray as xr

    spots, cell_prev = {}, {}
    for r in ('es', 'med', 'eu'):
        cat = json.loads((AQUI / f'cat/{r}.json').read_text())
        prev = json.loads(next((AQUI / 'prev').glob(f'{r}-*.json')).read_text())['spots']
        for s in cat['spots']:
            spots[s['id']] = (s['coord']['lat'], s['coord']['lng']) if isinstance(s['coord'], dict) else tuple(s['coord'])
            cell_prev[s['id']] = (prev.get(s['id']) or {}).get('seaCellKm', {})
    # Qué modelos usa la app en cada spot: los regionales de la previsión con la celda a 3 km o menos.
    usa = {}
    for sid, sc in cell_prev.items():
        ms = []
        if sc.get('med') is not None and sc['med'] <= MAX_CELL_KM:
            ms.append('med')
        if sc.get('ibi') is not None and sc['ibi'] <= MAX_CELL_KM:
            ms.append('ibi')
        if sc.get('reg') is not None and sc['reg'] <= MAX_CELL_KM:
            ms.append(modelo_reg(spots[sid]) or 'reg?')
        usa[sid] = ms
    print(len(spots), 'spots;', sum(1 for m in usa.values() if m), 'con algún modelo regional a ≤ 3 km', flush=True)

    t0, t1 = np.datetime64(a.desde), np.datetime64(a.hasta)
    (AQUI / 'salida').mkdir(exist_ok=True)
    series, info = {}, {sid: {'modelos': ms, 'celdas': {}} for sid, ms in usa.items()}
    for m, ds_id in DATASETS.items():
        if a.solo and m not in a.solo.split(','):
            continue
        sids = [s for s, ms in usa.items() if m in ms]
        if not sids:
            continue
        ds, nivel, url = abrir(ds_id)
        ds = ds.sel(time=slice(t0, t1 - np.timedelta64(1, 'h')))
        lat, lon = ds.latitude.values, ds.longitude.values
        dlat, dlon = float(lat[1] - lat[0]), float(lon[1] - lon[0])
        celda_km = abs(dlat) * 111.2
        print(f'{m}: {len(sids)} spots, nivel {nivel:.2f} m, {ds.sizes["time"]} horas, celda {celda_km:.1f} km', flush=True)
        # Máscara de mar en la primera hora (la misma que nearest_sea_cell en la primera hora de la previsión).
        caja = {}
        for sid in sids:
            la, lo = spots[sid]
            i0, j0 = int(round((la - lat[0]) / dlat)), int(round((lo - lon[0]) / dlon))
            caja[sid] = (i0, j0)
        r = int(math.ceil(0.15 / min(abs(dlat), abs(dlon))))
        celdas = sorted({(i, j) for (i0, j0) in caja.values() for i in range(i0 - r, i0 + r + 1) for j in range(j0 - r, j0 + r + 1)
                         if 0 <= i < len(lat) and 0 <= j < len(lon)})
        t = time.time()
        with dask.config.set(scheduler='threads', num_workers=a.hilos):
            m0 = ds.uo.isel(time=0, latitude=xr.DataArray([c[0] for c in celdas], dims='p'),
                            longitude=xr.DataArray([c[1] for c in celdas], dims='p')).values
        mar = {c for c, v in zip(celdas, m0) if np.isfinite(v)}
        print(f'  máscara: {len(celdas)} celdas, {len(mar)} de mar ({time.time() - t:.0f} s)', flush=True)
        elegida, vecinas = {}, {}
        for sid in sids:
            la, lo = spots[sid]
            i0, j0 = caja[sid]
            cand = [(i, j) for i in range(i0 - r, i0 + r + 1) for j in range(j0 - r, j0 + r + 1) if (i, j) in mar]
            if not cand:
                continue
            c = min(cand, key=lambda c: pipeline.km((la, lo), (float(lat[c[0]]), float(lon[c[1]]))))
            d = pipeline.km((la, lo), (float(lat[c[0]]), float(lon[c[1]])))
            nueve = [(i, j) for i in range(i0 - 1, i0 + 2) for j in range(j0 - 1, j0 + 2)]
            elegida[sid] = c
            vecinas[sid] = [(i, j) for i in range(c[0] - VEC, c[0] + VEC + 1) for j in range(c[1] - VEC, c[1] + VEC + 1) if (i, j) in mar]
            info[sid]['celdas'][m] = {'km': round(d, 2), 'kmPrevision': cell_prev[sid].get('reg' if m in ('nws', 'bal', 'blk') else m),
                                      'marPct': round(100 * sum(x in mar for x in nueve) / 9), 'celdaKm': round(celda_km, 2),
                                      'nivel': round(nivel, 2)}
        todas = sorted({c for sid in elegida for c in vecinas[sid]} | set(elegida.values()))
        print(f'  serie de {len(todas)} celdas…', flush=True)
        t = time.time()
        I = xr.DataArray([c[0] for c in todas], dims='p')
        J = xr.DataArray([c[1] for c in todas], dims='p')
        with dask.config.set(scheduler='threads', num_workers=a.hilos):
            sel = ds.isel(latitude=I, longitude=J)
            U, V = sel.uo.values.astype('float32'), sel.vo.values.astype('float32')
        S = np.hypot(U, V) * 3.6  # km/h, horas × celdas
        print(f'  descargada en {time.time() - t:.0f} s', flush=True)
        pos = {c: k for k, c in enumerate(todas)}
        times = sel.time.values
        np.savez_compressed(AQUI / 'salida' / f'series-{m}.npz', times=times.astype('datetime64[s]').astype(np.int64),
                            ids=np.array(list(elegida)), S=np.stack([S[:, pos[c]] for c in elegida.values()], axis=1))
        for sid, c in elegida.items():
            s = S[:, pos[c]]
            series.setdefault(sid, {})[m] = (times, s)
            p90v = [float(np.nanpercentile(S[:, pos[v]], 90)) for v in vecinas[sid] if np.isfinite(S[:, pos[v]]).sum() > 1000]
            info[sid]['celdas'][m]['vecinasP90'] = [round(min(p90v), 2), round(max(p90v), 2)] if p90v else None
            ok = np.isfinite(s)
            info[sid]['celdas'][m]['horas'] = int(ok.sum())
            info[sid]['celdas'][m]['pct'] = {p: round(float(np.percentile(s[ok], p)), 2) for p in PCTS} if ok.sum() else None

    out = {}
    for sid, d in info.items():
        if not d['modelos']:
            out[sid] = d
            continue
        ss = [series[sid][m] for m in d['modelos'] if m in series.get(sid, {})]
        if len(ss) != len(d['modelos']):
            d['falta'] = [m for m in d['modelos'] if m not in series.get(sid, {})]
            out[sid] = d
            continue
        if len(ss) == 1:
            tot = ss[0][1]
        else:
            # Dos modelos (MEDSEA e IBI): la media de las velocidades a la misma hora, como el motor.
            (ta, a_), (tb, b_) = ss
            comun, ia, ib = np.intersect1d(ta, tb, return_indices=True)
            tot = (a_[ia] + b_[ib]) / 2
        ok = np.isfinite(tot)
        d['horas'] = int(ok.sum())
        d['total'] = {p: round(float(np.percentile(tot[ok], p)), 2) for p in PCTS} if ok.sum() else None
        out[sid] = d
    (AQUI / 'salida').mkdir(exist_ok=True)
    f = AQUI / 'salida' / (f'habitual-europa-{a.solo}.json' if a.solo else 'habitual-europa.json')
    f.write_text(json.dumps({'desde': a.desde, 'hasta': a.hasta, 'datasets': DATASETS, 'spots': out}, ensure_ascii=False))
    print('→', f)


if __name__ == '__main__':
    main()
