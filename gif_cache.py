"""GIF_CACHE.PY — Landsat-v1"""
import os, hashlib, json
from datetime import datetime, timedelta

CACHE_DIR   = "docs/.cache"
CACHE_INDEX = "docs/.cache/index.json"

def generar_hash_config(volcan, tipo, fecha_inicio, fecha_fin):
    return hashlib.md5(f"{volcan}_{tipo}_{fecha_inicio}_{fecha_fin}".encode()).hexdigest()

def existe_en_cache(volcan, tipo, fecha_inicio, fecha_fin):
    if not os.path.exists(CACHE_INDEX):
        return None
    try:
        with open(CACHE_INDEX) as f:
            idx = json.load(f)
    except Exception:
        return None
    h = generar_hash_config(volcan, tipo, fecha_inicio, fecha_fin)
    if h in idx:
        p = idx[h]
        if os.path.exists(p):
            return p
        del idx[h]
        with open(CACHE_INDEX, 'w') as f:
            json.dump(idx, f, indent=2)
    return None

def guardar_en_cache(volcan, tipo, fecha_inicio, fecha_fin, gif_path):
    os.makedirs(CACHE_DIR, exist_ok=True)
    idx = {}
    if os.path.exists(CACHE_INDEX):
        try:
            with open(CACHE_INDEX) as f:
                idx = json.load(f)
        except Exception:
            pass
    idx[generar_hash_config(volcan, tipo, fecha_inicio, fecha_fin)] = gif_path
    with open(CACHE_INDEX, 'w') as f:
        json.dump(idx, f, indent=2)

def limpiar_cache_antiguo(dias=90):
    import glob
    cutoff = datetime.now() - timedelta(days=dias)
    eliminados = 0
    for p in glob.glob("docs/landsat/*/timelapses_ppt/*.gif"):
        partes = os.path.basename(p).replace('.gif','').split('_')
        if len(partes) >= 4:
            try:
                if datetime.strptime(partes[-1], '%Y-%m-%d') < cutoff:
                    os.remove(p); eliminados += 1
            except ValueError:
                pass
    if os.path.exists(CACHE_INDEX):
        try:
            with open(CACHE_INDEX) as f:
                idx = json.load(f)
            clean = {k: v for k, v in idx.items() if os.path.exists(v)}
            if len(clean) < len(idx):
                with open(CACHE_INDEX, 'w') as f:
                    json.dump(clean, f, indent=2)
        except Exception:
            pass
    return eliminados

def estadisticas_cache():
    if not os.path.exists(CACHE_INDEX):
        return {'total_entradas': 0, 'espacio_total_mb': 0}
    try:
        with open(CACHE_INDEX) as f:
            idx = json.load(f)
    except Exception:
        return {'total_entradas': 0, 'espacio_total_mb': 0}
    validas = [v for v in idx.values() if os.path.exists(v)]
    espacio = sum(os.path.getsize(p) for p in validas)
    return {'total_entradas': len(validas), 'espacio_total_mb': round(espacio/1048576, 2)}
