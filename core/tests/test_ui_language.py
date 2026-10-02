from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings


DJANGO_TAG_RE = re.compile(r"{[%{].*?[}%]}", re.DOTALL)
SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style)\b.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)
HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
HTML_ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+|#x[0-9a-fA-F]+);")
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+._/-]*")

ALLOWED_VISIBLE_LATIN = {
    "Aegis",
    "Authenticator",
    "FAS",
    "Google",
    "ID",
    "Ice",
    "MFA",
    "Microsoft",
    "OAuth",
    "QR",
    "School",
    "TOTP",
    "VK",
}


def _visible_static_text(template: str) -> str:
    text = SCRIPT_STYLE_RE.sub(" ", template)
    text = DJANGO_TAG_RE.sub(" ", text)
    text = HTML_TAG_RE.sub(" ", text)
    return HTML_ENTITY_RE.sub(" ", text)


def _latin_words(text: str) -> set[str]:
    words = set()
    for match in LATIN_WORD_RE.finditer(text):
        word = match.group(0).strip(".,:;!?()[]{}«»-")
        if not word:
            continue
        if word == "2FAS":
            word = "FAS"
        words.add(word)
    return words


def test_templates_have_no_unapproved_visible_latin_text():
    template_root = Path(settings.BASE_DIR) / "templates"
    offenders: dict[str, list[str]] = {}

    for path in sorted(template_root.rglob("*.html")):
        visible = _visible_static_text(path.read_text(encoding="utf-8"))
        unexpected = sorted(
            word
            for word in _latin_words(visible)
            if word not in ALLOWED_VISIBLE_LATIN
        )
        if unexpected:
            offenders[str(path.relative_to(settings.BASE_DIR))] = unexpected

    assert offenders == {}
