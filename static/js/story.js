/**
 * StoryBuddy child-facing story page.
 * Loop matches the backend: start session → narrate beat → voice respond → reaction → next / finish.
 */
import { AnswerListener } from "/static/js/storybuddy-audio.js";
import { mountMascot, preloadMascot } from "/static/js/mascot.js?v=8";

// Fetch the Rive runtime + Mr. Sprout .riv right away so the landing wave has no delay.
preloadMascot();

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const QUIET_WAIT_MS = 15000;

// Storybook painted scenes — same soft color schema as the home tree landscape.
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

// ——— Mr. Sprout, the Rive mascot (same character on every screen) ———————————
// Moods: idle · hello · talking · listening · thinking · happy · sad · celebrate.
// Every mascot is kept in sync (only the one on the visible screen renders), so a mood
// change always shows on whichever screen the child is looking at.

const buddies = { auth: null, home: null, story: null, finish: null };

function setMood(mood) {
  const next = mood || "idle";
  Object.values(buddies).forEach((m) => {
    if (m && m.mood !== next) m.setMood(next);
  });
}

function setAllMood(mood) {
  setMood(mood);
}

/** Wave hello; the mascot settles back to idle on its own after the wave. */
function wave() {
  setMood("hello");
}

/** Mascot on the screen the child is looking at right now. */
function activeBuddy() {
  const id = document.querySelector(".screen.active")?.id || "";
  return buddies[id.replace("screen-", "")] || buddies.story || buddies.home || buddies.auth;
}

const SENTIMENT_MOOD = {
  happy: "happy",
  correct: "happy",
  sad: "sad",
  frustrated: "sad",
  neutral: "idle",
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

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
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
    wave();
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
    wave();
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
  wave();
});

// ——— Screens ——————————————————————————————————————————————————————————

function showScreen(name) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  $(`screen-${name}`).classList.add("active");
  if (name === "home" || name === "auth" || name === "progress" || name === "report") {
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

let lipStop = null;

/** Lip-sync the active mascot to state.audio (one analyser at a time so audio isn't doubled). */
function startLipSync() {
  stopLipSync();
  try {
    lipStop = activeBuddy()?.trackAudio(state.audio) || null;
  } catch { lipStop = null; }
}

function stopLipSync() {
  try { lipStop?.(); } catch { /* ignore */ }
  lipStop = null;
}

async function playSpeech(urlPromise, after = null) {
  const run = state.runId;
  try {
    const url = await urlPromise;
    if (!url || run !== state.runId) return;
    stopAudio();
    if (state.audio.src) URL.revokeObjectURL(state.audio.src);
    state.audio.src = url;
    setMood("talking");
    startLipSync();
    await new Promise((resolve) => {
      state.audio.onended = resolve;
      state.audio.onpause = resolve;
      state.audio.play().catch(resolve);
    });
    stopLipSync();
    if (run === state.runId) setMood(after);
  } catch (e) {
    stopLipSync();
    setMood(after);
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
    const data = await (await fetch("/api/stories", { headers: authHeaders() })).json();
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
  const run = ++state.runId;
  stopAudio();
  stopLipSync();
  cancelListening();
  state.results = [];
  state.beat = 0;
  state.busy = false;
  status("");
  const chosen = storyId || state.selectedStoryId || null;
  setLoading(true, "Opening the story…");
  setMood("thinking"); // LLM is picking / writing the story
  try {
    const res = await fetch("/api/session/start", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ story_id: chosen }),
    });
    if (!res.ok) throw new Error(await errorText(res));
    const started = await res.json();
    state.story = started.story;
    state.sessionId = started.session_id;
    state.selectedStoryId = started.story.id;
  } catch (e) {
    setLoading(false);
    setMood(null);
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
  wave(); // greet before the first narration
  await new Promise((ok) => setTimeout(ok, 2200));
  if (run !== state.runId) return;
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
  playSpeech(fetchSpeech("The end! Thank you for reading with me today.", true), "celebrate");
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
    rows = await (await fetch("/api/sessions", { headers: authHeaders() })).json();
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

// ——— Therapist report dashboard ——————————————————————————————————————

function reportStatus(text, isError = false) {
  const el = $("report-status");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

function inlineMd(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|\s)\*(\S.*?\S|\S)\*(?=\s|$)/g, "$1<em>$2</em>");
}

