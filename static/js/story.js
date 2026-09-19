/**
 * StoryBuddy child-facing story page.
 * Loop matches the backend: start session → narrate beat → voice respond → reaction → next / finish.
 */
import { AnswerListener } from "/static/js/storybuddy-audio.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const QUIET_WAIT_MS = 15000;

// Storybook painted scenes — same soft color schema as the home tree landscape.
const BUNNY_SRC = "/static/img/buddy-bunny.png?v=14";
const HOME_ART = "/static/img/story-home.png?v=2";

const STORY_SCENES = {
  home: {
    theme: "home",
    art: HOME_ART,
  },
  "rosie-shares": {
    theme: "rosie",
    label: "Rosie Shares a Carrot",
    icon: "🥕",
    art: "/static/img/story-rosie-shares.png?v=7",
  },
  "leo-tells-the-truth": {
    theme: "leo",
    label: "Leo Tells the Truth",
    icon: "🌼",
    art: "/static/img/story-leo-tells-the-truth.png?v=7",
  },
  "sammy-swims": {
    theme: "sammy",
    label: "Sammy Swims to the Island",
    icon: "🦆",
    art: "/static/img/story-sammy-swims.png?v=7",
  },
};

const state = {
  story: null,
  sessionId: null,
  beat: 0,
  busy: false,
  results: [],
  runId: 0,
  audio: new Audio(),
  cue: new Audio(),
  listener: null,
  storyCards: [],
  nextInRotation: null,
  selectedStoryId: null,
};

// ——— White bunny narrator (same art on every page) ————————————————

function mountBuddy(hostId) {
  const host = $(hostId);
  host.innerHTML = `
    <div class="buddy" role="img" aria-label="Story bunny">
      <img class="buddy-art" src="${BUNNY_SRC}" alt="" draggable="false">
      <span class="buddy-sparkles" aria-hidden="true"></span>
    </div>`;
  return host.querySelector(".buddy");
}

const buddies = { auth: null, home: null, story: null, finish: null };

function setMood(mood) {
  const el = buddies.story || buddies.home || buddies.auth;
  if (!el) return;
  el.classList.remove("talking", "happy", "sad", "thinking", "celebrate", "listening");
  if (mood) el.classList.add(mood);
}

function setAllMood(mood) {
  Object.values(buddies).forEach((el) => {
    if (!el) return;
    el.classList.remove("talking", "happy", "sad", "thinking", "celebrate", "listening");
    if (mood) el.classList.add(mood);
  });
}

const SENTIMENT_MOOD = {
  happy: "happy",
  correct: "happy",
  sad: "sad",
  frustrated: "sad",
  neutral: null,
};

// ——— Theme / scene ————————————————————————————————————————————————————

function applyTheme(storyId) {
  const key = STORY_SCENES[storyId] ? storyId : "home";
  const cfg = STORY_SCENES[key];
  document.body.className = `theme-${cfg.theme}`;
  document.body.dataset.theme = cfg.theme;
  $("scene-art").style.backgroundImage = `url("${cfg.art}")`;
  const live = $("scene-live");
  if (live) live.innerHTML = "";
}

function renderStoryPicker() {
  const box = $("story-pick");
  if (!box) return;
  const cards = state.storyCards.length
    ? state.storyCards
    : Object.keys(STORY_SCENES)
        .filter((id) => id !== "home")
        .map((id) => ({ id, title: STORY_SCENES[id].label }));

  box.innerHTML = cards
    .map((s) => {
      const meta = STORY_SCENES[s.id] || {};
      const title = esc(s.title || meta.label || s.id);
      const sel = state.selectedStoryId === s.id ? " selected" : "";
      const art = esc(meta.art || HOME_ART);
      return `<button type="button" class="story-card${sel}" data-story-id="${esc(s.id)}" role="listitem">
        <span class="story-card-art" style="background-image:url('${art}')" aria-hidden="true"></span>
        <span class="story-card-title">${title}</span>
      </button>`;
    })
    .join("");
}

// ——— Auth —————————————————————————————————————————————————————————————

const AUTH_KEY = "storybuddy_token";
let authMode = "login"; // or "signup"
let currentUser = null;

function getToken() {
  return localStorage.getItem(AUTH_KEY);
}

