"""T124 (8 nov): de dónde sale el peso extra de la previsión compacta con la marea y cuánto bajaría con cada opción.
Compara la compacta de pruebas (con la marea) con la de producción (sin ella), medidas comprimidas como las descarga
la app. No publica nada. Deja el resultado en salida/peso.json.
"""
import copy
import gzip
import json
import os
import urllib.request
from datetime import datetime, timezone

STAGING = 'https://divy-staging.divy-api.workers.dev'
PROD = 'https://joindivy.com'
FLOJA = 0.5  # km/h: marea por debajo de esto no cambia el color de ningún spot (umbral más bajo de las reglas: 1 km/h)


def get(base, path):
    req = urllib.request.Request(base + path, headers={'User-Agent': 'divy-t124'})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def gz(x):
    return len(gzip.compress(json.dumps(x, ensure_ascii=False, separators=(',', ':')).encode(), 6))


def kb(n):
    return round(n / 1024)


out = {}
for r in ['sudeste', 'africa', 'caribe', 'es', 'filipinas']:
    path = '/api/prevision' if r == 'es' else f'/api/prevision/{r}'
    st, pr = get(STAGING, path + '?f=5'), get(PROD, path + '?f=5')
    spots = st['spots']
    debil = {sid for sid, s in spots.items() if not s.get('marea') or (s['marea'].get('max') or 0) < FLOJA}
    o = {'spots': len(spots), 'mareaDebil': len(debil), 'generadas': [st.get('generatedAt'), pr.get('generatedAt')],
         'produccion': kb(gz(pr)), 'pruebas': kb(gz(st))}

    # Lo que pesa cada parte nueva, quitándola sola.
    def sin(fn):
        v = copy.deepcopy(st)
        for sid, s in v['spots'].items():
            fn(sid, s)
        return kb(gz(v))

    def quita_corr(sid, s):
        s.pop('corr', None)
        (s.get('seaCellKm') or {}).pop('mar', None)

    def quita_eventos(sid, s):
        s.pop('marea', None)

    def corriente_de_produccion(sid, s, solo=None):
        p = pr['spots'].get(sid, {}).get('z', {})
        if solo is None or sid in solo:
            for k in ('cGlo', 'cDir'):
                if k in p and k in s.get('z', {}):
                    s['z'][k] = p[k]

    o['sinCorr'] = sin(quita_corr)
    o['sinEventos'] = sin(quita_eventos)
    o['corrienteSinSumar'] = sin(corriente_de_produccion)

    # Opciones.
    def opcion_a(sid, s):  # corr al catálogo + sin marea donde no cambia nada (eventos y suma)
        quita_corr(sid, s)
        if sid in debil:
            s.pop('marea', None)
            corriente_de_produccion(sid, s)

    def opcion_b(sid, s):  # A + eventos solo al abrir la ficha
        opcion_a(sid, s)
        s.pop('marea', None)

    def opcion_c(sid, s):  # A + días de vivas y muertas y tipo sin la lista de eventos (la app los calcula de la serie)
        opcion_a(sid, s)
        if s.get('marea'):
            s['marea'] = {k: v for k, v in s['marea'].items() if k != 'e'}

    o['A_corrAlCatalogo_y_sinMareaDondeNoCambia'] = sin(opcion_a)
    o['B_A_y_eventosAlAbrirLaFicha'] = sin(opcion_b)
    o['C_A_sinEventos_soloTipoYFases'] = sin(opcion_c)
    o['tamanoEventosPorSpotBytes'] = round(sum(len(json.dumps(s.get('marea', {}), separators=(',', ':'))) for s in spots.values()) / max(1, len(spots)))
    out[r] = o
    print(r, o, flush=True)

os.makedirs('salida', exist_ok=True)
json.dump({'medido': datetime.now(timezone.utc).isoformat(), 'regiones': out}, open('salida/peso.json', 'w'), indent=1)
