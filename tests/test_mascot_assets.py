"""The Rive mascot is served locally (no CDN) — make sure the files it needs are present and served."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

STATIC = config.STATIC_DIR


def test_mascot_and_runtime_files_exist():
    riv = STATIC / "mascot" / "sobo.riv"
    assert riv.read_bytes()[:4] == b"RIVE"
    assert (STATIC / "vendor" / "rive" / "rive.js").stat().st_size > 100_000
    wasm = (STATIC / "vendor" / "rive" / "rive.wasm").read_bytes()
    assert wasm[:4] == b"\x00asm"


@pytest.mark.parametrize(
    "path,mime",
    [
        ("/static/mascot/sobo.riv", None),
        ("/static/vendor/rive/rive.js", "javascript"),
        ("/static/vendor/rive/rive.wasm", "application/wasm"),
        ("/static/js/mascot.js", "javascript"),
    ],
)
def test_mascot_assets_are_served(path, mime):
    r = TestClient(app).get(path)
    assert r.status_code == 200
    if mime:
        assert mime in r.headers["content-type"]


def test_every_mascot_mood_maps_to_an_artboard_in_the_riv():
    import re

    js = (STATIC / "js" / "mascot.js").read_text(encoding="utf-8")
    riv = (STATIC / "mascot" / "sobo.riv").read_bytes()
    for board in set(re.findall(r'artboard:\s*"(SOBO-[A-Za-z0-9-]+)"', js)):
        assert board.encode() in riv, board
