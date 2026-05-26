#!/usr/bin/env bash
# Device container entrypoint: authorize the VPN server's SSH key, start sshd,
# then idle. Registration and WireGuard bring-up are driven from the host
# (run-devices.sh) once the container is up, so we can inspect each step.
set -euo pipefail

# Authorize the Factory VPN server's public key for the fio user (passed at run).
if [ -n "${PI_PUBKEY:-}" ]; then
    echo "$PI_PUBKEY" > /home/fio/.ssh/authorized_keys
    chmod 600 /home/fio/.ssh/authorized_keys
    chown fio:fio /home/fio/.ssh/authorized_keys
fi

# Host key + sshd.
ssh-keygen -A >/dev/null 2>&1 || true
/usr/sbin/sshd

echo "device $(hostname) ready: sshd up, fio user authorized=${PI_PUBKEY:+yes}"
exec sleep infinity
