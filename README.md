# StoryBuddy

Interactive speech-practice stories for kids. A painted landscape and an animated Rive mascot narrator (**Mr. Sprout**, a sprout-headed forest sprite) guide each story beat, listen to the child’s answer, and reply with warm feedback.

> **Safety:** StoryBuddy supports speech-therapy **practice**. It does not diagnose any condition and does not replace a speech therapist.

---

## What it does

1. **Child signs in** (username + password; signup also asks for a parent email).
2. **Picks a story** (or “Surprise me!”) against a storybook landscape.
3. **Mr. Sprout narrates** each beat (ElevenLabs TTS), then listens (mic + STT).
4. **Targeted practice words** get up to 3 gentle tries; open questions count as engagement.
5. **Session is saved in SQL** (SQLite locally, Postgres when `DATABASE_URL` is set) with accuracy, attempts, and acoustic features.
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

- **Landing page:** http://127.0.0.1:8000/
- **Child experience:** http://127.0.0.1:8000/app
- **Developer lab:** http://127.0.0.1:8000/lab
- **API docs:** http://127.0.0.1:8000/docs

## Frontend

The landing page (`static/landing.html`) and child-facing page (`static/index.html`) share one narrator, **Mr. Sprout**, an animated [Rive](https://rive.app) mascot (`static/mascot/sobo.riv`, runtime vendored in `static/vendor/rive/`, wrapper in `static/js/mascot.js`, styles in `static/css/mascot.css`).

Mr. Sprout reacts to what the app is doing:

| Mood | Rive artboard | When |
|------|---------------|------|
| `hello` | `SOBO-Hello` | waves on landing, after login, at story start |
| `talking` | `SOBO-Talk` | ElevenLabs narration / reaction audio plays (mouth follows the audio loudness via the `mouthOpen` view-model input) |
| `listening` | `SOBO-Listen` | mic is open, waiting for the child |
| `thinking` | `SOBO-Listen` + CSS thought bubble | Groq / story-generation / report requests are pending |
| `happy` | `SOBO-Yes` | positive reaction |
| `sad` | `SOBO-Idle` + CSS droop and tear | sad / frustrated reaction |
| `celebrate` | `SOBO-Yes` + CSS sparkles | story finished |
| `idle` | `SOBO-Idle` | otherwise |

Try it in the browser console: `SoboMascot.setAll("sad")`. Each mascot host exposes `data-mood` / `data-artboard`.

Other frontend features:

- Colorful sky scene with drifting clouds
- Mic answer or typed fallback (same session API as the lab)
- Progress stars after each reply; confetti at the end

All story / scoring / TTS / STT logic stays on the FastAPI backend.

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
| `DATABASE_URL` | Optional. Default: SQLite at `data/storybuddy.db`. For deploy: your Postgres URL |

---

## Data storage (needed for deploy)

Accounts and practice/report history are stored in **SQL**:

| Table | Contents |
|-------|----------|
| `users` | Username, parent email, password hash, auth tokens |
| `sessions` | Finished story runs (beats + summary) for *My stars* and the therapist report |

- **Local:** SQLite file `data/storybuddy.db` (created on first launch).
- **Production:** set `DATABASE_URL` to Postgres (Railway, Render, Neon, Supabase, Fly, etc.).
- First boot with an empty DB **imports** legacy `data/users.json` / `data/sessions.json` once if they exist.
- Logged-in sessions are tagged with the child’s username; progress/report use that child’s history plus shared seeded demo sessions.

---

## Deploy

### What you need

1. A host that runs Docker or a Python web process (Railway, Render, Fly.io, …).
2. A **Postgres** database → put its URL in `DATABASE_URL`.
3. Env vars: `ELEVENLABS_API_KEY`, `GROQ_API_KEY`, `DATABASE_URL` (plus optional settings from `.env.example`).
4. An **HTTPS** public URL (browser mic APIs need a secure context on real devices).

### Docker Compose (app + Postgres locally)

```powershell
# Put API keys in .env first
docker compose up --build
```

Open http://127.0.0.1:8000/

### Docker image (attach your own Postgres)

```powershell
docker build -t storybuddy .
docker run --rm -p 8000:8000 --env-file .env `
  -e DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME storybuddy
```

### Railway / Render (typical)

1. Add a **Postgres** plugin; copy `DATABASE_URL`.
2. Deploy this repo (Dockerfile included).
3. Set `ELEVENLABS_API_KEY`, `GROQ_API_KEY`, `DATABASE_URL`. Hosts usually inject `PORT`.
4. Health check: `GET /health` (`storage.backend` should be `postgres`).

---

## Child frontend

Served from `static/index.html` + `static/css/story.css` + `static/js/story.js`.

- **Login / signup** — login: username + password; signup also requires **parent’s email** (SQL `users` table).
- **Home** — painted landscape, Mr. Sprout, story cards, *Surprise me!*, *My stars*, *Therapist report*, *Log out*.
- **Story loop** — auto mic after narration; retry loop on practice words; soft reaction sounds + spoken companion line.
- **My stars** — recent finished sessions and practice-word accuracy.
- **Therapist report** — `POST /api/report`: parent / therapist / practice suggestion panels, stats, and trend charts from the report `charts` list (line charts when ≥3 spoken sessions; otherwise a calm placeholder). Open engagement is a bar/stat.

Art assets: `static/mascot/sobo.riv`, story landscapes under `static/img/`.

---

## Practice session & scoring

- **Stories** are JSON under `stories/` (typically 6 beats: ~4 targeted words + 2 open questions).
- **Start:** `POST /api/session/start` (send `Authorization: Bearer …` when logged in).
- **Answer:** `POST /api/session/{id}/respond` with WAV `audio` (or `text` for typed testing).
- **Targeted beats:** STT + lenient match → match / retry / move on after 3 tries.
- **Open beats:** participation only (not accuracy).
- **Acoustics** (Praat): mean pitch, pitch variation, speaking rate, pause count.
- Finished runs are stored in the **`sessions`** SQL table.

Typed / mixed sessions are saved but **excluded from the progress report**. Only `mode: "voice"` counts.

---

## Therapist report

- **Endpoint:** `POST /api/report` (scoped to the logged-in child when a token is sent).
- Compact time series → Groq (or template fallback) with parent / therapist / practice suggestion + `charts` JSON.
- Guardrails block clinical / banned wording.
- Demo history: `POST /api/sessions/seed` / `DELETE /api/sessions/seed`.

---

## Project layout

```
app/
  main.py          FastAPI routes + startup DB init
  config.py        Env + paths + DATABASE_URL
  db.py            SQLAlchemy models (users, sessions)
  users.py         Signup / login / tokens
  sessions.py      In-progress runs + SQL history
  report.py        Progress report + charts metadata
  …
static/            Landing + child UI + lab + images
stories/           Story JSON
data/              SQLite file, optional legacy JSON, caches
Dockerfile         Production image
docker-compose.yml App + Postgres for local/prod-like runs
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

Full docs: http://127.0.0.1:8000/docs

---

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests -q
```

---

## Notes for demos

- Finish a **voice** story (or seed demo sessions) before generating the therapist report.
- Trend charts need **at least 3** spoken sessions; fewer show placeholders.
- Hard-refresh (Ctrl+F5) after pulls so CSS/JS/images aren’t cached.
- Keep secrets in `.env` only. Prefer Postgres + `DATABASE_URL` for any shared deploy.
