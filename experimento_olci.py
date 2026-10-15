"""
Experimento (15 oct, homogeneizar el mundo; FUENTES-GLOBALES.md, paso 4): ¿sirve el satélite OLCI de 300 m global para
la visibilidad fuera de Europa?

Copernicus publica a 300 m en todo el mundo la clorofila (CHL), no el Kd490 que usa Divy. Se estima Kd490 con la
relación de Morel et al. (2007) para aguas abiertas: Kd490 = 0,0166 + 0,0773 · CHL^0,6715.
Se valida en el Mediterráneo, donde existe el Kd490 oficial a 300 m (la referencia), y se compara con el satélite
global de 4 km que se usa hoy fuera de Europa. Mismo método que el proceso: mediana alrededor de cada spot (~20 km)
y al menos un 20 % de píxeles sin nubes.

Uso (con las credenciales de Copernicus): python -I experimento_olci.py   → salida/experimento_olci.json
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pipeline import SAT_BOX, is_med, spots_from_catalog  # noqa: E402

DAYS = 6
REF = ('cmems_obs-oc_med_bgc-transp_nrt_l3-olci-300m_P1D', 'KD490')
CHL300 = ('cmems_obs-oc_glo_bgc-plankton_nrt_l3-olci-300m_P1D', 'CHL')
KD4KM = ('cmems_obs-oc_glo_bgc-transp_nrt_l3-multi-4km_P1D', 'KD490')


def kd_from_chl(chl):
    return 0.0166 + 0.0773 * np.power(chl, 0.6715)


def medians(ds_id, var, la, lo, start, end, fn=None):
    import copernicusmarine as cm

    ds = cm.open_dataset(dataset_id=ds_id, variables=[var], start_datetime=start, end_datetime=end,
                         minimum_latitude=la - SAT_BOX[0], maximum_latitude=la + SAT_BOX[0],
                         minimum_longitude=lo - SAT_BOX[1], maximum_longitude=lo + SAT_BOX[1])
    da = ds[var].load()
    out = {}
    for k, t in enumerate(da.time.values):
        vals = da.isel(time=k).values
        if fn is not None:
            vals = fn(vals)
        valid = float(np.isfinite(vals).mean()) if vals.size else 0.0
        if valid >= 0.2:
            out[str(t)[:10]] = float(np.nanmedian(vals))
    return out


def main():
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=DAYS)).strftime('%Y-%m-%dT00:00:00')
    end = now.strftime('%Y-%m-%dT00:00:00')
    # En GitHub (repositorio público, sin la app) los spots se piden a la API, como en el proceso.
    med = [(sid, pos) for sid, pos in spots_from_catalog('https://joindivy.com').items() if is_med(pos)]
    random.Random(15).shuffle(med)
    rows = []
    for sid, (la, lo) in med[:40]:
        try:
            ref = medians(*REF, la, lo, start, end)
            c300 = medians(*CHL300, la, lo, start, end, kd_from_chl)
            k4 = medians(*KD4KM, la, lo, start, end)
        except Exception as e:  # noqa: BLE001
            print(sid, 'error', str(e)[:100])
            continue
        for day, v in ref.items():
            rows.append({'spot': sid, 'day': day, 'ref': v, 'olci300': c300.get(day), 'multi4km': k4.get(day)})
        print(sid, len(ref), 'días', flush=True)

    def stats(key):
        pairs = [(r['ref'], r[key]) for r in rows if r[key] is not None]
        if len(pairs) < 5:
            return {'n': len(pairs)}
        a, b = np.array(pairs).T
        vis_a, vis_b = np.minimum(30, 0.6 / a), np.minimum(30, 0.6 / b)  # visibilidad estimada, como en el proceso
        return {
            'n': len(pairs),
            'cobertura %': round(100 * len(pairs) / len(rows)),
            'correlación log Kd': round(float(np.corrcoef(np.log(a), np.log(b))[0, 1]), 2),
            'error medio visibilidad (m)': round(float(np.mean(np.abs(vis_a - vis_b))), 1),
            'sesgo visibilidad (m)': round(float(np.mean(vis_b - vis_a)), 1),
        }

    result = {'spots': len({r['spot'] for r in rows}), 'días con referencia': len(rows), 'olci300 (CHL→Kd, Morel)': stats('olci300'), 'multi4km (hoy)': stats('multi4km')}
    out = HERE / 'salida' / 'experimento_olci.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'resumen': result, 'filas': rows}, ensure_ascii=False, indent=1))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
