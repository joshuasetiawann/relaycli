"""Config package — re-exports from core.config for backward compat."""

from relaycli.core.config import (
    CONFIG_DIR, CONFIG_FILE, PermissionMode, Settings,
    ensure_config_dir, get_settings, reload_settings,
)
from relaycli.config.manager import load_app_config, save_app_config, config_app
from relaycli.config.menu import run_configuration, run_settings

__all__ = [
    "CONFIG_DIR", "CONFIG_FILE", "PermissionMode", "Settings",
    "ensure_config_dir", "get_settings", "reload_settings",
    "load_app_config", "save_app_config", "config_app",
    "run_configuration", "run_settings",
]
