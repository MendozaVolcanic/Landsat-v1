# Plan: Híbrido Landsat L2 + relleno L1-RT (2026-06-12)

## Objetivo
Bajar latencia de Landsat. L2 (reflectancia de superficie, corregido atmosféricamente)
es lo preferido, pero USGS lo procesa con cola larga (T+1 a ~T+12d). L1 (TOA, sin
corrección atmosférica) sale ~T+1 como Real-Time. Estrategia: **L2 primario; cuando
L2 no está para una fecha, bajar L1-RT para no tener hueco; reemplazar por L2 cuando
USGS lo publique**.

## Hechos verificados (con token M2M en vivo)
- Datasets: L2 `landsat_ot_c2_l2`, L1 `landsat_ot_c2_l1`.
- Bandas L1: `_B2/_B3/_B4/_B6/_B7/_B10.TIF` (sin prefijo SR_/ST_).
- Elevación solar EN metadatos de scene-search (`Sun Elevation L0RA`) -> TOA correcto sin MTL.
- Lascar: L2 solo 05-26; L1 tiene 06-03 (RT) y 06-11 (T1). Confirma el caso de uso.

## Conversiones L1 (constantes Collection-2)
- TOA reflectancia (B2-B7): ρ' = 0.00002·DN − 0.1 ; ρ = ρ' / sin(sun_elev)
- Térmico B10: L = 0.0003342·DN + 0.1 ; BT_K = K2/ln(K1/L+1) ; °C = BT_K − 273.15
  - L8: K1=774.8853 K2=1321.0789 | L9: K1=799.0284 K2=1329.2405 (elegir por LC08/LC09)

## Pasos
1. [config_landsat.py] DATASET_L2/L1, BANDAS_L2/L1, constantes L1 (TOA + K1/K2). Back-compat.
2. [downloader] scene_search(dataset=...). get_band_urls(dataset, bandas). metadataType
   "full" solo en búsqueda L1 (para sun elev).
3. [downloader] composites nivel-aware: generar_rgb/swir/thermal con (nivel, sun_elev, sat).
4. [downloader] procesar_volcan híbrido: buscar L2+L1, best-por-fecha (L2>L1), leer nivel
   actual de metadata, bajar si falta o si upgrade L1->L2, procesar y registrar `nivel`.
5. [metadata] columna `nivel` (L2/L1). Inferir de scene_id en filas viejas (L2SP/L1TP).
6. [dashboard/Sala] marcador "L1 provisional" cuando la imagen mostrada es L1. Exponer
   nivel por fecha (JSON).
7. Verificación: correr --volcan Lascar; confirmar baja 06-03/06-11 como L1, y que un
   re-run no las re-baja; cuando exista L2 las upgradea. Test del filtro _sin_cobertura sigue.

## Riesgos / notas
- L1 RGB sin corrección atmosférica = más bruma. Aceptable para morfología.
- RT usa calibración preliminar; geometría suficiente para crop de ~5km.
- No romper el filtro _sin_cobertura (nodata) ni la predicción de pasadas.
- Data integrity: el geólogo DEBE saber si ve L1 provisional vs L2.
