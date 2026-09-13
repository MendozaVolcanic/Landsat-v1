# -*- coding: utf-8 -*-
"""Publica el buffer_km de cada volcan en docs/config_landsat.json.

POR QUE EXISTE
==============
Cada PNG Landsat cubre un cuadrado de lado 2 x buffer_km centrado en el volcan.
Sin ese numero no se puede dibujar una barra de escala honesta sobre la imagen.
El dashboard de Copernicus-v1 muestra escenas Landsat junto a Sentinel-2, pero
el buffer de aca NO es el mismo que el de alla (decision de encuadre por
resolucion: 30 m/px contra 20 m/px; ver comentario en config_landsat.py).
Usar el buffer de Sentinel-2 sobre una imagen Landsat daria una escala falsa
(Villarrica: 1.5 km aca, 1.0 km alla).

La unica fuente es config_landsat.py. Este JSON se deriva de ahi en cada corrida
de landsat.yml, para que no se vuelva otra copia con vida propia.

USO
===
    python scripts/generar_config_publica.py            # escribe docs/config_landsat.json
    python scripts/generar_config_publica.py --check    # sale 1 si esta desactualizado
"""
from __future__ import annotations

import argparse
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
SALIDA = os.path.join(RAIZ, "docs", "config_landsat.json")

from config_landsat import VOLCANES  # noqa: E402


def calcular():
    return {
        "descripcion": ("Derivado de config_landsat.py (no editar a mano). Cada PNG "
                        "Landsat cubre un cuadrado de lado 2 x buffer_km centrado en "
                        "el volcan."),
        "volcanes": {nombre: {"buffer_km": datos["buffer_km"]}
                     for nombre, datos in VOLCANES.items()},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    txt = json.dumps(calcular(), indent=1, ensure_ascii=False) + "\n"

    if args.check:
        try:
            with open(SALIDA, encoding="utf-8") as fh:
                actual = fh.read()
        except FileNotFoundError:
            print("[FALLA] %s no existe" % os.path.basename(SALIDA))
            return 1
        if actual.replace("\r\n", "\n") != txt:
            print("[FALLA] config_landsat.json desactualizado. Corre: "
                  "python scripts/generar_config_publica.py")
            return 1
        print("[ok] config_landsat.json al dia")
        return 0

    with open(SALIDA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(txt)
    print("[ok] %s (%d volcanes)" % (SALIDA, len(VOLCANES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
