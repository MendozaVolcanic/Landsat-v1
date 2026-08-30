"""
Genera docs/timelapses_ppt_manifest.json mapeando:
  { "<Volcan>": { "rgb": "...", "swir": "...", "desde": "YYYY-MM-DD", "hasta": "YYYY-MM-DD" } }

Reemplaza la logica fragil del ppt_builder que adivinaba nombres de archivo.
Disenado para correr al final del cron landsat.yml y de los workflows de PPT.

El ppt_builder usa pareja RGB + SWIR (THERMAL queda solo para el visor del
dashboard; no entra en el PPT por convencion de la plantilla
Cambios_morfologicos.pptx).

Uso:
  python scripts/generar_manifest_gifs.py
"""
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
LANDSAT = DOCS / "landsat"
MANIFEST = DOCS / "timelapses_ppt_manifest.json"

# Patron: <Volcan>_<TIPO>_YYYY-MM-DD_YYYY-MM-DD.gif
PATRON = re.compile(r"^(.+)_(RGB|SWIR|THERMAL)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.gif$")


def main() -> None:
    manifest: dict[str, dict] = {}
    if not LANDSAT.exists():
        print(f"[WARN] {LANDSAT} no existe")
        MANIFEST.write_text("{}", encoding="utf-8")
        return

    for volcan_dir in sorted(LANDSAT.iterdir()):
        if not volcan_dir.is_dir() or volcan_dir.name.startswith("_LEGACY"):
            continue
        tlp = volcan_dir / "timelapses_ppt"
        if not tlp.is_dir():
            continue

        volcan = volcan_dir.name
        rgb_match = None
        swir_match = None
        for gif in tlp.glob("*.gif"):
            m = PATRON.match(gif.name)
            if not m:
                continue
            _name, tipo, desde, hasta = m.groups()
            entry = (desde, hasta, gif.name)
            # Para cada tipo nos quedamos con el rango que termina mas tarde
            # (el GIF mas reciente que el cron de PPT haya generado).
            if tipo == "RGB" and (rgb_match is None or hasta > rgb_match[1]):
                rgb_match = entry
            elif tipo == "SWIR" and (swir_match is None or hasta > swir_match[1]):
                swir_match = entry

        # Solo incluimos el volcan si tiene la pareja RGB + SWIR completa
        # (la plantilla PPT necesita ambos).
        if rgb_match and swir_match:
            manifest[volcan] = {
                "rgb": f"landsat/{volcan}/timelapses_ppt/{rgb_match[2]}",
                "swir": f"landsat/{volcan}/timelapses_ppt/{swir_match[2]}",
                "desde": rgb_match[0],
                "hasta": rgb_match[1],
            }

    MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] Manifest con {len(manifest)} volcanes -> {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
