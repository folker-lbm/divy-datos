"""¿Predicen la lluvia y el oleaje de los 3 días previos los días de agua turbia (Kd490 por satélite) en Cabo de Palos?"""
import json, urllib.request
import numpy as np
import xarray as xr

LA, LO = 37.645, -0.665
S, E = '2024-01-01', '2026-09-30'
get = lambda u: json.load(urllib.request.urlopen(u, timeout=120))
rain = get(f'https://archive-api.open-meteo.com/v1/archive?latitude={LA}&longitude={LO}&start_date={S}&end_date={E}&daily=precipitation_sum&timezone=Europe%2FMadrid')['daily']
wave = get(f'https://marine-api.open-meteo.com/v1/marine?latitude={LA}&longitude={LO}&start_date={S}&end_date={E}&daily=wave_height_max&timezone=Europe%2FMadrid&cell_selection=sea')['daily']
R = dict(zip(rain['time'], rain['precipitation_sum']))
W = dict(zip(wave['time'], wave['wave_height_max']))
kd = xr.open_dataset('salida/kd490_historico.nc')['KD490']
days = []
for t in kd.time.values:
    sl = kd.sel(time=t).values
    valid = np.isfinite(sl).mean()
    if valid < 0.2: continue
    days.append((str(t)[:10], float(np.nanmedian(sl))))
dates = sorted(R)
idx = {d: i for i, d in enumerate(dates)}
def prev(dic, d, n=3):
    i = idx.get(d)
    if i is None or i < n: return None
    vals = [dic.get(dates[j]) for j in range(i - n, i)]
    return None if any(v is None for v in vals) else vals
rows = []
for d, k in days:
    r, w = prev(R, d), prev(W, d)
    if r is None or w is None: continue
    rows.append((d, k, sum(r), max(w)))
k = np.array([x[1] for x in rows])
thr = np.quantile(k, 0.8)
print(f'Días con satélite válido y datos previos: {len(rows)} · Kd490 mediana {np.median(k):.3f} · umbral «turbio» (percentil 80) {thr:.3f}')
turb = k >= thr
def evaluate(name, pred):
    pred = np.array(pred)
    tp = int((pred & turb).sum()); fp = int((pred & ~turb).sum()); fn = int((~pred & turb).sum())
    prec = tp / max(1, tp + fp); rec = tp / max(1, tp + fn)
    print(f'{name:45s} avisa {int(pred.sum()):4d} días · acierta {prec:5.0%} de los avisos (base {turb.mean():.0%}) · detecta {rec:5.0%} de los días turbios')
evaluate('Lluvia 3 días ≥ 10 mm', [x[2] >= 10 for x in rows])
evaluate('Oleaje máx. 3 días ≥ 1,2 m', [x[3] >= 1.2 for x in rows])
evaluate('Lluvia ≥ 10 mm u oleaje ≥ 1,2 m', [x[2] >= 10 or x[3] >= 1.2 for x in rows])
evaluate('Lluvia ≥ 10 mm y oleaje ≥ 1,2 m', [x[2] >= 10 and x[3] >= 1.2 for x in rows])
evaluate('Oleaje máx. 3 días ≥ 1,5 m', [x[3] >= 1.5 for x in rows])
# Persistencia: el satélite de 1–3 días antes
kdd = dict((d, v) for d, v in days)
pers = []
for d, v, *_ in rows:
    i = idx[d]; p = [kdd.get(dates[j]) for j in range(i - 3, i) if dates[j] in kdd]
    pers.append(bool(p) and p[-1] >= thr)
evaluate('Persistencia: último satélite (≤ 3 días) turbio', pers)
# Mediana de Kd490 según clase de evento
for name, m in (('sin lluvia ni oleaje', [x[2] < 10 and x[3] < 1.2 for x in rows]), ('con lluvia u oleaje', [x[2] >= 10 or x[3] >= 1.2 for x in rows])):
    m = np.array(m); print(f'Kd490 mediano {name}: {np.median(k[m]):.3f} ({int(m.sum())} días)')

# Combinación propuesta: persistencia del satélite si hay dato de ≤ 3 días; si no, oleaje ≥ 1,5 m.
print('--- combinaciones')
has_sat, combo, combo_or = [], [], []
for (d, v, r, w), p in zip(rows, pers):
    i = idx[d]; recent = [kdd.get(dates[j]) for j in range(i - 3, i) if dates[j] in kdd]
    has_sat.append(bool(recent))
    combo.append(p if recent else w >= 1.5)
    combo_or.append(p or w >= 1.5)
evaluate('Satélite si lo hay; si no, oleaje ≥ 1,5 m', combo)
evaluate('Satélite turbio u oleaje ≥ 1,5 m', combo_or)
print(f'Días con satélite de ≤ 3 días disponible: {sum(has_sat)} de {len(rows)}')
