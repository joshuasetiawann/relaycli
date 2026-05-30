#!/usr/bin/env sh
set -eu

REPO_URL="${RELAYCLI_REPO_URL:-https://github.com/joshuasetiawann/relaycli.git}"
INSTALL_HOME="${RELAYCLI_HOME:-$HOME/.relaycli}"
SRC_DIR="$INSTALL_HOME/src"
VENV_DIR="$INSTALL_HOME/venv"
BIN_DIR="$HOME/.local/bin"

say() { printf '%s\n' "$*"; }
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    say "error: '$1' is required. Install it first, then rerun this script."
    exit 1
  fi
}

need git
need python3
mkdir -p "$INSTALL_HOME" "$BIN_DIR"

if [ -d "$SRC_DIR/.git" ]; then
  say "Updating RelayCLI in $SRC_DIR"
  git -C "$SRC_DIR" pull --ff-only
else
  say "Installing RelayCLI into $SRC_DIR"
  git clone "$REPO_URL" "$SRC_DIR"
fi

relaycli_cmd=""
if command -v uv >/dev/null 2>&1; then
  say "Installing command with uv tool"
  uv tool install --force "$SRC_DIR"
  relaycli_cmd="$(command -v relaycli || true)"
elif command -v pipx >/dev/null 2>&1; then
  say "Installing command with pipx"
  pipx install --force "$SRC_DIR"
  relaycli_cmd="$(command -v relaycli || true)"
else
  say "Installing command with a private virtualenv"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -e "$SRC_DIR"
  cat > "$BIN_DIR/relaycli" <<WRAP
#!/usr/bin/env sh
exec "$VENV_DIR/bin/relaycli" "\$@"
WRAP
  chmod +x "$BIN_DIR/relaycli"
  relaycli_cmd="$BIN_DIR/relaycli"
fi

if [ -z "$relaycli_cmd" ] && [ -x "$BIN_DIR/relaycli" ]; then
  relaycli_cmd="$BIN_DIR/relaycli"
fi
if [ -z "$relaycli_cmd" ]; then
  say "RelayCLI installed, but relaycli is not on PATH. Add $BIN_DIR to PATH."
  relaycli_cmd="$BIN_DIR/relaycli"
fi

say "RelayCLI is installed."
say "Tip: if 'relaycli' is not found, add this to your shell profile: export PATH=\"$BIN_DIR:\$PATH\""

if [ "${RELAYCLI_INSTALL_NO_INIT:-0}" != "1" ]; then
  say "Starting guided setup. You can choose Ollama, n8n, web, or Postgres services here."
  # RELAYCLI_INIT_ARGS is intentionally shell-split so users can pass flags like:
  # RELAYCLI_INIT_ARGS='--services ollama,n8n --start-services --yes'
  # shellcheck disable=SC2086
  "$relaycli_cmd" init ${RELAYCLI_INIT_ARGS:-}
fi

say "Done. Run: relaycli doctor"
