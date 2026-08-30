/* ============================================================================
 * countdown.js — Cuenta regresiva al próximo paso satelital (componente compartido)
 * ============================================================================
 * Extraído de sala_monitoreo.html (2026-07-26) para que la Sala Y el dashboard
 * usen EL MISMO componente: antes vivía solo en la Sala y duplicarlo garantizaba
 * que divergieran. Fuente única de verdad para la lógica y el CSS del badge.
 *
 * QUÉ MUESTRA (y por qué así):
 * El PASO del satélite es casi-certeza (mecánica orbital). La aparición de la
 * IMAGEN es una distribución (latencia de procesamiento + nubes). Por eso NO se
 * muestra un número al minuto para la imagen (sería falsa precisión): se cuenta
 * al paso y se da una VENTANA de disponibilidad. Tres estados:
 *   1. "⏱️ paso en Xh Ym"    → el paso todavía no ocurrió.
 *   2. "🛰️ imagen en camino"  → el paso ya ocurrió y la imagen está en la cola de
 *      publicación. En S2 se muestra la franja horaria (habitual=mediana →
 *      a más tardar=P95) con una barra donde el punto "ahora" avanza.
 *   3. "⚠️ dato atrasado"     → pasó el P95 sin imagen (probable nubosidad o
 *      demora del proveedor). Solo ~5% de las escenas cae acá.
 *
 * USO:
 *   <script src="countdown.js"></script>
 *   CountdownPasadas.configurar({ predS2, fechasS2, predLandsat, fechasLandsat });
 *   const badge = CountdownPasadas.crearBadge('Lascar', 's2');   // o 'inline'
 *   contenedor.appendChild(badge);
 *   CountdownPasadas.actualizarTodos('s2');       // pinta/refresca todos
 *   CountdownPasadas.iniciarTick(60000, () => 's2');
 *
 * El sensor de cada badge sale de su `dataset.sensor`, o del sensor por defecto
 * que se pase (la Sala rota entre s2/landsat; el dashboard es solo s2).
 * ========================================================================== */
