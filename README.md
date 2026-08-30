# 🛰️ Landsat-v1 — Monitoreo Volcánico con Landsat 8/9

**Landsat-v1** descarga, publica y visualiza imágenes **Landsat 8 y Landsat 9** (NASA/USGS) para los **43 volcanes activos de Chile**, con actualización automática vía GitHub Actions. Incluye dashboard interactivo propio, timelapses GIF automáticos y generación de reportes PowerPoint.

> ⚠️ Herramienta de análisis científico independiente. No reemplaza los canales oficiales de alerta de SERNAGEOMIN/OVDAS.

---

## 🌐 Dashboard

Dashboard interactivo con 5 modos de visualización, calendario de fechas, timelapses automáticos y generación de reportes:

> **[👉 Ver Dashboard Landsat-v1](https://mendozavolcanic.github.io/Landsat-v1/)**

### Modos del dashboard

| Modo | Descripción |
|---|---|
| **Vista principal** | Volcán individual con timelapses RGB/SWIR/THERMAL de últimos 30 días, calendar picker, panel de metadatos |
| **Mapa Multi-Volcán** | Grid de todos los volcanes de una zona (Norte/Centro/Sur/Austral), GIFs de fondo |
| **Monitoreo Personal** | Selección personalizada de volcanes (persiste en `localStorage`) |
| **14 Riesgosos** | Volcanes con mayor categoría de riesgo según SERNAGEOMIN |
| **PPT Individual / Completo** | Dispara workflows en GitHub Actions para generar reportes PowerPoint |

---

## 🛰️ Landsat 8 vs Landsat 9

Ambos satélites son **prácticamente idénticos** en diseño, sensores y resolución. La diferencia es orbital:

| | Landsat 8 | Landsat 9 |
|---|---|---|
| Lanzamiento | Febrero 2013 | Septiembre 2021 |
| Sensores | OLI + TIRS | OLI-2 + TIRS-2 |
| Resolución espacial | 30m (óptico), 100m (termal) | 30m (óptico), 100m (termal) |
| Revisita individual | 16 días | 16 días |
| Revisita combinada | **~8 días** (desfasados 8 días entre sí) |

---

## 📊 Composiciones espectrales

Por cada volcán y fecha disponible se generan **3 imágenes**:

### 🔵 RGB — Color Natural (B4-B3-B2)
- Aspecto visual natural del terreno
- Detección de flujos de lava solidificada, cambios morfológicos en cráteres, depósitos de ceniza, nieve/hielo

### 🟡 SWIR — Anomalías Volcánicas (B7-B6-B4)
- Nieve azul brillante; zonas calientes en amarillo/rojo/blanco
- Detecta fumarolas activas, flujos calientes, zonas de desgasificación
- Penetra humo delgado y niebla mejor que RGB

### 🔴 THERMAL — Temperatura Superficial (B10 TIRS)
- Temperatura en Kelvin con paleta fría→caliente
- 30 veces más resolución que MIROVA (MODIS 1km vs Landsat 30m remuestreado desde 100m nativo)

---

## 🌋 Red de Vigilancia — 43 Volcanes

| Zona | Volcanes |
|---|---|
| **NORTE** (8) | Taapaca, Parinacota, Guallatiri, Isluga, Irruputuncu, Ollagüe, San Pedro, Láscar |
| **CENTRO** (9) | Tupungatito, San José, Tinguiririca, Planchón-Peteroa, Descabezado Grande, Tatara-San Pedro, Laguna del Maule, Nevado de Longaví, Nevados de Chillán |
| **SUR** (13) | Antuco, Copahue, Callaqui, Lonquimay, Llaima, Sollipulli, Villarrica, Quetrupillán, Lanín, Mocho-Choshuenco, Carrán-Los Venados, Puyehue-Cordón Caulle, Antillanca-Casablanca |
| **AUSTRAL** (13) | Osorno, Calbuco, Yate, Hornopirén, Huequi, Michinmahuida, Chaitén, Corcovado, Melimoyu, Mentolat, Cay, Maca, Hudson |

**Configuración:**
- Buffer espacial: 3 km por defecto
- Cobertura: ~6 km × 6 km por volcán
- Retención: últimos 60 días

---

## 🚀 Arquitectura del Sistema

### Fuente de datos — USGS EROS Machine-to-Machine API

> **Migración 2026-05-22:** se reemplazó Microsoft Planetary Computer (lag T+5–15d) por la API oficial USGS EROS M2M. Ahora las escenas aparecen en el dashboard ~24 h después del paso del satélite, contra los ~10 días que tenía PC.

| Característica | Valor |
|---|---|
| Dataset | `landsat_ot_c2_l2` (Landsat 8/9 OLI-TIRS Collection 2 Level 2) |
| Productos | SR (Surface Reflectance) + ST (Surface Temperature) |
| Latencia | **T+1 día** desde el paso del satélite |
| Formato | Cloud-Optimized GeoTIFF (COG) — leído con `rasterio` por ventana |
| Acceso | USGS EROS — cuenta gratuita + Application Token |
| Cuota | ~10.000 descargas/día (consumimos ~250 en cron pico) |
| Costo | **$0** (sin método de pago asociado, imposible cobro accidental) |
| Ventaja clave | Window-reads HTTPS: bajamos ~200 KB por banda en vez de los 87 MB del TIF completo |

### Cómo funciona el flujo M2M

```
1. POST /login-token (username + appToken)            → apiKey
2. POST /scene-search (bbox + acquisitionFilter)      → entityIds + metadatos
3. POST /download-options (entityIds)                 → productos "Band File" disponibles
4. POST /download-request (bandas necesarias)         → URLs HTTPS directas a cada COG
5. rasterio.open(url) + window-read 800×800 px        → numpy array
6. Apilar canales + colormap → PNG composite
```

Las URLs entregadas por `download-request` apuntan a `dds.cr.usgs.gov` (CDN USGS) con la autorización embebida en el path — la HTTP GET con range requests funciona sin más auth.

### Workflows — GitHub Actions

```
landsat.yml (cron 10:00 + 20:00 UTC, o manual)
  ├── landsat_downloader.py     — descarga nuevas escenas
  ├── timelapse_auto.py         — regenera GIFs de últimos 30 días
  └── commit + push             — auto-actualiza GitHub Pages

ppt_individual.yml (workflow_dispatch)
  ├── timelapse_generator.py    — GIF custom para un volcán + rango de fechas
  └── ppt_generator.py          — PPT con timelapses RGB + SWIR

ppt_completo.yml (workflow_dispatch)
  ├── timelapse_generator.py    — GIFs de TODOS los volcanes
  ├── ppt_generator.py          — PPT individual por volcán + combinado
  └── gh release upload         — sube PPT combinado a Releases (tag: landsat-ppt-completo)
```

### Scripts

| Script | Función |
|---|---|
| `config_landsat.py` | Coordenadas de 43 volcanes, configuración de bandas |
| `landsat_downloader.py` | Motor de descarga (USGS M2M API + COG windowed read directo sobre URL CDN) |
| `timelapse_auto.py` | Genera GIFs RGB/SWIR/THERMAL de últimos 30 días → `docs/timelapses_landsat/` |
| `timelapse_generator.py` | Genera GIFs para un rango de fechas custom, con cache MD5 |
| `gif_cache.py` | Cache de GIFs por hash de config (volcán + tipo + fechas) |
| `gif_optimizer.py` | Compresión adaptativa — MAXCOVERAGE para THERMAL (preserva píxeles calientes raros), ADAPTIVE para RGB/SWIR |
| `ppt_generator.py` | PPT desde plantilla `Cambios_morfologicos.pptx` con timelapses RGB+SWIR + combinado vía ZIP |

---

## 📂 Estructura del Repositorio

```
Landsat-v1/
├── .github/workflows/
│   ├── landsat.yml           — Descarga 2x/día + timelapses auto
│   ├── ppt_individual.yml    — PPT manual de un volcán
│   └── ppt_completo.yml      — PPT manual de los 43 volcanes
│
├── docs/                     — GitHub Pages
│   ├── index.html            — Dashboard completo
│   ├── plantillas/
│   │   └── Cambios_morfologicos.pptx     — Plantilla PPT
│   ├── fechas_disponibles_landsat.json   — Índice de fechas por volcán
│   ├── timelapses_landsat/
│   │   └── {Volcan}_{RGB|SWIR|THERMAL}.gif   — GIFs auto últimos 30 días
│   ├── landsat/
│   │   └── {Volcan}/
│   │       ├── YYYY-MM-DD_RGB.png
│   │       ├── YYYY-MM-DD_SWIR.png
│   │       ├── YYYY-MM-DD_THERMAL.png
│   │       ├── metadata.csv
│   │       ├── timelapses_ppt/           — GIFs custom para PPT (cache)
│   │       └── reportes/                 — PPTs individuales
│   ├── reportes/             — PPT combinado de todos los volcanes
│   └── .cache/index.json     — Índice del cache de GIFs
│
├── config_landsat.py
├── landsat_downloader.py
├── timelapse_auto.py
├── timelapse_generator.py
├── gif_cache.py
├── gif_optimizer.py
├── ppt_generator.py
├── requirements.txt
└── README.md
```

---

## 🧪 Ejecución Local

```bash
pip install -r requirements.txt

# Credenciales USGS M2M (se obtienen en https://ers.cr.usgs.gov/profile/access)
export USGS_USERNAME="tu_usuario_eros"
export USGS_M2M_TOKEN="el_application_token_largo"

# Descarga
python landsat_downloader.py                             # Modo workflow diario (30 días)
python landsat_downloader.py --volcan "Villarrica" --dias 60
python landsat_downloader.py --dias 7                    # Solo última semana

# Timelapses
python timelapse_auto.py                                 # GIFs de últimos 30 días para todos
VOLCAN="Lascar" FECHA_INICIO=2026-03-01 FECHA_FIN=2026-04-30 python timelapse_generator.py

# PPT
VOLCAN="Lascar" FECHA_INICIO=2026-03-01 FECHA_FIN=2026-04-30 python ppt_generator.py
```

## ⚙️ Ejecución Manual en GitHub Actions

| Workflow | Trigger | Inputs |
|---|---|---|
| `Landsat Download + Timelapse Auto` | Manual / Cron | `dias`, `volcan` (opcional) |
| `PPT Individual Landsat` | Manual | `volcan` (dropdown 43), `fecha_inicio`, `fecha_fin` |
| `PPT Completo Landsat` | Manual | `fecha_inicio`, `fecha_fin` (todos los volcanes) |

PPT Completo sube el archivo a GitHub Releases tag `landsat-ppt-completo` (los archivos grandes no se commitean al repo).

---

## 📋 Formato de datos

### metadata.csv (por volcán)
```csv
fecha,satelite,cloud_cover,scene_id,RGB,SWIR,THERMAL,descargado
2026-03-23,landsat-9,0.74,LC09_...,ok,ok,ok,2026-04-04 18:32
```

### fechas_disponibles_landsat.json
```json
{
  "Villarrica": ["2026-03-24","2026-03-23", ...],
  "Lascar":     ["2026-03-23","2026-03-07", ...]
}
```

---

## 📊 Uso de Recursos

- **GitHub Actions:** ~5-15 min por corrida diaria (descarga + timelapses)
- **GitHub Actions minutos:** ~300-600 min/mes (dentro del límite free de 2,000)
- **Tamaño del repo:** ~600 MB-1 GB (incluye GIFs auto, ~150 KB promedio × 129 GIFs)
- **USGS M2M API:** ~250 download-requests por cron pico, contra cuota de ~10.000/día. Costo: $0.

---

## 🔗 Repositorios relacionados

| Sistema | Fuente | Descripción |
|---|---|---|
| [Copernicus-v1](https://github.com/MendozaVolcanic/Copernicus-v1) | Sentinel-2 (ESA) | Dashboard con misma arquitectura, 10m resolución |
| [Landsat-v1](https://github.com/MendozaVolcanic/Landsat-v1) | Landsat 8/9 (NASA/USGS) | Este repositorio |
| [Mirova-v1](https://github.com/MendozaVolcanic/Mirova-v1) | MIROVA (U. Florencia) | Monitoreo VRP |

---

## 🛠️ Tecnologías

- **Python 3.11** + requests (M2M API), rasterio (window-reads), Pillow, NumPy, pandas, pytz
- **python-pptx + lxml** para generación de reportes PowerPoint
- **Vanilla JavaScript** (DOM-only, sin frameworks) para el dashboard
- **GitHub Actions + Pages** para automatización e infraestructura

---

## 📄 Licencia y Datos

- **Código:** MIT License
- **Imágenes Landsat:** Dominio público (USGS, libre acceso)
- **Fuente API:** USGS EROS Machine-to-Machine (gratuita, requiere cuenta EROS sin tarjeta)

---

**Última actualización:** 22 de mayo de 2026 — migración a USGS M2M API
**Estado:** Producción ✅
