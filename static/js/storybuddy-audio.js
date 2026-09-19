// StoryBuddy browser audio helpers (ES module, no dependencies).
//
// The backend's speech analysis (Praat) needs WAV. Browsers record WebM/Ogg/MP4, so
// convert before uploading:
//
//   import { WavRecorder, toWav } from "/static/js/storybuddy-audio.js";
//
//   const rec = new WavRecorder();
//   await rec.start();              // asks for the microphone the first time
//   const wav = await rec.stop();   // Blob, audio/wav, 16 kHz mono 16-bit
//   form.append("audio", wav, "answer.wav");
//
//   const wav2 = await toWav(fileFromAnInput);  // any browser-decodable audio -> WAV

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
  source.connect(offline.destination); // mixes down to mono
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
  view.setUint32(16, 16, true);          // fmt chunk size
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
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

  /** Throws if the microphone is blocked or unavailable. */
  async start() {
    if (this.recording) return;
    this.#stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.#chunks = [];
    this.#recorder = new MediaRecorder(this.#stream);
    this.#recorder.ondataavailable = (e) => { if (e.data.size) this.#chunks.push(e.data); };
    this.#recorder.start();
  }

  /** Stops recording, releases the microphone, and returns the answer as WAV. */
  async stop() {
    if (!this.#recorder) throw new Error("Not recording.");
    const recorder = this.#recorder;
    await new Promise((resolve) => {
      recorder.onstop = resolve;
      recorder.stop();
    });
    this.#stream.getTracks().forEach((t) => t.stop());
    this.#recorder = null;
    return toWav(new Blob(this.#chunks, { type: recorder.mimeType }));
  }

  /** Stop without keeping the audio (e.g. the user navigated away). */
  cancel() {
    if (this.#recorder?.state === "recording") this.#recorder.stop();
    this.#stream?.getTracks().forEach((t) => t.stop());
    this.#recorder = null;
  }
}
