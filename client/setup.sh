#!/usr/bin/env bash
# setup_client.sh — set up the allocator client CLI environment and verify deps
#
# Usage:
#   ./setup_client.sh                                  # create venv + install + check
#   ./setup_client.sh --dev                            # also install test/dev extras
#   ./setup_client.sh --venv ~/.venvs/allocator        # custom venv location
#   ./setup_client.sh --manager-url http://host:8000   # also write a sourceable env file
#   ./setup_client.sh --check                          # verify an existing env only
#
# There is no API key: the client's identity is this host's name (X-Client-Id).
#
# Safe to re-run at any time: an existing venv is reused and dependencies are
# upgraded in place. Does NOT require root (everything lives in a virtualenv).
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── locate repo root ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$SCRIPT_DIR"                      # this script lives in client/
CONTRACT_DIR="$(cd "$SCRIPT_DIR/../contract" && pwd 2>/dev/null)" || CONTRACT_DIR=""

# ── parse arguments ───────────────────────────────────────────────────────────
VENV_DIR="$CLIENT_DIR/.venv"
INSTALL_DEV=0
CHECK_ONLY=0
MANAGER_URL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv)        VENV_DIR="$2"; shift 2 ;;
        --dev)         INSTALL_DEV=1; shift ;;
        --check)       CHECK_ONLY=1; shift ;;
        --manager-url) MANAGER_URL="$2"; shift 2 ;;
        -h|--help)     sed -n '2,14p' "$0"; exit 0 ;;
        *)             die "Unknown argument: $1 (try --help)" ;;
    esac
done

# Minimum Python required by client/pyproject.toml
PY_MIN_MAJOR=3
PY_MIN_MINOR=12

[ -d "$CLIENT_DIR" ] || die "client/ not found at $CLIENT_DIR"

# ── find a suitable interpreter ───────────────────────────────────────────────
# Returns the first python that satisfies >= 3.12.
find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3 python; do
        if command -v "$candidate" &>/dev/null; then
            if "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)" 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# ── check system prerequisites ────────────────────────────────────────────────
section "Checking system prerequisites"

if ! PYTHON="$(find_python)"; then
    die "No Python >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR} found. Install it and re-run."
fi
info "Python: $("$PYTHON" --version) ($(command -v "$PYTHON"))"

"$PYTHON" -c "import venv" 2>/dev/null || die "The 'venv' module is missing — install python3-venv."
"$PYTHON" -c "import ensurepip" 2>/dev/null || warn "ensurepip missing; venv creation may need python3-venv."

# usbip is needed to attach/detach devices a session hands out. The CLI can
# still talk to the manager without it, so this is a warning, not a failure.
if command -v usbip &>/dev/null; then
    info "usbip: $(command -v usbip)"
else
    warn "usbip not found — 'usbip attach/detach' won't work."
    warn "  Debian/Ubuntu: sudo apt-get install usbip linux-tools-generic"
    warn "  The client modprobe's vhci-hcd at attach time (needs root)."
fi