function setSession(username, token) {
  currentUser = username;
  localStorage.setItem(AUTH_KEY, token);
  $("home-hello").textContent = `Hi, ${username}!`;
}

function clearSession() {
  currentUser = null;
  localStorage.removeItem(AUTH_KEY);
}

function authMsg(text, isError = false) {
  const el = $("auth-msg");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

function setAuthMode(mode) {
  authMode = mode;
  const signup = mode === "signup";
  $("auth-tagline").textContent = signup ? "Create an account to start" : "Log in to start reading";
  $("auth-submit").textContent = signup ? "Sign up" : "Log in";
  $("auth-toggle").textContent = signup ? "Have an account? Log in" : "New here? Create an account";
  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  $("auth-form").classList.toggle("is-signup", signup);
  const parentField = $("auth-parent-field");
  const parentInput = $("auth-parent-email");
  parentField.hidden = !signup;
  parentInput.required = signup;
  parentInput.disabled = !signup;
  if (!signup) parentInput.value = "";
  authMsg("");
}

async function restoreSession() {
  const token = getToken();
  if (!token) {
    showScreen("auth");
    return false;
  }
  try {
    const res = await fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error("expired");
    const me = await res.json();
    setSession(me.username, token);
    showScreen("home");
    refreshStoryRotation();
    return true;
  } catch {
    clearSession();
    showScreen("auth");
    return false;
  }
}

$("auth-toggle").addEventListener("click", () => {
  setAuthMode(authMode === "login" ? "signup" : "login");
});

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  const parentEmail = $("auth-parent-email").value.trim();
  const btn = $("auth-submit");
  btn.disabled = true;
  authMsg(authMode === "signup" ? "Creating your account…" : "Logging in…");
  try {
    const payload = { username, password };
    if (authMode === "signup") payload.parent_email = parentEmail;
    const res = await fetch(authMode === "signup" ? "/api/auth/signup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await errorText(res));
    const data = await res.json();
    setSession(data.username, data.token);
    $("auth-password").value = "";
    $("auth-parent-email").value = "";
    authMsg("");
    showScreen("home");
    refreshStoryRotation();
  } catch (err) {
    authMsg(err.message || "Could not sign in.", true);
  } finally {
    btn.disabled = false;
  }
});

$("btn-logout").addEventListener("click", async () => {
  const token = getToken();
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch { /* ignore */ }
  clearSession();
  setAuthMode("login");
  showScreen("auth");
  setAllMood("happy");
});

// ——— Screens ——————————————————————————————————————————————————————————

function showScreen(name) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  $(`screen-${name}`).classList.add("active");
  if (name === "home" || name === "auth") {
    applyTheme("home");
  }
}

function setLoading(on, text = "Getting ready…") {
  const el = $("loading");
  el.textContent = text;
  el.classList.toggle("show", on);
}

function status(text, isError = false) {
  const el = $("status-msg");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

async function errorText(res) {
  try {
    const j = await res.json();
    return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
  } catch {
    return `Something went wrong (${res.status})`;
  }
}

// ——— Audio (always on for the child) ——————————————————————————————————

function stopAudio() {
  state.audio.pause();
  state.audio.currentTime = 0;
  state.cue.pause();
}

function cancelListening() {
  state.listener?.cancel();
  state.listener = null;
  $("btn-mic").classList.remove("recording", "listening");
  $("level-fill").style.width = "0%";
  $("rec-timer").textContent = "";
}

async function fetchSpeech(text, cache) {
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, cache }),
    });
    if (!res.ok) throw new Error(await errorText(res));
    return URL.createObjectURL(await res.blob());
  } catch (e) {
    status(`Voice: ${e.message}`, true);
    return null;
  }
}

async function playSpeech(urlPromise) {
  const run = state.runId;
  try {
    const url = await urlPromise;
    if (!url || run !== state.runId) return;
    stopAudio();
    if (state.audio.src) URL.revokeObjectURL(state.audio.src);
    state.audio.src = url;
    setMood("talking");
    await new Promise((resolve) => {
      state.audio.onended = resolve;
      state.audio.onpause = resolve;
      state.audio.play().catch(resolve);
    });
    if (run === state.runId) setMood(null);
  } catch (e) {
    setMood(null);
    status(`Voice: ${e.message}`, true);
  }
}

