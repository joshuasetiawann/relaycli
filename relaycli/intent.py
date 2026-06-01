"""Tiny local intent gate for inputs that should not start an agent run.

This is deliberately conservative: real work requests still go to the model,
while greetings, bare acknowledgements, and one-word commands get a local
clarifying reply. That keeps "halo" from spending tokens or waking the relay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalReply:
    """A local assistant reply that bypasses the LLM."""

    text: str
    reason: str


_GREETINGS = {
    "halo", "hallo", "helo", "hai", "hi", "hello", "hey", "yo",
    "pagi", "siang", "sore", "malam", "assalamualaikum", "salam",
}
_ACKS = {"ok", "oke", "sip", "siap", "gas", "lanjut", "mantap", "thanks", "thank"}
_FILLER = {"bang", "bro", "kak", "min", "dong", "ya", "yaa", "pls", "please", "tolong"}
_ACTION_WORDS = {
    "add", "audit", "baca", "bagusin", "bangun", "benerin", "build", "buat",
    "buka", "cek", "change", "check", "commit", "debug", "deploy", "edit",
    "explain", "fix", "ganti", "hapus", "implement", "improve", "inspect", "install",
    "jalanin", "jalankan", "jelaskan", "lint", "optimize", "pasang", "perbaiki",
    "push", "read", "refactor", "remove", "review", "run", "search", "setup",
    "start", "stop", "tambah", "test", "tests", "tes", "ubah", "update",
    "analisa", "analisis", "bahas", "jelasin", "terangkan", "terangin",
}
_BARE_ACTIONS = {
    "add", "audit", "bagusin", "benerin", "buat", "cek", "check", "commit",
    "debug", "edit", "fix", "install", "jalanin", "jalankan", "push", "review",
    "run", "setup", "test", "tes", "update",
}
_TOKEN_RE = re.compile(r"[a-z0-9_./-]+", re.IGNORECASE)

_GUIDE_TEXT = (
    "Halo, aku siap bantu.\n"
    "Ketik saja tujuanmu dengan bahasa bebas. Kalau ada file, folder, error, "
    "atau hasil akhir yang kamu mau, sebutkan sekalian supaya aku bisa langsung "
    "baca konteks, edit, lalu test."
)

_VAGUE_TEXT = (
    "Aku belum punya target yang bisa dikerjakan.\n"
    "Tulis satu tujuan kecil dengan bahasa biasa: mau dibuat, dibenerin, "
    "dijelaskan, dites, atau dijalankan di bagian mana. Kalau mau menu command, "
    "ketik `/`."
)

_SLASH_TEXT = (
    "Command cepat RelayCLI:\n"
    "  /setup - pilih model, API key, Ollama, atau service tambahan\n"
    "  /desktop - buka web UI\n"
    "  /model - ganti model aktif\n"
    "  /mode - suggest, auto-edit, atau full-auto\n"
    "  /relay - nyalakan pipeline planner/coder/reviewer\n"
    "  /doctor - cek kesehatan install dan konfigurasi"
)

_CAPABILITY_TEXT = (
    "Aku bisa bantu kerja coding langsung di folder ini: baca repo, ubah file, "
    "bikin fitur, run test, debug error, setup model/API key/Ollama, dan review "
    "hasilnya. Kirim saja targetnya; kalau instruksinya sudah cukup jelas aku "
    "langsung jalan, kalau belum aku tanya singkat."
)

_FOLLOWUP_CONSENT_RE = re.compile(
    r"\b("
    r"apa\s+aja|terserah|bebas|bebas\s+aja|lanjut\s+aja|gas\s+aja|"
    r"pilih\s+sendiri|yang\s+penting|sesuai\s+kamu"
    r")\b",
    re.IGNORECASE,
)


def local_reply_for(text: str) -> LocalReply | None:
    """Return a local reply for tiny/vague input, otherwise None."""

    raw = (text or "").strip()
    if not raw:
        return None
    if raw in {"/", "/help", "/?"}:
        return LocalReply(_SLASH_TEXT, "slash-help")

    # Multi-line or longer requests are usually real tasks; let the model see
    # them even if they start with a greeting.
    if "\n" in raw or len(raw) > 80:
        return None

    normalized = re.sub(r"\s+", " ", raw.lower()).strip(" .,!?:;")
    tokens = [t.strip(" .,!?:;") for t in _TOKEN_RE.findall(normalized)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    if _is_greeting_only(tokens):
        return LocalReply(_GUIDE_TEXT, "greeting")

    if len(tokens) == 1 and _looks_like_greeting(tokens[0]):
        return LocalReply(_GUIDE_TEXT, "greeting")

    if _is_capability_question(normalized, tokens):
        return LocalReply(_CAPABILITY_TEXT, "capability")

    if len(tokens) == 1 and tokens[0] in _BARE_ACTIONS:
        return LocalReply(_VAGUE_TEXT, "bare-action")

    # "ini", "bantu dong", "coba", "oke lanjut" are too ambiguous to spend
    # a planner run on. Two-word actionable commands still pass through:
    # "run tests", "fix auth", "jelaskan repo".
    has_action = any(t in _ACTION_WORDS for t in tokens)
    if len(tokens) <= 3 and not has_action:
        return LocalReply(_VAGUE_TEXT, "vague")

    return None


def continuation_for(text: str, previous_request: str | None) -> str | None:
    """Merge a short permissive follow-up with the last actionable request.

    In chat UIs users often answer a clarification with "apa aja" or
    "terserah, lanjut" instead of restating the whole task. The model should
    see that as permission to choose defaults for the previous request, not as
    a brand-new vague request.
    """

    followup = (text or "").strip()
    previous = (previous_request or "").strip()
    if not followup or not previous:
        return None
    if local_reply_for(previous) is not None:
        return None
    if not is_permissive_followup(followup):
        return None
    return (
        "Original request:\n"
        f"{previous}\n\n"
        "User follow-up:\n"
        f"{followup}\n\n"
        "Interpret the follow-up as permission to choose reasonable defaults. "
        "Continue the original request now, preserve any exact names/paths the "
        "user gave, and do not ask more clarification unless action is unsafe "
        "or impossible."
    )


def is_permissive_followup(text: str) -> bool:
    """True for short replies that mean "choose defaults and continue"."""

    raw = (text or "").strip()
    if not raw or "\n" in raw:
        return False
    tokens = _TOKEN_RE.findall(raw.lower())
    if len(tokens) > 16:
        return False
    return bool(_FOLLOWUP_CONSENT_RE.search(raw))


def _is_greeting_only(tokens: list[str]) -> bool:
    meaningful = [t for t in tokens if t not in _FILLER]
    if not meaningful:
        return False
    allowed = _GREETINGS | _ACKS
    return len(meaningful) <= 3 and all(t in allowed for t in meaningful)


def _looks_like_greeting(token: str) -> bool:
    return bool(re.fullmatch(r"(ha)?lo+w*s*|he+y+|hi+i*|hai+i*", token))


def _is_capability_question(normalized: str, tokens: list[str]) -> bool:
    if len(tokens) > 8:
        return False
    phrases = (
        "kamu bisa apa",
        "bisa apa aja",
        "bisa ngapain",
        "apa aja yang bisa",
        "what can you do",
    )
    return any(p in normalized for p in phrases)
