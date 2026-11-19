"""
T124 en Europa: efecto de un año en el nivel de la corriente (solo ese factor, sin oleaje ni viento), con la serie del
modelo regional de cada spot (salida/series-*.npz) y horas de buceo de 8 a 18 (hora local aproximada: UTC+1, Canarias
UTC+0). Antes: regla fija (mar abierto: < 1 buena, ≤ 2 regular, > 2 difícil; fuera de mar abierto no cuenta). Después:
regular si pasa de su p90 y de 1,5 km/h; si no, buena.
"""
import json
from collections import Counter
from pathlib import Path

import numpy as np

A = Path('/Volumes/MacExt/Divy-cache/T124/europa')  # carpeta de trabajo: catálogos, previsiones, trozos y salidas (pesados, fuera del repositorio)
corr = json.loads((A / 'salida/corr-europa.json').read_text())
cat = {}
for r in ('es', 'med', 'eu'):
    for s in json.loads((A / f'cat/{r}.json').read_text())['spots']:
        cat[s['id']] = {**s, 'r': r}
series = {}
for f in sorted((A / 'salida').glob('series-*.npz')):
    m = f.stem.split('-', 1)[1]
    z = np.load(f)
    T, S = z['times'], z['S']  # una sola lectura: cada z['S'] descomprime la matriz entera
    for k, sid in enumerate(z['ids']):
        series.setdefault(str(sid), {})[m] = (T, S[:, k])

tot = Counter()
spots = []
for sid, e in corr.items():
    if 'hab' not in e:
        continue
    ss = [series[sid][m] for m in e['modelos']]
    t, v = ss[0]
    if len(ss) == 2:
        t, ia, ib = np.intersect1d(t, ss[1][0], return_indices=True)
        v = (v[ia] + ss[1][1][ib]) / 2
    v = np.round(v, 1)  # el motor redondea la media a 0,1
    s = cat[sid]
    lng = s['coord']['lng']
    hora = ((t // 3600) % 24 + (0 if lng < -12 else 1)) % 24
    dia = (hora >= 8) & (hora < 18) & np.isfinite(v)
    v = v[dia]
    ow = bool(s.get('openWater'))
    antes = np.where(v < 1, 0, np.where(v <= 2, 1, 2)) if ow else np.zeros_like(v, dtype=int)
    p90 = e['hab'][1]
    despues = np.where((v >= 1.5) & (v > p90), 1, 0)
    n = len(v)
    c = Counter(zip(antes.tolist(), despues.tolist()))
    for k, x in c.items():
        tot[k] += x
    tot['n'] += n
    spots.append({'id': sid, 'r': s['r'], 'name': s['name'], 'openWater': ow, 'p90': p90, 'conf': e['conf'], 'n': n,
                  'antesAmarillo': round(100 * float((antes >= 1).mean()), 1), 'antesRojo': round(100 * float((antes == 2).mean()), 1),
                  'despuesAmarillo': round(100 * float((despues == 1).mean()), 1)})
nombres = ['buena', 'regular', 'difícil']
print('horas de buceo de un año (spot × hora):', tot['n'])
for (a, b), x in sorted((k, v) for k, v in tot.items() if k != 'n'):
    print(f'  {nombres[a]:>8} → {nombres[b]:<8} {x:>9}  {100 * x / tot["n"]:.2f} %')
(A / 'salida/anual.json').write_text(json.dumps({'tot': {f'{a}-{b}': v for (a, b), v in ((k, v) for k, v in tot.items() if k != 'n')}, 'n': tot['n'], 'spots': spots}, ensure_ascii=False))
ga = sum(1 for s in spots if s['antesAmarillo'] > 0)
gd = sum(1 for s in spots if s['despuesAmarillo'] > 0)
print(f'spots con alguna hora amarilla o roja por la corriente: antes {ga}, después {gd} (de {len(spots)})')
for k in ('antesAmarillo', 'despuesAmarillo'):
    top = sorted(spots, key=lambda s: -s[k])[:12]
    print(k, [(s['id'], s[k], s['p90'], s['openWater']) for s in top])
