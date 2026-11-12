"""T124 (7 nov): recortes posibles del tamaño de la previsión compacta con la marea (mide en la de pruebas)."""
import gzip, json, os, urllib.request, copy
from datetime import datetime, timezone
STAGING = 'https://divy-staging.divy-api.workers.dev'
ESC = {'cGlo': 100, 'cDir': 10}
def dec(serie, esc):
    a = 0; out = []
    for x in serie:
        if x is None: out.append(None); continue
        a += x; out.append(a / esc)
    return out
def enc(serie, esc):
    a = 0; out = []
    for x in serie:
        if x is None: out.append(None); continue
        n = round(x * esc); out.append(n - a); a = n
    return out
gz = lambda x: len(gzip.compress(json.dumps(x, ensure_ascii=False, separators=(',', ':')).encode())) // 1024  # noqa: E731
out = {}
for r in ['sudeste', 'africa', 'caribe', 'es']:
    path = '/api/prevision' if r == 'es' else f'/api/prevision/{r}'
    d = json.loads(urllib.request.urlopen(urllib.request.Request(STAGING + path + '?f=5', headers={'User-Agent': 'divy-t124'}), timeout=120).read())
    o = {'total': gz(d)}
    t0 = datetime.fromisoformat(d['window']['from'].replace('Z', '+00:00'))
    ahora = datetime.now(timezone.utc)
    def variante(cglo_dec=None, cdir_paso=None, marea_desde_h=None, marea_int=False):
        v = copy.deepcopy(d)
        for s in v['spots'].values():
            z = s.get('z', {})
            if cglo_dec is not None and 'cGlo' in z:
                z['cGlo'] = enc([None if x is None else round(x, cglo_dec) for x in dec(z['cGlo'], 100)], 100)
            if cdir_paso and 'cDir' in z:
                z['cDir'] = enc([None if x is None else (round(x / cdir_paso) * cdir_paso) % 360 for x in dec(z['cDir'], 10)], 10)
            m = s.get('marea')
            if m and (marea_desde_h is not None or marea_int):
                ev = []
                for e in m['e']:
                    t = datetime(t0.year if e[0][:2] >= t0.strftime('%m') else t0.year + 1, int(e[0][:2]), int(e[0][2:4]), int(e[0][4:6]), int(e[0][6:]), tzinfo=timezone.utc)
                    if marea_desde_h is not None and (ahora - t).total_seconds() / 3600 > marea_desde_h:
                        continue
                    ev.append([int((t - t0).total_seconds() // 60), e[1], e[2]] if marea_int else e)
                m['e'] = ev
        return gz(v)
    o['cGlo1decimal'] = variante(cglo_dec=1)
    o['cGlo1decimal+cDir5'] = variante(cglo_dec=1, cdir_paso=5)
    o['eventosDesde24h'] = variante(marea_desde_h=24)
    o['eventosDesde24hEnMinutos'] = variante(marea_desde_h=24, marea_int=True)
    o['todo'] = variante(cglo_dec=1, cdir_paso=5, marea_desde_h=24, marea_int=True)
    out[r] = o
    print(r, o, flush=True)
os.makedirs('salida', exist_ok=True)
json.dump(out, open('salida/comprobacion.json', 'w'), indent=1)
