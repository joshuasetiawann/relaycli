"""read_file tool — return a text file's contents, safely and confined."""

from __future__ import annotations

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "read_file"
DESCRIPTION = (
    "Read a UTF-8 text file from the project and return its contents. "
    "Refuses paths outside the project and binary files. Reading a "
    "secret-like or git-ignored file requires the human user's explicit "
    "approval (you cannot bypass this)."
)

# Default cap so a giant file can't blow up the context window.
_DEFAULT_MAX_BYTES = 100_000
_BINARY_SNIFF = 8192


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path relative to the project root.")
    max_bytes: int = Field(
        default=_DEFAULT_MAX_BYTES,
        ge=1,
        le=2_000_000,
        description="Maximum number of bytes to read before truncating.",
    )


def read_file(args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
    proj = ctx.project
    try:
        path = proj.resolve(args.path, must_exist=True)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"read {args.path} (refused)")

    if not path.is_file():
        hint = " Use list_dir to list a directory." if path.is_dir() else ""
        return ToolResult.error(
            f"'{args.path}' is not a regular file.{hint}",
            summary=f"read {args.path} (refused)",
        )

    rel = proj.relative(path)

    # Reading a secret/git-ignored file is a decision only the HUMAN can make —
    # never the model. Secrets use the always-prompt `read_secret` action so
    # they are never auto-approved by mode (not even full-auto); merely ignored
    # files use the normal mode-gated `read` action.
    if proj.is_secret(path):
        decision = ctx.permissions.confirm(
            "read_secret",
            prompt_text=f"Read secret-like file '{escape(rel)}'? Its contents will be sent to the model.",
        )
        if not decision.approved:
            return ToolResult.error(
                f"Refusing to read secret-like file '{args.path}' without explicit human approval.",
                summary=f"read {args.path} (refused: secret)",
            )
    elif proj.is_ignored(path):
        decision = ctx.permissions.confirm(
            "read", prompt_text=f"Read git-ignored file '{escape(rel)}'?"
        )
        if not decision.approved:
            return ToolResult.error(
                f"'{args.path}' is git-ignored; read was not approved.",
                summary=f"read {args.path} (refused: ignored)",
            )

    # Bound the read itself so a huge file cannot exhaust process memory
    # (max_bytes previously only bounded what was sent to the model). Read one
    # extra byte to detect truncation.
    try:
        with path.open("rb") as fh:
            data = fh.read(args.max_bytes + 1)
    except OSError as exc:
        return ToolResult.error(f"Could not read '{args.path}': {exc}")

    if b"\x00" in data[:_BINARY_SNIFF]:
        return ToolResult.error(
            f"'{args.path}' appears to be binary; refusing to read it as text.",
            summary=f"read {args.path} (refused: binary)",
        )

    try:
        total = path.stat().st_size
    except OSError:
        total = len(data)

    truncated = len(data) > args.max_bytes
    text = data[: args.max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[... truncated: {total} bytes total, showing first {args.max_bytes} ...]"

    return ToolResult(
        ok=True,
        output=text,
        summary=f"read {rel}",
        meta={"bytes": total, "truncated": truncated},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=ReadFileArgs, func=read_file))