function renderMdBlock(md) {
  if (!md || !md.trim()) return "<p>—</p>";
  const out = [];
  let para = [];
  let inList = false;
  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.join(" ")}</p>`);
      para = [];
    }
  };
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of md.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet && !/^\*\*/.test(line.trim())) {
      flushPara();
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      const text = bullet[1];
      let cls = "";
      if (/^Improved\b/i.test(text)) cls = "improved";
      else if (/^Watch\b/i.test(text)) cls = "watch";
      else if (/^Stable\b/i.test(text)) cls = "stable";
      out.push(`<li${cls ? ` class="${cls}"` : ""}>${inlineMd(text)}</li>`);
    } else if (!line.trim()) {
      flushPara();
      closeList();
    } else {
      closeList();
      para.push(inlineMd(line.trim()));
    }
  }
  flushPara();
  closeList();
  return out.join("") || "<p>—</p>";
}

function splitReportSections(md) {
  const text = String(md || "").trim();
  const sections = { parent: "", therapist: "", practice: "", closing: "" };
  const closingRe = /This is a practice-tracking summary[\s\S]*$/i;
  const closingMatch = text.match(closingRe);
  if (closingMatch) sections.closing = closingMatch[0].trim();
  const body = text.replace(closingRe, "").trim();

  const markers = [
    { key: "parent", re: /\*\*For the parent:\*\*/i },
    { key: "therapist", re: /\*\*For the therapist:\*\*/i },
    { key: "practice", re: /\*\*Practice suggestion:\*\*/i },
  ];
  const hits = markers
    .map((m) => {
      const found = body.match(m.re);
      return found ? { key: m.key, index: found.index, len: found[0].length } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.index - b.index);

  if (!hits.length) {
    sections.parent = body;
    return sections;
  }
  for (let i = 0; i < hits.length; i++) {
    const start = hits[i].index + hits[i].len;
    const end = i + 1 < hits.length ? hits[i + 1].index : body.length;
    sections[hits[i].key] = body.slice(start, end).trim();
  }
  return sections;
}

function fmtChange(change, unit = "") {
  if (change == null || Number.isNaN(change)) return "";
  const sign = change > 0 ? "+" : "";
  return `${sign}${change}${unit}`;
}

const CHART_META = {
  practice_word_accuracy: { label: "Practice-word accuracy", unit: "%", lead: true },
  avg_attempts_per_word: { label: "Avg attempts / word", unit: "", lead: true },
  pitch_variation: { label: "Pitch variation", unit: " st" },
  speaking_rate: { label: "Speaking rate", unit: " syl/s" },
  pause_count: { label: "Pause count", unit: "" },
  open_engagement: { label: "Open-question engagement", unit: "" },
};

const CHART_ORDER = [
  "practice_word_accuracy",
  "avg_attempts_per_word",
  "pitch_variation",
  "speaking_rate",
  "pause_count",
  "open_engagement",
];

function sortCharts(charts) {
  const list = Array.isArray(charts) ? [...charts] : [];
  list.sort((a, b) => {
    const ia = CHART_ORDER.indexOf(a.metric);
    const ib = CHART_ORDER.indexOf(b.metric);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return list;
}

function seriesForMetric(rows, metric) {
  return rows.map((row) => {
    const date = row.date;
    if (metric === "open_engagement") {
      const eng = row.open_engagement || {};
      const total = eng.total ?? row.open_total ?? 0;
      const answered = eng.answered ?? row.open_answered ?? 0;
      const pct = total ? Math.round((answered / total) * 100) : 0;
      return { date, value: pct, answered, total, avgWords: eng.avg_words ?? row.open_avg_words };
    }
    const value = row[metric];
    return { date, value: value == null ? null : value };
  });
}

function sparkLineSvg(points, { neutral = false } = {}) {
  const vals = points.map((p) => p.value).filter((v) => v != null && !Number.isNaN(v));
  if (vals.length < 2) return "";
  const w = 280;
  const h = 72;
  const padX = 8;
  const padY = 10;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const step = (w - padX * 2) / Math.max(points.length - 1, 1);
  const coords = points.map((p, i) => {
    if (p.value == null) return null;
    const x = padX + i * step;
    const y = h - padY - ((p.value - min) / span) * (h - padY * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean);
  const stroke = neutral ? "#7a8694" : "#6a8f5c";
  const fill = neutral ? "rgba(122,134,148,0.12)" : "rgba(111,168,122,0.16)";
  const area = `${padX},${h - padY} ${coords.join(" ")} ${padX + (points.length - 1) * step},${h - padY}`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-hidden="true">
    <polygon points="${area}" fill="${fill}"></polygon>
    <polyline points="${coords.join(" ")}" fill="none" stroke="${stroke}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"></polyline>
  </svg>`;
}

