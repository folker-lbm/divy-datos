"""Une las salidas por modelo (salida/habitual-europa-<m>.json y series-<m>.npz) en salida/habitual-europa.json, con la
media de MEDSEA e IBI hora a hora donde la app usa los dos."""
import json
from pathlib import Path
import numpy as np
A = Path('/Volumes/MacExt/Divy-cache/T124/europa') / 'salida'  # carpeta de trabajo: catálogos, previsiones, trozos y salidas (pesados, fuera del repositorio)
PCTS = (50, 75, 90, 95, 98)
info, series = {}, {}
for f in sorted(A.glob('habitual-europa-*.json')):
    d = json.loads(f.read_text())
    for sid, v in d['spots'].items():
        e = info.setdefault(sid, {'modelos': v['modelos'], 'celdas': {}})
        e['celdas'].update(v['celdas'])
for f in sorted(A.glob('series-*.npz')):
    m = f.stem.split('-', 1)[1]
    z = np.load(f)
    T, S = z['times'], z['S']  # una sola lectura: cada z['S'] descomprime la matriz entera
    for k, sid in enumerate(z['ids']):
        series.setdefault(str(sid), {})[m] = (T, S[:, k])
for sid, d in info.items():
    ms = d['modelos']
    if not ms:
        continue
    ss = [series.get(sid, {}).get(m) for m in ms]
    if any(s is None for s in ss):
        d['falta'] = [m for m, s in zip(ms, ss) if s is None]
        continue
    t, tot = ss[0]
    for tb, b in ss[1:]:
        t, ia, ib = np.intersect1d(t, tb, return_indices=True)
        tot = (tot[ia] + b[ib]) / 2 if len(ss) == 2 else None
    ok = np.isfinite(tot)
    d['horas'] = int(ok.sum())
    d['total'] = {p: round(float(np.percentile(tot[ok], p)), 2) for p in PCTS} if ok.sum() else None
(A / 'habitual-europa.json').write_text(json.dumps({'spots': info}, ensure_ascii=False))
print(len(info), 'spots;', sum(1 for d in info.values() if d.get('total')), 'con lo habitual;', sum(1 for d in info.values() if d.get('falta')), 'con algún modelo sin serie')
