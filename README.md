# 🛰️ Landsat-v1 — Monitoreo Volcánico con Landsat 8/9

**Landsat-v1** descarga y publica imágenes **Landsat 8 y Landsat 9** (NASA/USGS) para los **43 volcanes activos de Chile**, con actualización automática vía GitHub Actions. Las imágenes se integran directamente en el dashboard de [Copernicus-v1](https://github.com/MendozaVolcanic/Copernicus-v1).

> ⚠️ Herramienta de análisis científico independiente. No reemplaza los canales oficiales de alerta de SERNAGEOMIN/OVDAS.

---

## 🌐 Dashboard

Las imágenes de este repositorio se visualizan en el panel **"🛰 Landsat 8/9"** del dashboard principal:

> **[👉 Ver Dashboard Copernicus-v1 (panel Landsat incluido)](https://mendozavolcanic.github.io/Copernicus-v1/)**

---

## 🛰️ Landsat 8 vs Landsat 9 — ¿Cuál es cuál?

Ambos satélites son **prácticamente idénticos** en diseño, sensores y resolución. La diferencia es orbital:

| | Landsat 8 | Landsat 9 |
|---|---|---|
| Lanzamiento | Febrero 2013 | Septiembre 2021 |
| Sensores | OLI + TIRS | OLI-2 + TIRS-2 |
| Resolución espacial | 30m (óptico), 100m (termal) | 30m (óptico), 100m (termal) |
| Revisita individual | 16 días | 16 días |
| Revisita combinada | **~8 días** (desfasados 8 días entre sí) |
| Compatibilidad | 100% intercambiables para análisis |

Al volar desfasados, entre ambos pueden cubrir el mismo volcán cada ~8 días, lo que casi duplica la frecuencia de monitoreo respecto a usar solo uno.

---

## 📊 Composiciones espectrales

Por cada volcán y fecha disponible se generan **3 imágenes**:

### 🔵 RGB — Color Natural
- **Bandas:** B4 (Rojo) + B3 (Verde) + B2 (Azul)
- **Resolución:** 30 m/píxel
- **Lo que ves:** Aspecto visual natural del terreno — igual que una fotografía aérea
- **Uso volcánico:** Detectar flujos de lava solidificada, cambios morfológicos en cráteres, depósitos de ceniza, nieve/hielo
- **Comparación directa con:** Sentinel-2 RGB (mismas bandas, diferente resolución: 10m vs 30m)

### 🟡 SWIR — Infrarrojo de Onda Corta (Anomalías Volcánicas)
- **Bandas:** B7 (SWIR2, 2.2µm) + B6 (SWIR1, 1.6µm) + B4 (Rojo)
- **Resolución:** 30 m/píxel
- **Lo que ves:** La nieve aparece azul brillante; las zonas calientes (fumarolas, coladas) aparecen en amarillo/rojo/blanco
- **Uso volcánico:** Detectar anomalías termales que no son visibles en RGB — fumarolas activas, flujos calientes, zonas de desgasificación subsuperficial
- **Ventaja clave:** Penetra humo delgado y niebla mejor que el RGB

### 🔴 THERMAL — Temperatura Superficial (Banda 10 TIRS)
- **Banda:** B10 (TIRS1, 10.9µm)
- **Resolución:** 100 m/píxel (remuestreado a 30m para visualización)
- **Lo que ves:** Temperatura en Kelvin, visualizada con paleta fría→caliente: azul = frío (<250K), amarillo = tibio, rojo = caliente (>350K)
- **Uso volcánico:** Medir temperatura superficial real, detectar anomalías termales, complementar datos de MIROVA
- **Relación con MIROVA:** MIROVA usa el sensor MODIS (1km de resolución); la banda Thermal de Landsat ofrece **30 veces más resolución** para las mismas anomalías

---

## 🌋 Red de Vigilancia — 43 Volcanes

### **ZONA NORTE (8 volcanes)**
Taapaca, Parinacota, Guallatiri, Isluga, Irruputuncu, Ollagüe, San Pedro, Láscar

### **ZONA CENTRO (9 volcanes)**
Tupungatito, San José, Tinguiririca, Planchón-Peteroa, Descabezado Grande, Tatara-San Pedro, Laguna del Maule, Nevado de Longaví, Nevados de Chillán

### **ZONA SUR (13 volcanes)**
Antuco, Copahue, Callaqui, Lonquimay, Llaima, Sollipulli, Villarrica, Quetrupillán, Lanín, Mocho-Choshuenco, Carrán-Los Venados, Puyehue-Cordón Caulle, Antillanca-Casablanca

### **ZONA AUSTRAL (13 volcanes)**
Osorno, Calbuco, Yate, Hornopirén, Huequi, Michinmahuida, Chaitén, Corcovado, Melimoyu, Mentolat, Cay, Maca, Hudson

**Configuración de descarga:**
- Buffer espacial: 3 km por defecto (variable según volcán)
- Área de cobertura: ~6 km × 6 km por volcán
- Cobertura de nubes: hasta 100% (se descargan todas las escenas disponibles)
- Retención: últimos 60 días

---

## 🚀 Arquitectura del Sistema

### Fuente de datos — Microsoft Planetary Computer

Las imágenes provienen de **Microsoft Planetary Computer**, que espeja el catálogo completo de Landsat Collection 2 Level-2 de USGS con horas de latencia:

| Característica | Valor |
|---|---|
| Colección | Landsat Collection 2 Level-2 |
| Corrección | Surface Reflectance (ya procesada) |
| Formato | Cloud-Optimized GeoTIFF (COG) |
| Acceso | Público, sin credenciales |
| Librería | `planetary-computer` + `pystac-client` |
| Ventaja clave | Descarga solo el parche del volcán (~6×6 km), no la escena completa (~180×180 km) |

### Workflow — GitHub Actions

```
Cron: 10:00 UTC y 20:00 UTC (mismo horario que Copernicus-v1)
         ↓
landsat_downloader.py --dias 2
  ├── Busca escenas nuevas por volcán (STAC search)
  ├── Si hay escena nueva → descarga bandas RGB + SWIR + THERMAL
  ├── Guarda PNGs en docs/landsat/{Volcan}/
  ├── Actualiza metadata.csv por volcán
  └── Actualiza docs/fechas_disponibles_landsat.json
         ↓
git commit y push (solo si hay imágenes nuevas)
         ↓
GitHub Pages actualiza automáticamente
         ↓
Dashboard Copernicus-v1 carga imágenes via raw.githubusercontent.com
```

### Scripts

| Script | Función |
|---|---|
| `config_landsat.py` | Coordenadas de 43 volcanes, configuración de bandas y composites |
| `landsat_downloader.py` | Motor de descarga (Planetary Computer API, COG windowed read) |

---

## 📂 Estructura del Repositorio

```
Landsat-v1/
├── .github/
│   └── workflows/
│       ├── landsat.yml      # Descarga 2x diario + commit automático
│       └── deploy.yml       # GitHub Pages (activado en push a main)
│
├── docs/                    # Carpeta pública (GitHub Pages + raw.githubusercontent.com)
│   ├── index.html           # Página informativa (dashboard en Copernicus-v1)
│   ├── fechas_disponibles_landsat.json  # Índice de fechas por volcán
│   └── landsat/
│       └── {Volcan}/
│           ├── YYYY-MM-DD_RGB.png       # Color natural
│           ├── YYYY-MM-DD_SWIR.png      # Anomalías volcánicas
│           ├── YYYY-MM-DD_THERMAL.png   # Temperatura superficial
│           └── metadata.csv             # Registro de descargas
│
├── config_landsat.py        # Configuración volcanes y bandas
├── landsat_downloader.py    # Motor de descarga
├── requirements.txt
└── README.md
```

---

## 🧪 Ejecución Local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Test con 3 volcanes (Villarrica, Lascar, Calbuco) — últimos 60 días
python landsat_downloader.py --test

# Volcán específico
python landsat_downloader.py --volcan "Villarrica" --dias 60

# Todos los volcanes (últimos 2 días — igual que el workflow diario)
python landsat_downloader.py --dias 2
```

## ⚙️ Ejecución Manual en GitHub Actions

```
Actions → Landsat Downloader → Run workflow
  dias: 60       ← para repoblar todo el histórico
  volcan: ""     ← vacío = todos los 43 volcanes
```

---

## 📋 Formato de datos

### metadata.csv (por volcán)

```csv
fecha,satelite,cloud_cover,scene_id,RGB,SWIR,THERMAL,descargado
2026-03-23,landsat-9,0.74,LC09_...,ok,ok,ok,2026-04-04 18:32
2026-03-07,landsat-8,0.39,LC08_...,ok,ok,ok,2026-04-04 18:32
```

### fechas_disponibles_landsat.json

```json
{
  "Villarrica": ["2026-03-24", "2026-03-23", "2026-03-16", ...],
  "Lascar":     ["2026-03-23", "2026-03-07", "2026-02-27", ...]
}
```

---

## 📊 Uso de Recursos

- **GitHub Actions:** ~5-15 min por corrida para 43 volcanes (depende de escenas disponibles)
- **GitHub Actions minutos:** ~300-600 min/mes (dentro del límite free de 2,000 min)
- **Tamaño del repo:** ~500-800 MB estable (retención 60 días × 43 volcanes × 3 imágenes × ~300 KB promedio)
- **Planetary Computer:** Sin límite de uso, acceso público gratuito

---

## 🔗 Repositorios relacionados

| Sistema | Fuente | Descripción |
|---|---|---|
| [Copernicus-v1](https://github.com/MendozaVolcanic/Copernicus-v1) | Sentinel-2 (ESA) | Dashboard principal — integra imágenes Landsat |
| [Landsat-v1](https://github.com/MendozaVolcanic/Landsat-v1) | Landsat 8/9 (NASA/USGS) | Este repositorio |
| [Mirova-v1](https://github.com/MendozaVolcanic/Mirova-v1) | MIROVA (U. Florencia) | Monitoreo de radiación de potencia volcánica (VRP) |

---

## 🛠️ Tecnologías

- **Python 3.11**
- **planetary-computer** — firma automática de URLs de Planetary Computer
- **pystac-client** — búsqueda STAC estándar
- **rasterio** — lectura de COGs (Cloud-Optimized GeoTIFF) por ventana
- **Pillow** — exportación a PNG
- **NumPy** — procesamiento de bandas y composites
- **GitHub Actions + Pages** — automatización e infraestructura

---

## 📄 Licencia y Datos

- **Código:** MIT License
- **Imágenes Landsat:** Dominio público (USGS, libre acceso sin restricciones)
- **Fuente API:** Microsoft Planetary Computer (acceso gratuito, términos de uso de Microsoft)

---

**Última actualización:** Abril 2026
**Estado:** Producción ✅
