/*
 * Mr. Sprout — Rive mascot for StoryBuddy (ES module, no build step).
 *
 *   import { mountMascot, preloadMascot } from "/static/js/mascot.js";
 *   const sobo = mountMascot(hostEl, { initial: "hello", then: "idle" });
 *   sobo.setMood("talking");        // any name in MOODS
 *   sobo.setMouth(0.6);             // 0..1 lip-sync amount (talking only)
 *   sobo.trackAudio(audioElement);  // auto lip-sync from an <audio>/Audio()
 *
 * The .riv (static/mascot/sobo.riv) has one dedicated artboard per pose:
 *   SOBO-Idle · SOBO-Hello (wave) · SOBO-Listen · SOBO-Talk · SOBO-Yes (happy nod)
 * plus a view model { mouthOpen:number, numState:number, trigState:trigger }.
 * There is no authored "sad" or "thinking" pose, so those reuse Idle/Listen with a
 * CSS modifier (see .sobo-* rules in story.css). Every mount sets:
 *   host.dataset.mood   — the current mood (handy for tests)
 *   host.dataset.artboard — the Rive artboard currently on screen
 */

const RIVE_JS = "/static/vendor/rive/rive.js";
const RIVE_WASM = "/static/vendor/rive/rive.wasm";
const RIV_SRC = "/static/mascot/sobo.riv?v=1";
const STATE_MACHINE = "State Machine";

/** mood -> { artboard, cls } ; cls is an extra CSS modifier on the mascot root. */
export const MOODS = {
  idle:      { artboard: "SOBO-Idle" },
  hello:     { artboard: "SOBO-Hello" },
  talking:   { artboard: "SOBO-Talk" },
  listening: { artboard: "SOBO-Listen" },
  thinking:  { artboard: "SOBO-Listen", cls: "sobo-thinking" },
  happy:     { artboard: "SOBO-Yes" },
  celebrate: { artboard: "SOBO-Yes", cls: "sobo-celebrate" },
  sad:       { artboard: "SOBO-Idle", cls: "sobo-sad" },
};
// Old class names / null from the pre-Rive code paths.
const ALIASES = { null: "idle", "": "idle", undefined: "idle", wave: "hello", speaking: "talking" };

// ——— One-time runtime + file loading (shared by every mount) ———————————

let runtimePromise = null;
let bufferPromise = null;

function loadRuntime() {
  if (runtimePromise) return runtimePromise;
  runtimePromise = new Promise((resolve, reject) => {
    if (window.rive) return resolve(window.rive);
    const s = document.createElement("script");
    s.src = RIVE_JS;
    s.onload = () => resolve(window.rive);
    s.onerror = () => reject(new Error("Rive runtime failed to load"));
    document.head.appendChild(s);
  }).then((rive) => {
    rive.RuntimeLoader.setWasmUrl(RIVE_WASM);
    // The runtime fetches the wasm lazily on first use; start it now so the first wave isn't delayed.
    return new Promise((res) => rive.RuntimeLoader.getInstance(() => res(rive)));
  });
  return runtimePromise;
}

function loadBuffer() {
  if (!bufferPromise) {
    bufferPromise = fetch(RIV_SRC).then((r) => {
      if (!r.ok) throw new Error(`mascot file: HTTP ${r.status}`);
      return r.arrayBuffer();
    });
  }
  return bufferPromise;
}

/** Start fetching the runtime + .riv now so the first wave has no delay. */
export function preloadMascot() {
  return Promise.all([loadRuntime(), loadBuffer()]).catch((e) => {
    console.warn("[mascot] preload failed:", e);
  });
}

// ——— Mount ———————————————————————————————————————————————————————————

export const mascots = new Set();

/**
 * @param {HTMLElement} host   Empty container; the mascot fills it.
 * @param {{initial?:string, then?:string, helloMs?:number, label?:string}} [opts]
 *   initial  mood to show on first visible frame (default "idle")
 *   then     mood to settle into after a "hello" (default "idle")
 *   helloMs  how long the wave plays before settling (default 3200)
 */
