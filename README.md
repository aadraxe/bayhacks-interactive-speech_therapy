# StoryBuddy

Interactive speech-therapy practice stories for kids. A cartoon narrator (**Buddy**) reads each beat, listens to the child's answer, and reacts with warm feedback.

> StoryBuddy supports speech-therapy practice. It does not diagnose any condition and does not replace a speech therapist.

## Setup

```powershell
# 1. Create a virtualenv and install dependencies
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# 2. Configure API keys
copy .env.example .env
# Edit .env and set ELEVENLABS_API_KEY and GROQ_API_KEY

# 3. Run the server
.\.venv\Scripts\python run.py
```

Then open:

- **Landing page:** http://127.0.0.1:8000/
- **Child experience:** http://127.0.0.1:8000/app
- **Developer lab:** http://127.0.0.1:8000/lab
- **API docs:** http://127.0.0.1:8000/docs

## Frontend

The child-facing page (`static/index.html`) uses Buddy the fox as the story narrator:

- Animated expressions (talking, happy, sad, thinking, celebrate)
- Colorful sky scene with drifting clouds
- Mic answer or typed fallback (same session API as the lab)
- Progress stars after each reply; confetti at the end

All story / scoring / TTS / STT logic stays on the existing FastAPI backend.
