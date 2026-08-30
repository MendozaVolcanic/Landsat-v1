"""
Landsat 8/9 Downloader v3 — USGS EROS Machine-to-Machine API

Reemplazo del downloader basado en CDSE Sentinel Hub (lag T+5-15d).
Ahora usa USGS M2M API + rasterio window-reads directamente sobre los
COG publicados por USGS, frescos T+1d.

Flujo:
1. login-token (USGS_USERNAME + USGS_M2M_TOKEN) -> apiKey
2. scene-search por bbox+datetime -> entityIds
3. download-options entityId -> productId del "Band File"
4. download-request por banda -> URL directa al COG
5. rasterio window-read sobre URL (~50-200 KB por banda)
6. composites locales + PNG 800x800

Costo: $0, sin tarjeta. Limite: 10000 downloads/dia (consumimos ~60).

Uso:
  python landsat_downloader.py
  python landsat_downloader.py --dias 30
  python landsat_downloader.py --volcan Villarrica
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from math import cos, radians, sin
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import requests
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from PIL import Image

from config_landsat import (
    USGS_USERNAME, USGS_M2M_TOKEN, M2M_API_URL, DATASET_NAME, BANDAS_NECESARIAS,
    DATASET_L2, DATASET_L1, BANDAS_L2, BANDAS_L1, bandas_por_nivel, dataset_por_nivel,
    L1_REFLECTANCE_MULT, L1_REFLECTANCE_ADD,
    L1_RADIANCE_MULT_B10, L1_RADIANCE_ADD_B10, L1_THERMAL_K,
    COMPOSITES,
    MAX_CLOUD_COVER, BUFFER_KM, IMAGE_SIZE,
    DIAS_ATRAS, DIAS_RETENCION, MAX_ESCENAS_POR_RUN,
    VOLCANES, get_active_volcanoes, get_bbox, get_image_path, get_metadata_path,
    validate_credentials,
)

# GDAL config para HTTP COG reads eficientes
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
os.environ.setdefault("CPL_VSIL_CURL_USE_HEAD", "NO")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ============================================================
# CLIENTE USGS M2M API
# ============================================================
class M2MClient:
    """Wrapper minimo del M2M API. Re-login automatico si la sesion expira."""

    def __init__(self):
        validate_credentials()
        self.api_key: str | None = None
        self.session = requests.Session()

    def _call(self, endpoint: str, body: dict, _retry: bool = False) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Auth-Token"] = self.api_key
        r = self.session.post(f"{M2M_API_URL}/{endpoint}", json=body, headers=headers, timeout=60)
        r.raise_for_status()
        d = r.json()
        if d.get("errorCode") == "AUTH_INVALID" and not _retry:
            log.warning("Sesion M2M expirada, re-login")
            self.api_key = None
            self._login()
            return self._call(endpoint, body, _retry=True)
        if d.get("errorCode"):
            raise RuntimeError(f"M2M {endpoint}: {d['errorCode']} - {d.get('errorMessage','')}")
        return d.get("data")

    def _login(self):
        log.info("USGS M2M login-token...")
        r = self.session.post(
            f"{M2M_API_URL}/login-token",
            json={"username": USGS_USERNAME, "token": USGS_M2M_TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("errorCode") or not d.get("data"):
            raise SystemExit(
                f"\n[ERROR FATAL] USGS M2M login fallo: {d.get('errorCode')} - "
                f"{d.get('errorMessage', 'sin detalles')}\n"
                "  Causa probable: USGS_USERNAME o USGS_M2M_TOKEN incorrectos,\n"
                "  o no se solicito acceso M2M en https://ers.cr.usgs.gov/profile/access\n"
            )
        self.api_key = d["data"]
        log.info(f"Token M2M OK (len={len(self.api_key)})")

    def logout(self):
        if self.api_key:
            try:
                self._call("logout", {})
            except Exception:
                pass
            self.api_key = None

    # --- API publica ---

    def login(self):
        if not self.api_key:
            self._login()

    def scene_search(self, lat: float, lon: float, buffer_km: float,
                     fecha_inicio: datetime, fecha_fin: datetime,
                     max_cloud: int = MAX_CLOUD_COVER,
                     dataset: str = DATASET_L2,
                     metadata_type: str = "summary") -> list[dict]:
        """Retorna lista de escenas ordenadas por fecha desc.

        dataset: landsat_ot_c2_l2 (default) o landsat_ot_c2_l1.
        metadata_type: 'full' para L1 (necesitamos 'Sun Elevation' para TOA).
        """
        bbox = get_bbox(lat, lon, buffer_km)
        body = {
            "datasetName": dataset,
            "sceneFilter": {
                "spatialFilter": {
                    "filterType": "mbr",
                    "lowerLeft":  {"latitude": bbox[1], "longitude": bbox[0]},
                    "upperRight": {"latitude": bbox[3], "longitude": bbox[2]},
                },
                "acquisitionFilter": {
                    "start": fecha_inicio.strftime("%Y-%m-%d"),
                    "end":   fecha_fin.strftime("%Y-%m-%d"),
                },
                "cloudCoverFilter": {"min": 0, "max": max_cloud, "includeUnknown": True},
            },
            "maxResults": 100,
            "metadataType": metadata_type,
        }
        d = self._call("scene-search", body) or {}
        results = d.get("results", []) or []
        # Normalizar
        for s in results:
            s["_date"] = (s.get("temporalCoverage", {}) or {}).get("startDate", "")[:10]
            s["_cloud"] = s.get("cloudCover", 0)
            s["_sun"] = _extraer_sun_elevation(s)
        results.sort(key=lambda s: s["_date"], reverse=True)
        return results

    def get_band_urls(self, entity_id: str, dataset: str = DATASET_L2,
                      bandas: dict | None = None) -> dict[str, str]:
        """
        Devuelve {nombre_logico: url_directa_https} para las bandas que necesitamos.
        Si alguna banda no esta disponible, falta en el dict (downstream lo maneja).
        """
        if bandas is None:
            bandas = BANDAS_L2
        # 1) Obtener Band File option con sus secondaryDownloads
        d = self._call("download-options", {
            "datasetName": dataset,
            "entityIds": [entity_id],
        }) or []
        band_opts = [o for o in d if "Band File" in (o.get("productName") or "")
                     and o.get("available")]
        if not band_opts:
            log.warning(f"  Sin 'Band File' disponible para {entity_id}")
            return {}
        sd_list = band_opts[0].get("secondaryDownloads") or []

        # 2) Mapear nombre_logico -> entry para las bandas que nos importan
        wanted: dict[str, dict] = {}
        for nombre, sufijo in bandas.items():
            entry = next((s for s in sd_list if (s.get("displayId") or "").endswith(sufijo)), None)
            if entry is not None:
                wanted[nombre] = entry

        if not wanted:
            log.warning(f"  Ninguna banda necesaria encontrada para {entity_id}")
            return {}

        # 3) Pedir URLs en una sola llamada bulk
        downloads = [{"entityId": e["entityId"], "productId": e["id"]} for e in wanted.values()]
        d2 = self._call("download-request", {"downloads": downloads}) or {}
        avail = d2.get("availableDownloads", []) or []
        prep = d2.get("preparingDownloads", []) or []
        if prep:
            log.warning(f"  {len(prep)} bandas en 'preparing' (no inmediatamente disponibles)")

        # Cruzar URLs con nombre logico via entityId
        url_by_entity = {a["entityId"]: a["url"] for a in avail}
        urls: dict[str, str] = {}
        for nombre, entry in wanted.items():
            # El downloadId asignado tiene entityId derivado del archivo, no el original
            # Lo identificamos por displayId del entry
            display = entry.get("displayId") or ""
            for a in avail:
                # entityId de download tiene formato L2SR_<display_id_sin_ext>_<ext>
                # Comparar mejor con que el display original aparezca en el del download
                if display.replace(".", "_") in a.get("entityId", ""):
                    urls[nombre] = a["url"]
                    break
        return urls


# ============================================================
# LECTURA DE BANDAS (window-read directo a URL M2M)
# ============================================================
def leer_banda(asset_href: str, lat: float, lon: float, buffer_km: float,
               size: int = IMAGE_SIZE):
    """Window-read COG via HTTPS, sin descargar el archivo completo."""
    bbox_wgs84 = get_bbox(lat, lon, buffer_km)
    try:
        with rasterio.open(asset_href) as src:
            bbox_crs = transform_bounds(
                CRS.from_epsg(4326), src.crs,
                bbox_wgs84[0], bbox_wgs84[1], bbox_wgs84[2], bbox_wgs84[3],
            )
            window = src.window(*bbox_crs)
            data = src.read(1, window=window, out_shape=(size, size),
                            resampling=rasterio.enums.Resampling.bilinear)
        return data.astype(np.float32)
    except Exception as e:
        log.warning(f"  Error window-read: {type(e).__name__}: {e}")
        return None


# ============================================================
# HELPERS NIVEL-AWARE (L2 = superficie corregida / L1 = TOA crudo)
# ============================================================
def _extraer_sun_elevation(esc):
    """Lee 'Sun Elevation' de los metadatos de scene-search (solo en 'full')."""
    for m in esc.get("metadata", []) or []:
        if "sun elevation" in (m.get("fieldName") or "").lower():
            try:
                return float(m.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _satelite_de(esc):
    did = (esc.get("displayId") or esc.get("entityId") or "")
    return "Landsat-9" if "LC09" in did or "LC9" in did else "Landsat-8"


def _reflectancia_toa(data, sun_elev):
    """DN crudo L1 -> reflectancia TOA corregida por angulo solar."""
    refl = data * L1_REFLECTANCE_MULT + L1_REFLECTANCE_ADD
    if sun_elev:
        s = sin(radians(sun_elev))
        if s > 0:
            refl = refl / s
    return refl


def _celsius_l1(data, sat):
    """DN crudo B10 L1 -> radiancia TOA -> temperatura de brillo en Celsius."""
    rad = data * L1_RADIANCE_MULT_B10 + L1_RADIANCE_ADD_B10
    k = L1_THERMAL_K.get(sat, L1_THERMAL_K["Landsat-8"])
    rad = np.clip(rad, 1e-6, None)  # evitar log de <=0 en nodata
    bt_k = k["K2"] / np.log(k["K1"] / rad + 1.0)
    return bt_k - 273.15


# ============================================================
# GENERACION DE COMPOSITES (nivel-aware)
# ============================================================
def _reflectancia(data, cfg, nivel, sun_elev):
    if nivel == "L1":
        refl = _reflectancia_toa(data, sun_elev)
        # Dark-object subtraction (DOS): el TOA arrastra la radiancia de camino
        # atmosferica (bruma, sobre todo en el azul). Restar por banda el "objeto
        # oscuro" (percentil 1 de pixeles validos) aproxima la correccion
        # atmosferica del L2 -> RGB con mejor contraste, menos lavado.
        valid = refl[refl > 0.01]
        if valid.size:
            refl = refl - np.percentile(valid, 1)
        return refl
    return data * cfg["factor_escala"] + cfg["offset"]   # L2 reflectancia de superficie


def generar_rgb(item, lat, lon, buffer_km, nivel="L2", sun_elev=None):
    cfg = COMPOSITES["RGB"]
    canales = []
    for banda in cfg["bandas"]:
        if banda not in item.assets:
            log.warning(f"  Banda RGB '{banda}' no disponible")
            return None
        data = leer_banda(item.assets[banda].href, lat, lon, buffer_km)
        if data is None:
            return None
        canales.append(_reflectancia(data, cfg, nivel, sun_elev))
    return _apilar_y_realzar(canales, cfg["realce"], cfg.get("curva"))


def generar_swir(item, lat, lon, buffer_km, nivel="L2", sun_elev=None):
    cfg = COMPOSITES["SWIR"]
    canales = []
    for banda in cfg["bandas"]:
        if banda not in item.assets:
            log.warning(f"  Banda SWIR '{banda}' no disponible")
            return None
        data = leer_banda(item.assets[banda].href, lat, lon, buffer_km)
        if data is None:
            return None
        canales.append(_reflectancia(data, cfg, nivel, sun_elev))
    return _apilar_y_realzar(canales, cfg["realce"])


def generar_thermal(item, lat, lon, buffer_km, nivel="L2", sat="Landsat-8"):
    """B10 -> Celsius -> colormap. L2 = temp de superficie; L1 = temp de brillo TOA."""
    cfg = COMPOSITES["THERMAL"]
    banda = cfg["bandas"][0]
    if banda not in item.assets:
        log.warning(f"  Banda thermal '{banda}' no disponible")
        return None
    data = leer_banda(item.assets[banda].href, lat, lon, buffer_km)
    if data is None:
        return None
    if nivel == "L1":
        celsius = _celsius_l1(data, sat)
    else:
        celsius = data * cfg["factor_escala"] + cfg["offset"]
    c_min, c_max = cfg["celsius_min"], cfg["celsius_max"]
    normalizado = np.clip((celsius - c_min) / (c_max - c_min), 0, 1)
    return _colormap_thermal(normalizado)


def _srgb(x):
    """Curva gamma sRGB sobre reflectancia 0-1. Sin punto de quiebre.

    Es la misma que Copernicus-v1 adopto el 2026-08-18 en su EVALSCRIPT_RGB, y
    llego alla despues de dos intentos fallidos que conviene no repetir:
    multiplicar por una ganancia fija clipeaba todo pixel sobre 0.40 a blanco, y
    un HighlightCompress con maxVal fijo no cubre los dos regimenes -- con el
    valor bueno para el invierno nevado el verano se va a negro, y al reves.
    """
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _apilar_y_realzar(canales, realce, curva=None):
    """curva='srgb' aplica gamma sobre la reflectancia; si no, ganancia lineal."""
    stack = np.stack(canales, axis=-1)
    if curva == "srgb":
        stack = _srgb(stack)
    else:
        stack = np.clip(stack * realce, 0, 1)
    return (stack * 255).astype(np.uint8)


# Fraccion de pixeles "negros" (nodata) por encima de la cual la escena se
# descarta. El scene-search filtra por MBR (rectangulo envolvente), pero el
# footprint WRS-2 real es un paralelogramo rotado: una escena de un path
# adyacente puede intersectar el MBR del volcan sin cubrirlo, y el window-read
# devuelve todo nodata -> imagen 100% negra. Antes se guardaba igual y pasaba a
# ser la "ultima imagen" del volcan. 0.9 = solo descarta las casi-totalmente
# vacias; una escena con cobertura parcial real (volcan visible) se conserva.
UMBRAL_NODATA = 0.9


def _sin_cobertura(arr_rgb) -> bool:
    """True si la escena no cubre realmente el volcan (mayoria nodata/negro)."""
    if arr_rgb is None:
        return True
    negras = np.all(arr_rgb < 8, axis=-1).mean()
    return negras > UMBRAL_NODATA


def _colormap_thermal(normalizado):
    h, w = normalizado.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    mask1 = normalizado < 0.5
    t = normalizado[mask1] * 2
    rgb[mask1, 0] = (t * 255).astype(np.uint8)
    rgb[mask1, 1] = (t * 255).astype(np.uint8)
    rgb[mask1, 2] = ((1 - t) * 255).astype(np.uint8)
    mask2 = normalizado >= 0.5
    t = (normalizado[mask2] - 0.5) * 2
    rgb[mask2, 0] = 255
    rgb[mask2, 1] = ((1 - t) * 255).astype(np.uint8)
    rgb[mask2, 2] = 0
    return rgb


# ============================================================
# I/O y metadata
# ============================================================
def guardar_png(array_rgb, ruta_salida):
    img = Image.fromarray(array_rgb, mode="RGB")
    img.save(ruta_salida, "PNG", optimize=True)
    size_kb = os.path.getsize(ruta_salida) // 1024
    log.info(f"    Guardado: {ruta_salida} ({size_kb} KB)")


def _nivel_de_scene_id(scene_id: str) -> str:
    """Infiere nivel del scene_id: L2SP -> L2, L1TP/L1GT -> L1."""
    sid = scene_id or ""
    return "L1" if "_L1" in sid else "L2"


def actualizar_metadata(volcano_name: str, date_str: str, scene_info: dict,
                        composites_ok: list[str], nivel: str = "L2"):
    ruta = get_metadata_path(volcano_name)
    existe = os.path.isfile(ruta)
    row = {
        "fecha": date_str,
        "satelite": scene_info.get("satellite", scene_info.get("displayId", "Landsat")),
        "cloud_cover": scene_info.get("_cloud", ""),
        "scene_id": scene_info.get("displayId", scene_info.get("entityId", "")),
        "RGB": "ok" if "RGB" in composites_ok else "error",
        "SWIR": "ok" if "SWIR" in composites_ok else "error",
        "THERMAL": "ok" if "THERMAL" in composites_ok else "error",
        "descargado": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nivel": nivel,
    }
    # Si el CSV viejo no tiene la columna 'nivel', reescribir header preservando
    # filas previas (one-time) para no romper el DictWriter.
    fieldnames = list(row.keys())
    if existe:
        try:
            with open(ruta, newline="", encoding="utf-8") as f:
                prev = list(csv.DictReader(f))
            if prev and "nivel" not in prev[0]:
                for p in prev:
                    p["nivel"] = _nivel_de_scene_id(p.get("scene_id", ""))
                with open(ruta, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writeheader()
                    w.writerows(prev)
        except Exception:
            pass
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not existe:
            w.writeheader()
        w.writerow(row)


def niveles_actuales(volcano_name: str) -> dict[str, str]:
    """fecha -> nivel ('L2'/'L1') de lo que YA tenemos en disco (RGB presente).
    Si una fecha aparece varias veces en metadata, gana la ultima (upgrades)."""
    ruta = get_metadata_path(volcano_name)
    if not os.path.isfile(ruta):
        return {}
    por_fecha = {}
    try:
        with open(ruta, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                fecha = row.get("fecha", "")
                if len(fecha) != 10:
                    continue
                nivel = row.get("nivel") or _nivel_de_scene_id(row.get("scene_id", ""))
                por_fecha[fecha] = nivel
    except Exception:
        return {}
    # Solo cuenta si el PNG RGB existe (la limpieza por retencion borra PNGs).
    return {f: n for f, n in por_fecha.items()
            if os.path.isfile(get_image_path(volcano_name, f, "RGB"))}


def limpiar_imagenes_antiguas(volcano_name: str, dias: int = DIAS_RETENCION):
    base = Path(f"docs/landsat/{volcano_name}")
    if not base.is_dir():
        return
    limite = datetime.now() - timedelta(days=dias)
    n = 0
    for f in base.glob("*.png"):
        try:
            d = datetime.strptime(f.stem.split("_")[0], "%Y-%m-%d")
            if d < limite:
                f.unlink()
                n += 1
        except ValueError:
            continue
    if n:
        log.info(f"  Limpieza: {n} PNGs antiguos borrados (>{dias}d)")


def generar_json_fechas_disponibles():
    out = {}
    base = Path("docs/landsat")
    if not base.is_dir():
        Path("docs/fechas_disponibles_landsat.json").write_text("{}", encoding="utf-8")
        return
    # FIX 2026-08-13: iterar el CONFIG, no el disco. Iterando docs/landsat/ se
    # colaba cualquier carpeta suelta como si fuera un volcan --- en particular
    # las cuarentenas _LEGACY_* --- y el dashboard las publicaba. Copernicus-v1
    # ya lo hace asi (sentinel2_downloader.generar_json_fechas_disponibles):
    # las carpetas que no estan en VOLCANES simplemente no existen para el JSON.
    for nombre in get_active_volcanoes().keys():
        vd = base / nombre
        if not vd.is_dir():
            continue
        fechas = set()
        for f in vd.glob("*_RGB.png"):
            try:
                fechas.add(f.stem.split("_")[0])
            except Exception:
                pass
        if fechas:
            out[nombre] = sorted(fechas)
    Path("docs/fechas_disponibles_landsat.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    total = sum(len(v) for v in out.values())
    log.info(f"JSON fechas regenerado: {len(out)} volcanes, {total} fechas")
    generar_json_niveles()


def generar_json_niveles():
    """docs/niveles_landsat.json: {volcan: {fecha: 'L1'|'L2'}}.
    Lo consume el dashboard/Sala para marcar las imagenes L1 como provisionales."""
    base = Path("docs/landsat")
    out = {}
    if base.is_dir():
        for vd in sorted(base.iterdir()):
            if not vd.is_dir():
                continue
            niv = niveles_actuales(vd.name)
            # solo listar las L1 (provisionales); L2 es el caso normal -> menos peso
            l1 = {f: "L1" for f, n in niv.items() if n == "L1"}
            if l1:
                out[vd.name] = l1
    Path("docs/niveles_landsat.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    n_l1 = sum(len(v) for v in out.values())
    log.info(f"JSON niveles regenerado: {n_l1} imagenes L1 provisionales en {len(out)} volcanes")


# ============================================================
# PROCESAR UN VOLCAN
# ============================================================
def procesar_volcan(nombre: str, datos: dict, client: M2MClient,
                    fecha_inicio: datetime, fecha_fin: datetime,
                    presupuesto: list[int]) -> list[str]:
    """presupuesto: lista de 1 elemento, decremento mutable del cap global."""
    lat = datos["lat"]
    lon = datos["lon"]
    buffer_km = datos.get("buffer_km", BUFFER_KM)

    log.info(f"[{nombre}] Buscando escenas L2+L1 ({fecha_inicio.date()} -> {fecha_fin.date()})")
    try:
        esc_l2 = client.scene_search(lat, lon, buffer_km, fecha_inicio, fecha_fin,
                                     dataset=DATASET_L2)
    except Exception as e:
        log.error(f"  Error scene-search L2: {e}")
        esc_l2 = []
    try:
        # 'full' para traer Sun Elevation (necesario para reflectancia TOA en L1)
        esc_l1 = client.scene_search(lat, lon, buffer_km, fecha_inicio, fecha_fin,
                                     dataset=DATASET_L1, metadata_type="full")
    except Exception as e:
        log.error(f"  Error scene-search L1: {e}")
        esc_l1 = []

    # Mejor escena por fecha: L2 preferido sobre L1.
    best: dict[str, tuple] = {}
    for esc in esc_l1:
        f = esc["_date"]
        if len(f) == 10:
            best[f] = (esc, "L1")
    for esc in esc_l2:
        f = esc["_date"]
        if len(f) == 10:
            best[f] = (esc, "L2")   # pisa L1 si la misma fecha tiene L2

    if not best:
        log.info(f"  Sin escenas (ni L2 ni L1)")
        return []

    have = niveles_actuales(nombre)   # fecha -> nivel que ya tenemos en disco
    nuevas = []

    for fecha in sorted(best.keys(), reverse=True):
        esc, nivel = best[fecha]
        cur = have.get(fecha)
        if cur == "L2":
            continue                 # ya tenemos lo mejor
        if cur == nivel:
            continue                 # ya tenemos ese nivel (L1==L1, aun sin L2)
        es_upgrade = cur == "L1" and nivel == "L2"

        if presupuesto[0] <= 0:
            log.warning(f"  Tope MAX_ESCENAS_POR_RUN alcanzado, abortando")
            break

        rutas = {c: get_image_path(nombre, fecha, c) for c in ("RGB", "SWIR", "THERMAL")}
        accion = "Upgrade L1->L2" if es_upgrade else f"Descargando {nivel}"
        log.info(f"  {accion} {fecha} (cloud: {esc.get('_cloud','?')}%, "
                 f"{esc.get('displayId','?')[:38]})")

        try:
            urls = client.get_band_urls(esc["entityId"], dataset_por_nivel(nivel),
                                        bandas_por_nivel(nivel))
        except Exception as e:
            log.error(f"  Error obteniendo band URLs: {e}")
            continue
        if len(urls) < 6:
            log.warning(f"  Solo {len(urls)}/6 bandas disponibles, saltando escena")
            continue

        item = SimpleNamespace(
            assets={banda: SimpleNamespace(href=url) for banda, url in urls.items()},
            id=esc.get("displayId", ""),
            properties={"datetime": fecha, "platform": esc.get("satellite", "Landsat")},
        )
        sun_elev = esc.get("_sun") if nivel == "L1" else None
        sat = _satelite_de(esc)

        # RGB primero = PRUEBA DE COBERTURA (footprint adyacente -> nodata -> negra).
        # En upgrade siempre regeneramos (sobreescribimos el L1 provisional).
        arr_rgb = generar_rgb(item, lat, lon, buffer_km, nivel, sun_elev)
        if _sin_cobertura(arr_rgb):
            log.info(f"  Escena {fecha} sin cobertura real del volcan "
                     f"(footprint de path adyacente / nodata), descartada")
            continue

        ok = []
        guardar_png(arr_rgb, rutas["RGB"]); ok.append("RGB")
        arr_swir = generar_swir(item, lat, lon, buffer_km, nivel, sun_elev)
        if arr_swir is not None:
            guardar_png(arr_swir, rutas["SWIR"]); ok.append("SWIR")
        arr_th = generar_thermal(item, lat, lon, buffer_km, nivel, sat)
        if arr_th is not None:
            guardar_png(arr_th, rutas["THERMAL"]); ok.append("THERMAL")

        if ok:
            actualizar_metadata(nombre, fecha, esc, ok, nivel)
            nuevas.append((fecha, nivel, es_upgrade))
            presupuesto[0] -= 1
    return nuevas


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=DIAS_ATRAS,
                        help=f"Dias hacia atras (default {DIAS_ATRAS})")
    parser.add_argument("--volcan", type=str, default="",
                        help="Procesar solo un volcan (vacio = todos)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info(" LANDSAT DOWNLOADER v3 (USGS M2M API)")
    log.info(f" Dataset: {DATASET_NAME}  |  Ventana: {args.dias} dias")
    log.info("=" * 60)

    fecha_fin = datetime.now()
    fecha_inicio = fecha_fin - timedelta(days=args.dias)

    client = M2MClient()
    client.login()

    try:
        volcanes = get_active_volcanoes()
        if args.volcan:
            if args.volcan not in volcanes:
                raise SystemExit(f"Volcan '{args.volcan}' no esta en VOLCANES activos.")
            volcanes = {args.volcan: volcanes[args.volcan]}

        log.info(f"Volcanes a procesar: {len(volcanes)}")
        presupuesto = [MAX_ESCENAS_POR_RUN]
        nuevas_por_volcan = {}
        errores = 0

        for i, (nombre, datos) in enumerate(volcanes.items(), 1):
            log.info(f"[{i}/{len(volcanes)}] {nombre}")
            try:
                nuevas = procesar_volcan(nombre, datos, client,
                                         fecha_inicio, fecha_fin, presupuesto)
                if nuevas:
                    nuevas_por_volcan[nombre] = nuevas
                limpiar_imagenes_antiguas(nombre)
            except SystemExit:
                raise
            except Exception as e:
                log.error(f"  ERROR procesando {nombre}: {e}")
                errores += 1

        generar_json_fechas_disponibles()

        # Resumen por nivel (nuevas = lista de (fecha, nivel, es_upgrade))
        n_l2 = n_l1 = n_up = 0
        for lst in nuevas_por_volcan.values():
            for _f, niv, up in lst:
                if up: n_up += 1
                elif niv == "L1": n_l1 += 1
                else: n_l2 += 1
        log.info("=" * 60)
        log.info(f"Completado: {len(nuevas_por_volcan)} volcanes con cambios, {errores} errores")
        log.info(f"  Nuevas L2: {n_l2} | Nuevas L1 (provisional): {n_l1} | Upgrades L1->L2: {n_up}")
        log.info(f"Saldo escenas restante: {presupuesto[0]}/{MAX_ESCENAS_POR_RUN}")
        log.info("=" * 60)
    finally:
        client.logout()


if __name__ == "__main__":
    main()
