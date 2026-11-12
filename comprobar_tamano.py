"""T124 (7 nov): qué parte del crecimiento de la previsión compacta (la que descarga la app) se lleva cada cosa."""
import gzip, json, os, urllib.request, copy
STAGING = 'https://divy-staging.divy-api.workers.dev'
out = {}
for r in ['sudeste', 'africa', 'caribe', 'es']:
    path = '/api/prevision' if r == 'es' else f'/api/prevision/{r}'
    raw = urllib.request.urlopen(urllib.request.Request(STAGING + path + '?f=5', headers={'User-Agent': 'divy-t124'}), timeout=120).read()
    d = json.loads(raw)
    gz = lambda x: len(gzip.compress(json.dumps(x, ensure_ascii=False, separators=(',', ':')).encode())) // 1024  # noqa: E731
    o = {'total': gz(d)}
    a = copy.deepcopy(d)
    for s in a['spots'].values(): s.pop('marea', None)
    o['sinMarea'] = gz(a)
    b = copy.deepcopy(d)
    for s in b['spots'].values(): s.pop('corr', None)
    o['sinCorr'] = gz(b)
    c = copy.deepcopy(d)
    for s in c['spots'].values():
        s.pop('marea', None); s.pop('corr', None)
    o['sinMareaNiCorr'] = gz(c)
    e = copy.deepcopy(c)
    for s in e['spots'].values():
        z = s.get('z', {})
        if 'cGlo' in z: z.pop('cGlo'); z.pop('cDir', None)
    o['sinMareaNiCorrNiCorriente'] = gz(e)
    out[r] = o
    print(r, o, flush=True)
os.makedirs('salida', exist_ok=True)
json.dump(out, open('salida/comprobacion.json', 'w'), indent=1)
