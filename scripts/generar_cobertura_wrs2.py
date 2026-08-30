# -*- coding: utf-8 -*-
"""Cuantas huellas WRS-2 cubren cada volcan, y por lo tanto cada cuanto lo vemos.

POR QUE EXISTE
==============
Landsat 8 y 9 repiten cada path WRS-2 cada 16 dias, desfasados 8 entre si. Un
volcan que cae en el solape de DOS paths se ve cada ~4 dias; uno que cae en un
solo path se ve cada ~8. Es el doble de espera, es permanente, y hasta ahora el
dashboard no lo decia en ninguna parte.

El caso que lo destapo: Nevados de Chillan aparecia con 5 fechas donde sus
vecinos a 60 km tenian 14, y se sospecho que era residuo del borrado de la
cuarentena de Antuco. No lo era. El catalogo M2M ofrece escenas del path 232
sobre Chillan --el MBR de busqueda las alcanza-- pero el footprint WRS-2 real es
un paralelogramo rotado que no cubre el volcan: al renderizar
LC09_L2SP_232086_20260722 el recorte sale 98.2% nodata y el pipeline lo descarta,
correctamente. Chillan esta fuera del solape.

Sin este dato, en turno "Lascar lleva 8 dias sin imagen nueva" y "Villarrica lleva
8 dias sin imagen nueva" se leen igual, y no significan lo mismo: en Lascar es lo
normal y en Villarrica es que algo fallo.

COMO LO CALCULA
===============
Del path/row que aparece en el scene_id de cada fila de los metadata.csv ya
publicados. Es gratis, no toca la API, y refleja lo que de verdad produjo
imagenes utiles -- no lo que el catalogo ofrece, que es justamente la distincion
que confundio el diagnostico de Chillan.

USO
===
    python scripts/generar_cobertura_wrs2.py            # escribe docs/cobertura_wrs2.json
    python scripts/generar_cobertura_wrs2.py --check    # sale 1 si esta desactualizado
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, "docs", "cobertura_wrs2.json")

# Una huella util tiene que haber producido varias imagenes: un path/row con una
# o dos apariciones sueltas suele ser una escena de borde que casi no cubre el
# volcan, no una fuente real de revisita.
MIN_ESCENAS_HUELLA = 3


def path_row(scene_id: str):
    for p in (scene_id or "").split("_"):
        if len(p) == 6 and p.isdigit():
            return p
    return None


def calcular():
    datos = {}
    patron = os.path.join(RAIZ, "docs", "landsat", "*", "metadata.csv")
    for f in sorted(glob.glob(patron)):
        volcan = os.path.basename(os.path.dirname(f))
        conteo = collections.Counter()
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for fila in csv.DictReader(fh):
                    pr = path_row(fila.get("scene_id", ""))
                    if pr:
                        conteo[pr] += 1
        except OSError:
            continue
        if not conteo:
            continue
        huellas = sorted(pr for pr, n in conteo.items() if n >= MIN_ESCENAS_HUELLA)
        if not huellas:                      # todas marginales: nos quedamos con la mayor
            huellas = [conteo.most_common(1)[0][0]]
        # 16 dias por satelite y por path; L8 y L9 van desfasados 8 dias.
        revisita = round(16.0 / (2 * len(huellas)), 1)
        datos[volcan] = {
            "huellas_wrs2": huellas,
            "n_huellas": len(huellas),
            "revisita_dias_nominal": revisita,
            "escenas_por_huella": {pr: conteo[pr] for pr in huellas},
        }
    return {
        "descripcion": ("Huellas WRS-2 que realmente producen imagenes de cada volcan, "
                        "derivadas del path/row del scene_id en los metadata.csv "
                        "publicados. revisita_dias_nominal = 16 / (2 huellas), porque "
                        "Landsat 8 y 9 repiten cada path cada 16 dias desfasados 8."),
        "min_escenas_para_contar_una_huella": MIN_ESCENAS_HUELLA,
        "volcanes": datos,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    nuevo = calcular()
    if not nuevo["volcanes"]:
        print("[ERROR] no encontre ningun metadata.csv bajo docs/landsat/")
        return 2

    txt = json.dumps(nuevo, indent=1, ensure_ascii=False) + "\n"

    if args.check:
        try:
            with open(SALIDA, encoding="utf-8") as fh:
                actual = fh.read()
        except FileNotFoundError:
            print("[FALLA] %s no existe" % os.path.basename(SALIDA))
            return 1
        if actual.replace("\r\n", "\n") != txt:
            print("[FALLA] cobertura_wrs2.json desactualizado. Corre: "
                  "python scripts/generar_cobertura_wrs2.py")
            return 1
        print("[ok] cobertura_wrs2.json al dia")
        return 0

    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(txt)

    por_n = collections.Counter(v["n_huellas"] for v in nuevo["volcanes"].values())
    print("[ok] %s" % SALIDA)
    print("   volcanes por numero de huellas: %s" % dict(sorted(por_n.items())))
    solos = sorted(k for k, v in nuevo["volcanes"].items() if v["n_huellas"] == 1)
    print("   con UNA sola huella (revisita ~8 d, el doble de espera): %s" % ", ".join(solos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
