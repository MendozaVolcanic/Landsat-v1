"""
Configuracion Landsat - 43 VOLCANES ACTIVOS DE CHILE
Usa USGS EROS Machine-to-Machine (M2M) API para Landsat 8/9 C2 L2 SR/ST.

FIX 2026-05-22: migrado desde CDSE Sentinel Hub (lag T+5-15d real,
contrario a su doc) a USGS M2M API que entrega T+1d con free tier
ilimitado (~10000 downloads/dia, sin tarjeta).

Flujo:
  1. login-token con USGS_USERNAME + USGS_M2M_TOKEN -> apiKey
  2. scene-search por bbox+datetime -> lista de entityIds
  3. download-options entityId -> lista de "Band File" individuales
  4. download-request por banda -> URL HTTPS directa al COG
  5. rasterio window-read sobre URL (~50-200 KB por banda)
"""

import os
from datetime import datetime, timedelta

# ============================================
# CREDENCIALES USGS M2M
# ============================================
USGS_USERNAME = os.getenv("USGS_USERNAME")
USGS_M2M_TOKEN = os.getenv("USGS_M2M_TOKEN")

M2M_API_URL = "https://m2m.cr.usgs.gov/api/api/json/stable"

# --- Hibrido L2 + L1 ---------------------------------------------------------
# L2 (reflectancia de SUPERFICIE, corregido atmosfericamente) es lo preferido,
# pero USGS lo procesa con cola larga (T+1 a ~T+12d). L1 (TOA, sin correccion
# atmosferica) sale ~T+1 como Real-Time. Bajamos L1 para rellenar el hueco y lo
# reemplazamos por L2 cuando USGS lo publica. Ver tasks/PLAN_hibrido_L1_L2.md.
DATASET_L2 = "landsat_ot_c2_l2"   # Landsat 8/9 OLI-TIRS Collection 2 Level 2
DATASET_L1 = "landsat_ot_c2_l1"   # Landsat 8/9 OLI-TIRS Collection 2 Level 1
DATASET_NAME = DATASET_L2         # default / back-compat

# Bandas por nivel (mapeo nombre_logico -> sufijo del archivo en el Band File).
# Mismos nombres logicos para que generar_*() funcione igual; cambia el sufijo.
BANDAS_L2 = {
    "blue":   "SR_B2.TIF",
    "green":  "SR_B3.TIF",
    "red":    "SR_B4.TIF",
    "swir16": "SR_B6.TIF",
    "swir22": "SR_B7.TIF",
    "lwir11": "ST_B10.TIF",   # Surface Temperature product
}
BANDAS_L1 = {
    "blue":   "_B2.TIF",
    "green":  "_B3.TIF",
    "red":    "_B4.TIF",
    "swir16": "_B6.TIF",
    "swir22": "_B7.TIF",
    "lwir11": "_B10.TIF",     # DN crudo -> radiancia TOA -> temp de brillo
}
BANDAS_NECESARIAS = BANDAS_L2  # back-compat

def bandas_por_nivel(nivel):
    return BANDAS_L1 if nivel == "L1" else BANDAS_L2

def dataset_por_nivel(nivel):
    return DATASET_L1 if nivel == "L1" else DATASET_L2

# Constantes de conversion L1 (Collection-2, fijas; no requieren MTL salvo sun_elev,
# que viene en los metadatos de scene-search como 'Sun Elevation L0RA').
L1_REFLECTANCE_MULT = 2.0e-05     # ρ' = MULT·DN + ADD
L1_REFLECTANCE_ADD  = -0.1        # luego ρ = ρ' / sin(sun_elev)
L1_RADIANCE_MULT_B10 = 3.342e-04  # L = MULT·DN + ADD  (W/m²/sr/µm)
L1_RADIANCE_ADD_B10  = 0.1
# K1/K2 banda 10 por satelite (para temp de brillo: BT = K2/ln(K1/L+1))
L1_THERMAL_K = {
    "Landsat-8": {"K1": 774.8853, "K2": 1321.0789},
    "Landsat-9": {"K1": 799.0284, "K2": 1329.2405},
}

