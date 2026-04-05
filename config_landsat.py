"""
Configuracion Landsat - 43 VOLCANES ACTIVOS DE CHILE
Usa Microsoft Planetary Computer (Landsat Collection 2 Level-2)
Sin credenciales requeridas — acceso publico con firma automatica de URLs
"""

import os
from datetime import datetime, timedelta

# ============================================
# CONFIGURACION IMAGENES
# ============================================
MAX_CLOUD_COVER = 100   # Descargar todas (igual que Sentinel)
BUFFER_KM = 3           # Radio por defecto (se sobreescribe por volcan)
IMAGE_SIZE = 800        # Pixeles de salida (mismo que Sentinel para compatibilidad)
DIAS_ATRAS = 60         # Ventana de busqueda
DIAS_RETENCION = 60     # Limpiar imagenes mas antiguas

# Coleccion Planetary Computer
LANDSAT_COLLECTION = "landsat-c2-l2"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

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
        "realce": 3.5,       # Factor multiplicador para visualizacion
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
        "lat": -19.16737, "lon": -68.82225, "buffer_km": 3.5,
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
        "lat": -37.41096, "lon": -71.35231, "buffer_km": 3.3,
        "zona": "Centro", "activo": True
    },

    # ZONA SUR (13 volcanes)
    "Antuco": {
        "lat": -37.41859, "lon": -71.34097, "buffer_km": 3.0,
        "zona": "Sur", "activo": True
    },
    "Copahue": {
        "lat": -37.85715, "lon": -71.16836, "buffer_km": 2.0,
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
        "lat": -39.42052, "lon": -71.93939, "buffer_km": 1.5,
        "zona": "Sur", "activo": True
    },
    "Quetrupillan": {
        "lat": -39.53150, "lon": -71.70337, "buffer_km": 5.5,
        "zona": "Sur", "activo": True
    },
    "Lanin": {
        "lat": -39.62762, "lon": -71.47923, "buffer_km": 4.5,
        "zona": "Sur", "activo": True
    },
    "Mocho-Choshuenco": {
        "lat": -39.93439, "lon": -72.00281, "buffer_km": 5.0,
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
        "lat": -40.76716, "lon": -72.15114, "buffer_km": 5.5,
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
        "lat": -41.77750, "lon": -72.38678, "buffer_km": 4.5,
        "zona": "Austral", "activo": True
    },
    "Hornopiren": {
        "lat": -41.88132, "lon": -72.43178, "buffer_km": 2.5,
        "zona": "Austral", "activo": True
    },
    "Huequi": {
        "lat": -42.38094, "lon": -72.58103, "buffer_km": 1.5,
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
        "lat": -44.07612, "lon": -72.85073, "buffer_km": 7.0,
        "zona": "Austral", "activo": True
    },
    "Mentolat": {
        "lat": -44.69272, "lon": -73.07507, "buffer_km": 3.0,
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
