# StoryBuddy

Interactive speech-practice stories for kids. A painted landscape and a soft cartoon bunny narrator (**Buddy**) guide each story beat, listen to the child’s answer, and reply with warm feedback.

> **Safety:** StoryBuddy supports speech-therapy **practice**. It does not diagnose any condition and does not replace a speech therapist.

---

## What it does

1. **Child signs in** (username + password; signup also asks for a parent email).
2. **Picks a story** (or “Surprise me!”) against a storybook landscape.
3. **Buddy narrates** each beat (ElevenLabs TTS), then listens (mic + STT).
4. **Targeted practice words** get up to 3 gentle tries; open questions count as engagement.
5. **Session is saved** to `data/sessions.json` with accuracy, attempts, and acoustic features.
6. **Therapist report** (home → *Therapist report*) asks Groq for a parent/therapist summary plus chart specs, then renders a calm practice dashboard.

There is also a **developer lab** for trying each pipeline piece in isolation.

---

## Quick start

```powershell
# 1. Virtualenv + dependencies (includes pytest)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# 2. API keys
copy .env.example .env
# Edit .env — set ELEVENLABS_API_KEY and GROQ_API_KEY

# 3. Run (uvicorn with reload on http://127.0.0.1:8000)
.\.venv\Scripts\python run.py
```

Equivalent:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

| Page | URL |
|------|-----|
| **Child app** (login, stories, report) | http://127.0.0.1:8000/ |
| **Developer lab** | http://127.0.0.1:8000/lab |
| **API docs** (Swagger) | http://127.0.0.1:8000/docs |

One uvicorn process serves **both** the API and the static frontend (`/`, `/static/...`, `/lab`).

---

## Environment

Copy `.env.example` → `.env` (never commit `.env`):

| Variable | Purpose |
|----------|---------|
| `ELEVENLABS_API_KEY` | Narrator TTS + speech-to-text |
| `GROQ_API_KEY` | Warm beat reactions + therapist progress report |
| `ELEVENLABS_VOICE_ID` | Default narrator voice (changeable in the lab) |
| `ELEVENLABS_SPEED` | TTS speed `0.7`–`1.2` (default `0.8`) |
| `GROQ_MODEL` | Default `openai/gpt-oss-120b` |
| `SOUND_VOLUME` | Reaction-sound volume `0.0`–`1.0` |
| `CORS_ORIGINS` | Extra browser origins if a separate frontend calls the API |

---

## Child frontend

Served from `static/index.html` + `static/css/story.css` + `static/js/story.js`.

- **Login / signup** — login: username + password; signup also requires **parent’s email**. Accounts live in `data/users.json` (PBKDF2 password hashes + bearer tokens).
- **Home** — painted tree landscape, transparent bunny, story cards (Rosie / Leo / Sammy art), *Surprise me!*, *My stars*, *Therapist report*, *Log out*.
- **Story loop** — auto mic after narration; retry loop on practice words; soft reaction sounds + spoken companion line.
- **My stars** — recent finished sessions and practice-word accuracy.
- **Therapist report** — calls `POST /api/report`, shows parent / therapist / practice suggestion panels, session stats, and **practice trend charts** driven by the report’s `charts` list (line charts only when there are 3+ spoken sessions; otherwise a calm “trends appear after a few more sessions” placeholder). Open-question engagement is a simple bar/stat.

Art assets live under `static/img/` (e.g. `buddy-bunny.png`, `story-home.png`, per-story landscapes).

---

## Practice session & scoring

- **Stories** are JSON under `stories/` (typically 6 beats: ~4 targeted words + 2 open questions, moral, sound cues).
- **Start:** `POST /api/session/start` with optional `story_id`.
- **Answer:** `POST /api/session/{id}/respond` with WAV `audio` (or `text` for typed testing).
- **Targeted beats:** STT with the target as a keyterm → lenient match → match / retry / move on after `MAX_ATTEMPTS` (3).
- **Open beats:** participation only (answered + word count), not accuracy.
- **Acoustics** (Praat / parselmouth): mean pitch, pitch variation, speaking rate, pause count on successful practice-word audio.
- When the story finishes, the run is appended to **`data/sessions.json`**.

Typed / mixed sessions are saved but **excluded from the progress report** (no reliable voice measures). Only `mode: "voice"` sessions count.

---

## Therapist report

- **Endpoint:** `POST /api/report`
- Builds a compact time series from spoken sessions (accuracy, avg attempts per word, acoustics, open engagement).
- Groq writes a practice-tracking summary (not a clinical assessment) with sections:
  - **For the parent**
  - **For the therapist** (Improved / Stable / Watch when ≥3 sessions; **baseline** wording when fewer)
  - **Practice suggestion**
  - Closing disclaimer
  - A **`charts`** JSON list the frontend uses for plotting
- Guardrails reject banned clinical wording; if Groq fails checks, a **template** report is used instead.
- Optional **demo history:** `POST /api/sessions/seed` (six weeks of labelled demo sessions) / `DELETE /api/sessions/seed`.

---

## Project layout

```
app/
  main.py          FastAPI routes
  config.py        Env + paths
  users.py         Signup / login / tokens
  stories.py       Load stories + rotation
  sessions.py      In-progress + sessions.json
  scoring.py       Practice-word match / accuracy
  voice.py         ElevenLabs TTS + STT
  acoustics.py     Pitch, rate, pauses
  companion.py     Warm reaction lines (Groq)
  report.py        Progress report + charts metadata
  seed.py          Demo sessions for demos / charts
  …
static/            Child UI + lab + images
stories/           Story JSON files
data/              users.json, sessions.json, settings, …
sounds/            Soft reaction cues
samples/           Dev audio samples
scripts/           Helpers (e.g. generate sounds)
tests/             Offline unit tests
run.py             uvicorn launcher
```

---

## Main API surface

| Area | Examples |
|------|----------|
| Account | `POST /api/auth/signup`, `/login`, `/logout`, `GET /api/auth/me` |
| Stories | `GET /api/stories`, `GET /api/story?story_id=` |
| Session | `POST /api/session/start`, `POST /api/session/{id}/respond` |
| Progress | `GET /api/sessions`, `POST /api/sessions/seed`, `POST /api/report` |
| Narrator | `POST /api/speak`, `GET /api/voices`, `POST /api/settings/voice` |
| Tools | `GET /health`, `POST /api/features`, lab at `/lab` |

Full interactive docs: http://127.0.0.1:8000/docs

---

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests -q
```

Tests cover scoring/retry behaviour, session summaries, demo seeding, and report guardrails (no live Groq calls required for those checks).

---

## Notes for demos

- Use a **voice** story finish (or seed demo sessions) before generating the therapist report.
- Charts that need a trend stay in placeholder mode until there are **at least 3** spoken sessions.
- Hard-refresh the browser (Ctrl+F5) after pulling frontend changes so CSS/JS/images aren’t stuck in cache.
- Keep API keys only in `.env`; `data/users.json` and `data/sessions.json` are local runtime data.
