"""
T124 (7 nov): comprueba la previsión publicada en pruebas (divy-staging) con la marea encendida, frente a producción.
Se ejecuta en GitHub Actions (flujo «Experimento»): desde España, las IP de Cloudflare de staging están bloqueadas los
días de partido. No publica nada: lee y deja salida/comprobacion.json.
"""
import gzip
import json
import os
import urllib.request
from datetime import datetime, timezone

STAGING = 'https://divy-staging.divy-api.workers.dev'
PROD = 'https://joindivy.com'
REGIONES = ['es', 'sudeste', 'africa', 'caribe']
MUESTRA = {'sudeste': ['cauldron', 'batu-bolong-2'], 'africa': ['miyaru-kandu'], 'caribe': ['klein-curacao', 'palancar'], 'es': []}


def get(base, region, compacta=False):
    path = '/api/prevision' if region == 'es' else f'/api/prevision/{region}'
    url = base + path + ('?f=5' if compacta else '')
    req = urllib.request.Request(url, headers={'User-Agent': 'divy-t124-comprobacion', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw), raw


def ahora_idx(d):
    t0 = datetime.fromisoformat(d['window']['from'].replace('Z', '+00:00'))
    return int((datetime.now(timezone.utc) - t0).total_seconds() // 3600)


out = {}
for r in REGIONES:
    try:
        st, raw_st = get(STAGING, r)
        pr, raw_pr = get(PROD, r)
        try:
            stc, raw_stc = get(STAGING, r, True)
            comp = {'KB': len(raw_stc) // 1024, 'gzipKB': len(gzip.compress(raw_stc)) // 1024, 'formato': stc.get('version'),
                    'conMarea': sum(1 for s in stc['spots'].values() if 'marea' in s)}
        except Exception as e:  # noqa: BLE001
            comp = {'error': str(e)[:120]}
        o = {
            'staging': {'generatedAt': st['generatedAt'], 'copernicusAt': st.get('copernicusAt'), 'spots': len(st['spots']),
                        'conMarea': sum(1 for s in st['spots'].values() if 'marea' in s), 'conCorr': sum(1 for s in st['spots'].values() if 'corr' in s),
                        'KB': len(raw_st) // 1024, 'gzipKB': len(gzip.compress(raw_st)) // 1024, 'avisos': [w for w in st['warnings'] if 'area' in w][:5]},
            'produccion': {'generatedAt': pr['generatedAt'], 'copernicusAt': pr.get('copernicusAt'), 'KB': len(raw_pr) // 1024, 'gzipKB': len(gzip.compress(raw_pr)) // 1024},
            'compactaStaging': comp,
            'muestra': {},
        }
        i_st, i_pr = ahora_idx(st), ahora_idx(pr)
        for sid in MUESTRA[r]:
            a, b = st['spots'].get(sid, {}), pr['spots'].get(sid, {})
            o['muestra'][sid] = {
                'cGloStagingProx12h': a.get('cGlo', [])[i_st:i_st + 12], 'cGloProduccionProx12h': b.get('cGlo', [])[i_pr:i_pr + 12],
                'cDirStaging': a.get('cDir', [])[i_st:i_st + 12], 'marea': a.get('marea'), 'corr': a.get('corr'), 'seaCellKm': a.get('seaCellKm'),
            }
        out[r] = o
    except Exception as e:  # noqa: BLE001
        out[r] = {'error': f'{type(e).__name__} {str(e)[:160]}'}
os.makedirs('salida', exist_ok=True)
json.dump(out, open('salida/comprobacion.json', 'w'), indent=1, ensure_ascii=False)
print(json.dumps(out, indent=1, ensure_ascii=False)[:20000])
