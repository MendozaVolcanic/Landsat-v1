"""GIF_OPTIMIZER.PY — Landsat-v1"""
from PIL import Image
import numpy as np, os

def analizar_complejidad_visual(imagenes):
    if not imagenes: return 0.5
    variaciones = []
    for img in imagenes[:3]:
        if img.mode != 'RGB': img = img.convert('RGB')
        arr = np.array(img)
        variaciones.append((np.std(arr[:,:,0])+np.std(arr[:,:,1])+np.std(arr[:,:,2]))/3.0)
    return min(float(np.mean(variaciones))/100.0, 1.0)

def calcular_parametros_compresion(imagenes, target_mb=0.8):
    c = analizar_complejidad_visual(imagenes)
    print(f"    Complejidad visual: {c:.2f}")
    if   c > 0.8:  params = {'colors':256,'quality':90,'scale':1.0,'modo':'Alta detalle'}
    elif c > 0.6:  params = {'colors':192,'quality':85,'scale':1.0,'modo':'Detalle medio-alto'}
    elif c > 0.4:  params = {'colors':128,'quality':80,'scale':1.0,'modo':'Detalle medio'}
    elif c > 0.25: params = {'colors':96, 'quality':75,'scale':1.0,'modo':'Nubes parciales'}
    else:          params = {'colors':64, 'quality':70,'scale':0.95,'modo':'Nubes uniformes'}
    print(f"    Modo: {params['modo']} ({params['colors']} colores)")
    return params

def comprimir_gif_inteligente(imagenes, output_path, duracion=1000, target_mb=0.8, tipo='RGB'):
    if not imagenes: raise ValueError("Lista de imágenes vacía")
    params = calcular_parametros_compresion(imagenes, target_mb)
    # THERMAL y SWIR: MAXCOVERAGE preserva pixeles calientes (outliers estadisticos
    # raros). ADAPTIVE los descarta: medido sobre
    # docs/landsat/Descabezado Grande/2026-06-28_SWIR.png -> ADAPTIVE 0/5 pixeles
    # rojos conservados con 64/96/128/256 colores; MAXCOVERAGE 5/5 con >=128.
    # SWIR (B7-B6-B4) es el composite de anomalias termicas intensas de Landsat,
    # equivalente a SWIR_B8A de Copernicus-v1, que ya estaba cubierto alla.
    # Solo RGB usa ADAPTIVE.
    es_thermal = tipo.upper() in ('THERMAL', 'SWIR')
    if es_thermal:
        params['colors'] = 256
        params['quality'] = max(params['quality'], 90)
        print("    THERMAL: MAXCOVERAGE para preservar anomalias termicas")

    proc = imagenes
    if params['scale'] < 1.0:
        proc = [img.resize((int(img.width*params['scale']),int(img.height*params['scale'])),
                           Image.Resampling.LANCZOS) for img in imagenes]

    optimizadas = []
    for img in proc:
        if img.mode == 'RGBA':
            bg = Image.new('RGB', img.size, (255,255,255)); bg.paste(img,(0,0),img); img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        if es_thermal:
            optimizadas.append(img.quantize(colors=params['colors'],
                                            method=Image.Quantize.MAXCOVERAGE))
        else:
            optimizadas.append(img.convert('P', palette=Image.ADAPTIVE,
                                           colors=params['colors']))

    optimizadas[0].save(output_path, save_all=True, append_images=optimizadas[1:],
                        duration=duracion, loop=0, optimize=True, quality=params['quality'])
    size_mb = os.path.getsize(output_path)/1048576
    print(f"    Tamano inicial: {size_mb:.2f} MB")

    if size_mb > target_mb:
        print(f"    Excede target ({target_mb} MB), recomprimiendo...")
        reducidas = [img.resize((int(img.width*0.85),int(img.height*0.85)),
                                Image.Resampling.LANCZOS) for img in optimizadas]
        reducidas[0].save(output_path, save_all=True, append_images=reducidas[1:],
                          duration=duracion, loop=0, optimize=True,
                          quality=max(params['quality']-10, 60))
        size_mb = os.path.getsize(output_path)/1048576
    return size_mb