window.CountdownPasadas = (() => {
  'use strict';

  // Ventana de disponibilidad de la imagen tras el paso (texto para el usuario).
  const VENTANA_IMG = { s2: '~6-8 h', landsat: '~1 día' };

  // Horizonte de latencia: cuánto tiempo tras el paso seguimos esperando la
  // imagen. Más allá, si no llegó, asumimos que esa pasada fue muy nubosa o no
  // se procesó. S2 publica ~6-8h; Landsat ~1-2 días.
  const HORIZONTE_CAMINO_H = { s2: 24, landsat: 48 };

  // Modelo de latencia de publicación. Se lee de los JSON de predicción; estos
  // son los fallbacks si falta el campo (medidos sobre el histórico real).
  const LATENCIA_FALLBACK = { s2:      { mediana_h: 4.7, p95_h: 11.7 },
                              landsat: { mediana_h: 44,  p95_h: 48 } };

  // Estado inyectado por el host (predicciones + fechas disponibles).
  const F = { predS2: null, predLandsat: null, fechasS2: null, fechasLandsat: null };

  let tickTimer = null;

  function configurar(obj) {
    if (!obj) return;
    for (const k of Object.keys(F)) if (obj[k] !== undefined) F[k] = obj[k];
  }

  // ---------------------------------------------------------------- utilidades
  function fmtCountdown(ms) {
    if (ms < 0) ms = 0;
    const min = Math.floor(ms / 60000);
    const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    return m + 'm';
  }

  // Hora local Chile "HH:MM" (DST-aware) de un instante UTC en ms.
  function hhmmChile(ms) {
    return new Date(ms).toLocaleString('es-CL', {
      timeZone: 'America/Santiago', hour: '2-digit', minute: '2-digit', hour12: false
    });
  }

  function latenciaSensor(sensor) {
    const pred = sensor === 's2' ? F.predS2 : F.predLandsat;
    const lat = pred && (pred.latencia_l2a || pred.latencia_l2);
    if (lat && lat.mediana_h != null && lat.p95_h != null) return lat;
    return LATENCIA_FALLBACK[sensor] || LATENCIA_FALLBACK.s2;
  }

  // ------------------------------------------------------------------- cálculo
  // Devuelve {linea1, ventana, detalle, camino, [atrasado], [barra]} o null si
  // no hay predicción para ese volcán.
  function info(volcan, sensor) {
    const pred = sensor === 's2' ? F.predS2 : F.predLandsat;
    const inf = pred && pred.volcanes && pred.volcanes[volcan];
    if (!inf || !inf.proxima_combinada || !inf.proxima_combinada.length) return null;
    const horaUtc = inf.hora_utc_estimada || (sensor === 's2' ? '14:34' : '14:45');
    const ventana = VENTANA_IMG[sensor] || '';
    const horizonte = (HORIZONTE_CAMINO_H[sensor] || 24) * 3600000;
    const now = Date.now();

    // Última imagen que YA tenemos (listas ascendentes -> último elemento).
    const fechasObj = sensor === 's2' ? F.fechasS2 : F.fechasLandsat;
    const lista = fechasObj && fechasObj[volcan];
    const ultimaImg = (lista && lista.length) ? lista[lista.length - 1] : null;

    const pasos = inf.proxima_combinada.map(p => ({
      p, ms: new Date(p.fecha + 'T' + horaUtc + ':00Z').getTime()
    }));

    // 1) Paso que YA ocurrió, cuya imagen aún NO tenemos, y sigue dentro del
    //    horizonte -> "imagen en camino" (prioridad: es lo más inminente).
    const reciente = pasos
      .filter(x => x.ms <= now && (now - x.ms) <= horizonte && (!ultimaImg || x.p.fecha > ultimaImg))
      .sort((a, b) => b.ms - a.ms)[0];
    if (reciente) {
      const passMs = reciente.ms;
      const detalleBase = 'Paso ' + (reciente.p.sat || '') + ' del ' + reciente.p.fecha + ' ya ocurrió';

      if (sensor === 's2') {
        // Ventana horaria + barra: la imagen aparece dentro de una franja (de la
        // hora habitual=mediana al límite "a más tardar"=P95).
        const lat = latenciaSensor('s2');
        const medMs = passMs + lat.mediana_h * 3600000;
        const p95Ms = passMs + lat.p95_h * 3600000;
        if (now < p95Ms) {
          const span = p95Ms - passMs;
          return {
            linea1: '🛰️ imagen en camino',
            ventana: 'esperada ' + hhmmChile(medMs) + ' (habitual) – ' + hhmmChile(p95Ms) + ' (a más tardar)',
            detalle: detalleBase + ' · imagen habitualmente ~' + hhmmChile(medMs) +
                     ', a más tardar ~' + hhmmChile(p95Ms) + ' (hora Chile)',
            camino: true,
            barra: { posNow: Math.max(0, Math.min(1, (now - passMs) / span)),
                     posMed: (medMs - passMs) / span }
          };
        }
        return {
          linea1: '⚠️ dato atrasado',
          ventana: 'se esperaba antes de ' + hhmmChile(p95Ms),
          detalle: detalleBase + ' · superó el plazo máximo normal (~' + hhmmChile(p95Ms) +
                   ' hora Chile) sin imagen — probable nubosidad o demora del proveedor',
          camino: false,
          atrasado: true
        };
      }

      // Landsat: latencia larga (~1 día) que cruza días -> ventana relativa.
      return {
        linea1: '🛰️ imagen en camino',
        ventana: 'en ' + (VENTANA_IMG.landsat || '~1 día'),
        detalle: detalleBase + ' · imagen ' + (VENTANA_IMG.landsat || '~1 día') + ' después del paso',
        camino: true
      };
    }

    // 2) Cuenta regresiva al próximo paso futuro.
    const futuro = pasos.find(x => x.ms > now);
    if (futuro) {
      return {
        linea1: '⏱️ paso en ' + fmtCountdown(futuro.ms - now),
        ventana: 'imagen ' + ventana,
        detalle: 'Próximo paso ' + (futuro.p.sat || '') + ': ' + futuro.p.fecha +
                 ' · imagen ' + ventana + ' después del paso',
        camino: false
      };
    }
    return null;
  }

  // -------------------------------------------------------------------- pintado
  // Sin innerHTML: el proyecto lo prohíbe con contenido dinámico (hook de
  // seguridad). Todo por createElement/textContent.
  function pintar(el, inf) {
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(document.createTextNode(inf.linea1));
    const vent = document.createElement('span');
    vent.className = 'cd-ventana';
    vent.textContent = inf.ventana;
    el.appendChild(vent);
    if (inf.barra) {
      const barra = document.createElement('div');
      barra.className = 'cd-barra';
      const tick = document.createElement('span');
      tick.className = 'cd-tick';
      tick.style.left = (inf.barra.posMed * 100).toFixed(1) + '%';
      barra.appendChild(tick);
      const dot = document.createElement('span');
      dot.className = 'cd-dot';
      dot.style.left = (inf.barra.posNow * 100).toFixed(1) + '%';
      barra.appendChild(dot);
      el.appendChild(barra);
    }
    el.classList.toggle('img-camino', !!inf.camino);
    el.classList.toggle('atrasado', !!inf.atrasado);
  }

  // Crea el badge listo para insertar.
  //   posicion: 'overlay' -> absoluto abajo-derecha (sobre la imagen; Sala)
  //             'inline'  -> en el flujo normal (dentro del header; dashboard)
  function crearBadge(volcan, sensor, posicion) {
    const el = document.createElement('div');
    el.className = 'card-countdown ' +
      (posicion === 'inline' ? 'cd-inline' : 'cd-overlay');
    el.dataset.volcan = volcan;
    if (sensor) el.dataset.sensor = sensor;
    const inf = info(volcan, sensor || 's2');
    if (inf) { pintar(el, inf); el.title = inf.detalle; }
    return el;
  }

  // Refresca todos los badges ya pintados (tick liviano, sin re-render).
  function actualizarTodos(sensorPorDefecto) {
    document.querySelectorAll('.card-countdown').forEach(el => {
      const sensor = el.dataset.sensor || sensorPorDefecto || 's2';
      const inf = info(el.dataset.volcan, sensor);
      if (!inf) {
        while (el.firstChild) el.removeChild(el.firstChild);
        el.title = '';
        return;
      }
      pintar(el, inf);
      el.title = inf.detalle;
    });
  }

  // Tick periódico. sensorFn permite que el host decida el sensor vigente
  // (la Sala rota entre s2/landsat).
  function iniciarTick(ms, sensorFn) {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(() => {
      actualizarTodos(typeof sensorFn === 'function' ? sensorFn() : sensorFn);
    }, ms || 60000);
    return tickTimer;
  }

  // Helper opcional: carga los JSON de predicción y/o fechas y los configura.
  // No lanza: si una fuente falla, el badge simplemente no aparece para ese
  // sensor (mejor que romper el dashboard entero).
  async function cargarFuentes(urls) {
    const tareas = [];
    const dest = {};
    for (const [clave, url] of Object.entries(urls || {})) {
      if (!url) continue;
      tareas.push(
        fetch(url + (url.includes('?') ? '&' : '?') + 't=' + Date.now())
          .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
          .then(j => { dest[clave] = j; })
          .catch(e => console.warn('[countdown] no se pudo cargar ' + clave + ' (' + url + '):', e))
      );
    }
    await Promise.allSettled(tareas);
    configurar(dest);
    return dest;
  }

  // ------------------------------------------------------------------ estilos
  // El CSS viaja con el componente (fuente única). Posicionamiento separado en
  // modificadores para que sirva sobre la imagen (Sala) o en el flujo (dashboard).
  const CSS = `
  .card-countdown {
    z-index: 3;
    background: rgba(0,0,0,0.62);
    color: #cdd9e5;
    padding: 2px 7px;
    border-radius: 8px;
    font-size: 0.62em;
    font-weight: 600;
    white-space: pre-line;
    text-align: right;
    line-height: 1.25;
    text-shadow: 0 1px 2px rgba(0,0,0,0.85);
  }
  .card-countdown.cd-overlay {
    position: absolute;
    bottom: 2px; right: 3px;
    max-width: calc(100% - 6px);
  }
  .card-countdown.cd-inline {
    display: block;
    margin-top: 4px;
    text-align: left;
    font-size: 0.72em;
    background: rgba(0,0,0,0.35);
    border-radius: 6px;
    padding: 3px 8px;
  }
  .card-countdown .cd-ventana {
    display: block;
    font-weight: 500;
    font-size: 0.92em;
    color: #9aa7b3;
  }
  .card-countdown.img-camino { color: #ffd479; }
  .card-countdown.img-camino .cd-ventana { color: #d8b364; }
  .card-countdown.atrasado { color: #ff6b6b; }
  .card-countdown.atrasado .cd-ventana { color: #e89090; }
  .card-countdown .cd-barra {
    position: relative; width: 100%; min-width: 92px;
    height: 4px; margin-top: 5px;
    background: #21262d; border-radius: 2px;
  }
  .card-countdown .cd-barra .cd-tick {
    position: absolute; top: -2px; width: 2px; height: 8px;
    background: #8b949e; transform: translateX(-1px);
  }
  .card-countdown .cd-barra .cd-dot {
    position: absolute; top: -3px; width: 9px; height: 9px;
    border-radius: 50%; background: #ffd479;
    border: 1.5px solid rgba(0,0,0,0.65); transform: translateX(-4.5px);
  }`;

  function inyectarCSS() {
    if (document.getElementById('countdown-css')) return;
    const st = document.createElement('style');
    st.id = 'countdown-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }
  if (document.head) inyectarCSS();
  else document.addEventListener('DOMContentLoaded', inyectarCSS);

  return { configurar, cargarFuentes, info, pintar, crearBadge,
           actualizarTodos, iniciarTick, fmtCountdown, hhmmChile,
           VENTANA_IMG, LATENCIA_FALLBACK };
})();
