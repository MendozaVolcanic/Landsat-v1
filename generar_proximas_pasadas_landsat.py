"""
Genera docs/proximas_pasadas_landsat.json con la prediccion de proximas
pasadas de Landsat 8/9 sobre cada volcan, basado en el historial descargado.

LOGICA (espejo del generador Sentinel-2):
- Cada satelite (Landsat-8 / Landsat-9) tiene revisita exacta de 16 dias.
- L8 y L9 estan desfasados 8 dias en la misma orbita WRS-2, asi que la
  revisita COMBINADA sobre un mismo path/row es de ~8 dias.
- Volcanes en el solape lateral de dos paths adyacentes se ven en dias
  consecutivos (par +1 dia). Eso lo expresamos como ventana en la UI, no
  en el ancla de la prediccion.
- Para cada (volcan, satelite): ultima fecha observada + 16 dias = siguiente
  pasada de ESE satelite. La lista combinada ordena ambas.

Latencia de publicacion (auditada con git + columna 'descargado'):
- Landsat nunca aparece antes de ~30 h tras el paso; modos ~30 h y ~44 h,
  mediana ~44 h, 95% dentro de 48 h. => ventana "~1,5-2 dias despues".

Hora de paso: Landsat es heliosincronico, cruza Chile ~10:45 hora local
(~14:45 UTC). Variacion <15 min entre paths => estimacion fija aceptable
dado que la imagen util se publica recien 1-2 dias despues.

Uso (idealmente desde cron):
    python generar_proximas_pasadas_landsat.py

Output: docs/proximas_pasadas_landsat.json
"""
import os, sys, glob, json
from collections import Counter
import pandas as pd
from datetime import datetime, timedelta, timezone

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from zoneinfo import ZoneInfo
from config_landsat import VOLCANES

