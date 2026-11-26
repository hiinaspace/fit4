// Audio contour for the timeline. audio = {step, rms:[dBFS], peak:[dBFS], onset:[0..100+]} from data/audio.json.
export async function loadAudio(url = "../data/audio.json") {
  try { return await (await fetch(url)).json(); } catch { return null; }
}

const DB_LO = -50, DB_HI = 0;
const norm = (db) => Math.min(1, Math.max(0, (db - DB_LO) / (DB_HI - DB_LO)));

/**
 * drawAudio(canvas, audio, D, {color, peakColor, onsetColor, mirror=true, height=36, from=0, to=D})
 * Sizes the canvas backing store from its CSS width (clientWidth) and `height` (CSS px), devicePixelRatio aware.
 * Draws RMS as a filled mirrored contour (peak as a fainter outline, onsets as short ticks at the bottom).
 * `from`/`to` draw only that time range stretched over the full width (for a zoomed timeline).
 */
export function drawAudio(canvas, audio, D, { color = "#6ea8ff", peakColor, onsetColor, mirror = true, height = 36, from = 0, to = D, ticks = true } = {}) {
  const W = Math.max(1, canvas.clientWidth || canvas.width), H = height, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
  canvas.style.height = H + "px";
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  if (!audio || !audio.rms?.length) return;
  const { step, rms, peak, onset } = audio, span = to - from;
  const mid = mirror ? H / 2 : H - 1, amp = mirror ? H / 2 - 1 : H - 2;
  // one column per CSS pixel: max over the windows it covers, so spikes survive downsampling
  const cols = Math.ceil(W), env = new Float32Array(cols), pk = new Float32Array(cols), on = new Float32Array(cols);
  for (let x = 0; x < cols; x++) {
    const i0 = Math.max(0, Math.floor((from + (x / W) * span) / step)), i1 = Math.min(rms.length, Math.max(i0 + 1, Math.ceil((from + ((x + 1) / W) * span) / step)));
    let e = 0, p = 0, o = 0;
    for (let i = i0; i < i1; i++) { e = Math.max(e, norm(rms[i])); p = Math.max(p, norm(peak[i])); if (onset) o = Math.max(o, onset[i]); }
    env[x] = e; pk[x] = p; on[x] = o;
  }
  const path = (arr) => {
    const p = new Path2D();
    p.moveTo(0, mid);
    for (let x = 0; x < cols; x++) p.lineTo(x, mid - arr[x] * amp);
    p.lineTo(cols, mid);
    if (mirror) { for (let x = cols - 1; x >= 0; x--) p.lineTo(x, mid + arr[x] * amp); p.lineTo(0, mid); }
    p.closePath();
    return p;
  };
  g.globalAlpha = 0.28; g.fillStyle = peakColor || color; g.fill(path(pk));
  g.globalAlpha = 0.9; g.fillStyle = color; g.fill(path(env));
  g.globalAlpha = 1;
  if (ticks && onset) {
    g.fillStyle = onsetColor || "#ffffff"; g.globalAlpha = 0.7;
    for (let x = 0; x < cols; x++) if (on[x] > 70) g.fillRect(x, H - 2 - Math.min(5, (on[x] - 70) / 8), 1, 2 + Math.min(5, (on[x] - 70) / 8));
    g.globalAlpha = 1;
  }
}
