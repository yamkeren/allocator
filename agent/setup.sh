#!/usr/bin/env bash
# setup_agent.sh — install, start, OR update the allocator node agent
#
# Usage:
#   sudo ./setup_agent.sh --manager-url http://192.168.1.100:8000
#
# Safe to re-run at any time:
#   - First run: installs deps, writes systemd unit, starts service
#   - Re-run after code changes: rsyncs source, reinstalls, restarts service
#
# What it does:
#   1. Checks/installs system dependencies (usbip, usbipd, Python 3.12)
#   2. Rsyncs agent source to INSTALL_DIR and reinstalls the package
#   3. Writes systemd units for usbipd and allocator-agent
#   4. Enables and (re)starts both services
#
# Must be run as root (needed to write to /etc/systemd and to manage usbip).
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── must be root ──────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Run this script as root: sudo $0 $*"

# ── parse arguments ───────────────────────────────────────────────────────────
MANAGER_URL=""
NODE_NAME=""
AGENT_PORT=5000
INSTALL_DIR="/opt/allocator-agent"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --manager-url) MANAGER_URL="$2"; shift 2 ;;
        --node-name)   NODE_NAME="$2";   shift 2 ;;
        --port)        AGENT_PORT="$2";  shift 2 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: sudo $0 --manager-url URL [--node-name NAME] [--port PORT] [--install-dir DIR]"
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

if [ -z "$MANAGER_URL" ]; then
    read -rp "Manager URL (e.g. http://192.168.1.100:8000): " MANAGER_URL
fi
[ -z "$MANAGER_URL" ] && die "--manager-url is required"

NODE_NAME="${NODE_NAME:-$(hostname)}"

# ── locate agent source ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SRC="$SCRIPT_DIR"                       # this script lives in agent/
CONTRACT_SRC="$(cd "$SCRIPT_DIR/../contract" && pwd)"
[ -f "$AGENT_SRC/pyproject.toml" ] || die "Agent source not found at $AGENT_SRC"
[ -f "$CONTRACT_SRC/pyproject.toml" ] || die "Shared contract not found at $CONTRACT_SRC"

# ── check / install system dependencies ──────────────────────────────────────
section "System dependencies"

if command -v apt-get &>/dev/null; then
    MISSING_PKGS=()
    dpkg -l usbutils &>/dev/null 2>&1 || MISSING_PKGS+=(usbutils)

    # usbip is in linux-tools-generic on Ubuntu, or usbip on Debian
    if ! command -v usbip &>/dev/null; then
        MISSING_PKGS+=(usbip linux-tools-generic)
    fi

    if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
        info "Installing: ${MISSING_PKGS[*]}"
        apt-get update -q
        apt-get install -y --no-install-recommends "${MISSING_PKGS[@]}"
    fi
else
    warn "Not a Debian/Ubuntu system — ensure usbip and usbutils are installed manually"
fi

command -v usbip &>/dev/null || die "usbip not found after install attempt"
info "usbip: $(usbip version 2>/dev/null | head -1)"

# Load the kernel modules needed by usbip
modprobe usbip_core  2>/dev/null || warn "Could not load usbip_core — may already be loaded"
modprobe usbip_host  2>/dev/null || warn "Could not load usbip_host — may already be loaded"
modprobe vhci_hcd    2>/dev/null || warn "Could not load vhci_hcd — may already be loaded"

# Persist modules across reboots
for mod in usbip_core usbip_host vhci_hcd; do
    grep -qxF "$mod" /etc/modules 2>/dev/null || echo "$mod" >> /etc/modules
done

# ── usbipd systemd service ────────────────────────────────────────────────────
section "Setting up usbipd"

