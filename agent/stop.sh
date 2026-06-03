#!/usr/bin/env bash
# stop.sh — stop the allocator node agent completely.
#
# Usage:
#   ./stop.sh                 # stop allocator-agent + usbipd
#   ./stop.sh --keep-usbipd   # stop only the agent, leave usbipd running
#   ./stop.sh --disable       # ALSO disable the services from starting on boot
#
# Stops the systemd units written by setup.sh. Needs root for systemctl (sudo
# is used automatically). Does not uninstall anything.
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── parse arguments ───────────────────────────────────────────────────────────
KEEP_USBIPD=0
DISABLE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-usbipd) KEEP_USBIPD=1; shift ;;
        --disable)     DISABLE=1; shift ;;
        -h|--help)     sed -n '2,10p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1 (try --help)" ;;
    esac
done

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

command -v systemctl &>/dev/null || die "systemctl not found — this agent is managed by systemd"

stop_unit() {
    local unit="$1"
    if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}"; then
        $SUDO systemctl stop "$unit" 2>/dev/null || true
        if [ "$DISABLE" -eq 1 ]; then
            $SUDO systemctl disable "$unit" 2>/dev/null || true
            info "$unit stopped and disabled"
        else
            info "$unit stopped"
        fi
    else
        info "$unit not installed — skipping"
    fi
}

section "Stopping agent"
stop_unit allocator-agent.service

if [ "$KEEP_USBIPD" -eq 0 ]; then
    stop_unit usbipd.service
else
    info "leaving usbipd running (--keep-usbipd)"
fi

section "Done"
echo -e "Agent stopped. Restart: ${GREEN}sudo systemctl start allocator-agent${NC}  (or re-run agent/setup.sh)"