# ============================================
# CONFIGURACION IMAGENES
# ============================================
MAX_CLOUD_COVER = 100    # Descargar todas (igual que Sentinel)
BUFFER_KM = 3            # Radio por defecto (se sobreescribe por volcan)
IMAGE_SIZE = 800         # Pixeles de salida (compatible con Sentinel-2)
DIAS_ATRAS = 60          # Ventana de busqueda
DIAS_RETENCION = 60      # Limpiar imagenes mas antiguas que esto

# Tope duro de seguridad: nunca pedir mas de N escenas por cron run.
# Aun si hay un bug que itera infinitamente, esto frena el gasto de PU.
MAX_ESCENAS_POR_RUN = 200


# ============================================
# VALIDACION CREDENCIALES (fail-fast)
# ============================================
def validate_credentials():
    """Aborta si USGS_USERNAME o USGS_M2M_TOKEN no estan seteados."""
    if not USGS_USERNAME or not USGS_M2M_TOKEN:
        raise SystemExit(
            "\n[ERROR FATAL] USGS_USERNAME o USGS_M2M_TOKEN no estan en el entorno.\n"
            "  En GitHub Actions: definir como Secrets del repo Landsat-v1.\n"
            "  Generar token: https://ers.cr.usgs.gov/profile/access\n"
            "  Local: export USGS_USERNAME=... && export USGS_M2M_TOKEN=...\n"
        )


# Mantener este simbolo para back-compat si algun script viejo lo importa
LANDSAT_COLLECTION = DATASET_NAME

# ============================================
# BANDAS LANDSAT 8/9 Collection 2 Level-2
# ============================================
# Nombre en STAC     | Banda fisica | Longitud de onda
# ------------------------------------------------
# "red"              | B4           | Rojo (0.65 um)
# "green"            | B3           | Verde (0.56 um)
# "blue"             | B2           | Azul (0.48 um)
# "swir22"           | B7           | SWIR 2.2 um (anomalias termicas)
# "swir16"           | B6           | SWIR 1.6 um
# "lwir11"           | B10          | Termal 10.9 um (temperatura)

COMPOSITES = {
    "RGB": {
        "bandas": ["red", "green", "blue"],
        "descripcion": "Color natural",
        "factor_escala": 0.0000275,
        "offset": -0.2,
        # Gamma sRGB sobre la reflectancia cruda, sin ganancia (2026-08-30).
        #
        # Antes: realce lineal 3.5, que satura desde una reflectancia de 0.286.
        # La nieve y la nube estan muy por encima de eso, asi que el terreno se
        # perdia. Medido sobre los PNG publicados, mismo volcan y mismo dia que
        # Copernicus-v1 (que uso lineal hasta el 18-ago y despues gamma sRGB):
        #
        #                          Copernicus (sRGB)   Landsat (lineal 3.5)
        #   Villarrica 2026-08-22   12.2% blanco 5.63    73.9% blanco 2.91 bits
        #   Llaima     2026-08-22   14.4% blanco 5.81    87.1% blanco 1.52 bits
        #   Lascar (sin nieve)       3.4% blanco 7.41    46.6% blanco 5.27 bits
        #
        # Lascar esta en altiplano arido: descarta que fuera solo efecto de la
        # nieve. El peor caso por volcan era Nevados de Chillan, 95.4% blanco y
        # 0.57 bits de entropia -- una imagen practicamente sin informacion.
        #
        # De donde salia el 3.5 no estaba escrito en ninguna parte: ni commit,
        # ni comentario, ni nota de calibracion.
        #
        # SWIR conserva el 3.5 a proposito. Su trabajo es detectar anomalias, no
        # que el terreno se lea bien, y ya esta medido que bajar su ganancia NO
        # recupera anomalias en Landsat: el limite es la dilucion sub-pixel a
        # 30 m, no el realce.
        #
        # Si se vuelve a tocar: validar sobre un dia DESPEJADO de invierno con
        # nieve Y una escena de verano con terreno oscuro, midiendo % de blanco,
        # % de negro y entropia. No a ojo, y nunca sobre escenas nubladas: ese
        # fue exactamente el error de junio en Copernicus.
        "curva": "srgb",
        "realce": None,      # sin uso con curva='srgb'; se deja por trazabilidad
    },
    "SWIR": {
        "bandas": ["swir22", "swir16", "red"],
        "descripcion": "Anomalias termicas volcanicas (SWIR7-6-4)",
        "factor_escala": 0.0000275,
        "offset": -0.2,
        "realce": 3.5,
    },
    "THERMAL": {
        "bandas": ["lwir11"],
        "descripcion": "Temperatura superficial en Celsius (Banda 10 TIRS)",
        "factor_escala": 0.00341802,   # Factor escala Landsat C2 L2
        "offset": -124.15,             # 149.0 - 273.15 → resultado en °C directamente
        "celsius_min": -20,            # Fondo frio (nieve/glaciares)
        "celsius_max": 80,             # Volcanes activos con fumarolas intensas
    },
}