function playSound(url, volume) {
  if (!url) return Promise.resolve();
  return new Promise((resolve) => {
    state.cue.pause();
    state.cue.src = url;
    state.cue.volume = Math.max(0, Math.min(1, volume ?? 0.5));
    state.cue.onended = resolve;
    state.cue.onerror = resolve;
    state.cue.play().catch(resolve);
    setTimeout(resolve, 3000);
  });
}

function prefetchBeat(i) {
  const beat = state.story?.beats[i];
  if (!beat) return;
  fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: `${beat.narration} ${beat.prompt}`, cache: true }),
  }).catch(() => {});
}

// ——— Stories ——————————————————————————————————————————————————————————

// ——— Stories ——————————————————————————————————————————————————————————

async function refreshStoryRotation() {
  try {
    const data = await (await fetch("/api/stories")).json();
    state.storyCards = data.stories;
    state.nextInRotation = data.next_in_rotation;
    if (!state.selectedStoryId && state.nextInRotation) {
      state.selectedStoryId = state.nextInRotation;
    }
    renderStoryPicker();
  } catch {
    renderStoryPicker();
  }
}

function renderPips(current) {
  const n = state.story.beats.length;
  $("pips").innerHTML = Array.from({ length: n }, (_, i) => {
    const cls = i < current ? "done" : i === current ? "current" : "";
    return `<span class="pip ${cls}"></span>`;
  }).join("");
}

function setAnswering(on) {
  $("answer-bar").hidden = !on;
}

async function startStory(storyId = null) {
  state.runId++;
  stopAudio();
  cancelListening();
  state.results = [];
  state.beat = 0;
  state.busy = false;
  status("");
  const chosen = storyId || state.selectedStoryId || null;
  setLoading(true, "Opening the story…");
  try {
    const res = await fetch("/api/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ story_id: chosen }),
    });
    if (!res.ok) throw new Error(await errorText(res));
    const started = await res.json();
    state.story = started.story;
    state.sessionId = started.session_id;
    state.selectedStoryId = started.story.id;
  } catch (e) {
    setLoading(false);
    const hm = $("home-msg");
    if (hm) {
      hm.textContent = e.message || "Could not start the story.";
      hm.classList.add("error");
    }
    return;
  }
  setLoading(false);
  applyTheme(state.story.id);
  $("story-title").textContent = state.story.title;
  showScreen("story");
  setMood("happy");
  await showBeat(0);
}

