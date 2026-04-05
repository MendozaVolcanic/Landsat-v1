"""
Landsat Downloader — Microsoft Planetary Computer
Descarga imagenes Landsat 8/9 Collection 2 Level-2 para 43 volcanes activos de Chile.

Composites descargados por volcan:
  - RGB.png     → Color natural (B4-B3-B2)
  - SWIR.png    → Anomalias termicas volcanicas (B7-B6-B4)
  - THERMAL.png → Temperatura superficial (B10 TIRS)

Uso local (test con 3 volcanes):
  python landsat_downloader.py --test

Uso completo:
  python landsat_downloader.py

GitHub Actions:
  python landsat_downloader.py --dias 2   (solo busca imagenes de los ultimos 2 dias)
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from math import cos, radians
from pathlib import Path

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from PIL import Image

from config_landsat import (
    VOLCANES, COMPOSITES, LANDSAT_COLLECTION, STAC_URL,
    MAX_CLOUD_COVER, DIAS_ATRAS, DIAS_RETENCION, IMAGE_SIZE,
    get_active_volcanoes, get_bbox, get_image_path, get_metadata_path,
)

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================
# CLIENTE STAC (Planetary Computer)
# ============================================
def get_catalog():
    """Abre conexion al catalogo STAC de Planetary Computer."""
    return pystac_client.Client.open(
        STAC_URL,
        modifier=planetary_computer.sign_inplace,
    )


# ============================================
# BUSQUEDA DE ESCENAS
# ============================================
def buscar_escenas(catalog, lat, lon, buffer_km, fecha_inicio, fecha_fin):
    """
    Busca escenas Landsat disponibles para un area y rango de fechas.
    Retorna lista de items STAC ordenados por fecha descendente.
    """
    bbox = get_bbox(lat, lon, buffer_km)
    fecha_str = f"{fecha_inicio.strftime('%Y-%m-%d')}/{fecha_fin.strftime('%Y-%m-%d')}"

    search = catalog.search(
        collections=[LANDSAT_COLLECTION],
        bbox=bbox,
        datetime=fecha_str,
        query={"eo:cloud_cover": {"lte": MAX_CLOUD_COVER}},
        sortby="-datetime",
    )

    items = list(search.items())
    log.debug(f"  Encontradas {len(items)} escenas en el rango")
    return items


# ============================================
# LECTURA DE BANDAS (windowed COG)
# ============================================
def leer_banda(asset_href, lat, lon, buffer_km, size=IMAGE_SIZE):
    """
    Lee una banda especifica del COG, recortando solo el area del volcan.
    Retorna array 2D normalizado al rango [0, 1] segun escala Landsat C2 L2.
    """
    bbox_wgs84 = get_bbox(lat, lon, buffer_km)

    with rasterio.open(asset_href) as src:
        # Convertir bbox WGS84 al CRS del raster
        try:
            bbox_crs = transform_bounds(
                CRS.from_epsg(4326),
                src.crs,
                bbox_wgs84[0], bbox_wgs84[1],
                bbox_wgs84[2], bbox_wgs84[3],
            )
        except Exception as e:
            log.warning(f"  Error transformando bbox: {e}")
            return None

        # Obtener ventana de lectura
        window = src.window(*bbox_crs)

        # Leer datos en la ventana
        try:
            data = src.read(1, window=window, out_shape=(size, size),
                            resampling=rasterio.enums.Resampling.bilinear)
        except Exception as e:
            log.warning(f"  Error leyendo banda: {e}")
            return None

    return data.astype(np.float32)


# ============================================
# GENERACION DE COMPOSITES
# ============================================
def generar_rgb(item, lat, lon, buffer_km):
    """Genera composite RGB (color natural) como array H×W×3 uint8."""
    cfg = COMPOSITES["RGB"]
    canales = []
    for banda in cfg["bandas"]:
        if banda not in item.assets:
            log.warning(f"  Banda '{banda}' no disponible en este item")
            return None
        href = item.assets[banda].href
        data = leer_banda(href, lat, lon, buffer_km)
        if data is None:
            return None
        # Aplicar escala y offset Landsat C2 L2 (Surface Reflectance)
        data = data * cfg["factor_escala"] + cfg["offset"]
        canales.append(data)

    return _apilar_y_realzar(canales, cfg["realce"])


def generar_swir(item, lat, lon, buffer_km):
    """Genera composite SWIR (B7-B6-B4) para anomalias termicas volcanicas."""
    cfg = COMPOSITES["SWIR"]
    canales = []
    for banda in cfg["bandas"]:
        if banda not in item.assets:
            log.warning(f"  Banda '{banda}' no disponible en este item")
            return None
        href = item.assets[banda].href
        data = leer_banda(href, lat, lon, buffer_km)
        if data is None:
            return None
        data = data * cfg["factor_escala"] + cfg["offset"]
        canales.append(data)

    return _apilar_y_realzar(canales, cfg["realce"])


def generar_thermal(item, lat, lon, buffer_km):
    """
    Genera imagen termal (B10 TIRS) como PNG falso color en Celsius.
    Paleta: azul (frio) → amarillo → rojo (caliente).
    Rango: -20°C (nieve/glaciares) → 80°C (fumarolas activas).
    """
    cfg = COMPOSITES["THERMAL"]
    banda = cfg["bandas"][0]   # "lwir11"

    if banda not in item.assets:
        log.warning(f"  Banda termal '{banda}' no disponible")
        return None

    href = item.assets[banda].href
    data = leer_banda(href, lat, lon, buffer_km)
    if data is None:
        return None

    # Convertir a Celsius directamente (offset ya incluye -273.15)
    celsius = data * cfg["factor_escala"] + cfg["offset"]

    # Normalizar al rango de visualizacion (-20°C a 80°C)
    c_min, c_max = cfg["celsius_min"], cfg["celsius_max"]
    normalizado = np.clip((celsius - c_min) / (c_max - c_min), 0, 1)

    # Aplicar colormap termico (azul→amarillo→rojo)
    img_rgb = _colormap_thermal(normalizado)
    return img_rgb


def _apilar_y_realzar(canales, realce):
    """
    Apila 3 canales, aplica realce y convierte a uint8.
    Retorna array H×W×3.
    """
    stack = np.stack(canales, axis=-1)   # H×W×3, float

    # Realzar contraste
    stack = np.clip(stack * realce, 0, 1)

    # Convertir a uint8
    return (stack * 255).astype(np.uint8)


def _colormap_thermal(normalizado):
    """
    Aplica paleta falso-color para termal.
    0 = azul (frio), 0.5 = amarillo, 1 = rojo (caliente).
    """
    h, w = normalizado.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    # Segmento 1: 0.0 → 0.5 = azul → amarillo
    mask1 = normalizado < 0.5
    t = normalizado[mask1] * 2
    rgb[mask1, 0] = (t * 255).astype(np.uint8)          # R: 0→255
    rgb[mask1, 1] = (t * 255).astype(np.uint8)          # G: 0→255
    rgb[mask1, 2] = ((1 - t) * 255).astype(np.uint8)    # B: 255→0

    # Segmento 2: 0.5 → 1.0 = amarillo → rojo
    mask2 = normalizado >= 0.5
    t = (normalizado[mask2] - 0.5) * 2
    rgb[mask2, 0] = 255                                   # R: 255
    rgb[mask2, 1] = ((1 - t) * 255).astype(np.uint8)    # G: 255→0
    rgb[mask2, 2] = 0                                     # B: 0

    return rgb


# ============================================
# GUARDAR IMAGEN
# ============================================
def guardar_png(array_rgb, ruta_salida):
    """Guarda array H×W×3 uint8 como PNG."""
    img = Image.fromarray(array_rgb, mode="RGB")
    img.save(ruta_salida, "PNG", optimize=True)
    size_kb = os.path.getsize(ruta_salida) // 1024
    log.info(f"    Guardado: {ruta_salida} ({size_kb} KB)")


# ============================================
# METADATA
# ============================================
def actualizar_metadata(volcano_name, date_str, item, composites_ok):
    """Agrega o actualiza una fila en metadata.csv del volcan."""
    ruta = get_metadata_path(volcano_name)
    existe = os.path.isfile(ruta)

    row = {
        "fecha": date_str,
        "satelite": item.properties.get("platform", "landsat"),
        "cloud_cover": item.properties.get("eo:cloud_cover", ""),
        "scene_id": item.id,
        "RGB": "ok" if "RGB" in composites_ok else "error",
        "SWIR": "ok" if "SWIR" in composites_ok else "error",
        "THERMAL": "ok" if "THERMAL" in composites_ok else "error",
        "descargado": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    fieldnames = list(row.keys())
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not existe:
            writer.writeheader()
        writer.writerow(row)


# ============================================
# INDICE DE FECHAS DISPONIBLES
# ============================================
def actualizar_fechas_json(fechas_por_volcan):
    """
    Escribe docs/fechas_disponibles_landsat.json
    Formato compatible con el dashboard de Copernicus-v1.
    """
    ruta = os.path.join("docs", "fechas_disponibles_landsat.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(fechas_por_volcan, f, ensure_ascii=False, indent=2)
    log.info(f"Indice actualizado: {ruta}")


# ============================================
# LIMPIEZA DE IMAGENES ANTIGUAS
# ============================================
def limpiar_imagenes_antiguas(volcano_name, dias_retencion=DIAS_RETENCION):
    """Elimina PNGs mas antiguos que dias_retencion dias."""
    base = os.path.join("docs", "landsat", volcano_name)
    if not os.path.isdir(base):
        return 0

    limite = datetime.now() - timedelta(days=dias_retencion)
    eliminados = 0
    for archivo in Path(base).glob("*.png"):
        fecha_str = archivo.stem[:10]  # "2026-03-20"
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
            if fecha < limite:
                archivo.unlink()
                eliminados += 1
        except ValueError:
            pass

    if eliminados:
        log.info(f"  {volcano_name}: {eliminados} imagenes antiguas eliminadas")
    return eliminados


# ============================================
# PROCESAMIENTO DE UN VOLCAN
# ============================================
def procesar_volcan(catalog, nombre, datos, fecha_inicio, fecha_fin):
    """
    Descarga las imagenes de un volcan para el rango de fechas dado.
    Solo descarga fechas que aun no existen en disco.
    Retorna lista de fechas nuevas descargadas.
    """
    lat = datos["lat"]
    lon = datos["lon"]
    buffer_km = datos.get("buffer_km", 3.0)

    log.info(f"[{nombre}] Buscando escenas ({fecha_inicio.date()} → {fecha_fin.date()})")

    try:
        items = buscar_escenas(catalog, lat, lon, buffer_km, fecha_inicio, fecha_fin)
    except Exception as e:
        log.error(f"  Error en busqueda STAC: {e}")
        return []

    if not items:
        log.info(f"  Sin imagenes disponibles")
        return []

    fechas_nuevas = []

    for item in items:
        # Extraer fecha de la escena
        fecha_item = datetime.fromisoformat(
            item.properties["datetime"].replace("Z", "+00:00")
        ).strftime("%Y-%m-%d")

        # Verificar si ya tenemos las 3 imagenes de este dia
        rutas = {
            c: get_image_path(nombre, fecha_item, c)
            for c in ["RGB", "SWIR", "THERMAL"]
        }
        if all(os.path.isfile(r) for r in rutas.values()):
            log.debug(f"  {fecha_item}: ya existe, saltando")
            continue

        log.info(f"  Descargando {fecha_item} (cloud: {item.properties.get('eo:cloud_cover', '?')}%)")
        composites_ok = []

        # --- RGB ---
        if not os.path.isfile(rutas["RGB"]):
            arr = generar_rgb(item, lat, lon, buffer_km)
            if arr is not None:
                guardar_png(arr, rutas["RGB"])
                composites_ok.append("RGB")
        else:
            composites_ok.append("RGB")

        # --- SWIR ---
        if not os.path.isfile(rutas["SWIR"]):
            arr = generar_swir(item, lat, lon, buffer_km)
            if arr is not None:
                guardar_png(arr, rutas["SWIR"])
                composites_ok.append("SWIR")
        else:
            composites_ok.append("SWIR")

        # --- THERMAL ---
        if not os.path.isfile(rutas["THERMAL"]):
            arr = generar_thermal(item, lat, lon, buffer_km)
            if arr is not None:
                guardar_png(arr, rutas["THERMAL"])
                composites_ok.append("THERMAL")
        else:
            composites_ok.append("THERMAL")

        if composites_ok:
            actualizar_metadata(nombre, fecha_item, item, composites_ok)
            fechas_nuevas.append(fecha_item)
            log.info(f"  {fecha_item}: composites guardados → {composites_ok}")

    return fechas_nuevas


# ============================================
# MAIN
# ============================================
def main():
    parser = argparse.ArgumentParser(description="Descarga imagenes Landsat para volcanes")
    parser.add_argument("--test", action="store_true",
                        help="Modo test: procesa solo 3 volcanes (Villarrica, Lascar, Calbuco)")
    parser.add_argument("--dias", type=int, default=DIAS_ATRAS,
                        help=f"Dias atras para buscar (default: {DIAS_ATRAS})")
    parser.add_argument("--volcan", type=str, default=None,
                        help="Procesar solo un volcan especifico")
    parser.add_argument("--verbose", action="store_true",
                        help="Logging detallado")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Fechas de busqueda
    fecha_fin = datetime.now()
    fecha_inicio = fecha_fin - timedelta(days=args.dias)

    # Seleccion de volcanes
    volcanes = get_active_volcanoes()

    if args.test:
        log.info("=== MODO TEST: 3 volcanes ===")
        test_names = ["Villarrica", "Lascar", "Calbuco"]
        volcanes = {k: v for k, v in volcanes.items() if k in test_names}
    elif args.volcan:
        if args.volcan not in volcanes:
            log.error(f"Volcan '{args.volcan}' no encontrado")
            sys.exit(1)
        volcanes = {args.volcan: volcanes[args.volcan]}

    log.info(f"Procesando {len(volcanes)} volcanes | {fecha_inicio.date()} → {fecha_fin.date()}")

    # Conectar al catalogo
    log.info("Conectando a Planetary Computer...")
    try:
        catalog = get_catalog()
    except Exception as e:
        log.error(f"Error conectando al catalogo: {e}")
        sys.exit(1)

    # Procesar cada volcan
    fechas_por_volcan = {}
    ok = 0
    errores = 0

    for i, (nombre, datos) in enumerate(volcanes.items(), 1):
        log.info(f"\n[{i}/{len(volcanes)}] {nombre}")
        try:
            nuevas = procesar_volcan(catalog, nombre, datos, fecha_inicio, fecha_fin)
            # Recopilar todas las fechas existentes para el JSON
            base = os.path.join("docs", "landsat", nombre)
            if os.path.isdir(base):
                fechas_existentes = sorted(set(
                    f.stem[:10] for f in Path(base).glob("*_RGB.png")
                ), reverse=True)
                fechas_por_volcan[nombre] = fechas_existentes
            if nuevas:
                ok += 1
            # Limpiar imagenes antiguas
            limpiar_imagenes_antiguas(nombre)
        except Exception as e:
            log.error(f"  ERROR procesando {nombre}: {e}")
            errores += 1
            continue

    # Actualizar indice JSON
    os.makedirs("docs", exist_ok=True)
    actualizar_fechas_json(fechas_por_volcan)

    # Resumen
    log.info(f"\n{'='*50}")
    log.info(f"Completado: {ok} volcanes con nuevas imagenes, {errores} errores")
    log.info(f"Total volcanes con datos: {len(fechas_por_volcan)}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()