function renderChartCard(spec, rows) {
  const meta = CHART_META[spec.metric] || { label: spec.metric, unit: "" };
  const series = seriesForMetric(rows, spec.metric);
  const classes = ["report-chart"];
  if (meta.lead) classes.push("lead");
  if (spec.neutral) classes.push("neutral");

  if (spec.metric === "open_engagement") {
    const latest = series[series.length - 1];
    const pct = latest?.value ?? 0;
    return `<article class="${classes.join(" ")}">
      <div class="report-chart-label">${esc(meta.label)}</div>
      <div class="report-chart-value">${latest ? `${latest.answered}/${latest.total} answered` : "—"}</div>
      <div class="report-chart-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
      <div class="report-chart-note">${latest?.avgWords != null ? `${latest.avgWords} avg words` : "participation, not accuracy"}</div>
    </article>`;
  }

  if (!spec.chartable) {
    return `<article class="${classes.join(" ")}">
      <div class="report-chart-label">${esc(meta.label)}</div>
      <div class="report-chart-placeholder">Trends appear after a few more sessions.</div>
    </article>`;
  }

  const usable = series.filter((p) => p.value != null);
  const latest = usable[usable.length - 1];
  const first = usable[0];
  const delta = latest && first ? latest.value - first.value : null;
  const deltaText = delta == null ? "" : ` · ${fmtChange(Math.round(delta * 100) / 100, meta.unit)}`;
  return `<article class="${classes.join(" ")}">
    <div class="report-chart-label">${esc(meta.label)}</div>
    <div class="report-chart-value">${latest == null ? "—" : `${latest.value}${meta.unit}`}${esc(deltaText)}</div>
    ${sparkLineSvg(series, { neutral: !!spec.neutral })}
  </article>`;
}

function renderReportCharts(charts, input) {
  const rows = input?.sessions || [];
  const grid = $("report-charts-grid");
  const box = $("report-charts");
  if (!grid || !box) return;
  const ordered = sortCharts(charts);
  if (!ordered.length || !rows.length) {
    box.hidden = true;
    grid.innerHTML = "";
    return;
  }
  grid.innerHTML = ordered.map((spec) => renderChartCard(spec, rows)).join("");
  box.hidden = false;
}

