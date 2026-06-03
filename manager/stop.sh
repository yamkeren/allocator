#!/usr/bin/env bash
# stop.sh — stop the allocator manager completely (containers + mDNS publisher).
#
# Usage:
#   ./stop.sh              # stop & remove the stack containers + mDNS advertiser (keeps data)
#   ./stop.sh --volumes    # ALSO delete the Postgres volume (wipes all data!)
#   ./stop.sh --disable    # ALSO disable the mDNS service from starting on boot
#
# Stops: docker compose stack (postgres + manager + pgadmin) and the
# allocator-mdns.service systemd unit written by setup.sh. Data volumes are
# preserved unless --volumes is given.
set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── parse arguments ───────────────────────────────────────────────────────────
WIPE=0
DISABLE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --volumes) WIPE=1; shift ;;
        --disable) DISABLE=1; shift ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1 (try --help)" ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

# ── stop the docker stack ─────────────────────────────────────────────────────
section "Stopping manager stack"
if command -v docker &>/dev/null && { docker compose version &>/dev/null 2>&1 || command -v docker-compose &>/dev/null; }; then
    if docker compose version &>/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
    cd "$DEPLOY_DIR"
    if [ "$WIPE" -eq 1 ]; then
        warn "Removing containers AND volumes (Postgres data will be wiped)"
        $DC down -v
    else
        $DC down
    fi
    info "Containers stopped"
else
    warn "docker / docker compose not found — skipping container stop"
fi

# ── stop the mDNS publisher ───────────────────────────────────────────────────
section "Stopping mDNS publisher"
if systemctl list-unit-files 2>/dev/null | grep -q '^allocator-mdns.service'; then
    $SUDO systemctl stop allocator-mdns.service 2>/dev/null || true
    if [ "$DISABLE" -eq 1 ]; then
        $SUDO systemctl disable allocator-mdns.service 2>/dev/null || true
        info "allocator-mdns stopped and disabled"
    else
        info "allocator-mdns stopped"
    fi
else
    info "no allocator-mdns service installed — skipping"
fi

section "Done"
echo -e "Manager stopped."
[ "$WIPE" -eq 0 ] && echo -e "  ${YELLOW}data preserved${NC} (use --volumes to wipe). Restart: ${GREEN}manager/setup.sh --domain <name>${NC}"
