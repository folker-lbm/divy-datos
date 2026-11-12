"""
T124 en Europa: entradas «corr» (conf, por, hab) de los spots europeos con los modelos regionales, en el mismo formato
que las del resto del mundo (datos/corriente-spots.json de divy-datos), para que el motor tenga un solo camino.

Entrada: salida/habitual-europa.json (combinar.py), la geometría de la costa de T124 fase 2 (fase2/todos2.json, campo
`geo`) y el corriente-spots.json actual (rama t124-marea-smoc de divy-datos).
Salida: salida/corriente-spots.json (el de la rama con los spots europeos sustituidos) y salida/corr-europa.json (solo
los europeos, con las medidas que llevan a cada confianza).

Confianza (las mismas ideas que propuesta.confianza de divy-datos, medidas en la malla regional):
  baja   · paso: el agua frente al spot mide menos de 3 km (la misma medida de la costa que fuera de Europa);
         · vecinas: el p90 del año cambia más de 4 veces entre las celdas de mar de alrededor (5 × 5) y la más fuerte
           pasa de 1,5 km/h (un paso o una punta que el modelo no resuelve bien);
  media  · tierra: menos de 6 de las 9 celdas alrededor del spot son mar en el modelo;
         · estrecho: el agua frente al spot mide de 3 a 9 km;
         · celda: la celda de mar del modelo está a más de media celda del spot (el spot queda fuera de ella: bahías
           donde el modelo ve el mar de fuera). En las calas (sin openWater) cuenta como baja (Luka, 11 nov): la
           corriente de fuera puede no entrar, y así dos buceadores deciden el color.
Tope (Luka, 11 nov, validación crítica): en Europa el p90 de lo habitual se limita a TOPE_P90 km/h. En los spots de
marea fuerte (Tarifa, Scapa Flow, Dover, Port-Cros…) se bucea en el repunte y su corriente «normal» es justo la que
impide bucear: sin tope, 3,5 km/h saldría en verde. Con el tope, la corriente del modelo de 2 km/h o más nunca sale en
verde en Europa (sale amarillo, nunca rojo). Solo en los datos: el motor y el resto del mundo no cambian. El p90 sin
tope queda en `por` como ['tope', p90].
Rojo (Luka, 11 nov, revalidación): `rojo: 3.7` (2 nudos) en los spots europeos con lo habitual de los modelos
regionales. El motor lo lee si existe: desde esa corriente, rojo, y los reportes no lo bajan. Sin el campo, como antes
(la corriente sola nunca pone rojo). Los 16 que usan el global siguen la regla de fuera de Europa, sin `rojo`.
Sin ningún modelo regional a 3 km o menos, la app no usa la corriente del modelo: el spot queda sin `hab`.
"""
import json
from collections import Counter
from pathlib import Path

A = Path('/Volumes/MacExt/Divy-cache/T124/europa')  # carpeta de trabajo: catálogos, previsiones, trozos y salidas (pesados, fuera del repositorio)
PASO_KM, ESTRECHO_KM = 3.0, 9.0
VECINAS_X, VECINAS_MIN = 4.0, 1.5
MAR_PCT = 67
TOPE_P90 = 1.9
ROJO = 3.7  # 2 nudos: desde aquí la corriente del modelo pone rojo en Europa (revalidación, Luka 11 nov)


def confianza(d, geo, cala):
    baja, media = [], []
    an = (geo or {}).get('anchuraKm')
    if an is not None and an < PASO_KM:
        baja.append(['paso', an])
    vec = [c['vecinasP90'] for c in d['celdas'].values() if c.get('vecinasP90')]
    if vec:
        lo, hi = min(v[0] for v in vec), max(v[1] for v in vec)
        if hi >= VECINAS_MIN and hi / max(lo, 0.1) >= VECINAS_X:
            baja.append(['vecinas', [lo, hi]])
    mar = min(c['marPct'] for c in d['celdas'].values())
    if mar < MAR_PCT:
        media.append(['tierra', mar])
    if an is not None and PASO_KM <= an < ESTRECHO_KM:
        media.append(['estrecho', an])
    fuera = max((c['km'] / c['celdaKm'] for c in d['celdas'].values()), default=0)
    if fuera > 0.5:
        (baja if cala else media).append(['celda', round(max(c['km'] for c in d['celdas'].values()), 1)])
    return ('b' if baja else 'm' if media else 'a'), baja + media


def clase(p90):
    return 'suave' if p90 < 1 else 'moderada' if p90 <= 2 else 'fuerte'


def main():
    hab = json.loads((A / 'salida/habitual-europa.json').read_text())['spots']
    geo = {s: v.get('geo') for s, v in json.loads(Path('/Volumes/MacExt/Divy-cache/T124/fase2/todos2.json').read_text())['spots'].items()}
    antes = json.loads(Path('/Volumes/MacExt/Divy-cache/divy-datos-t124/datos/corriente-spots.json').read_text())
    nuevo, europa, cuenta = dict(antes), {}, Counter()
    for sid, d in hab.items():
        if not d['modelos']:
            # Sin modelo regional cerca: si el spot usa el global (soloGlobal hasta ahora), conserva lo de fuera de Europa;
            # si no, la app no usa la corriente del modelo y no lleva `hab`.
            e = antes.get(sid)
            if e and sid in GLO:
                cuenta['global'] += 1
                continue
            nuevo[sid] = {'conf': 'b', 'por': [['lejos', None]]}
            europa[sid] = {**nuevo[sid], 'modelos': []}
            cuenta['sinModelo'] += 1
            continue
        if not d.get('total'):
            nuevo.pop(sid, None)
            cuenta['sinSerie'] += 1
            continue
        t = d['total']
        conf, por = confianza(d, geo.get(sid), not OPEN.get(sid, False))
        p90 = min(t['90'], TOPE_P90)
        if p90 < t['90']:
            por.append(['tope', t['90']])
            cuenta['tope'] += 1
        e = {'conf': conf, 'por': por, 'hab': [min(t['50'], p90), p90, max(t['98'], p90)], 'clase': clase(t['90']), 'rojo': ROJO}
        nuevo[sid] = e
        europa[sid] = {**e, 'modelos': d['modelos'], 'celdas': d['celdas'], 'antes': antes.get(sid)}
        cuenta['conf-' + conf] += 1
    (A / 'salida/corriente-spots.json').write_text(json.dumps(nuevo, separators=(',', ':'), ensure_ascii=False))
    (A / 'salida/corr-europa.json').write_text(json.dumps(europa, ensure_ascii=False))
    print(dict(cuenta), Counter(k for e in europa.values() for k, _ in e.get('por', [])))


# Mar abierto según el catálogo de cada región (las calas son los spots sin openWater).
OPEN = {s['id']: bool(s.get('openWater')) for r in ('es', 'med', 'eu') for s in json.loads((A / f'cat/{r}.json').read_text())['spots']}

# Spots europeos que usan la corriente global (sin regional cerca y con el global a 10 km o menos en la previsión).
GLO = set()
for r in ('es', 'med', 'eu'):
    p = json.loads(next((A / 'prev').glob(f'{r}-*.json')).read_text())['spots']
    for sid, s in p.items():
        sc = s.get('seaCellKm', {})
        if sc.get('glo') is not None and sc['glo'] <= 10 and not any(sc.get(k) is not None and sc[k] <= 3 for k in ('med', 'ibi', 'reg')):
            GLO.add(sid)

if __name__ == '__main__':
    main()
