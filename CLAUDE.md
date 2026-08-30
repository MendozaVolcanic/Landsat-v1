# Landsat-v1 — Instrucciones del proyecto

Repositorio de imágenes Landsat 8/9 + dashboard interactivo + generación de timelapses y reportes PowerPoint para 43 volcanes activos de Chile.
Descarga automática vía GitHub Actions → **USGS EROS M2M API** (T+1d, $0, sin tarjeta).
Dashboard propio en `docs/index.html` (GitHub Pages).

**Cambio 2026-05-22:** migrado desde Microsoft Planetary Computer (lag T+5-15d)
a USGS M2M API. Las escenas frescas aparecen ~24h después del paso del satélite.

## Arquitectura

```
Landsat-v1/
├── landsat_downloader.py    — Descarga Landsat 8/9 vía USGS M2M API + rasterio window-reads
├── config_landsat.py        — Centroides de los 43 volcanes + buffers
├── timelapse_auto.py        — GIFs últimos 30 días (corre en cada workflow diario)
├── timelapse_generator.py   — GIFs con rango custom (workflows PPT) + cache MD5
├── gif_cache.py             — Cache por hash (volcán+tipo+fechas) en docs/.cache/index.json
├── gif_optimizer.py         — Compresión adaptativa; THERMAL=MAXCOVERAGE, RGB/SWIR=ADAPTIVE
├── ppt_generator.py         — PPT desde plantilla con timelapses RGB+SWIR + combinado vía ZIP
├── docs/
│   ├── index.html                              — Dashboard 4 modos
│   ├── plantillas/Cambios_morfologicos.pptx    — Plantilla PPT
│   ├── fechas_disponibles_landsat.json         — Índice consumido por el dashboard
│   ├── timelapses_landsat/<volcan>_<TIPO>.gif  — GIFs auto últimos 30 días
│   ├── landsat/<volcan>/<fecha>_<TIPO>.png     — PNGs por fecha
│   ├── landsat/<volcan>/timelapses_ppt/        — Cache GIFs para PPT
│   └── landsat/<volcan>/reportes/              — PPTs individuales
└── .github/workflows/
    ├── landsat.yml          — Cron HORARIO con gate watcher M2M (no 2x/día:
    │                            cambió el 2026-06-03 y la doc no siguió).
    │                            El job 'detectar' hace 1 query Chile-wide; la
    │                            descarga de los 43 solo corre si hay escena nueva
    ├── alerta_cron_caido.yml — abre Issue si un cron termina en 'failure'
    ├── ppt_individual.yml   — PPT manual de un volcán
    └── ppt_completo.yml     — PPT de los 43 + subida a Releases
```

## Composites generados

Por cada volcán × fecha se generan 3 PNGs:
- `<fecha>_RGB.png` — B4-B3-B2 color natural
- `<fecha>_SWIR.png` — B7-B6-B4 anomalías térmicas intensas
- `<fecha>_THERMAL.png` — B10 TIRS temperatura superficial (-20°C → 80°C)

## Tonalidad — divergencia abierta con Copernicus-v1

`COMPOSITES.RGB` usa **realce lineal 3.5 sin curva gamma**. Copernicus-v1 pasó a
**gamma sRGB el 18-ago-2026** tras medir que el lineal quemaba la escena. Acá no se
propagó, así que el mismo volcán el mismo día se ve distinto en los dos dashboards:

| Volcán · fecha | Copernicus (sRGB) | Landsat (lineal 3.5) |
|---|---|---|
| Villarrica 2026-08-22 | 12,2 % blanco · 5,63 bits | 73,9 % blanco · 2,91 bits |
| Llaima 2026-08-22 | 14,4 % blanco · 5,81 bits | 87,1 % blanco · 1,52 bits |
| Lascar (±2 d, sin nieve) | 3,4 % blanco · 7,41 bits | 46,6 % blanco · 5,27 bits |

