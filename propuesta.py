"""
Propuesta de reglas de la corriente (T124, fase 2; 7 nov 2026). NO aprobada: sirve para enseñar a Luka qué color y qué
confianza saldrían. El motor de la app (app/src/data/synthesis.ts) no cambia sin su OK (REGLAS-CONDICIONES.md).

1. La corriente de cada hora se compara con lo habitual en ese spot (percentiles de un año, habitual.py).
2. La confianza de la corriente se calcula con datos (geometria.py y marea.py): si el modelo puede ver el mar del spot.
"""

# Opciones de umbrales (percentil del año en ese spot a partir del cual la corriente pasa a regular o a difícil).
# Por debajo de SUELO_KMH, siempre buena: en un spot casi sin corriente, su «percentil 95» puede ser 0,4 km/h.
OPCIONES = {
    'A': {'regular': 90, 'dificil': None},  # amarillo en el 10 % de horas más fuertes del año; nunca rojo por sí sola
    'B': {'regular': 90, 'dificil': 98},    # además, rojo en el 2 % más fuerte
    'C': {'regular': 95, 'dificil': None},  # más permisiva: amarillo solo en el 5 % más fuerte
}
SUELO_KMH = 1.0
TECHO_KMH = None  # sin techo absoluto: el modelo se queda corto en los canales y un techo los pondría siempre en rojo


def percentil(x, pcts):
    """Tramo del año en que cae x en ese spot: '<50', '50-75', '75-90', '90-95', '95-98' o '>98'."""
    ks = sorted(int(k) for k in pcts)
    if x <= _p(pcts, ks[0]):
        return f'<{ks[0]}'
    for a, b in zip(ks, ks[1:]):
        if x <= _p(pcts, b):
            return f'{a}-{b}'
    return f'>{ks[-1]}'


def _p(pcts, k):
    return pcts[str(k)] if str(k) in pcts else pcts[k]


def nivel(x, pcts, opcion):
    if x < SUELO_KMH:
        return 'buena'
    o = OPCIONES[opcion]
    if o['dificil'] and x > _p(pcts, o['dificil']):
        return 'dificil'
    if x > _p(pcts, o['regular']):
        return 'regular'
    return 'buena'


def habitual_texto(h):
    """«Corriente habitual aquí»: la mediana del año, en las palabras de la app (calma, suave, moderada, fuerte) y si es
    sobre todo de marea."""
    t, m = _p(h['total'], 50), _p(h['marea'], 50)
    p90 = _p(h['total'], 90)
    clase = 'suave' if p90 < 1 else 'moderada' if p90 <= 2 else 'fuerte'
    marea = m >= 0.5 * t and p90 >= 1
    return {'clase': clase, 'porMarea': marea, 'medianaKmh': t, 'p90Kmh': p90}


# Confianza calculada (alta, media o baja) y sus motivos, de más a menos grave.
PASO_KM = 3.0        # agua frente al spot más estrecha que esto: un paso o una bahía que la celda de 9 km no ve
CELDA_LEJOS_KM = 8.0  # el modelo general no tiene mar más cerca que esto
VECINAS_X = 4.0      # la marea de las celdas de mar cercanas cambia más de 4 veces de una a otra…
VECINAS_MIN = 1.5    # …y la más fuerte pasa de 1,5 km/h
MAR_PCT = 67         # menos de 6 de las 9 celdas alrededor son mar para el modelo


def confianza(s):
    m, g = s['marea'], s.get('geo') or {}
    baja, media = [], []
    if g.get('anchuraKm') is not None and g['anchuraKm'] < PASO_KM:
        baja.append(('paso', g['anchuraKm']))
    if m.get('celdaGeneralKm') is None or m['celdaGeneralKm'] > CELDA_LEJOS_KM:
        baja.append(('lejos', m.get('celdaGeneralKm')))
    lo, hi = m.get('vecinasKmh', [0, 0])
    if hi >= VECINAS_MIN and hi / max(lo, 0.1) >= VECINAS_X:
        baja.append(('vecinas', [lo, hi]))
    if m.get('marPct', 100) < MAR_PCT:
        media.append(('tierra', m['marPct']))
    if g.get('anchuraKm') is not None and PASO_KM <= g['anchuraKm'] < 9:
        media.append(('estrecho', g['anchuraKm']))
    if m.get('celdaGeneralKm') is not None and 4 < m['celdaGeneralKm'] <= CELDA_LEJOS_KM:
        media.append(('celda', m['celdaGeneralKm']))
    return ('baja' if baja else 'media' if media else 'alta'), baja + media