async function showBeat(i) {
  const run = state.runId;
  const beat = state.story.beats[i];
  state.beat = i;
  renderPips(i);
  cancelListening();
  setAnswering(false);

  const bubble = $("speech-bubble");
  bubble.className = "speech";
  bubble.style.animation = "none";
  void bubble.offsetWidth;
  bubble.style.animation = "";
  $("narration").textContent = beat.narration;

  if (beat.type === "targeted" && beat.target_response) {
    const re = new RegExp(`(${beat.target_response.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i");
    if (re.test(beat.prompt)) {
      $("prompt").innerHTML = esc(beat.prompt).replace(re, `<span class="target">$1</span>`);
    } else {
      $("prompt").innerHTML = `${esc(beat.prompt)} <span class="target">${esc(beat.target_response)}</span>`;
    }
  } else {
    $("prompt").textContent = beat.prompt;
  }

  $("speech-area").querySelectorAll(".child-heard").forEach((n) => n.remove());

  $("answer-hint").textContent =
    beat.type === "targeted"
      ? `Say “${beat.target_response}” out loud`
      : "Tell me what you think!";

  prefetchBeat(i + 1);

  const spoken = `${beat.narration} ${beat.prompt}`;
  const narration = fetchSpeech(spoken, true);
  await playSound(beat.sound_effect_url, 0.5);
  await playSpeech(narration);

  if (run !== state.runId || state.busy) return;
  // After the prompt, mic turns on by itself and listens for the child.
  await beginListening(run);
}

async function beginListening(run) {
  setAnswering(true);
  setMood("listening");
  const btn = $("btn-mic");
  btn.classList.add("recording", "listening");
  btn.disabled = true;
  status("");

  const t0 = Date.now();
  const tick = setInterval(() => {
    if (!state.listener?.recording) {
      clearInterval(tick);
      return;
    }
    const sec = Math.floor((Date.now() - t0) / 1000);
    const left = Math.max(0, Math.ceil(QUIET_WAIT_MS / 1000) - sec);
    $("rec-timer").textContent =
      btn.dataset.phase === "speaking"
        ? "I hear you… keep going!"
        : `Listening… say it out loud (${left}s)`;
  }, 250);

  const listener = new AnswerListener().onLevel((lvl, phase) => {
    btn.dataset.phase = phase;
    $("level-fill").style.width = `${Math.round(lvl * 100)}%`;
    if (phase === "speaking") setMood("listening");
  });
  state.listener = listener;

  try {
    const wav = await listener.listen({ quietWaitMs: QUIET_WAIT_MS });
    clearInterval(tick);
    if (run !== state.runId) return;
    state.listener = null;
    btn.classList.remove("recording", "listening");
    $("rec-timer").textContent = "";
    $("level-fill").style.width = "0%";
    setMood("thinking");

    const form = new FormData();
    form.append("audio", wav, "answer.wav");
    await sendAnswer(form, "Listening…");
  } catch (e) {
    clearInterval(tick);
    state.listener = null;
    btn.classList.remove("recording", "listening");
    $("rec-timer").textContent = "";
    if (e?.name === "AbortError" || run !== state.runId) return;
    setMood(null);
    status("Microphone was blocked. Please allow the mic, then tap Home and start again.", true);
    setAnswering(false);
  }
}

async function sendAnswer(form, waitLabel) {
  if (state.busy) return;
  state.busy = true;
  const run = state.runId;
  stopAudio();
  setAnswering(false);
  setMood("thinking");
  status(waitLabel);

  let r;
  try {
    const res = await fetch(`/api/session/${encodeURIComponent(state.sessionId)}/respond`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(await errorText(res));
    r = await res.json();
  } catch (e) {
    state.busy = false;
    if (run !== state.runId) return;
    setMood(null);
    status(e.message, true);
    // Let them try this beat again
    await beginListening(run);
    return;
  }

  if (run !== state.runId) return;
  status("");
  state.results.push({ beat: state.story.beats[state.beat], r });

  const heard = document.createElement("div");
  heard.className = "child-heard";
  const stars = r.score
    ? Math.max(1, Math.round(r.score.accuracy * 3))
    : r.participation?.responded
      ? 3
      : 1;
  heard.innerHTML =
    `<div class="label">You said</div>` +
    `<div>${r.heard ? esc(r.heard) : "(quietly listening)"}</div>` +
    `<div class="stars">${[1, 2, 3].map((n) => `<span class="star ${n <= stars ? "on" : ""}">⭐</span>`).join("")}</div>`;
  $("speech-area").appendChild(heard);

  const bubble = $("speech-bubble");
  bubble.className = "speech reaction";
  bubble.style.animation = "none";
  void bubble.offsetWidth;
  bubble.style.animation = "";
  $("narration").textContent = "";
  $("prompt").textContent = r.reaction;

  setMood(SENTIMENT_MOOD[r.sentiment] || "happy");

  const speech = fetchSpeech(r.reaction, false);
  await playSound(r.sound.url, r.sound.volume);
  await playSpeech(speech);

  state.busy = false;
  if (run !== state.runId) return;

  // Retry: companion already invited another try — do NOT re-read the story beat.
  if (!r.beat_done) {
    await new Promise((ok) => setTimeout(ok, 350));
    if (run === state.runId) await retryBeat(r, run);
    return;
  }

  if (r.next_beat_index !== null) {
    await new Promise((ok) => setTimeout(ok, 350));
    if (run === state.runId) showBeat(r.next_beat_index);
  } else {
    finishStory();
  }
}

/** Same targeted beat, next try — mic on, no narration replay. */
async function retryBeat(r, run) {
  const beat = state.story.beats[r.beat_index ?? state.beat];
  const tryNum = (r.attempt || 1) + 1;
  const max = r.max_attempts || 3;
  $("answer-hint").textContent =
    beat?.target_response
      ? `Try again — say “${beat.target_response}” (${tryNum} of ${max})`
      : "Try again — say it out loud!";
  // Keep the companion's "let's try again" line on screen; clear old heard chips lightly.
  $("speech-area").querySelectorAll(".child-heard").forEach((n) => n.remove());
  await beginListening(run);
}

function finishStory() {
  cancelListening();
  setAnswering(false);
  const targeted = state.results.filter((x) => x.r.score);
  const acc = targeted.length
    ? Math.round((100 * targeted.reduce((a, x) => a + x.r.score.accuracy, 0)) / targeted.length)
    : 100;

  $("finish-score").textContent = `${acc}%`;
  $("finish-moral").textContent = state.story.moral;
  showScreen("finish");
  setAllMood("celebrate");
  launchConfetti();
  playSpeech(fetchSpeech("The end! Thank you for reading with me today.", true));
  refreshStoryRotation();
}

function launchConfetti() {
  const box = $("confetti");
  box.hidden = false;
  box.innerHTML = "";
  const colors = ["#ff6b5a", "#ffc93c", "#3dba8a", "#7ec8f5", "#4aa3f0", "#fff"];
  for (let i = 0; i < 48; i++) {
    const bit = document.createElement("i");
    bit.style.left = `${Math.random() * 100}%`;
    bit.style.background = colors[i % colors.length];
    bit.style.animationDuration = `${2 + Math.random() * 2.5}s`;
    bit.style.animationDelay = `${Math.random() * 0.8}s`;
    box.appendChild(bit);
  }
  setTimeout(() => {
    box.hidden = true;
    box.innerHTML = "";
  }, 4500);
}

// ——— Progress ————————————————————————————————————————————————————————

async function loadProgress() {
  $("progress-summary").textContent = "Loading…";
  $("progress-list").innerHTML = "";
  let rows;
  try {
    rows = await (await fetch("/api/sessions")).json();
  } catch {
    $("progress-summary").textContent = "Could not load progress.";
    return;
  }
  const voice = rows.filter((s) => s.mode === "voice");
  if (!rows.length) {
    $("progress-summary").textContent = "No stories finished yet. Let’s read one!";
    return;
  }
  $("progress-summary").textContent =
    `${voice.length} spoken stor${voice.length === 1 ? "y" : "ies"} saved`;

  $("progress-list").innerHTML = rows
    .slice()
    .reverse()
    .slice(0, 12)
    .map((s) => {
      const m = s.summary;
      const acc =
        m.targeted_accuracy === null ? "—" : `${Math.round(m.targeted_accuracy * 100)}%`;
      const badge = s.seeded ? "demo" : s.mode;
      return `<div style="display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:2px solid #e8f0f6;font-weight:700">
        <span>${esc(s.date.slice(0, 10))} <span style="color:var(--muted);font-size:0.85rem">(${esc(badge)})</span></span>
        <span style="color:var(--mint-deep)">${acc} ⭐</span>
      </div>`;
    })
    .join("");
}

// ——— Navigation ——————————————————————————————————————————————————————

$("story-pick").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-story-id]");
  if (!btn || state.busy) return;
  const id = btn.dataset.storyId;
  state.selectedStoryId = id;
  renderStoryPicker();
  const hm = $("home-msg");
  if (hm) {
    hm.textContent = "";
    hm.classList.remove("error");
  }
  startStory(id);
});

$("btn-surprise").addEventListener("click", () => {
  state.selectedStoryId = null;
  startStory(null);
});
$("btn-again").addEventListener("click", () => {
  showScreen("home");
  refreshStoryRotation();
});
$("btn-finish-home").addEventListener("click", () => {
  showScreen("home");
  refreshStoryRotation();
});
$("btn-home").addEventListener("click", () => {
  state.runId++;
  stopAudio();
  cancelListening();
  state.busy = false;
  showScreen("home");
  refreshStoryRotation();
});
$("btn-progress").addEventListener("click", () => {
  showScreen("progress");
  loadProgress();
});
$("btn-progress-back").addEventListener("click", () => showScreen("home"));

// ——— Boot —————————————————————————————————————————————————————————————

buddies.auth = mountBuddy("buddy-auth");
buddies.home = mountBuddy("buddy-home");
buddies.story = mountBuddy("buddy-story");
buddies.finish = mountBuddy("buddy-finish");
setAuthMode("login");
applyTheme("home");
refreshStoryRotation();
restoreSession();