USBIPD_BIN=""
for candidate in /usr/sbin/usbipd /usr/bin/usbipd /usr/lib/linux-tools/*/usbipd; do
    # shellcheck disable=SC2086
    for f in $candidate; do
        [ -x "$f" ] && USBIPD_BIN="$f" && break 2
    done
done

if [ -z "$USBIPD_BIN" ]; then
    warn "usbipd binary not found — USB/IP clients won't be able to attach devices"
    warn "Install with: apt install linux-tools-generic"
else
    info "usbipd: $USBIPD_BIN"

    cat > /etc/systemd/system/usbipd.service <<EOF
[Unit]
Description=USB/IP Host Daemon
After=network.target

[Service]
Type=simple
ExecStart=$USBIPD_BIN --tcp-port 3240
Restart=on-failure
RestartSec=3s

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable usbipd
    systemctl restart usbipd
    sleep 1
    if systemctl is-active --quiet usbipd; then
        info "usbipd is running on port 3240"
    else
        warn "usbipd failed to start — check: journalctl -u usbipd -n 20"
    fi
fi

# ── check Python 3.12+ ────────────────────────────────────────────────────────
section "Python"

PYTHON=""
for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR="${VER%%.*}"; MINOR="${VER##*.}"
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

[ -n "$PYTHON" ] || die "Python 3.12+ not found — install it first (apt install python3.12)"
info "Python: $($PYTHON --version)"

# ── install agent into INSTALL_DIR ────────────────────────────────────────────
section "Installing agent to $INSTALL_DIR"

mkdir -p "$INSTALL_DIR"

# Copy agent + the shared contract it depends on (rsync preserves timestamps).
# The agent lives at $INSTALL_DIR; the contract goes under $INSTALL_DIR/contract.
# Preserve .venv and contract/ across re-runs of the agent rsync.
if command -v rsync &>/dev/null; then
    rsync -a --delete --exclude='.venv' --exclude='contract' "$AGENT_SRC/" "$INSTALL_DIR/"
    rsync -a --delete "$CONTRACT_SRC/" "$INSTALL_DIR/contract/"
else
    cp -r "$AGENT_SRC/." "$INSTALL_DIR/"
    mkdir -p "$INSTALL_DIR/contract" && cp -r "$CONTRACT_SRC/." "$INSTALL_DIR/contract/"
fi

# Create virtualenv if it doesn't exist yet
VENV="$INSTALL_DIR/.venv"
if [ ! -d "$VENV" ]; then
    info "Creating virtualenv at $VENV"
    "$PYTHON" -m venv "$VENV"
fi

info "Installing Python dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR/contract"   # shared contract first
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR"

AGENT_BIN="$VENV/bin/allocator-agent"
[ -x "$AGENT_BIN" ] || die "allocator-agent binary not found after install"
info "Installed: $($AGENT_BIN --version 2>/dev/null || echo 'ok')"

# ── write systemd service unit ────────────────────────────────────────────────
section "Writing systemd service"

SERVICE_FILE="/etc/systemd/system/allocator-agent.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Allocator USB Node Agent
Documentation=https://github.com/your-org/allocator
After=network-online.target usbipd.service
Wants=network-online.target usbipd.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment=MANAGER_URL=$MANAGER_URL
Environment=NODE_NAME=$NODE_NAME
Environment=AGENT_PORT=$AGENT_PORT
Environment=LOG_JSON=true
ExecStart=$AGENT_BIN --host 0.0.0.0 --port $AGENT_PORT
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=allocator-agent

[Install]
WantedBy=multi-user.target
EOF

info "Wrote $SERVICE_FILE"

# ── enable and start ──────────────────────────────────────────────────────────
section "Starting service"

systemctl daemon-reload
systemctl enable allocator-agent
systemctl restart allocator-agent

# Give it a moment to come up
sleep 2

if systemctl is-active --quiet allocator-agent; then
    info "allocator-agent is running"
else
    warn "Service did not start cleanly — check logs:"
    warn "  journalctl -u allocator-agent -n 50"
    exit 1
fi

# ── done ──────────────────────────────────────────────────────────────────────
section "Done"
echo -e "  ${GREEN}Agent${NC}  http://localhost:$AGENT_PORT/api/v1/health"
echo -e "  ${GREEN}Node${NC}   $NODE_NAME  →  $MANAGER_URL"
echo ""
echo -e "Useful commands:"
echo -e "  journalctl -u allocator-agent -f   # stream logs"
echo -e "  systemctl status allocator-agent   # service status"
echo -e "  systemctl stop allocator-agent     # stop"
echo -e "  systemctl restart allocator-agent  # restart after config change"
echo ""
echo -e "To update the agent after a code change, re-run this script."
