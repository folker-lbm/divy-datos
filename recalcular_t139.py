"""
T139: recalcula `hsE`, `tm`, `k` y `wexp` de una salida de experimento_t139.py con el trenes.py de ahora, sin volver a
descargar (los trenes, la previsión y el catálogo ya están en la salida). Uso:
.venv/bin/python -I recalcular_t139.py <dir de la salida>   (reescribe sombra-<región>.json)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trenes as T  # noqa: E402

d = Path(sys.argv[1])
rayos = T.cargar_rayos()
for f in sorted(d.glob('sombra-*.json')):
    r = f.stem.split('-', 1)[1]
    som = json.loads(f.read_text())
    prev = json.loads((d / f'prev-{r}.json').read_text())
    cat = {s['id']: s for s in json.loads((d / f'catalogo-{r}.json').read_text())['spots']}
    barco = {k: s.get('entry', {}).get('label') == 'Barco' for k, s in cat.items()}
    # Sin los excluidos y sin los que la previsión saca de otro modelo u otra celda (CARICOOS).
    som['spots'] = {sid: v for sid, v in som['spots'].items() if sid in rayos and T.misma_fuente(v['tag'], v['km'], prev['spots'][sid].get('seaCellKm'))}
    for sid, v in som['spots'].items():
        p, ray = prev['spots'][sid], rayos[sid]
        for i, tot in enumerate(v['tot']):
            wind, wdir = p['wind'][i], p['wdir'][i]
            v['wexp'][i] = None if wdir is None else int(T.viento_cuenta(ray, wdir))
            v.setdefault('fE', [None] * len(v['tot']))
            if not tot:
                continue
            h, tm, _ = T.entrada(ray, {k: v[k][i] for k in ('ww', 'sw1', 'sw2')}, (wind, wdir), tot[0], cat[sid]['open'], cat[sid].get('reefOpen', T.SIN_ARRECIFE), barco.get(sid, False))
            v['hsE'][i], v['tm'][i], v['k'][i] = round(h, 2), tm, round(T.factor_periodo(tm, barco.get(sid, False)), 3)
            v['fE'][i] = round(h / tot[0], 3) if tot[0] else None
    f.write_text(json.dumps(som, separators=(',', ':')))
    print(r, len(som['spots']))