# ── create / reuse the virtualenv ─────────────────────────────────────────────
if [ "$CHECK_ONLY" -eq 0 ]; then
    section "Setting up virtualenv"
    # A valid venv has both bin/python and bin/activate. Reuse only if both
    # exist; otherwise (re)create — a previous failed run can leave a broken
    # dir without activate, which we must not silently reuse.
    if [ -x "$VENV_DIR/bin/python" ] && [ -f "$VENV_DIR/bin/activate" ]; then
        info "Reusing existing venv at $VENV_DIR"
    else
        [ -e "$VENV_DIR" ] && { warn "Removing incomplete venv at $VENV_DIR"; rm -rf "$VENV_DIR"; }
        info "Creating venv at $VENV_DIR"
        if ! "$PYTHON" -m venv "$VENV_DIR"; then
            die "venv creation failed. On Debian/Ubuntu install the venv package:
       sudo apt install python3-venv     (or python3.12-venv / python3.13-venv)"
        fi
    fi

    VENV_PY="$VENV_DIR/bin/python"
    [ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY"
    # 'python -m venv' writes activate AFTER bootstrapping pip, so a missing
    # activate means the pip step failed — almost always a missing venv package.
    [ -f "$VENV_DIR/bin/activate" ] || die "venv has no bin/activate — install the venv package and re-run:
       sudo apt install python3-venv"

    section "Installing the client package"
    "$VENV_PY" -m ensurepip --upgrade
    "$VENV_PY" -m pip install --quiet --upgrade pip
    # The client depends on the shared allocator-contract package; install it
    # (editable) first so the dependency resolves from the sibling directory.
    [ -f "$CONTRACT_DIR/pyproject.toml" ] || die "Shared contract not found at ../contract"
    info "Installing allocator-contract (editable)"
    "$VENV_PY" -m pip install --quiet -e "$CONTRACT_DIR"
    if [ "$INSTALL_DEV" -eq 1 ]; then
        info "Installing allocator-client[dev] (editable)"
        "$VENV_PY" -m pip install --quiet -e "$CLIENT_DIR[dev]"
    else
        info "Installing allocator-client (editable)"
        "$VENV_PY" -m pip install --quiet -e "$CLIENT_DIR"
    fi
    info "Install complete"
else
    VENV_PY="$VENV_DIR/bin/python"
    [ -x "$VENV_PY" ] || die "No venv at $VENV_DIR — run without --check first."
fi

# ── verify every dependency imports ───────────────────────────────────────────
section "Verifying dependencies"

"$VENV_PY" - <<'PY' || die "Dependency check failed — see above."
import importlib.metadata as md
import sys

# (import name, distribution name) for every runtime dependency
deps = [
    ("httpx", "httpx"),
    ("typer", "typer"),
    ("rich", "rich"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("structlog", "structlog"),
    ("allocator_contract", "allocator-contract"),
]
ok = True
for mod, dist in deps:
    try:
        __import__(mod)
        ver = md.version(dist)
        print(f"  [+] {dist:<20} {ver}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [x] {dist:<20} MISSING ({exc})")

# The client package itself + its console-script entry point
try:
    import allocator_client  # noqa: F401
    from allocator_client.cli.main import app  # noqa: F401
    print(f"  [+] {'allocator-client':<20} {md.version('allocator-client')}")
except Exception as exc:  # noqa: BLE001
    ok = False
    print(f"  [x] {'allocator-client':<20} IMPORT FAILED ({exc})")

sys.exit(0 if ok else 1)
PY
info "All Python dependencies present"

# The `allocator` console script should be on the venv's PATH and runnable.
if [ -x "$VENV_DIR/bin/allocator" ]; then
    if "$VENV_DIR/bin/allocator" --help &>/dev/null; then
        info "CLI entry point OK: $VENV_DIR/bin/allocator"
    else
        warn "'allocator' is installed but '--help' failed — check the install."
    fi
else
    warn "'allocator' entry point not found in venv bin/ — reinstall without --check."
fi

# ── optional: write a sourceable env file ─────────────────────────────────────
ENV_FILE="$CLIENT_DIR/.allocator-env"
if [ -n "$MANAGER_URL" ]; then
    section "Writing env file"
    {
        echo "# Source this file before using the allocator CLI:  source $ENV_FILE"
        echo "source \"$VENV_DIR/bin/activate\""
        echo "export ALLOCATOR_URL=\"$MANAGER_URL\""
    } > "$ENV_FILE"
    info "Wrote $ENV_FILE"
fi

# ── done ──────────────────────────────────────────────────────────────────────
section "Done"
echo -e "Activate the environment:"
echo -e "  ${GREEN}source $VENV_DIR/bin/activate${NC}"
echo ""
echo -e "Configure the CLI:"
echo -e "  ${GREEN}export ALLOCATOR_URL=${NC}http://localhost:8000   ${YELLOW}# default if unset${NC}"
echo -e "  ${YELLOW}# No API key. Identity = this host's name ($(hostname)).${NC}"
echo -e "  ${YELLOW}# Override with: export ALLOCATOR_CLIENT_ID=<name>${NC}"
[ -f "$ENV_FILE" ] && echo -e "  ${GREEN}# or:${NC} source $ENV_FILE"
echo ""
echo -e "Try it:"
echo -e "  ${GREEN}allocator --help${NC}"
echo -e "  ${GREEN}allocator node list${NC}"
echo -e "  ${GREEN}allocator device list${NC}"
