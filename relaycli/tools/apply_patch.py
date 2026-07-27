from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult
from relaycli.tools.registry import Tool, ToolRegistry
from relaycli.ui.render import structured_diff


class ApplyPatchArgs(BaseModel):
    patch: str = Field(description="Unified diff/patch content to apply")


def apply_patch(args: ApplyPatchArgs, ctx: ToolContext | None) -> ToolResult:
    if ctx is not None:
        # One pre-flight check covering every file the patch touches,
        # before either the primary (patch binary) or fallback path runs —
        # neither knows target paths without parsing the patch text first,
        # so parsing here once covers both rather than duplicating it.
        for target_path, _lines in _parse_patches(args.patch):
            lease_error = ctx.lease_error(target_path)
            if lease_error is not None:
                return ToolResult.error(lease_error, summary="patch (no lease)")

        preview = args.patch if len(args.patch) <= 2000 else args.patch[:2000] + "\n... (truncated)"
        decision = ctx.permissions.confirm(
            "write", prompt_text=f"Apply this patch?\n{preview}"
        )
        if not decision.approved:
            return ToolResult.error("Patch was not approved.", summary="patch (declined)")

    root = ctx.project.root if ctx else Path.cwd()
    patch_file = root / ".relaycli_patch.tmp"
    try:
        patch_file.write_text(args.patch, encoding="utf-8")
        proc = subprocess.run(
            ["patch", "-p0", "-i", str(patch_file)],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        output = proc.stdout.strip()
        if proc.stderr:
            output = output + "\n" + proc.stderr.strip() if output else proc.stderr.strip()
        if proc.returncode != 0:
            return ToolResult(ok=False, output=output or "patch failed",
                              summary="patch failed")
        return ToolResult(ok=True, output=output or "patch applied",
                          summary="patch applied")
    except FileNotFoundError:
        return _fallback_apply(args.patch, root, ctx)
    except OSError as exc:
        return ToolResult.error(str(exc))
    except subprocess.TimeoutExpired:
        return ToolResult.error("patch command timed out")
    finally:
        try:
            patch_file.unlink()
        except OSError:
            pass


def _fallback_apply(patch_text: str, root: Path, ctx: ToolContext | None) -> ToolResult:
    """Pure-Python fallback for simple patches when `patch` is unavailable."""
    hunks = _parse_patches(patch_text)
    if not hunks:
        return ToolResult.error("No parseable hunks in patch (and `patch` command not found)")
    results = []
    diffs = []
    for filepath, old_content, new_content in _apply_hunks(hunks, root):
        rel = str(filepath.relative_to(root))
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(new_content, encoding="utf-8")
            if ctx is not None:
                ctx.file_cache.invalidate(filepath)
            diffs.append(structured_diff(old_content, new_content, rel))
            results.append(f"patched: {rel}")
        except OSError as exc:
            results.append(f"FAIL: {rel} - {exc}")
    return ToolResult(ok=not any(r.startswith("FAIL") for r in results),
                      output="\n".join(results) or "(no changes)",
                      summary=f"{len(results)} files patched",
                      diff=diffs)


_HUNK_RE = re.compile(
    r"^---\s+(\S+)\s*\n^\+\+\+\s+(\S+)\s*",
    re.MULTILINE,
)


def _parse_patches(text: str) -> list[tuple[str, list[str]]]:
    sections = re.split(r"(?=^---\s+\S+)", text, flags=re.MULTILINE)
    result = []
    for sec in sections:
        m = _HUNK_RE.search(sec)
        if not m:
            continue
        old = m.group(1)
        new = m.group(2)
        lines = sec.splitlines(keepends=True)
        result.append((new, lines))
    return result


def _apply_hunks(hunks, root):
    for filepath, lines in hunks:
        target = root / filepath
        if target.exists():
            content = target.read_text(encoding="utf-8")
        else:
            content = ""
        new_content = _apply_diff(content, lines)
        if new_content is not None and new_content != content:
            yield target, content, new_content


def _apply_diff(content: str, diff_lines: list[str]) -> str | None:
    result = list(content.splitlines(keepends=True))
    modified = False
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith("---") or line.startswith("+++") or line.startswith("diff"):
            i += 1
            continue
        if line.startswith("@@"):
            m = re.match(r"^@@ -(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@", line)
            if m:
                old_start = int(m.group(1)) - 1
                old_len = int(m.group(2) or 1) if m.group(2) else 1
            i += 1
            continue
        if line.startswith("-"):
            text = line[1:]
            if i + 1 < len(diff_lines) and diff_lines[i + 1].startswith("+"):
                replace = diff_lines[i + 1][1:]
                if old_start < len(result):
                    result[old_start] = replace
                old_start += 1
                i += 2
                modified = True
                continue
            if old_start < len(result) and result[old_start] == text:
                del result[old_start]
                modified = True
            i += 1
            continue
        if line.startswith("+"):
            result.insert(old_start, line[1:])
            old_start += 1
            modified = True
            i += 1
            continue
        i += 1
        old_start += 1
    return "".join(result) if modified else None


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name="apply_patch", description="Apply a unified diff/patch to the codebase",
                 args_model=ApplyPatchArgs, func=apply_patch))