De dónde salió el 3.5: **no está escrito en ninguna parte**. No hay commit, comentario
ni nota que diga contra qué se calibró.

⏸️ **No cambiar la curva sin decisión explícita de Nicolás**: toca la tonalidad de una
serie histórica y la comparabilidad fotométrica de la serie RGB está en pausa como
decisión abierta. Ver `../Copernicus-v1/AUDITORIA_BRECHAS_2026-08-30.md`.

## Calidad de imagen — quién mide

`../Copernicus-v1/auditoria_imagenes.py --landsat <ruta>` audita **este** repo también,
ahora incluyendo el camino RGB. Lo corre a diario el workflow `calidad_imagenes.yml`
de Copernicus-v1 (23:30 UTC). No hay instrumento propio acá; si alguna vez se separan
los repos, hay que portarlo.

## Reglas

- **Escala 30m/px** para RGB/SWIR, **100m nativa resampleada a 30m** para THERMAL
- **Formato fecha**: `YYYY-MM-DD` estricto (todo el sistema parsea así)
- **Nombres de volcanes**: deben coincidir EXACTAMENTE entre `config_landsat.py`, `docs/index.html` (VOLCANES_DATA) y los workflows
- **THERMAL y SWIR en GIFs**: SIEMPRE `Image.Quantize.MAXCOVERAGE` (no ADAPTIVE) —
  ADAPTIVE descarta los píxeles rojos raros de anomalía térmica (0/44 vs 44/44 en
  Copernicus; medido acá el 30-ago: ADAPTIVE 0/5 con 64/96/128/256 colores,
  MAXCOVERAGE 5/5 con ≥128, sobre `docs/landsat/Descabezado Grande/2026-06-28_SWIR.png`).
  **SWIR estuvo un tiempo fuera** porque al portar `gif_optimizer.py` la condición se
  simplificó a una igualdad exacta con `'THERMAL'`. Si agregas un composite térmico
  nuevo, agrégalo también a esa condición.
- **PPT**: usa pareja RGB + SWIR (no THERMAL). La plantilla `Cambios_morfologicos.pptx` busca textos "color verdadero" (RGB) y "falso color" (SWIR)
- **PPT combinado**: se sube a GitHub Release tag `landsat-ppt-completo` y se borra del filesystem antes del commit (archivos grandes)
- **Dashboard JS**: usar `document.createElement` + `el.textContent` para datos del usuario; NUNCA `el.innerHTML = \`...${variable}...\`` (security hook bloquea esto)
- **Si agregás un volcán nuevo**: actualizar `config_landsat.py`, `docs/index.html` VOLCANES_DATA, y los `options` del dropdown en `.github/workflows/ppt_individual.yml`

## Dependencia con dashboard propio

URLs servidas desde GitHub Pages:
- JSON: `mendozavolcanic.github.io/Landsat-v1/fechas_disponibles_landsat.json`
- PNGs: `.../landsat/<volcan>/<fecha>_<TIPO>.png`
- GIFs auto: `.../timelapses_landsat/<volcan>_<TIPO>.gif`

Metadatos consumidos desde `raw.githubusercontent.com/MendozaVolcanic/Landsat-v1/master/docs/landsat/<volcan>/metadata.csv` (columna clave: `cloud_cover`, no `cobertura_nubosa`).

## Skills a usar proactivamente

- **`python-pro`** — scripts Python (async, type hints, retry logic)
- **`systematic-debugging`** — fallos de USGS M2M API / rasterio window-reads / PIL / python-pptx
- **`verification-before-completion`** — validar que los 3 PNG/GIF se generan antes de marcar OK
- **`github-actions-templates`** — workflows de descarga y PPT
- **`test-driven-development`** — antes de tocar lógica de composites o cache
- **`anthropic-skills:pptx`** — modificaciones a `ppt_generator.py` o a la plantilla
- **`playwright-expert`** — tests E2E del dashboard si se requieren

Ver `../../CLAUDE.md` para reglas globales del repositorio.