CICLO_DIAS = 16          # revisita por satelite (L8 o L9)
PROYECCIONES = 4         # cuantos ciclos predecir hacia adelante por satelite
# Horizonte del calendario. Antes se guardaban solo las 6 pasadas mas cercanas,
# insuficiente para una vista mensual (mismo fix que en Copernicus-v1).
HORIZONTE_DIAS = 35
# Una fase orbital se considera REGULAR si aparece al menos este numero de
# veces en la ventana de analisis. Las que aparecen menos son solapes
# marginales del borde del swath: proyectarlas como regulares anunciaba
# pasadas que no ocurrian (backtest: 27% de acierto en S2 vs 100% de las
# regulares). Se derivan del ciclo: 35 d de ventana / ciclo.
VENTANA_FASES_DIAS = 35
MIN_APARICIONES_FASE = max(2, VENTANA_FASES_DIAS // CICLO_DIAS)
HORA_UTC = "14:45"       # paso aproximado sobre Chile
TZ_CHILE = ZoneInfo('America/Santiago')


def calcular_hora_chile(hora_utc_str, fecha_iso):
    """UTC -> hora local Chile (DST-aware: UTC-4 invierno / UTC-3 verano austral).

    Antes era fijo '10:45' (UTC-4) -> 1h corrido ~7 meses al ano. Derivamos del
    instante UTC concreto con zoneinfo, igual que el generador de Sentinel-2.
    """
    h, m = map(int, hora_utc_str.split(':'))
    y, mo, d = map(int, fecha_iso.split('-'))
    return datetime(y, mo, d, h, m, tzinfo=timezone.utc).astimezone(TZ_CHILE).strftime('%H:%M')


def _sat_desde_fila(row):
    """Determina Landsat-8/9 de forma robusta.

    OJO: el formato de metadata.csv no es homogeneo. En filas viejas la
    columna 'satelite' dice 'landsat-8'/'landsat-9'; en filas nuevas contiene
    el scene_id completo ('LC08_L2SP_...'). El identificador LC08/LC09 del
    scene_id es la fuente confiable en ambos casos.
    """
    blob = ' '.join(str(row.get(c, '')) for c in ('satelite', 'scene_id'))
    if 'LC08' in blob or 'landsat-8' in blob:
        return 'Landsat-8'
    if 'LC09' in blob or 'landsat-9' in blob:
        return 'Landsat-9'
    return None


def cargar_historial():
    """Carga todas las descargas registradas en docs/landsat/*/metadata.csv."""
    todos = []
    for csv in glob.glob('docs/landsat/*/metadata.csv'):
        vol = os.path.basename(os.path.dirname(csv))
        try:
            df = pd.read_csv(csv)
            df['volcan'] = vol
            df['sat'] = df.apply(_sat_desde_fila, axis=1)
            df['fecha'] = pd.to_datetime(df['fecha'], errors='coerce')
            df = df.dropna(subset=['fecha', 'sat'])
            todos.append(df[['volcan', 'fecha', 'sat']])
        except Exception:
            continue
    return pd.concat(todos, ignore_index=True) if todos else pd.DataFrame()


def predecir(historial):
    """Proyecta proximas pasadas por FASE ORBITAL (espejo del fix de Sentinel-2).

    La orbita repite EXACTO cada 16 dias, asi que el residuo `fecha % 16` de una
    pasada es estable. Cada volcan suele caer en el solape de 2-3 path/rows WRS-2
    -> cada satelite lo fotografia varias veces por ciclo (varias fases). El modelo
    viejo anclaba a la UNICA ultima descarga + 16d y solo guardaba ciclos[0] por
    satelite -> predecia la mitad (huecos fantasma de 16d; real ~4-8d). Proyectar
    por fase DESDE HOY captura todos los paths y se auto-corrige si falto descarga.
    """
    hoy = datetime.now(timezone.utc).date()
    hoy_ord = hoy.toordinal()
    hoy_iso = hoy.isoformat()
    proxima = {}

    for vol, cfg in VOLCANES.items():
        df_vol = historial[historial['volcan'] == vol] if not historial.empty else historial
        sats_data = {}
        proximas_sat = []

        for sat in ['Landsat-8', 'Landsat-9']:
            df_s = df_vol[df_vol['sat'] == sat].sort_values('fecha') if not df_vol.empty else df_vol
            if df_s.empty:
                continue
            obs = [d.date() for d in df_s['fecha']]
            ultima = max(obs)

            # Fases (residuo mod 16) de las observaciones recientes (<=50d). El
            # solape de hasta 3 path/rows da varias fases distintas por satelite.
            recientes = [d for d in obs if (ultima - d).days <= 50]
            # Frecuencia de cada fase: distingue pasada regular de solape marginal.
            _cuenta_fase = Counter(d.toordinal() % CICLO_DIAS for d in recientes)
            fases = sorted(_cuenta_fase) or [ultima.toordinal() % CICLO_DIAS]

            ciclos = []
            conf_por_fecha = {}
            for fase in fases:
                # confianza: 'alta' si la fase se repite como pasada regular
                conf = 'alta' if _cuenta_fase.get(fase, 0) >= MIN_APARICIONES_FASE else 'baja'
                off = (fase - hoy_ord) % CICLO_DIAS  # dias hasta la 1a fecha >= hoy con esa fase
                primera = hoy + timedelta(days=off)
                for k in range(PROYECCIONES):
                    _f = (primera + timedelta(days=CICLO_DIAS * k)).isoformat()
                    ciclos.append(_f)
                    # si dos fases caen el mismo dia, gana la de mayor confianza
                    if conf == 'alta' or _f not in conf_por_fecha:
                        conf_por_fecha[_f] = conf
            ciclos = sorted(set(ciclos))

            sats_data[sat] = {
                'ultima_observacion': ultima.isoformat(),
                'proximas': ciclos[:PROYECCIONES * 3],
            }
            for f in ciclos:
                proximas_sat.append((f, sat, conf_por_fecha.get(f, 'baja')))

        # Combinada: futuras (>= hoy; el paso de hoy se conserva porque el L2 de
        # Landsat se publica recien 1-2 dias despues), dedup por fecha, las 6 mas
        # cercanas. Intercala L8/L9 -> cadencia real ~4-8d, no 16.
        limite_iso = (hoy + timedelta(days=HORIZONTE_DIAS)).isoformat()
        proximas_sat.sort()
        vistos = set()
        combinada = []
        for f, s, conf in proximas_sat:
            if f >= hoy_iso and f <= limite_iso and f not in vistos:
                vistos.add(f)
                combinada.append({'fecha': f, 'sat': s, 'confianza': conf,
                                  'hora_chile': calcular_hora_chile(HORA_UTC, f)})

        ultima_imagen = df_vol['fecha'].max().date().isoformat() if not df_vol.empty else None

        # Revisita observada (mediana de dias entre imagenes consecutivas). En
        # Landsat la combinada L8+L9 da ~4 d, pero varia por volcan segun el
        # solape de paths adyacentes (sidelap), que crece con la latitud.
        revisita = None
        if not df_vol.empty:
            _f = sorted(set(d.date() for d in df_vol['fecha']))
            _iv = [(_f[i] - _f[i-1]).days for i in range(1, len(_f))]
            if _iv:
                _iv.sort()
                revisita = round(_iv[len(_iv)//2], 1)

        proxima[vol] = {
            'lat': cfg['lat'],
            'lon': cfg['lon'],
            'zona': cfg.get('zona', ''),
            'hora_utc_estimada': HORA_UTC,
            'hora_chile_estimada': calcular_hora_chile(HORA_UTC, hoy_iso),
            'ultima_imagen': ultima_imagen,
            'revisita_mediana_d': revisita,
            'satelites': sats_data,
            'proxima_combinada': combinada,
        }
    return proxima


def main():
    print("Cargando historial Landsat...")
    historial = cargar_historial()
    print(f"  {len(historial)} observaciones registradas")

    print("Prediciendo proximas pasadas...")
    pred = predecir(historial)

    output = {
        'generado_utc': datetime.now(timezone.utc).isoformat(),
        'ciclo_dias_por_satelite': CICLO_DIAS,
        # ~8d en path unico (L8+L9 desfasados 8d); ~4d en volcanes en el solape de
        # 2-3 path/rows (la mayoria). La prediccion por fase ya refleja la cadencia real.
        'ciclo_combinado_aprox_dias': '4-8 (segun solape de path/rows)',
        'latencia_l2': {'min_h': 30, 'mediana_h': 44, 'p95_h': 48,
                        'nota': '~1,5-2 dias tras el paso (Collection 2 L2 USGS)'},
        'satelites': ['Landsat-8', 'Landsat-9'],
        'volcanes': pred,
    }

    os.makedirs('docs', exist_ok=True)
    out_path = 'docs/proximas_pasadas_landsat.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nGenerado: {out_path}")

    print(f"\n=== PROXIMAS PASADAS LANDSAT (10 mas cercanas) ===")
    todas = []
    for vol, info in pred.items():
        for p in info['proxima_combinada'][:1]:
            todas.append((p['fecha'], vol, p['sat']))
    todas.sort()
    for fecha, vol, sat in todas[:10]:
        print(f"  {fecha} {calcular_hora_chile(HORA_UTC, fecha)} Chile | {vol:30} | {sat}")


if __name__ == "__main__":
    main()
