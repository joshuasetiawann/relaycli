"""Shared slash-command metadata for terminal and desktop UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    hint: str
    description: str
    group: str = "Session"

    @property
    def usage(self) -> str:
        return f"/{self.name} {self.hint}".rstrip()


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("model", "[name|search q]", "Show, search, or switch the model.", "Model"),
    SlashCommand("mode", "[m]", "Permission mode: suggest | auto-edit | full-auto.", "Safety"),
    SlashCommand("relay", "[on|off]", "Toggle the Planner -> Coder -> Reviewer pipeline.", "Agents"),
    SlashCommand("agents", "[r on|off]", "Show relay agents; toggle explorer/tester/tasks.", "Agents"),
    SlashCommand("skill", "[name]", "Toggle a skill on/off for this session.", "Skills"),
    SlashCommand("skills", "", "List available skills.", "Skills"),
    SlashCommand("config", "", "Roles, per-role models, and provider keys.", "Setup"),
    SlashCommand("settings", "", "General preferences.", "Setup"),
    SlashCommand("setup", "", "Guided setup: model, keys, and optional services.", "Setup"),
    SlashCommand("init", "", "Alias of /setup.", "Setup"),
    SlashCommand("services", "[start names]", "Show/start optional services.", "Setup"),
    SlashCommand("doctor", "", "Run a local health check.", "Setup"),
    SlashCommand("memory", "", "Show long-term memory.", "Session"),
    SlashCommand("desktop", "", "Open the desktop web UI.", "Session"),
    SlashCommand("mcp", "", "Show MCP connectors and their tools.", "Setup"),
    SlashCommand("diff", "", "Show uncommitted changes.", "Session"),
    SlashCommand("clear", "", "Reset the conversation.", "Session"),
    SlashCommand("help", "", "Show all commands and keys.", "Session"),
    SlashCommand("exit", "", "Quit.", "Session"),
    SlashCommand("quit", "", "Quit.", "Session"),
)


SLASH_COMMANDS: dict[str, tuple[str, str]] = {
    command.name: (command.hint, command.description) for command in COMMANDS
}


ARG_COMPLETIONS: dict[str, tuple[str, ...]] = {
    "mode": ("suggest", "auto-edit", "full-auto"),
    "relay": ("on", "off"),
    "agents": ("explorer", "tester", "tasks"),
    "services": ("start", "ollama", "web", "postgres", "n8n"),
    "model": (
        "recent",
        "search",
        "provider",
        "ollama",
        "ollama_chat/qwen3:4b",
        "ollama_chat/qwen2.5-coder:1.5b",
        "ollama_chat/qwen2.5-coder:0.5b",
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "groq/llama-3.3-70b-versatile",
        "mistral-large-latest",
        "openrouter/cohere/north-mini-code:free",
        "openrouter/qwen/qwen3-coder:free",
        "openrouter/qwen/qwen3-coder-next",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/z-ai/glm-4.7",
        "openrouter/moonshotai/kimi-k2.6",
        "openrouter/openai/gpt-oss-120b:free",
    ),
}


def command_payload() -> list[dict[str, str]]:
    """JSON-friendly command metadata for the web UI."""

    return [
        {
            "name": command.name,
            "usage": command.usage,
            "hint": command.hint,
            "description": command.description,
            "group": command.group,
        }
        for command in COMMANDS
    ]

