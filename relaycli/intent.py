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
}
_BARE_ACTIONS = {
    "add", "audit", "bagusin", "benerin", "buat", "cek", "check", "commit",
    "debug", "edit", "fix", "install", "jalanin", "jalankan", "push", "review",
    "run", "setup", "test", "tes", "update",
}
_TOKEN_RE = re.compile(r"[a-z0-9_./-]+", re.IGNORECASE)

_GUIDE_TEXT = (
    "Halo, aku siap bantu.\n"
    "Tulis tujuan kecilnya biar aku bisa pilih jalur yang pas. Contoh:\n"
    "  /setup\n"
    "  jelaskan repo ini\n"
    "  fix test yang gagal\n"
    "  buat tampilan web lebih rapi"
)

_VAGUE_TEXT = (
    "Aku belum punya target yang cukup jelas.\n"
    "Kasih konteks sedikit: file/fitur yang mau disentuh dan hasil yang kamu mau. "
    "Kalau butuh menu cepat di terminal, ketik `/`."
)

_SLASH_TEXT = (
    "Di terminal interaktif, `/` membuka command palette.\n"
    "Yang sering dipakai: /setup, /services, /doctor, /desktop, /model, /mode, /relay."
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

    if len(tokens) == 1 and tokens[0] in _BARE_ACTIONS:
        return LocalReply(_VAGUE_TEXT, "bare-action")

    # "ini", "bantu dong", "coba", "oke lanjut" are too ambiguous to spend
    # a planner run on. Two-word actionable commands still pass through:
    # "run tests", "fix auth", "jelaskan repo".
    has_action = any(t in _ACTION_WORDS for t in tokens)
    if len(tokens) <= 3 and not has_action:
        return LocalReply(_VAGUE_TEXT, "vague")

    return None


def _is_greeting_only(tokens: list[str]) -> bool:
    meaningful = [t for t in tokens if t not in _FILLER]
    if not meaningful:
        return False
    allowed = _GREETINGS | _ACKS
    return len(meaningful) <= 3 and all(t in allowed for t in meaningful)
