#!/usr/bin/env bash
#
# Host-side orchestration for fiofleet's local test fleet.
#
# Spins up lightweight Docker "devices" (generic Linux + fioup + WireGuard + sshd)
# that register to your factory and connect to a Factory WireGuard server (e.g. a
# Raspberry Pi running factory-wireguard.py). See harness/README.md for the full
# picture and the one-time Pi server setup.
#
# Usage:
#   PI_PUBKEY="ssh-ed25519 AAAA... vpn-server"   # the VPN server's SSH public key
#   ./run-devices.sh build
#   ./run-devices.sh start  dev-eu-01 dev-us-01
#   ./run-devices.sh register dev-eu-01          # interactive: approve in browser
#   ./run-devices.sh register dev-us-01
#   # ...then enable on the control host:  fiofleet wg enable --tag main
#   ./run-devices.sh connect dev-eu-01 dev-us-01 # report pubkey + bring up tunnel
#   ./run-devices.sh reset                       # tear the fleet down
#
set -euo pipefail

IMAGE="${IMAGE:-fiofleet-device:latest}"
FACTORY="${FOUNDRIES_FACTORY:?set FOUNDRIES_FACTORY}"
TAG="${DEVICE_TAG:-main}"
HERE="$(cd "$(dirname "$0")" && pwd)"

build() {
    docker build -t "$IMAGE" "$HERE"
}

start() {
    : "${PI_PUBKEY:?set PI_PUBKEY to the VPN server's SSH public key}"
    for name in "$@"; do
        docker rm -f "$name" >/dev/null 2>&1 || true
        docker run -d --name "$name" --hostname "$name" \
            --cap-add NET_ADMIN -e PI_PUBKEY="$PI_PUBKEY" "$IMAGE" >/dev/null
        echo "started $name"
    done
}

register() {
    for name in "$@"; do
        echo "registering $name — approve the printed code at https://app.foundries.io/activate/"
        docker exec "$name" fioup register --factory "$FACTORY" --name "$name" --tag "$TAG"
    done
}

connect() {
    for name in "$@"; do
        echo "--- $name ---"
        docker exec "$name" python3 /usr/local/bin/wg-agent.py run
    done
}

reset() {
    docker ps -a --format '{{.Names}}' | grep -E '^dev-' | while read -r n; do
        docker rm -f "$n" >/dev/null && echo "removed $n"
    done
}

cmd="${1:-}"; shift || true
case "$cmd" in
    build) build ;;
    start) start "$@" ;;
    register) register "$@" ;;
    connect) connect "$@" ;;
    reset) reset ;;
    *) grep '^#' "$0" | sed 's/^# \?//'; exit 1 ;;
esac
