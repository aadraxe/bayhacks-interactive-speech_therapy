# StoryBuddy

Interactive speech-practice stories for kids. A painted landscape and an animated Rive mascot narrator (**Mr. Sprout**, a sprout-headed forest sprite) guide each story beat, listen to the child’s answer, and reply with warm feedback.

> **Safety:** StoryBuddy supports speech-therapy **practice**. It does not diagnose any condition and does not replace a speech therapist.

---

## What it does

1. **Child signs in** (username + password; signup also asks for a parent email).
2. **Picks a story** (or “Surprise me!”) against a storybook landscape.
3. **Mr. Sprout narrates** each beat (ElevenLabs TTS), then listens (mic + STT).
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

All story / scoring / TTS / STT logic stays on the existing FastAPI backend.
