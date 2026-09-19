"""StoryBuddy — FastAPI app entrypoint."""

import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import acoustics, companion, config, report, scoring, sessions, settings, stories, users, voice
from app.seed import seed_demo_sessions

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SPEAK_CHARS = 600  # one beat's narration + prompt; stops a stray call burning credits
DEMO_STORY_ID = "rosie-shares"  # the story the demo sessions pretend to have used

TAGS = [
    {"name": "Account", "description": "Sign up, log in, and stay signed in."},
    {"name": "Story session", "description": "The main loop: start a session, send each answer, get the reaction and the next beat."},
    {"name": "Stories", "description": "Pre-written 6-beat stories in stories/ (4 targeted words + 2 open questions, a moral, a sound cue per beat)."},
    {"name": "Narrator", "description": "Text-to-speech audio, reaction sounds, and the voice/speed/volume settings."},
    {"name": "Progress", "description": "Saved sessions, demo data and the parent/therapist report."},
    {"name": "Tools", "description": "Health check and developer tools (acoustic analysis, samples)."},
]

app = FastAPI(
    title="StoryBuddy API",
    description=(
        f"{config.SAFETY_BANNER}\n\n"
        "Frontend guide with examples: docs/API.md in the repo."
    ),
    version="0.1.0",
    openapi_tags=TAGS,
)

# Lets a separately served frontend (e.g. a Vite/React dev server) call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
app.mount("/samples", StaticFiles(directory=str(config.SAMPLES_DIR)), name="samples")
app.mount("/sounds", StaticFiles(directory=str(config.SOUNDS_DIR)), name="sounds")


def _json_file_status(path) -> dict:
    """Whether a local JSON store exists and parses. Never raises."""
    if not path.exists():
        return {"present": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": True, "valid": False, "error": str(exc)}
    return {"present": True, "valid": True, "entries": len(data) if isinstance(data, list) else None}


def _save_upload(audio: UploadFile) -> str:
    """Write an uploaded recording to a temp file and return its path. Caller deletes it."""
    data = audio.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The recording was empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too large (20 MB max).")
    suffix = Path(audio.filename or "").suffix.lower() or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
    return tmp.name


@app.get("/health", tags=["Tools"], summary="Health and configuration check")
def health():
    """Liveness + configuration check."""
    missing = config.missing_keys()
    narrator = settings.get()
    return {
        "status": "ok",
        "app": "StoryBuddy",
        "banner": config.SAFETY_BANNER,
        "keys_configured": not missing,
        "missing_keys": missing,
        "voice_id": narrator["voice_id"],
        "voice_name": settings.voice_name(narrator["voice_id"]),
        "voice_speed": narrator["speed"],
        "tts_model": config.ELEVENLABS_TTS_MODEL,
        "stt_model": config.ELEVENLABS_STT_MODEL,
        "llm_model": config.GROQ_MODEL,
        "storage": {
            "stories": len(stories.load_all()),
            "sessions.json": _json_file_status(config.SESSIONS_FILE),
        },
    }


@app.get("/api/samples", tags=["Tools"], summary="List test recordings")
def list_samples():
    """Test recordings in samples/, playable at /samples/<name>."""
    return [
        {"name": p.name, "size_kb": round(p.stat().st_size / 1024)}
        for p in sorted(config.SAMPLES_DIR.glob("*.wav"))
    ]


@app.post("/api/features", tags=["Tools"], summary="Analyse a recording (pitch, rate, pauses)")
def features(audio: UploadFile = File(...), detail: bool = False):
    """Run the acoustic engine on an uploaded recording.

    detail=true adds the pitch/intensity tracks, syllable and pause positions.
    """
    path = _save_upload(audio)
    try:
        return acoustics.analyze(path, include_tracks=detail)
    except acoustics.AudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        os.unlink(path)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEAK_CHARS)
    cache: bool = True
    # Optional overrides, for previewing a voice/speed before saving it.
    voice_id: str | None = None
    speed: float | None = Field(default=None, ge=settings.SPEED_MIN, le=settings.SPEED_MAX)


