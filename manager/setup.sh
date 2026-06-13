#!/usr/bin/env bash
# setup.sh — start OR update the allocator manager stack (PostgreSQL + manager),
#            and publish a LAN domain name for it over mDNS (Avahi).
#
# Usage:
#   ./setup.sh --domain allocator              # http://allocator.local   (port 80)
#   ./setup.sh --domain allocator --port 8000  # http://allocator.local:8000
#
# The --domain name is published as <name>.local via Avahi, so any client/agent
# on the LAN with mDNS (Linux/macOS by default; Windows needs Bonjour) can reach
# the manager without touching /etc/hosts or a DNS server. The manager is
# published on --port (default 80), so the URL needs no port when left at 80.
#
# Safe to re-run at any time (restarts the manager, re-publishes the name).
# Publishing the name needs root for apt + systemd (sudo is used automatically).
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── parse arguments ───────────────────────────────────────────────────────────
DOMAIN=""
PORT=80                       # host port the manager is published on (default 80)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --port)   PORT="$2";   shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) die "Unknown argument: $1 (try --help)" ;;
    esac
done
[ -n "$DOMAIN" ] || die "--domain is required, e.g. --domain allocator (published as <name>.local)"
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "--port must be 1-65535"

# Published host port → container's 8000 (compose reads MANAGER_PORT).
export MANAGER_PORT="$PORT"
# Omit the default :80 from displayed URLs; show it for any other port.
[ "$PORT" = "80" ] && PORT_SUFFIX="" || PORT_SUFFIX=":$PORT"

# mDNS only resolves the .local TLD. Take the first label and append .local so
# both 'allocator' and 'allocator.lan' become 'allocator.local'.
MDNS_NAME="${DOMAIN%%.*}.local"
if [ "$DOMAIN" != "${DOMAIN%%.*}" ] && [ "$DOMAIN" != "$MDNS_NAME" ]; then
    warn "mDNS uses the .local TLD — publishing as '$MDNS_NAME' (from '$DOMAIN')"
fi

# Privileged steps (apt + systemd) use sudo unless already root.
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

# ── locate deploy dir (this script lives in manager/) ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"

# ── check prerequisites ───────────────────────────────────────────────────────
section "Checking prerequisites"

command -v docker &>/dev/null || die "docker not found — install Docker first"

# Support both 'docker compose' (plugin) and 'docker-compose' (standalone)
if docker compose version &>/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose &>/dev/null; then
    DC="docker-compose"
else
    die "docker compose not found — install Docker Compose"
fi

info "Docker: $(docker --version)"
info "Compose: $($DC version)"

# ── start postgres ────────────────────────────────────────────────────────────
section "Starting PostgreSQL"
cd "$DEPLOY_DIR"

$DC up -d postgres
info "Waiting for PostgreSQL to be healthy..."

for i in $(seq 1 30); do
    if $DC exec -T postgres pg_isready -U allocator -q 2>/dev/null; then
        info "PostgreSQL is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        die "PostgreSQL did not become healthy after 30s"
    fi
    sleep 1
done

# ── (re)create manager ────────────────────────────────────────────────────────
section "Starting Manager"
# 'up -d --build' recreates the container when the compose config, port, or
# image (Dockerfile) changed — unlike 'restart', which reuses the old container
# and ignores those changes. Source edits are picked up live via the volume
# mount + uvicorn --reload; the container start runs 'alembic upgrade head'.
$DC up -d --build manager
info "Waiting for /health... (a fresh container installs deps first)"

for i in $(seq 1 60); do
    if curl -sf "http://localhost:${PORT}/health" &>/dev/null; then
        info "Manager is up"
        break
    fi
    if [ "$i" -eq 60 ]; then
        warn "Manager did not respond after 60s — check logs:"
        warn "  $DC logs manager"
        exit 1
    fi
    sleep 1
done

# ── publish the LAN domain over mDNS (Avahi) ──────────────────────────────────
section "Publishing $MDNS_NAME over mDNS"

# Install avahi if needed (Debian/Ubuntu).
if ! command -v avahi-publish &>/dev/null; then
    if command -v apt-get &>/dev/null; then
        info "Installing avahi-daemon + avahi-utils"
        $SUDO apt-get update -q
        $SUDO apt-get install -y --no-install-recommends avahi-daemon avahi-utils
    else
        die "avahi-publish not found and apt-get unavailable — install avahi-utils manually"
    fi
fi
$SUDO systemctl enable --now avahi-daemon &>/dev/null || warn "could not enable avahi-daemon"

# The IP this host uses on the LAN (source address toward the default route).
HOST_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)"
[ -n "$HOST_IP" ] || HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$HOST_IP" ] || die "could not determine this host's LAN IP"
AVAHI_PUBLISH="$(command -v avahi-publish)"

# A long-running service keeps the alias advertised (avahi-publish must stay up).
info "Publishing $MDNS_NAME -> $HOST_IP"
$SUDO tee /etc/systemd/system/allocator-mdns.service >/dev/null <<EOF
[Unit]
Description=Allocator manager mDNS alias ($MDNS_NAME)
After=network-online.target avahi-daemon.service
Wants=network-online.target avahi-daemon.service

[Service]
ExecStart=$AVAHI_PUBLISH -a -R $MDNS_NAME $HOST_IP
Restart=on-failure
RestartSec=3s

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now allocator-mdns.service
$SUDO systemctl restart allocator-mdns.service
sleep 1
if $SUDO systemctl is-active --quiet allocator-mdns.service; then
    info "$MDNS_NAME is being advertised on the LAN"
else
    warn "mDNS publish service failed — check: journalctl -u allocator-mdns -n 20"
fi

MANAGER_URL="http://${MDNS_NAME}${PORT_SUFFIX}"

# ── done ──────────────────────────────────────────────────────────────────────
section "Done"
echo -e "  ${GREEN}LAN URL${NC}    $MANAGER_URL          ${YELLOW}# use this on clients/agents${NC}"
echo -e "  ${GREEN}Local${NC}      http://localhost${PORT_SUFFIX}"
echo -e "  ${GREEN}Dashboard${NC}  $MANAGER_URL/          ${YELLOW}# operator console (GUI)${NC}"
echo -e "  ${GREEN}Docs${NC}       $MANAGER_URL/docs"
echo ""
echo -e "Point the others at it:"
echo -e "  client:  ${GREEN}export ALLOCATOR_URL=$MANAGER_URL${NC}"
echo -e "  agent:   ${GREEN}sudo agent/setup.sh --manager-url $MANAGER_URL${NC}"
echo ""
echo -e "Useful commands:"
echo -e "  $DC logs -f manager                     # stream logs"
echo -e "  $DC down                                # stop everything"
echo -e "  ${SUDO} systemctl status allocator-mdns   # mDNS advertiser status"
