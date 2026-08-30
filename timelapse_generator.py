"""
TIMELAPSE_GENERATOR.PY — Landsat-v1
GIFs timelapse RGB/SWIR/THERMAL con cache e compresion inteligente.
Vars de entorno: VOLCAN, FECHA_INICIO, FECHA_FIN (para workflows manuales).
"""
import os, glob
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from config_landsat import VOLCANES, BUFFER_KM
from gif_cache import existe_en_cache, guardar_en_cache, limpiar_cache_antiguo, estadisticas_cache
from gif_optimizer import comprimir_gif_inteligente

VOLCANES_ACTIVOS = list(VOLCANES.keys())
DURACION_FRAME   = 1000  # ms
TARGET_MB        = 1.2

OVERLAY_TEXTO = {
    'RGB':     'Landsat 8/9 RGB (B4-B3-B2)',
    'SWIR':    'Landsat 8/9 SWIR (B7-B6-B4)',
    'THERMAL': 'Landsat 8/9 THERMAL (B10, grados C)',
}

def crear_logo():
    logo = Image.new('RGBA', (150, 50), (0,0,0,0))
    draw = ImageDraw.Draw(logo)
    draw.rectangle([(0,0),(150,50)], fill=(30,50,100,240))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
    draw.text((8,18), "LANDSAT 8/9", fill=(255,255,255,255), font=font)
    return logo

