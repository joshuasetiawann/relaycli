"""File tools — list_dir, read_file, write_file, edit_file, create_folder, find_files."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from relaycli.tools.base import ToolContext, ToolResult, atomic_write
from relaycli.tools.registry import Tool, ToolRegistry


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Directory path to list")

def list_dir(args: ListDirArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        resolved = ctx.project.resolve(args.path)
    except Exception as exc:
        return ToolResult.error(str(exc))
    if not resolved.is_dir():
        return ToolResult.error(f"Not a directory: {args.path}")
    entries: list[str] = []
    try:
        for entry in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name)):
            marker = "/" if entry.is_dir() else ""
            if ctx.project.is_ignored(entry):
                continue
            entries.append(f"{entry.name}{marker}")
    except OSError as exc:
        return ToolResult.error(f"Failed to list directory: {exc}")
    output = "\n".join(entries) if entries else "(empty directory)"
    return ToolResult(ok=True, output=output, summary=f"listed {len(entries)} items")


def register_list_dir(reg: ToolRegistry) -> None:
    reg.add(Tool(name="list_dir", description="List files and directories in a folder",
                 args_model=ListDirArgs, func=list_dir))


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path to the file to read")
    offset: int | None = Field(default=None, description="Line number to start from (1-based)")
    limit: int | None = Field(default=None, description="Max lines to return")

def read_file(args: ReadFileArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        resolved = ctx.project.resolve(args.path, must_exist=True)
    except Exception as exc:
        return ToolResult.error(str(exc))
    if ctx.project.is_secret(resolved):
        decision = ctx.permissions.confirm("read_secret", prompt_text=f"Read secret file {resolved.name}?")
        if not decision.approved:
            return ToolResult.error("Reading secret file was declined.", summary="read_secret (declined)")
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.error(f"Failed to read file: {exc}")
    ctx.read_files.add(str(resolved))
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if args.offset is not None:
        start = max(0, args.offset - 1)
        lines = lines[start:]
    if args.limit is not None:
        lines = lines[:args.limit]
    content = "".join(lines)
    header = f"{resolved} ({total} lines, showing {len(lines)}):\n"
    if content and not content.endswith("\n"):
        content += "\n"
    return ToolResult(ok=True, output=header + content, summary=f"read {resolved.name}")


def register_read_file(reg: ToolRegistry) -> None:
    reg.add(Tool(name="read_file", description="Read a file from the project",
                 args_model=ReadFileArgs, func=read_file))


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path where to write the file")
    content: str = Field(description="Full content to write")

def write_file(args: WriteFileArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        resolved = ctx.project.resolve(args.path)
    except Exception as exc:
        return ToolResult.error(str(exc))
    is_new = not resolved.exists()
    label = "create" if is_new else "replace"
    decision = ctx.permissions.confirm("write", prompt_text=f"{label} {resolved.name}?")
    if not decision.approved:
        return ToolResult.error("Write was declined.", summary=f"write {resolved.name} (declined)")
    try:
        if is_new:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(resolved, args.content)
    except OSError as exc:
        return ToolResult.error(f"Failed to write file: {exc}")
    summary = f"created {resolved.name}" if is_new else f"wrote {resolved.name} ({len(args.content)} bytes)"
    return ToolResult(ok=True, output=f"Written to {resolved}.", summary=summary)


def register_write_file(reg: ToolRegistry) -> None:
    reg.add(Tool(name="write_file", description="Create or fully replace a file",
                 args_model=WriteFileArgs, func=write_file))


class EditFileArgs(BaseModel):
    path: str = Field(description="Path to the file")
    old_string: str = Field(description="Exact text to replace")
    new_string: str = Field(description="Replacement text")

def edit_file(args: EditFileArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        resolved = ctx.project.resolve(args.path, must_exist=True)
    except Exception as exc:
        return ToolResult.error(str(exc))
    if str(resolved) not in ctx.read_files:
        try:
            ctx.read_files.add(str(resolved))
        except Exception:
            pass
    decision = ctx.permissions.confirm("edit", prompt_text=f"edit {resolved.name}?")
    if not decision.approved:
        return ToolResult.error("Edit was declined.", summary=f"edit {resolved.name} (declined)")
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.error(f"Failed to read file: {exc}")
    if args.old_string not in text:
        # Show surrounding context for debugging
        idx = text.find(args.old_string[:50])
        if idx >= 0:
            snippet = text[max(0, idx - 40): idx + len(args.old_string) + 40]
        else:
            snippet = text[:500]
        return ToolResult.error(
            f"old_string not found in {resolved.name}. Current file excerpt:\n```\n{snippet}\n```",
            summary=f"edit {resolved.name} (not found)",
        )
    new_text = text.replace(args.old_string, args.new_string, 1)
    try:
        atomic_write(resolved, new_text)
    except OSError as exc:
        return ToolResult.error(f"Failed to write: {exc}")
    added = len(args.new_string) - len(args.old_string)
    sign = "+" if added >= 0 else ""
    return ToolResult(ok=True, output=f"Edited {resolved}.", summary=f"edit {resolved.name} ({sign}{added}b)")


def register_edit_file(reg: ToolRegistry) -> None:
    reg.add(Tool(name="edit_file", description="Replace exact text in an existing file",
                 args_model=EditFileArgs, func=edit_file))


class CreateFolderArgs(BaseModel):
    path: str = Field(description="Path of the folder to create")

def create_folder(args: CreateFolderArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        resolved = ctx.project.resolve(args.path)
    except Exception as exc:
        return ToolResult.error(str(exc))
    if resolved.exists():
        return ToolResult(ok=True, output=f"Folder '{resolved}' already exists.", summary="already exists",
                          meta={"path": str(resolved)})
    decision = ctx.permissions.confirm("write", prompt_text=f"create folder {resolved.name}?")
    if not decision.approved:
        return ToolResult.error("Folder creation declined.", summary="mkdir (declined)")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult.error(f"Failed to create folder: {exc}")
    return ToolResult(ok=True, output=f"Created folder '{resolved}'.", summary=f"mkdir {resolved.name}",
                      meta={"path": str(resolved)})


def register_create_folder(reg: ToolRegistry) -> None:
    reg.add(Tool(name="create_folder", description="Create a new empty directory",
                 args_model=CreateFolderArgs, func=create_folder))


class FindFilesArgs(BaseModel):
    pattern: str = Field(description="Glob pattern to match files (e.g. '**/*.py')")
    path: str | None = Field(default=None, description="Base directory (default: project root)")

def find_files(args: FindFilesArgs, ctx: ToolContext | None) -> ToolResult:
    try:
        base = ctx.project.resolve(args.path) if args.path else ctx.project.root
    except Exception as exc:
        return ToolResult.error(str(exc))
    if not base.is_dir():
        return ToolResult.error(f"Not a directory: {args.path or '.'}")
    matches: list[str] = []
    try:
        for p in base.rglob(args.pattern):
            if p.is_file() and not ctx.project.is_ignored(p):
                try:
                    matches.append(str(p.relative_to(ctx.project.root)))
                except ValueError:
                    matches.append(str(p))
    except OSError as exc:
        return ToolResult.error(f"Search failed: {exc}")
    if not matches:
        return ToolResult(ok=True, output="(no matching files)", summary="found 0 files")
    output = "\n".join(sorted(matches))
    return ToolResult(ok=True, output=output, summary=f"found {len(matches)} files")


def register_find_files(reg: ToolRegistry) -> None:
    reg.add(Tool(name="find_files", description="Recursively find files matching a glob",
                 args_model=FindFilesArgs, func=find_files))


def register(reg: ToolRegistry) -> None:
    register_list_dir(reg)
    register_read_file(reg)
    register_write_file(reg)
    register_edit_file(reg)
    register_create_folder(reg)
    register_find_files(reg)
