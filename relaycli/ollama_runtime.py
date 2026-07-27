"""Small runtime checks for local Ollama models."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


_LOCAL_PREFIXES = ("ollama_chat/", "ollama/")
_FAST_LOCAL_PREFERENCES = (
    "qwen2.5-coder:1.5b",
    "qwen2.5-coder:0.5b",
    "qwen2.5:0.5b",
    "smollm2:360m",
)


def slow_local_model_warning(model: str, *, timeout: float = 0.4) -> str | None:
    """Warn before running local models likely to look hung on CPU-only machines."""

    if os.environ.get("RELAYCLI_ALLOW_SLOW_LOCAL") == "1":
        return None
    local_name = _local_name(model)
    if not local_name:
        return None
    processor = _ollama_processor(local_name, timeout=timeout)
    if processor:
        processor_upper = processor.upper()
        if "CPU/GPU" in processor_upper:
            return _message(model, f"Ollama is only partially offloading it ({processor})")
        if "CPU" in processor_upper:
            return _message(model, "Ollama is running it on CPU")
        if "GPU" in processor_upper or "VULKAN" in processor_upper:
            return None

    params = _param_billions(local_name)
    if params is None or params < 3:
        return None

    if _ollama_server_prefers_gpu(timeout=timeout):
        return _message(
            model,
            "RelayCLI cannot verify this large model will stay 100% GPU before loading",
        )

    total_gib = _total_memory_gib()
    if total_gib is not None and total_gib < 8:
        return _message(model, f"this machine has about {total_gib:.1f} GiB RAM")

    return None


def recommended_fast_local_model(settings: Any, *, timeout: float = 0.8) -> str | None:
    """Return a small installed Ollama model suitable as an automatic fallback."""

    try:
        from relaycli.llm import ollama_models

        installed = ollama_models(settings, timeout=timeout)
    except Exception:
        return None
    if not installed:
        return None
    for name in _FAST_LOCAL_PREFERENCES:
        if name in installed:
            return f"ollama_chat/{name}"
    for name in installed:
        params = _param_billions(name)
        if params is None or params < 3:
            return f"ollama_chat/{name}"
    return None


def _message(model: str, reason: str) -> str:
    return (
        f"Model '{model}' is likely too slow here ({reason}). "
        "Switch to a smaller GPU-safe model like `ollama_chat/qwen2.5-coder:1.5b` "
        "or `ollama_chat/qwen2.5-coder:0.5b` with `/model <model>`, or set "
        "`RELAYCLI_ALLOW_SLOW_LOCAL=1` if you intentionally want to wait."
    )


def _local_name(model: str) -> str | None:
    for prefix in _LOCAL_PREFIXES:
        if model.startswith(prefix):
            return model.split("/", 1)[1]
    return None


def _param_billions(model_name: str) -> float | None:
    match = re.search(r"(?::|[-_])(\d+(?:\.\d+)?)b(?:\b|$)", model_name, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _ollama_processor(local_name: str, *, timeout: float) -> str | None:
    try:
        proc = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines()[1:]:
        cols = line.split()
        if cols and cols[0] == local_name:
            return " ".join(cols[4:6])
    return None


def _ollama_server_prefers_gpu(*, timeout: float) -> bool:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", r"ollama.*serve"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    for raw_pid in proc.stdout.split():
        if not raw_pid.isdigit():
            continue
        try:
            environ = Path(f"/proc/{raw_pid}/environ").read_bytes()
        except OSError:
            continue
        env = _parse_environ(environ)
        library = env.get("OLLAMA_LLM_LIBRARY", "").lower()
        if library in {"cuda", "rocm", "metal"}:
            return True
        if library == "vulkan" and env.get("OLLAMA_IGPU_ENABLE") == "1":
            return True
    return False


def _parse_environ(raw: bytes) -> dict[str, str]:
    env = {}
    for part in raw.split(b"\0"):
        if not part or b"=" not in part:
            continue
        key, value = part.split(b"=", 1)
        try:
            env[key.decode()] = value.decode()
        except UnicodeDecodeError:
            continue
    return env


def _total_memory_gib() -> float | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    if not isinstance(pages, int) or not isinstance(page_size, int):
        return None
    return pages * page_size / (1024 ** 3)
