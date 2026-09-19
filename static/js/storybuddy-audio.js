// StoryBuddy browser audio helpers (ES module, no dependencies).
//
//   import { WavRecorder, AnswerListener, toWav } from "/static/js/storybuddy-audio.js";
//
//   const listener = new AnswerListener();
//   const wav = await listener.listen();  // auto-stops after speech + silence, or 15s quiet

export const SAMPLE_RATE = 16000;

/** Decode any browser-playable audio Blob and re-encode it as 16 kHz mono 16-bit WAV. */
export async function toWav(blob, sampleRate = SAMPLE_RATE) {
  const ctx = new AudioContext();
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  } finally {
    ctx.close();
  }
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * sampleRate)), sampleRate);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const samples = (await offline.startRendering()).getChannelData(0);
  return encodeWav(samples, sampleRate);
}

/** 16-bit PCM WAV from Float32 samples in [-1, 1]. */
export function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** Microphone recorder whose stop() resolves to a WAV Blob ready for /respond. */
export class WavRecorder {
  #recorder = null;
  #stream = null;
  #chunks = [];

  get recording() {
    return this.#recorder?.state === "recording";
  }

  get stream() {
    return this.#stream;
  }

  async start() {
    if (this.recording) return;
    this.#stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    this.#chunks = [];
    this.#recorder = new MediaRecorder(this.#stream);
    this.#recorder.ondataavailable = (e) => { if (e.data.size) this.#chunks.push(e.data); };
    this.#recorder.start();
  }

  async stop() {
    if (!this.#recorder) throw new Error("Not recording.");
    const recorder = this.#recorder;
    const chunks = this.#chunks;
    const stream = this.#stream;
    await new Promise((resolve) => {
      recorder.onstop = resolve;
      recorder.stop();
    });
    stream.getTracks().forEach((t) => t.stop());
    this.#recorder = null;
    this.#stream = null;
    return toWav(new Blob(chunks, { type: recorder.mimeType }));
  }

  cancel() {
    if (this.#recorder?.state === "recording") this.#recorder.stop();
    this.#stream?.getTracks().forEach((t) => t.stop());
    this.#recorder = null;
    this.#stream = null;
  }
}

/**
 * Auto-listens for the child's answer.
 * - Opens the mic immediately
 * - If they speak: stops ~1.2s after they go quiet (mic off)
 * - If they stay quiet: waits quietWaitMs (default 15s), then stops
 */
export class AnswerListener {
  static SPEECH_LEVEL = 0.045;
  static SILENCE_AFTER_SPEECH_MS = 1200;
  static QUIET_WAIT_MS = 15000;
  static MAX_SPEECH_MS = 12000;

  #recorder = new WavRecorder();
  #raf = 0;
  #audioCtx = null;
  #cancelled = false;
  #onLevel = null;

  get recording() {
    return this.#recorder.recording;
  }

  /** Optional callback(level 0–1, phase: "waiting"|"speaking") for UI meters. */
  onLevel(cb) {
    this.#onLevel = cb;
    return this;
  }

  cancel() {
    this.#cancelled = true;
    cancelAnimationFrame(this.#raf);
    this.#audioCtx?.close().catch(() => {});
    this.#audioCtx = null;
    this.#recorder.cancel();
  }

  /**
   * @param {{ quietWaitMs?: number }} [opts]
   * @returns {Promise<Blob>} WAV ready for /respond
   */
  async listen({ quietWaitMs = AnswerListener.QUIET_WAIT_MS } = {}) {
    this.#cancelled = false;
    await this.#recorder.start();

    this.#audioCtx = new AudioContext();
    const source = this.#audioCtx.createMediaStreamSource(this.#recorder.stream);
    const analyser = this.#audioCtx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const data = new Float32Array(analyser.fftSize);

    const started = performance.now();
    let heardSpeech = false;
    let speechStartedAt = 0;
    let lastLoudAt = 0;

    const level = () => {
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      return Math.sqrt(sum / data.length);
    };

    await new Promise((resolve) => {
      const tick = () => {
        if (this.#cancelled || !this.#recorder.recording) {
          resolve();
          return;
        }
        const now = performance.now();
        const rms = level();
        const speaking = rms >= AnswerListener.SPEECH_LEVEL;

        if (speaking) {
          if (!heardSpeech) {
            heardSpeech = true;
            speechStartedAt = now;
          }
          lastLoudAt = now;
        }

        this.#onLevel?.(Math.min(1, rms * 8), heardSpeech ? "speaking" : "waiting");

        const waitedTooLong = !heardSpeech && now - started >= quietWaitMs;
        const finishedSpeaking =
          heardSpeech && now - lastLoudAt >= AnswerListener.SILENCE_AFTER_SPEECH_MS;
        const talkedTooLong =
          heardSpeech && now - speechStartedAt >= AnswerListener.MAX_SPEECH_MS;

        if (waitedTooLong || finishedSpeaking || talkedTooLong) {
          resolve();
          return;
        }
        this.#raf = requestAnimationFrame(tick);
      };
      this.#raf = requestAnimationFrame(tick);
    });

    cancelAnimationFrame(this.#raf);
    try {
      await this.#audioCtx.close();
    } catch { /* ignore */ }
    this.#audioCtx = null;

    if (this.#cancelled) throw new DOMException("Listening cancelled", "AbortError");
    return this.#recorder.stop();
  }
}