export function mountMascot(host, opts = {}) {
  const { initial = "idle", then = "idle", helloMs = 3200, label = "Mr. Sprout, your story buddy" } = opts;

  host.innerHTML = `
    <div class="sobo" role="img" aria-label="${label}">
      <canvas class="sobo-canvas sobo-front" aria-hidden="true"></canvas>
      <canvas class="sobo-canvas sobo-back" aria-hidden="true"></canvas>
      <span class="sobo-thought" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="sobo-tear" aria-hidden="true"></span>
      <span class="sobo-sparkles" aria-hidden="true"></span>
    </div>`;
  const root = host.querySelector(".sobo");
  let front = root.querySelector(".sobo-front");
  let back = root.querySelector(".sobo-back");

  let mood = null;          // requested mood (after alias resolution)
  let live = null;          // { rive, artboard } currently on screen
  let pending = 0;          // token for the newest swap request
  let mouth = 0;            // last lip-sync value 0..1
  let helloTimer = null;
  let helloHold = false;
  let visible = false;
  let destroyed = false;
  let audioStop = null;

  const api = {
    root,
    get mood() { return mood; },
    get mouth() { return mouth; },
    setMood, setMouth, trackAudio, destroy,
  };
  mascots.add(api);

  // Render only while on screen (screens toggle `hidden`); saves a Rive instance per hidden screen.
  const io = new IntersectionObserver((entries) => {
    const now = entries.some((e) => e.isIntersecting);
    if (now === visible) return;
    visible = now;
    if (visible) show(mood || initial);
    else release();
  });
  io.observe(host);

  function resolve(m) {
    const key = ALIASES[m] ?? m;
    return MOODS[key] ? key : "idle";
  }

  const MOOD_CLASSES = Object.keys(MOODS).map((k) => `sobo-${k}`);
  const MODIFIERS = ["sobo-thinking", "sobo-sad", "sobo-celebrate"];

  function setMood(next, { hold = false } = {}) {
    const key = resolve(next);
    if (key === mood) return api;   // e.g. hello -> hello must not cancel the settle timer
    clearTimeout(helloTimer);
    helloHold = hold;
    mood = key;
    host.dataset.mood = key;
    const def = MOODS[key];
    // classList (not className=) so sobo-ready / sobo-failed survive mood changes.
    root.classList.remove(...MOOD_CLASSES, ...MODIFIERS);
    root.classList.add(`sobo-${key}`);
    if (def.cls) root.classList.add(def.cls);
    if (visible) show(key);
    return api;
  }

  function setMouth(v) {
    mouth = Math.max(0, Math.min(1, v));
    const vmi = live?.rive?.viewModelInstance;
    const prop = vmi?.number?.("mouthOpen");
    if (prop) prop.value = mouth * 100;
  }

  async function show(key) {
    if (destroyed) return;
    const def = MOODS[key];
    host.dataset.artboard = def.artboard;
    if (live && live.artboard === def.artboard) { root.classList.add("sobo-ready"); return; } // same pose, CSS modifier only
    const token = ++pending;
    try {
      const [rive, buffer] = await Promise.all([loadRuntime(), loadBuffer()]);
      if (token !== pending || destroyed || !visible) return;
      const canvas = back;
      const inst = await new Promise((res, rej) => {
        const r = new rive.Rive({
          buffer,
          canvas,
          artboard: def.artboard,
          stateMachines: STATE_MACHINE,
          autoplay: true,
          autoBind: true,
          layout: new rive.Layout({ fit: rive.Fit.Contain, alignment: rive.Alignment.BottomCenter }),
          onLoad: () => res(r),
          onLoadError: (e) => rej(new Error(e?.data || "mascot load error")),
        });
      });
      if (token !== pending || destroyed || !visible) { inst.cleanup(); return; }
      fit(inst, canvas);
      // Swap: new artboard is already drawing on the back canvas; flip it to the front.
      const old = live;
      canvas.classList.replace("sobo-back", "sobo-front");
      front.classList.replace("sobo-front", "sobo-back");
      [front, back] = [canvas, front];
      live = { rive: inst, artboard: def.artboard };
      root.classList.add("sobo-ready");
      setMouth(mouth);
      // Start the wave clock only once the wave is actually on screen.
      if (key === "hello" && !helloHold) {
        clearTimeout(helloTimer);
        helloTimer = setTimeout(() => setMood(then), helloMs);
      }
      if (old) setTimeout(() => old.rive.cleanup(), 180);
    } catch (e) {
      console.warn("[mascot]", e);
      root.classList.add("sobo-failed");
    }
  }

  function fit(inst, canvas) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.max(2, Math.round(r.width * dpr));
    canvas.height = Math.max(2, Math.round(r.height * dpr));
    inst.resizeDrawingSurfaceToCanvas();
  }

  const ro = new ResizeObserver(() => {
    if (live) fit(live.rive, front);
  });
  ro.observe(host);

  function release() {
    pending++;
    if (live) { live.rive.cleanup(); live = null; }
    root.classList.remove("sobo-ready");
  }

  // Lip-sync: derive mouth openness from the audio element's loudness.
  // The element is routed to the speakers exactly once (source -> destination); each call only
  // adds a silent analyser "tap", so tracking several mascots never doubles the volume.
  function trackAudio(el) {
    if (audioStop) audioStop();
    let raf = 0;
    let analyser = null;
    let data = null;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = trackAudio.ctx || (trackAudio.ctx = new Ctx());
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      // Only reroute the element through Web Audio when the context is running (or it already is
      // routed); a suspended context would otherwise mute the narration.
      if (el.__soboSource || ctx.state === "running") {
        if (!el.__soboSource) {
          el.__soboSource = ctx.createMediaElementSource(el);
          el.__soboSource.connect(ctx.destination);
        }
        analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        el.__soboSource.connect(analyser);   // tap only; analyser is not connected onward
        data = new Uint8Array(analyser.fftSize);
      }
    } catch (e) {
      console.warn("[mascot] real lip-sync unavailable, using a synthetic mouth:", e);
      analyser = null;
    }
    // Adaptive envelope: auto-gain so quiet clips still open the mouth fully, fast attack / slow
    // release so it doesn't jitter, and a short hold so brief pauses between words don't slam it shut.
    let peak = 0.08;      // running loudness ceiling (decays slowly)
    let level = 0;        // smoothed mouth openness 0..1
    let lastVoice = 0;    // last time the audio was above the silence threshold
    const HOLD_MS = 380, FLOOR = 0.18;
    const tick = (now) => {
      if (el.paused) { level = 0; setMouth(0); raf = requestAnimationFrame(tick); return; }
      let target;
      if (analyser) {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
        const rms = Math.sqrt(sum / data.length);
        peak = Math.max(rms, peak * 0.9985, 0.05);
        target = Math.min(1, rms / (peak * 0.85));
        if (rms > 0.012) lastVoice = now;
      } else {
        // Fallback: a syllable-ish flap so the mouth still moves while narration plays.
        target = 0.5 + 0.4 * Math.sin(now / 90) * Math.sin(now / 230);
        lastVoice = now;
      }
      if (now - lastVoice < HOLD_MS) target = Math.max(target, FLOOR);   // bridge tiny gaps
      else if (now - lastVoice >= HOLD_MS) target = 0;                     // real silence
      level += (target - level) * (target > level ? 0.6 : 0.18);
      setMouth(level);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    audioStop = () => {
      cancelAnimationFrame(raf);
      try { analyser?.disconnect(); } catch { /* already gone */ }
      setMouth(0);
      audioStop = null;
    };
    return audioStop;
  }

  function destroy() {
    destroyed = true;
    clearTimeout(helloTimer);
    io.disconnect();
    ro.disconnect();
    if (audioStop) audioStop();
    release();
    mascots.delete(api);
  }

  setMood(initial);
  return api;
}

// Handy for the console / automated checks: window.SoboMascot.setAll("sad")
window.SoboMascot = {
  mascots,
  setAll: (m) => mascots.forEach((s) => s.setMood(m)),
  moods: Object.keys(MOODS),
};
