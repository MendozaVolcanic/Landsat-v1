"""
TIMELAPSE_AUTO.PY — Landsat-v1
GIFs de ultimos 30 dias para el dashboard.
Salida fija: docs/timelapses_landsat/{Volcano}_{tipo}.gif
"""
import os, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
import pytz
from config_landsat import VOLCANES, BUFFER_KM

VOLCANES_ACTIVOS = list(VOLCANES.keys())
DIAS = 30
DURACION_FRAME = 1000

OVERLAY_TEXTO = {
    'RGB':     'Landsat 8/9 RGB (B4-B3-B2)',
    'SWIR':    'Landsat 8/9 SWIR (B7-B6-B4)',
    'THERMAL': 'Landsat 8/9 THERMAL (B10, grados C)',
}

def crear_logo():
    logo = Image.new('RGBA', (150,50), (0,0,0,0))
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
    px_km = w/(buffer_km*2)
    barra = int(px_km*buffer_km)
    x0, y0, pad = w-barra-30, h-50, 10
    draw.rectangle([(x0-pad,y0-pad-20),(x0+barra+pad,y0+pad+10)], fill=(0,0,0,180))
    draw.rectangle([(x0-2,y0-2),(x0+barra+2,y0+8)], fill=(0,0,0,255))
    draw.rectangle([(x0,y0),(x0+barra,y0+6)], fill=(255,255,255,255))
    for i in range(int(buffer_km)+1):
        xm = x0+int((barra/buffer_km)*i)
        draw.line([(xm,y0),(xm,y0+11)], fill=(255,255,255), width=2)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    draw.text((x0+barra//2-20,y0-20), f"{buffer_km:g} km", fill=(255,255,255), font=font)
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
    if logo: ov.paste(logo,(15,15),logo)
    w = c.size[0]
    bb = draw.textbbox((0,0),fecha,font=ff); tw=bb[2]-bb[0]; x=w-tw-20
    draw.rectangle([(x-8,7),(x+tw+8,53)], fill=(0,0,0,200))
    draw.text((x,15), fecha, fill=(255,255,255), font=ff)
    txt = OVERLAY_TEXTO.get(tipo, f"Landsat 8/9 {tipo}")
    h = c.size[1]; bt=draw.textbbox((0,0),txt,font=ft); tw_t=bt[2]-bt[0]; yt=h-40
    draw.rectangle([(10,yt-5),(10+tw_t+10,yt+25)], fill=(0,0,0,200))
    draw.text((15,yt), txt, fill=(200,200,200), font=ft)
    final = Image.alpha_composite(c,ov)
    final = agregar_escala(final, buffer_km=buffer_km)
    if final.mode == 'RGBA':
        bg = Image.new('RGB',final.size,(0,0,0)); bg.paste(final,(0,0),final); final=bg
    return final

def _es_rojo(c):
    r, g, b = (c[..., i].astype(int) for i in range(3))
    return (r > 150) & (r - g > 80) & (r - b > 80)

def _mas_cercano(colores, paleta):
    """Indice del color de paleta mas cercano (exacto, distancia euclidiana)."""
    out = np.empty(len(colores), dtype=np.int32)
    p = paleta.astype(np.int32)
    for i in range(0, len(colores), 4096):
        c = colores[i:i + 4096].astype(np.int32)
        d = ((c[:, None, :] - p[None, :, :]) ** 2).sum(axis=2)
        out[i:i + 4096] = d.argmin(axis=1)
    return out

def cuantizar_reservando_rojos(img, n_rojo=32):
    """Paleta de 256 con hasta n_rojo colores reservados para los pixeles rojos.

    FIX 2026-11-16 (hallazgo 201 de la auditoria 2026-11-15): el GIF SWIR se
    guardaba sin cuantizar a mano y Pillow elegia la paleta por frecuencia; los
    pixeles de anomalia son pocos y quedaban fuera: Villarrica 2026-11-11 pasaba
    de 173 rojos a 0 (0 % por posicion), Nevados de Chillan 548 a 0, Maca 82 a 0.

    La asignacion se hace a mano y por grupos: un pixel rojo solo puede tomar un
    color rojo y uno no rojo solo un color no rojo. Una primera version dejaba
    la asignacion a Pillow, que es aproximada, y pintaba de rojo pixeles marrones
    o magenta (Nevados de Chillan: 788 rojos falsos sobre 548 reales). Asi el GIF
    no pierde anomalias ni inventa otras. Rojo = R>150, R-G>80, R-B>80.
    """
    rgb = img.convert('RGB')
    a = np.asarray(rgb)
    h, w, _ = a.shape
    plano = a.reshape(-1, 3)
    m = _es_rojo(plano)
    n = min(n_rojo, int(m.sum()))
    if n:
        rojos = Image.fromarray(plano[m].reshape(-1, 1, 3))
        pal_rojo = np.array(rojos.quantize(n, method=Image.Quantize.MEDIANCUT)
                            .getpalette()[:3 * n], dtype=np.uint8).reshape(-1, 3)
    else:
        pal_rojo = np.zeros((0, 3), dtype=np.uint8)
    n_resto = 256 - len(pal_rojo)
    resto = Image.fromarray(plano[~m].reshape(-1, 1, 3))
    pal_resto = np.array(resto.quantize(n_resto, method=Image.Quantize.MEDIANCUT)
                         .getpalette()[:3 * n_resto], dtype=np.uint8).reshape(-1, 3)
    # Un promedio de colores no rojos podria caer dentro del rango rojo: se
    # lleva al borde para que nunca cuente como anomalia.
    falso = _es_rojo(pal_resto)
    pal_resto[falso, 0] = np.minimum(pal_resto[falso, 0],
                                     pal_resto[falso, 1:].max(axis=1).astype(int) + 80).astype(np.uint8)
    idx = np.empty(len(plano), dtype=np.uint8)
    for grupo, pal, base in ((m, pal_rojo, 0), (~m, pal_resto, len(pal_rojo))):
        if not grupo.any():
            continue
        unicos, inv = np.unique(plano[grupo], axis=0, return_inverse=True)
        idx[grupo] = (_mas_cercano(unicos, pal)[inv.ravel()] + base).astype(np.uint8)
    out = Image.fromarray(idx.reshape(h, w), 'P')
    out.putpalette(np.concatenate([pal_rojo, pal_resto]).ravel().tolist())
    return out

def generar_gif_auto(volcan, tipo, logo, buffer_km=None):
    print(f"\nAuto: {volcan} / {tipo}")
    carpeta = f"docs/landsat/{volcan}"
    if not os.path.exists(carpeta): return None
    ahora = datetime.now(pytz.utc)
    limite = (ahora - timedelta(days=DIAS)).strftime('%Y-%m-%d')
    paths  = [p for p in sorted(glob.glob(f"{carpeta}/*_{tipo}.png"))
              if os.path.basename(p).split('_')[0] >= limite]
    if not paths: print(f"    Sin imagenes en ultimos {DIAS} dias"); return None
    buf = buffer_km or VOLCANES.get(volcan, {}).get('buffer_km', BUFFER_KM)
    imagenes, fechas = [], []
    for p in paths:
        try:
            img = Image.open(p)
            f   = os.path.basename(p).split('_')[0]
            fechas.append(f)
            imagenes.append(agregar_overlay(img,f,tipo,logo,buffer_km=buf))
            print(f"       {f}")
        except Exception as e:
            print(f"    Error {p}: {e}")
    if not imagenes: return None
    os.makedirs("docs/timelapses_landsat", exist_ok=True)
    out = f"docs/timelapses_landsat/{volcan}_{tipo}.gif"
    if tipo.upper() == 'THERMAL':
        frames = [img.quantize(colors=256,method=Image.Quantize.MAXCOVERAGE)
                  if img.mode=='RGB' else img for img in imagenes]
    elif tipo.upper() == 'SWIR':
        frames = [cuantizar_reservando_rojos(img) for img in imagenes]
    else:
        frames = imagenes
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=DURACION_FRAME, loop=0, optimize=True, quality=85)
    size_mb = os.path.getsize(out)/1048576
    print(f"    OK {size_mb:.2f} MB ({fechas[0]} a {fechas[-1]})")
    return out

def main():
    print("="*60)
    print(f"TIMELAPSE AUTO — ultimos {DIAS} dias")
    print("="*60)
    logo = crear_logo()
    total = 0
    for v in VOLCANES_ACTIVOS:
        buf = VOLCANES.get(v,{}).get('buffer_km', BUFFER_KM)
        for tipo in ['RGB','SWIR','THERMAL']:
            if generar_gif_auto(v, tipo, logo, buffer_km=buf): total += 1
    print(f"\n{total} GIFs auto generados")

if __name__ == "__main__":
    main()