# ============================================
# 43 VOLCANES ACTIVOS DE CHILE
# (mismas coordenadas que Copernicus-v1)
# ============================================
# ---------------------------------------------------------------------------
# DUENO DE LOS CENTROIDES: Copernicus-v1/config_sentinel2.py
#
# El 2026-08-30 se detecto que seis volcanes apuntaban a puntos distintos en los
# dos repos, hasta 2.35 km -- medio radio de recorte en Isluga, Lanin y Mocho.
# Los dos dashboards mostraban terreno parcialmente distinto para el mismo
# volcan, asi que compararlos a ojo comparaba cosas distintas. La revision de
# centroides se habia hecho solo en Copernicus y nunca se propago.
#
# Alineados ese dia (lat/lon tomadas de config_sentinel2.py):
#   Mocho-Choshuenco 2.35 km · Lanin 2.29 · Antillanca 1.90 · Isluga 1.82
#   Melimoyu 1.35 · Antuco 1.25
#
# Y de paso los cinco que diferian por debajo del kilometro (Yate 0.58,
# Mentolat 0.43, Copahue 0.17, Huequi 0.17, Villarrica 0.05), para que los 43
# queden en igualdad EXACTA. Es a proposito: un invariante de igualdad exacta
# se verifica sin discutir tolerancias, y cualquier deriva futura falla el mismo
# dia en vez de esconderse bajo un umbral.
#
# OJO al leer las series: para esos seis volcanes el encuadre cambia el
# 2026-08-30. Las imagenes anteriores estan centradas en el punto viejo.
#
# Los buffer_km NO se alinearon a proposito: el centroide es un hecho del
# volcan y tiene una sola respuesta correcta, pero el buffer es una decision de
# encuadre que legitimamente difiere entre sensores (20 m/px en Sentinel-2
# contra 30 m/px aca). Isluga es el caso claro: 1.0 km alla serian 66 px de
# ancho en Landsat.
#
# scripts/verificar_divergencia_repos.py (en Copernicus-v1) falla en CI si los
# dos configs vuelven a separarse.
# ---------------------------------------------------------------------------
VOLCANES = {
    # ZONA NORTE (8 volcanes)
    "Taapaca": {
        "lat": -18.10922, "lon": -69.50584, "buffer_km": 5.0,
        "zona": "Norte", "activo": True
    },
    "Parinacota": {
        "lat": -18.17126, "lon": -69.14534, "buffer_km": 2.5,
        "zona": "Norte", "activo": True
    },
    "Guallatiri": {
        "lat": -18.42781, "lon": -69.08500, "buffer_km": 2.5,
        "zona": "Norte", "activo": True
    },
    "Isluga": {
        "lat": -19.155746, "lon": -68.834406, "buffer_km": 3.5,
        "zona": "Norte", "activo": True
    },
    "Irruputuncu": {
        "lat": -20.73329, "lon": -68.56041, "buffer_km": 1.4,
        "zona": "Norte", "activo": True
    },
    "Ollague": {
        "lat": -21.30685, "lon": -68.17941, "buffer_km": 3.5,
        "zona": "Norte", "activo": True
    },
    "San Pedro": {
        "lat": -21.88485, "lon": -68.40706, "buffer_km": 4.5,
        "zona": "Norte", "activo": True
    },
    "Lascar": {
        "lat": -23.36726, "lon": -67.73611, "buffer_km": 2.8,
        "zona": "Norte", "activo": True
    },

    # ZONA CENTRO (9 volcanes)
    "Tupungatito": {
        "lat": -33.40849, "lon": -69.82181, "buffer_km": 3.5,
        "zona": "Centro", "activo": True
    },
    "San Jose": {
        "lat": -33.78682, "lon": -69.89732, "buffer_km": 2.5,
        "zona": "Centro", "activo": True
    },
    "Tinguiririca": {
        "lat": -34.80794, "lon": -70.34917, "buffer_km": 2.8,
        "zona": "Centro", "activo": True
    },
    "Planchon-Peteroa": {
        "lat": -35.24212, "lon": -70.57189, "buffer_km": 1.3,
        "zona": "Centro", "activo": True
    },
    "Descabezado Grande": {
        "lat": -35.60431, "lon": -70.74830, "buffer_km": 7.0,
        "zona": "Centro", "activo": True
    },
    "Tatara-San Pedro": {
        "lat": -35.99755, "lon": -70.84533, "buffer_km": 3.5,
        "zona": "Centro", "activo": True
    },
    "Laguna del Maule": {
        "lat": -36.07100, "lon": -70.49828, "buffer_km": 9.0,
        "zona": "Centro", "activo": True
    },
    "Nevado de Longavi": {
        "lat": -36.20001, "lon": -71.17010, "buffer_km": 5.0,
        "zona": "Centro", "activo": True
    },
    "Nevados de Chillan": {
        # FIX 2026-08-13: las coords anteriores (-37.41096/-71.35231) apuntaban a
        # ANTUCO, 60 km al sur. Quedaban a 1.3 km de la entrada "Antuco" de este
        # mismo config, asi que ambas huellas cubrian el mismo edificio y el
        # dashboard publicaba Antuco bajo el nombre de Chillan desde el setup
        # inicial (2026-04-04). Mismo error que Copernicus-v1 corrigio el
        # 2026-05-17 y que nunca se propago a este repo.
        # Coord oficial SERNAGEOMIN: -36.870 / -71.380 (Volcan Chillan Nuevo).
        "lat": -36.870, "lon": -71.380, "buffer_km": 3.3,
        "zona": "Centro", "activo": True
    },

    # ZONA SUR (13 volcanes)
    "Antuco": {
        "lat": -37.41093, "lon": -71.351307, "buffer_km": 3.0,
        "zona": "Sur", "activo": True
    },
    "Copahue": {
        "lat": -37.858693, "lon": -71.16832, "buffer_km": 2.0,
        "zona": "Sur", "activo": True
    },
    "Callaqui": {
        "lat": -37.92554, "lon": -71.46113, "buffer_km": 5.0,
        "zona": "Sur", "activo": True
    },
    "Lonquimay": {
        "lat": -38.38216, "lon": -71.58530, "buffer_km": 3.0,
        "zona": "Sur", "activo": True
    },
    "Llaima": {
        "lat": -38.71238, "lon": -71.73447, "buffer_km": 4.0,
        "zona": "Sur", "activo": True
    },
    "Sollipulli": {
        "lat": -38.98103, "lon": -71.51557, "buffer_km": 5.0,
        "zona": "Sur", "activo": True
    },
    "Villarrica": {
        "lat": -39.42021, "lon": -71.93987, "buffer_km": 1.5,
        "zona": "Sur", "activo": True
    },
    "Quetrupillan": {
        "lat": -39.53150, "lon": -71.70337, "buffer_km": 5.5,
        "zona": "Sur", "activo": True
    },
    "Lanin": {
        "lat": -39.637488, "lon": -71.502686, "buffer_km": 4.5,
        "zona": "Sur", "activo": True
    },
    "Mocho-Choshuenco": {
        "lat": -39.933961, "lon": -72.030398, "buffer_km": 5.0,
        "zona": "Sur", "activo": True
    },
    "Carran - Los Venados": {
        "lat": -40.37922, "lon": -72.10509, "buffer_km": 6.5,
        "zona": "Sur", "activo": True
    },
    "Puyehue - Cordon Caulle": {
        "lat": -40.54783, "lon": -72.14826, "buffer_km": 10.0,
        "zona": "Sur", "activo": True
    },
    "Antillanca - Casablanca": {
        "lat": -40.774523, "lon": -72.171543, "buffer_km": 5.5,
        "zona": "Sur", "activo": True
    },

    # ZONA AUSTRAL (13 volcanes)
    "Osorno": {
        "lat": -41.10453, "lon": -72.49271, "buffer_km": 4.0,
        "zona": "Austral", "activo": True
    },
    "Calbuco": {
        "lat": -41.33035, "lon": -72.60399, "buffer_km": 2.5,
        "zona": "Austral", "activo": True
    },
    "Yate": {
        "lat": -41.78269, "lon": -72.387644, "buffer_km": 4.5,
        "zona": "Austral", "activo": True
    },
    "Hornopiren": {
        "lat": -41.88132, "lon": -72.43178, "buffer_km": 2.5,
        "zona": "Austral", "activo": True
    },
    "Huequi": {
        "lat": -42.38142, "lon": -72.582982, "buffer_km": 1.5,
        "zona": "Austral", "activo": True
    },
    "Michinmahuida": {
        "lat": -42.83733, "lon": -72.43927, "buffer_km": 9.5,
        "zona": "Austral", "activo": True
    },
    "Chaiten": {
        "lat": -42.83276, "lon": -72.65155, "buffer_km": 2.7,
        "zona": "Austral", "activo": True
    },
    "Corcovado": {
        "lat": -43.19300, "lon": -72.78979, "buffer_km": 2.5,
        "zona": "Austral", "activo": True
    },
    "Melimoyu": {
        "lat": -44.074015, "lon": -72.867431, "buffer_km": 7.0,
        "zona": "Austral", "activo": True
    },
    "Mentolat": {
        "lat": -44.696206, "lon": -73.072694, "buffer_km": 3.0,
        "zona": "Austral", "activo": True
    },
    "Cay": {
        "lat": -45.07068, "lon": -72.96318, "buffer_km": 3.5,
        "zona": "Austral", "activo": True
    },
    "Maca": {
        "lat": -45.11210, "lon": -73.16908, "buffer_km": 3.5,
        "zona": "Austral", "activo": True
    },
    "Hudson": {
        "lat": -45.90915, "lon": -72.96508, "buffer_km": 8.0,
        "zona": "Austral", "activo": True
    },
}

# ============================================
# FUNCIONES AUXILIARES
# ============================================

def get_active_volcanoes():
    return {k: v for k, v in VOLCANES.items() if v.get("activo", False)}


def get_bbox(lat, lon, buffer_km):
    """Calcula bounding box en grados a partir de coordenadas y radio en km."""
    from math import cos, radians
    delta_lat = buffer_km / 111.0
    delta_lon = buffer_km / (111.0 * abs(cos(radians(lat))))
    return [lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat]


def get_image_path(volcano_name, date_str, composite):
    """Genera ruta de salida para una imagen. composite: RGB | SWIR | THERMAL"""
    base_dir = os.path.join("docs", "landsat", volcano_name)
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"{date_str}_{composite}.png")


def get_metadata_path(volcano_name):
    base_dir = os.path.join("docs", "landsat", volcano_name)
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "metadata.csv")


def count_by_zone():
    zones = {}
    for v in VOLCANES.values():
        z = v.get("zona", "Sin zona")
        zones[z] = zones.get(z, 0) + 1
    return zones
