"""create_folder tool — safely create a directory inside the project."""

from __future__ import annotations

from pydantic import BaseModel, Field
from rich.markup import escape

from relaycli.context import PathSafetyError
from relaycli.tools import Tool, ToolRegistry
from relaycli.tools.base import ToolContext, ToolResult

NAME = "create_folder"
DESCRIPTION = (
    "Create a directory inside the project root. Use this for requests like "
    "'buat folder bernama ...'. Parent directories are created when needed."
)


class CreateFolderArgs(BaseModel):
    path: str | None = Field(
        default=None,
        description="Folder path relative to the project root.",
    )
    folder_name: str | None = Field(
        default=None,
        description="Alternative folder name/path when the model uses folder_name.",
    )
    folder_path: str | None = Field(
        default=None,
        description="Alternative folder path when the model uses folder_path.",
    )
    name: str | None = Field(
        default=None,
        description="Alternative folder name/path when the model uses name.",
    )
    directory: str | None = Field(
        default=None,
        description="Alternative folder name/path when the model uses directory.",
    )
    directory_name: str | None = Field(
        default=None,
        description="Alternative folder name/path when the model uses directory_name.",
    )
    dir_name: str | None = Field(
        default=None,
        description="Alternative folder name/path when the model uses dir_name.",
    )


def create_folder(args: CreateFolderArgs, ctx: ToolContext) -> ToolResult:
    raw = (
        args.path
        or args.folder_name
        or args.folder_path
        or args.name
        or args.directory
        or args.directory_name
        or args.dir_name
        or ""
    ).strip()
    if not raw:
        return ToolResult.error(
            "create_folder needs a path, folder_name, or name.",
            summary="create folder (invalid)",
        )

    proj = ctx.project
    try:
        path = proj.resolve(raw)
    except PathSafetyError as exc:
        return ToolResult.error(str(exc), summary=f"create folder {raw} (refused)")

    rel = proj.relative(path)
    if path.exists():
        if path.is_dir():
            return ToolResult(
                ok=True,
                output=f"Folder '{rel}' already exists.",
                summary=f"create folder {rel} (exists)",
            )
        return ToolResult.error(
            f"'{rel}' already exists and is not a directory.",
            summary=f"create folder {rel} (refused)",
        )

    decision = ctx.permissions.confirm(
        "write", prompt_text=f"Create folder {escape(rel)}?"
    )
    if not decision.approved:
        return ToolResult.error(
            f"Folder creation for '{rel}' was declined.",
            summary=f"create folder {rel} (declined)",
        )

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult.error(
            f"Failed to create folder '{rel}': {exc}",
            summary=f"create folder {rel} (failed)",
        )

    return ToolResult(
        ok=True,
        output=f"Created folder '{rel}'.",
        summary=f"create folder {rel}",
        meta={"path": rel},
    )


def register(reg: ToolRegistry) -> None:
    reg.add(Tool(name=NAME, description=DESCRIPTION, args_model=CreateFolderArgs, func=create_folder))