def agregar_escala(img, buffer_km=3):
    draw = ImageDraw.Draw(img)
    w, h = img.size
    px_km = w / (buffer_km * 2)
    barra = int(px_km * buffer_km)
    x0, y0, pad = w-barra-30, h-50, 10
    draw.rectangle([(x0-pad, y0-pad-20),(x0+barra+pad, y0+pad+10)], fill=(0,0,0,180))
    draw.rectangle([(x0-2, y0-2),(x0+barra+2, y0+8)], fill=(0,0,0,255))
    draw.rectangle([(x0, y0),(x0+barra, y0+6)], fill=(255,255,255,255))
    for i in range(int(buffer_km)+1):
        xm = x0 + int((barra/buffer_km)*i)
        draw.line([(xm,y0),(xm,y0+11)], fill=(255,255,255), width=2)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    draw.text((x0+barra//2-20, y0-20), f"{buffer_km:g} km", fill=(255,255,255), font=font)
    return img

def agregar_overlay(img, fecha, tipo, logo, buffer_km=3):
    c = img.copy()
    if c.mode != 'RGBA': c = c.convert('RGBA')
    ov = Image.new('RGBA', c.size, (255,255,255,0))
    draw = ImageDraw.Draw(ov)
    try:
        ff = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except Exception:
        ff = ft = ImageFont.load_default()
    if logo: ov.paste(logo, (15,15), logo)
    # Fecha (arriba derecha)
    w = c.size[0]
    bb = draw.textbbox((0,0), fecha, font=ff)
    tw = bb[2]-bb[0]
    x = w-tw-20
    draw.rectangle([(x-8,7),(x+tw+8,53)], fill=(0,0,0,200))
    draw.text((x,15), fecha, fill=(255,255,255), font=ff)
    # Tipo (abajo izquierda)
    txt = OVERLAY_TEXTO.get(tipo, f"Landsat 8/9 {tipo}")
    h = c.size[1]
    bt = draw.textbbox((0,0), txt, font=ft)
    tw_t = bt[2]-bt[0]
    yt = h-40
    draw.rectangle([(10,yt-5),(10+tw_t+10,yt+25)], fill=(0,0,0,200))
    draw.text((15,yt), txt, fill=(200,200,200), font=ft)
    final = Image.alpha_composite(c, ov)
    final = agregar_escala(final, buffer_km=buffer_km)
    if final.mode == 'RGBA':
        bg = Image.new('RGB', final.size, (0,0,0))
        bg.paste(final, (0,0), final)
        final = bg
    return final

def generar_gif(volcan, tipo='RGB', logo=None, fecha_inicio=None, fecha_fin=None,
                buffer_km=None, max_cloud_cover=80):
    print(f"\n--- {volcan} / {tipo}")
    if fecha_inicio and fecha_fin:
        cached = existe_en_cache(volcan, tipo, fecha_inicio, fecha_fin)
        if cached:
            print(f"    Cache hit: {os.path.basename(cached)}")
            return cached, fecha_inicio, fecha_fin

    carpeta = f"docs/landsat/{volcan}"
    if not os.path.exists(carpeta):
        print(f"    No existe: {carpeta}"); return None

    fechas_validas = set()
    meta = f"{carpeta}/metadata.csv"
    if os.path.exists(meta):
        try:
            import pandas as pd
            df = pd.read_csv(meta)
            fechas_validas = set(df[df['cloud_cover'] <= max_cloud_cover]['fecha'].unique())
            print(f"    {len(fechas_validas)} fechas con nubes <= {max_cloud_cover}%")
        except Exception as e:
            print(f"    No se pudo leer metadata: {e}")

    paths = sorted(glob.glob(f"{carpeta}/*_{tipo}.png"))
    if not paths: print(f"    Sin imagenes {tipo}"); return None

    if fecha_inicio and fecha_fin:
        paths = [p for p in paths
                 if fecha_inicio <= os.path.basename(p).split('_')[0] <= fecha_fin
                 and (not fechas_validas or os.path.basename(p).split('_')[0] in fechas_validas)]
    if not paths: print("    Sin imagenes tras filtros"); return None

    buf = buffer_km or VOLCANES.get(volcan, {}).get('buffer_km', BUFFER_KM)
    imagenes, fechas = [], []
    for p in paths:
        try:
            img = Image.open(p)
            f   = os.path.basename(p).split('_')[0]
            fechas.append(f)
            imagenes.append(agregar_overlay(img, f, tipo, logo, buffer_km=buf))
            print(f"       {f}")
        except Exception as e:
            print(f"    Error {p}: {e}")
    if not imagenes: return None

    carpeta_gif = f"docs/landsat/{volcan}/timelapses_ppt"
    os.makedirs(carpeta_gif, exist_ok=True)
    f_ini, f_fin = fechas[0], fechas[-1]
    out = f"{carpeta_gif}/{volcan}_{tipo}_{f_ini}_{f_fin}.gif"
    try:
        size_mb = comprimir_gif_inteligente(imagenes, out, duracion=DURACION_FRAME,
                                            target_mb=TARGET_MB, tipo=tipo)
        if fecha_inicio and fecha_fin:
            guardar_en_cache(volcan, tipo, f_ini, f_fin, out)
        print(f"    OK {size_mb:.2f} MB ({f_ini} a {f_fin})")
        return out, f_ini, f_fin
    except Exception as e:
        print(f"    Error generando GIF: {e}"); return None

def main():
    print("="*60)
    print("TIMELAPSE GENERATOR — Landsat-v1")
    print("="*60)
    limpiar_cache_antiguo(dias=90)
    volcan_env      = os.getenv('VOLCAN')
    fecha_inicio    = os.getenv('FECHA_INICIO')
    fecha_fin       = os.getenv('FECHA_FIN')
    volcanes        = [volcan_env] if volcan_env else VOLCANES_ACTIVOS
    logo            = crear_logo()
    resultados      = []
    for v in volcanes:
        buf = VOLCANES.get(v, {}).get('buffer_km', BUFFER_KM)
        for tipo in ['RGB', 'SWIR', 'THERMAL']:
            r = generar_gif(v, tipo, logo, fecha_inicio, fecha_fin, buffer_km=buf)
            if r: resultados.append(r)
    print(f"\n{len(resultados)} GIFs generados")
    st = estadisticas_cache()
    print(f"Cache: {st['total_entradas']} entradas, {st['espacio_total_mb']} MB")

if __name__ == "__main__":
    main()
