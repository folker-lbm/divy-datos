"""
Avisos oficiales para los spots (FUENTES-DATOS.md, paso 2; aprobado por Luka el 13 oct).

España: AEMET publica sus avisos (Plan Meteoalerta) en CAP 1.2 con el contorno de cada zona, sin clave, a partir de un
RSS. Meteoalarm reúne los mismos avisos para toda Europa, pero sin contornos; por la regla «global salvo que lo local sea
mucho mejor», en España se usa AEMET directamente y Meteoalarm queda para otros países (pendiente).

Se usan los avisos que afectan al buceo: costeros (mar y viento en el mar), vientos y tormentas. Un spot queda afectado
si está dentro del contorno o a menos de 3 km (las coordenadas de los spots son aproximadas y pueden caer en tierra).
"""
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

RSS = 'https://www.aemet.es/documentos_d/eltiempo/prediccion/avisos/rss/CAP_AFAE_wah_RSS.xml'
NS = {'cap': 'urn:oasis:names:tc:emergency:cap:1.2'}
LEVELS = {'yellow': 'amarillo', 'orange': 'naranja', 'red': 'rojo'}
# Tipos de aviso (parámetro AEMET-Meteoalerta fenomeno / título del RSS) que importan para bucear.
KINDS = {'costeros': 'costeros', 'vientos': 'vientos', 'tormentas': 'tormentas'}
NEAR_DEG = 3 / 111  # ~3 km


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'divy-pipeline (joindivy.com)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _kind(title):
    t = title.lower()
    return next((v for k, v in KINDS.items() if k in t), None)


def _info_es(root):
    infos = root.findall('cap:info', NS)
    return next((i for i in infos if (i.findtext('cap:language', '', NS) or '').startswith('es')), infos[0] if infos else None)


def _polygon(text):
    pts = [tuple(map(float, p.split(','))) for p in text.split()]
    return Polygon([(lon, lat) for lat, lon in pts]) if len(pts) >= 3 else None


def aemet(spots, now, warnings):
    """{spot_id: [aviso]} con los avisos vigentes o próximos. `spots`: {id: (lat, lon)}."""
    out = {}
    try:
        rss = ET.fromstring(_get(RSS))
    except Exception as e:  # noqa: BLE001
        warnings.append(f'AEMET avisos: no se pudo leer el RSS ({str(e)[:120]})')
        return out
    seen = set()
    items = rss.findall('./channel/item')
    for item in items:
        title = item.findtext('title') or ''
        kind = _kind(title)
        link = item.findtext('link') or ''
        if not kind or not link or link in seen:
            continue
        seen.add(link)
        try:
            root = ET.fromstring(_get(link))
        except Exception as e:  # noqa: BLE001
            warnings.append(f'AEMET aviso {link[-40:]}: {str(e)[:80]}')
            continue
        info = _info_es(root)
        if info is None:
            continue
        expires = info.findtext('cap:expires', '', NS)
        try:
            if datetime.fromisoformat(expires) < now:
                continue
        except ValueError:
            pass
        level = None
        for p in info.findall('cap:parameter', NS):
            if p.findtext('cap:valueName', '', NS) == 'AEMET-Meteoalerta nivel':
                level = LEVELS.get(p.findtext('cap:value', '', NS), p.findtext('cap:value', '', NS))
        if level is None:
            m = re.search(r'nivel (amarillo|naranja|rojo)', title.lower())
            level = m.group(1) if m else None
        if level not in ('amarillo', 'naranja', 'rojo'):
            continue
        polys = [_polygon(a.findtext('cap:polygon', '', NS)) for a in info.findall('cap:area', NS) if a.findtext('cap:polygon', '', NS)]
        polys = [p for p in polys if p is not None and p.is_valid]
        if not polys:
            continue
        area = unary_union(polys)
        zone = '; '.join(a.findtext('cap:areaDesc', '', NS) for a in info.findall('cap:area', NS))
        alert = {
            'source': 'AEMET',
            'level': level,
            'kind': kind,
            'zone': re.sub(r'^Costa - ', '', zone),
            'from': info.findtext('cap:onset', '', NS) or info.findtext('cap:effective', '', NS),
            'to': expires,
            'text': (info.findtext('cap:description', '', NS) or '').strip()[:240],
        }
        for sid, (la, lo) in spots.items():
            if area.distance(Point(lo, la)) <= NEAR_DEG:
                out.setdefault(sid, []).append(alert)
    n = sum(len(v) for v in out.values())
    print(f'  · avisos AEMET: {len(seen)} revisados, {n} asignados a {len(out)} spots')
    return out


if __name__ == '__main__':
    # Prueba rápida: python avisos.py
    w = []
    res = aemet({'cala-reona': (37.6235, -0.6967), 'cadiz': (36.53, -6.30), 'tarifa': (36.01, -5.61)}, datetime.now(timezone.utc), w)
    print(res, w)