function renderReportStats(input, meta) {
  const rows = input?.sessions || [];
  const trend = input?.trend || {};
  const accs = rows.map((r) => r.practice_word_accuracy).filter((v) => v != null);
  const latestAcc = accs.length ? accs[accs.length - 1] : null;
  const accChange = trend.practice_word_accuracy?.change;
  const maxAcc = Math.max(1, ...accs, 100);
  const spark = accs
    .slice(-8)
    .map((v) => `<i style="height:${Math.max(8, Math.round((v / maxAcc) * 28))}px" title="${v}%"></i>`)
    .join("");

  const dateRange =
    trend.first_date && trend.latest_date
      ? trend.first_date === trend.latest_date
        ? trend.first_date
        : `${trend.first_date} → ${trend.latest_date}`
      : "—";

  $("report-stats").innerHTML = `
    <div class="report-stat">
      <div class="label">Sessions</div>
      <div class="value">${meta.sessions_used ?? rows.length}</div>
      <div class="hint">${meta.demo_sessions_used ? `${meta.demo_sessions_used} demo` : "spoken practice"}</div>
    </div>
    <div class="report-stat">
      <div class="label">Latest accuracy</div>
      <div class="value">${latestAcc == null ? "—" : `${latestAcc}%`}</div>
      <div class="hint">${accChange == null ? "practice words" : fmtChange(accChange, " pts")}</div>
      ${spark ? `<div class="spark" aria-hidden="true">${spark}</div>` : ""}
    </div>
    <div class="report-stat">
      <div class="label">Speaking rate</div>
      <div class="value">${trend.speaking_rate?.latest ?? rows.at(-1)?.speaking_rate ?? "—"}</div>
      <div class="hint">${trend.speaking_rate ? fmtChange(trend.speaking_rate.change) + " syl/s" : "syllables / sec"}</div>
    </div>
    <div class="report-stat">
      <div class="label">Date range</div>
      <div class="value" style="font-size:1.05rem;margin-top:8px">${esc(dateRange)}</div>
      <div class="hint">${trend.sparse_data ? "baseline only" : "trend ready"}</div>
    </div>`;
  $("report-stats").hidden = false;
}

function paintReport(payload) {
  const sections = splitReportSections(payload.report);
  $("report-parent").innerHTML = renderMdBlock(sections.parent);
  $("report-therapist").innerHTML = renderMdBlock(sections.therapist);
  $("report-practice").innerHTML = renderMdBlock(sections.practice);
  $("report-board").hidden = false;

  if (sections.closing) {
    $("report-closing").textContent = sections.closing.replace(/^\*+|\*+$/g, "").trim();
    $("report-closing").hidden = false;
  } else {
    $("report-closing").hidden = true;
  }

  renderReportStats(payload.input, payload);
  renderReportCharts(payload.charts, payload.input);
}

async function loadReport({ force = false } = {}) {
  const btn = $("btn-report-refresh");
  btn.disabled = true;
  btn.textContent = "Writing…";
  reportStatus("Asking the practice assistant to write the report…");
  setMood("thinking"); // Groq is writing the report
  $("report-board").hidden = true;
  $("report-stats").hidden = true;
  $("report-charts").hidden = true;
  $("report-closing").hidden = true;
  try {
    const res = await fetch("/api/report", { method: "POST", headers: authHeaders() });
    if (!res.ok) throw new Error(await errorText(res));
    const data = await res.json();
    paintReport(data);
    reportStatus("");
  } catch (err) {
    reportStatus(err.message || "Could not generate the report.", true);
  } finally {
    setMood(null);
    btn.disabled = false;
    btn.textContent = "Generate again";
  }
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
  stopAudio();
  setMood(null);
  showScreen("home");
  refreshStoryRotation();
});
$("btn-finish-home").addEventListener("click", () => {
  stopAudio();
  setMood(null);
  showScreen("home");
  refreshStoryRotation();
});
$("btn-home").addEventListener("click", () => {
  state.runId++;
  stopAudio();
  cancelListening();
  state.busy = false;
  stopLipSync();
  setMood(null);
  showScreen("home");
  refreshStoryRotation();
});
$("btn-progress").addEventListener("click", () => {
  showScreen("progress");
  loadProgress();
});
$("btn-progress-back").addEventListener("click", () => showScreen("home"));
$("btn-report").addEventListener("click", () => {
  showScreen("report");
  loadReport();
});
$("btn-report-back").addEventListener("click", () => showScreen("home"));
$("btn-report-refresh").addEventListener("click", () => loadReport({ force: true }));

// ——— Boot —————————————————————————————————————————————————————————————

// Landing: Mr. Sprout waves hello as soon as the auth screen shows, then settles to idle.
buddies.auth = mountMascot($("buddy-auth"), { initial: "hello", then: "idle" });
buddies.home = mountMascot($("buddy-home"));
buddies.story = mountMascot($("buddy-story"));
buddies.finish = mountMascot($("buddy-finish"));
setAuthMode("login");
applyTheme("home");
refreshStoryRotation();
restoreSession();
