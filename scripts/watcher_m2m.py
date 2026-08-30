"""
Watcher M2M — detección barata de escenas Landsat nuevas sobre Chile.

CONTEXTO (por qué existe):
El cron de Landsat hacía 43 scene-searches (una por volcán) cada vez, aunque
no hubiera nada nuevo. USGS M2M es gratis, pero eso son 43 llamadas × N runs/día
de carga innecesaria sobre el servidor de USGS.

Este watcher hace UNA sola query Chile-wide (1.1s) que devuelve el campo
`publishDate` de cada escena. Si hay un producto landsat_ot_c2_l2 publicado
que aún no procesamos → has_new=true y corre la descarga de los 43 volcanes.
Si no → exit (el cron horario no hace nada más).

Mismo patrón que el watcher OData de Copernicus-v1, pero adaptado:
  - Allá: CDSE OData (gratis) gatea el Process API (que cuesta PU)
  - Acá: M2M scene-search (gratis) gatea la descarga (también gratis), así
    que el ahorro es carga sobre USGS + detección más rápida, no dinero.

Salida:
  - has_new=true|false en $GITHUB_OUTPUT
  - actualiza docs/.landsat_last_pub.json con la última publishDate vista

Uso:
  USGS_USERNAME=... USGS_M2M_TOKEN=... python scripts/watcher_m2m.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://m2m.cr.usgs.gov/api/api/json/stable"
# Mira AMBOS niveles: L1 (RT, ~T+1d) se publica antes que L2 (T+1 a ~T+12d).
# El downloader hibrido baja L1 para no tener hueco y upgradea a L2 cuando sale.
# Si el watcher solo mirara L2, el cron no se disparara con la L1 fresca y el
# beneficio de frescura del hibrido quedaria bloqueado.
DATASETS = ["landsat_ot_c2_l2", "landsat_ot_c2_l1"]
MARKER_PATH = "docs/.landsat_last_pub.json"

# Chile continental (mbr lat/lon)
CHILE_LL = {"latitude": -56, "longitude": -76}
CHILE_UR = {"latitude": -17, "longitude": -66}

# Ventana de adquisición a inspeccionar (los productos publicados ~T+1d)
LOOKBACK_DAYS = 5


# Un blip de USGS no deberia abrir un issue cada hora; un token vencido si.
# Reintentar separa las dos cosas: lo transitorio se absorbe, lo persistente
# llega hasta el raise y hace fallar el job.
REINTENTOS = 3
ESPERA_S = (10, 30)


def _call(ep: str, body: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Auth-Token"] = api_key
    ultimo: Exception | None = None
    for intento in range(REINTENTOS):
        try:
            req = urllib.request.Request(
                f"{API}/{ep}", data=json.dumps(body).encode(), headers=headers
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 - se re-lanza abajo
            ultimo = e
            if intento < REINTENTOS - 1:
                espera = ESPERA_S[min(intento, len(ESPERA_S) - 1)]
                print(
                    f"[watcher-landsat] {ep}: {type(e).__name__}: {e} "
                    f"-> reintento {intento + 2}/{REINTENTOS} en {espera}s"
                )
                time.sleep(espera)
    raise ultimo  # type: ignore[misc]


def leer_marcador() -> datetime:
    """Última publishDate procesada (UTC). Default: hace 6h."""
    if os.path.isfile(MARKER_PATH):
        try:
            d = json.load(open(MARKER_PATH, encoding="utf-8"))
            return datetime.fromisoformat(d["last_publication"])
        except Exception:
            pass
    return datetime.now(timezone.utc) - timedelta(hours=6)


def escribir_marcador(dt_utc: datetime) -> None:
    os.makedirs(os.path.dirname(MARKER_PATH), exist_ok=True)
    json.dump(
        {
            "last_publication": dt_utc.astimezone(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        open(MARKER_PATH, "w", encoding="utf-8"),
        indent=2,
    )


def parse_publish(s: str) -> datetime | None:
    """publishDate viene como '2026-06-02 12:14:54-05'. Normalizar a UTC.

    OJO: USGS da el offset como '-05' (solo horas). Python %z NO lo acepta
    hasta 3.11+ (necesita '-0500'). Normalizamos antes de parsear.
    """
    import re
    s = s.strip()
    # '-05' -> '-0500' (si el offset al final es solo ±HH, padear con '00')
    if re.search(r"[+-]\d{2}$", s):
        s = s + "00"
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def set_output(key: str, value: str) -> None:
    print(f"[OUTPUT] {key}={value}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main() -> None:
    user = os.environ.get("USGS_USERNAME")
    token = os.environ.get("USGS_M2M_TOKEN")
    if not user or not token:
        raise SystemExit("[ERROR] USGS_USERNAME / USGS_M2M_TOKEN no estan en el entorno.")

    marcador = leer_marcador()
    print(f"[watcher-landsat] Marcador previo (ultima publicacion): {marcador.isoformat()}")

    try:
        key = _call("login-token", {"username": user, "token": token})["data"]
        hoy = datetime.now(timezone.utc).date()
        inicio = (hoy - timedelta(days=LOOKBACK_DAYS)).isoformat()
        resultados = []
        for ds in DATASETS:
            d = _call("scene-search", {
                "datasetName": ds,
                "sceneFilter": {
                    "spatialFilter": {"filterType": "mbr", "lowerLeft": CHILE_LL, "upperRight": CHILE_UR},
                    "acquisitionFilter": {"start": inicio, "end": hoy.isoformat()},
                },
                "maxResults": 1000,   # L1 Chile-wide en 5d puede superar 100
                "metadataType": "summary",
            }, key)
            res = (d.get("data", {}) or {}).get("results", []) or []
            print(f"[watcher-landsat] {ds}: {len(res)} escenas en ventana")
            resultados.extend(res)
        _call("logout", {}, key)
    except Exception as e:
        # Un fallo del API NO es lo mismo que "no hay escena nueva". Devolver
        # has_new=false en los dos casos hacia que un token vencido, un USGS
        # caido o un timeout terminaran el job en 'success', que es
        # exactamente lo que alerta_cron_caido.yml dice cazar y no podia:
        # solo dispara con conclusion == 'failure'.
        # Las dos ramas quedan separables: la de datos sale por has_new=false
        # con exit 0; la de infraestructura sale por api_error=true con
        # exit 1, que es lo que hace fallar el job y disparar la alarma.
        print(f"[watcher-landsat] ERROR consultando M2M: {type(e).__name__}: {e}")
        set_output("has_new", "false")
        set_output("api_error", "true")
        raise SystemExit(
            f"[ERROR] M2M no respondio tras {REINTENTOS} intentos "
            f"({type(e).__name__}: {e}). El job falla a proposito para que la "
            f"alarma de cron caido lo vea."
        )

    pubs = []
    for s in resultados:
        dt = parse_publish(s.get("publishDate", "") or "")
        if dt:
            pubs.append((dt, s.get("displayId", "")))

    nuevas = [p for p in pubs if p[0] > marcador]
    if not nuevas:
        print(f"[watcher-landsat] Sin escenas nuevas (de {len(pubs)} revisadas). has_new=false")
        set_output("has_new", "false")
        return

    nuevas.sort(reverse=True)
    nueva_max = nuevas[0][0]
    print(f"[watcher-landsat] {len(nuevas)} escenas nuevas publicadas desde el marcador:")
    for dt, did in nuevas[:5]:
        print(f"           {dt.isoformat()}  {did[:45]}")

    escribir_marcador(nueva_max)
    print(f"[watcher-landsat] Marcador avanzado a: {nueva_max.astimezone(timezone.utc).isoformat()}")
    set_output("has_new", "true")


if __name__ == "__main__":
    main()