@app.post("/api/speak", tags=["Narrator"], summary="Speak text in the narrator voice (MP3)",
          responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 audio"}})
def speak(body: SpeakRequest):
    """Narrator voice for `text`. Returns MP3. Story lines are cached; reactions pass cache=false."""
    if body.voice_id and body.voice_id not in {v["id"] for v in settings.NARRATOR_VOICES}:
        raise HTTPException(status_code=400, detail="Unknown voice.")
    try:
        audio = voice.speak(body.text, use_cache=body.cache, voice_id=body.voice_id, speed=body.speed)
    except voice.VoiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=audio, media_type="audio/mpeg")


# --- Accounts ------------------------------------------------------------------

class AuthBody(BaseModel):
    username: str = Field(min_length=1, max_length=24)
    password: str = Field(min_length=1, max_length=72)
    parent_email: str | None = Field(default=None, max_length=120)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    kind, _, token = authorization.partition(" ")
    if kind.lower() != "bearer" or not token:
        return None
    return token.strip()


@app.post("/api/auth/signup", tags=["Account"], summary="Create a new account")
def auth_signup(body: AuthBody):
    try:
        return users.signup(body.username, body.password, body.parent_email or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/auth/login", tags=["Account"], summary="Log in")
def auth_login(body: AuthBody):
    try:
        return users.login(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/auth/logout", tags=["Account"], summary="Log out")
def auth_logout(authorization: str | None = Header(default=None)):
    users.logout(_bearer(authorization))
    return {"ok": True}


@app.get("/api/auth/me", tags=["Account"], summary="Who is signed in")
def auth_me(authorization: str | None = Header(default=None)):
    user = users.user_from_token(_bearer(authorization))
    if not user:
        raise HTTPException(status_code=401, detail="Please log in.")
    return user


# --- Narrator voice ------------------------------------------------------------

@app.get("/api/voices", tags=["Narrator"], summary="Narrator voices and current settings")
def list_voices():
    """Auditioned narrator voices, the current choice, and the allowed speed range."""
    return {
        "voices": settings.NARRATOR_VOICES,
        "current": settings.get(),
        "speed_range": [settings.SPEED_MIN, settings.SPEED_MAX],
    }


class VoiceSettings(BaseModel):
    voice_id: str | None = None
    speed: float | None = None
    sound_volume: float | None = Field(default=None, ge=0.0, le=1.0)


@app.post("/api/settings/voice", tags=["Narrator"], summary="Change narrator voice, speed and/or sound volume")
def update_voice(body: VoiceSettings):
    try:
        return settings.update(voice_id=body.voice_id, speed=body.speed, sound_volume=body.sound_volume)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _sound(name: str) -> dict:
    return {"name": name, "url": f"/sounds/{name}.wav", "volume": settings.get()["sound_volume"]}


def _with_cue_urls(story: dict) -> dict:
    """Copy of the story where each beat also has sound_effect_url (None if no file)."""
    beats = []
    for beat in story["beats"]:
        cue = beat.get("sound_effect")
        exists = bool(cue) and (config.SOUNDS_DIR / "story" / f"{cue}.wav").exists()
        beats.append({**beat, "sound_effect_url": f"/sounds/story/{cue}.wav" if exists else None})
    return {**story, "beats": beats}


@app.get("/api/sounds", tags=["Narrator"], summary="Reaction sounds, the sentiment map and volume")
def list_sounds():
    """The soft reaction sounds (in sounds/), which sentiment plays which, and the volume."""
    return {
        "sounds": {p.stem: f"/sounds/{p.name}" for p in sorted(config.SOUNDS_DIR.glob("*.wav"))},
        "sentiment_map": config.SOUND_FOR_SENTIMENT,
        "final_success": config.FINAL_SUCCESS_SOUND,
        "default": config.DEFAULT_SOUND,
        "story_cues": {p.stem: f"/sounds/story/{p.name}" for p in sorted((config.SOUNDS_DIR / "story").glob("*.wav"))},
        "volume": settings.get()["sound_volume"],
    }


# --- Stories -------------------------------------------------------------------

def _story_card(story: dict) -> dict:
    return {k: story.get(k) for k in ("id", "title", "moral", "target_sounds", "target_words")}


@app.get("/api/stories", tags=["Stories"], summary="List the pre-written stories")
def list_stories():
    """All stories in stories/, plus which one the rotation would pick next."""
    return {
        "stories": [_story_card(s) for s in stories.load_all()],
        "next_in_rotation": stories.next_in_rotation(sessions.load_all())["id"],
    }


@app.get("/api/story", tags=["Stories"], summary="Get one story with all its beats")
def get_story(story_id: str):
    try:
        return _with_cue_urls(stories.get(story_id))
    except stories.StoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class StartSession(BaseModel):
    story_id: str | None = None


@app.post("/api/session/start", tags=["Story session"], summary="Start a story session")
def start_session(body: StartSession | None = None):
    """Begin a story run with `story_id`, or omit it to rotate to the least recently used story."""
    try:
        if body and body.story_id:
            story = stories.get(body.story_id)
        else:
            story = stories.next_in_rotation(sessions.load_all())
    except stories.StoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    session = sessions.start(story)
    return {"session_id": session["session_id"], "story": _with_cue_urls(story), "beat_index": 0}


@app.post("/api/session/{session_id}/respond", tags=["Story session"], summary="Send the child's answer to the current beat")
def respond(
    session_id: str,
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
):
    """One turn: the child's answer to the current beat in, the companion's reaction out.

    Send a WAV recording as `audio`, or `text` to test the conversation without a mic.

    Targeted beat (retry loop, at most config.MAX_ATTEMPTS tries per word):
      transcribe (keyterm = target) -> is_close_match (lenient)
        match                  -> happy_chime (cheer on the last beat), warm reaction, next beat
        no match, tries left   -> soft_try_again, warm "let's say it together" line, SAME beat again
        no match, out of tries -> gentle_encourage, "we'll practise it another time", next beat
      The word is logged once it's finished: every try's heard_text, attempts_taken
      (1/2/3 or "not_yet"), final_result, and acoustics from the successful try.
    Open beat: transcribe + participation only (no accuracy, no retries).
    After the last beat the session is appended to sessions.json.
    """
    session = sessions.get_active(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="This story session has ended or doesn't exist. Start a new one.")
    story = session["story"]
    beats = story["beats"]
    beat_index = len(session["beats"])
    beat = beats[beat_index]
    targeted = beat["type"] == "targeted"
    is_last_beat = beat_index == len(beats) - 1

    analysis, words = None, None
    if audio is not None and audio.filename:
        path = _save_upload(audio)
        try:
            heard = voice.transcribe(path, [beat["target_response"]] if targeted else [])
            analysis = acoustics.extract_features(path)
        except voice.VoiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except acoustics.AudioError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        finally:
            os.unlink(path)
        child_text, words, input_mode = heard["text"], heard["words"], "voice"
    elif text is not None:
        child_text, input_mode = " ".join(text.split())[:500], "typed"
    else:
        raise HTTPException(status_code=400, detail="Send a recording or some text.")

    score, match, participation, outcome, attempt = None, None, None, None, None
    if targeted:
        target = beat["target_response"]
        attempts = sessions.pending_attempts(session)
        attempt = len(attempts) + 1
        match = scoring.is_close_match(child_text, target)
        score = scoring.score_response(child_text, target, words)
        features = None
        if analysis:
            features = {k: analysis[k] for k in sessions.FEATURE_KEYS}
            features["reliable"] = analysis["reliable"]
        attempts.append({
            "attempt": attempt, "heard_text": child_text, "input": input_mode,
            "match": match["match"], "reason": match["reason"], "accuracy": score["accuracy"],
            "features": features,
        })
        # Hard cap: after MAX_ATTEMPTS the beat always finishes, match or not.
        if match["match"]:
            outcome = "match"
        elif attempt < config.MAX_ATTEMPTS:
            outcome = "retry"
        else:
            outcome = "not_yet"
        reaction = companion.react(story, beat_index, child_text, outcome=outcome, attempt=attempt)
        sound = companion.sound_for_outcome(outcome, is_last_beat)
        record = None if outcome == "retry" else sessions.targeted_record(beat_index, target, attempts)
    else:
        participation = {
            "responded": bool(child_text),
            "word_count": len(child_text.split()),
            "duration_s": analysis["speech_s"] if analysis else None,
        }
        reaction = companion.react(story, beat_index, child_text)
        sound = companion.sound_for(reaction["sentiment"], is_last_beat)
        record = {"index": beat_index, "type": "open", "heard": child_text, "input": input_mode, **participation}

    beat_done = record is not None
    saved = None
    if beat_done:
        record["sentiment"] = reaction["sentiment"]
        sessions.record_beat(session, record)
        if is_last_beat:
            saved = sessions.finish(session_id)
    return {
        "beat_index": beat_index,
        "beat_type": beat["type"],
        "input": input_mode,
        "heard": child_text,
        # Targeted beats: this try's result and where the retry loop stands.
        "outcome": outcome,                 # "match" | "retry" | "not_yet" | None (open beat)
        "attempt": attempt,
        "max_attempts": config.MAX_ATTEMPTS if targeted else None,
        "match": match,                     # is_close_match() result
        "score": score,
        "features": analysis if targeted else None,
        "participation": participation,
        "sentiment": reaction["sentiment"],
        "sound": _sound(sound),
        "reaction": reaction["reaction_text"],
        "reaction_source": reaction["source"],
        "fallback_reason": reaction.get("fallback_reason"),
        "beat_done": beat_done,
        "beat_record": record,              # the saved log for this beat, once it's finished
        "next_beat_index": None if (beat_done and is_last_beat) else (beat_index + 1 if beat_done else beat_index),
        "saved_session": saved,
    }


@app.get("/api/sessions", tags=["Progress"], summary="List saved sessions (for the progress chart)")
def list_sessions():
    """Saved sessions, oldest first (summaries only; beats are in sessions.json)."""
    return [
        {k: s[k] for k in ("session_id", "date", "story_id", "mode", "seeded", "summary")}
        for s in sessions.load_all()
    ]


@app.post("/api/sessions/seed", tags=["Progress"], summary="Add demo sessions")
def seed_sessions():
    """Add (or refresh) six weeks of labelled demo sessions. Real sessions are kept."""
    all_sessions = sessions.replace_seeded(seed_demo_sessions(stories.get(DEMO_STORY_ID)))
    return {"seeded": sum(1 for s in all_sessions if s.get("seeded")), "total": len(all_sessions)}


@app.delete("/api/sessions/seed", tags=["Progress"], summary="Remove demo sessions")
def remove_seeded_sessions():
    all_sessions = sessions.replace_seeded([])
    return {"seeded": 0, "total": len(all_sessions)}


@app.post("/api/report", tags=["Progress"], summary="Generate the progress report")
def progress_report():
    """Parent/therapist progress report from spoken sessions (typed test sessions excluded)."""
    usable = sessions.for_report(sessions.load_all())
    result = report.generate_report(usable)
    if result["report"] is None:
        raise HTTPException(status_code=409, detail="No spoken sessions yet. Finish a story out loud, or add demo sessions.")
    return {
        "report": result["report"],
        "charts": result.get("charts") or [],
        "source": result["source"],
        "note": result.get("note"),
        "model": config.GROQ_MODEL if result["source"] == "groq" else None,
        "sessions_used": len(result["input"]["sessions"]),
        "demo_sessions_used": sum(1 for s in usable[-report.MAX_SESSIONS:] if s.get("seeded")),
        "input": result["input"],
    }


@app.get("/lab", include_in_schema=False)
def lab():
    """Developer test bench: try each piece of StoryBuddy as it is built."""
    return FileResponse(str(config.STATIC_DIR / "lab.html"))


@app.get("/", include_in_schema=False)
def index():
    page = config.STATIC_DIR / "index.html"
    if page.exists():
        return FileResponse(str(page))
    return {"message": "StoryBuddy backend is running.", "banner": config.SAFETY_BANNER}
