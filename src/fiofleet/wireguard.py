"""WireGuard fleet operations.

Two ways to flip WireGuard on a device:

* the API path (default) — PATCH the device config directly, no external
  binary required. Works anywhere the API is reachable.
* the fioctl path (opt-in) — shell out to `fioctl devices config wireguard`,
  for users who prefer to keep fioctl as the single source of truth.

Either way, the change is *queued*; the device applies it on its next check-in.
`wait_applied` polls the API until the platform reports the device as a live
VPN peer (or the config shows applied), or until it times out.
"""

import subprocess
import time


# --- API path (no external dependency) ---

def enable(api, device):
    api.enable_wireguard(device)


def disable(api, device):
    api.disable_wireguard(device)


# --- fioctl path (opt-in) ---

def fioctl_enable(device, fioctl="fioctl"):
    return _run_fioctl(["devices", "config", "wireguard", device, "enable"], fioctl)


def fioctl_disable(device, fioctl="fioctl"):
    return _run_fioctl(["devices", "config", "wireguard", device, "disable"], fioctl)


def _run_fioctl(args, fioctl):
    result = subprocess.run([fioctl, *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"{fioctl} {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


# --- waiting ---

def wait_applied(api, device, timeout=900, interval=15):
    """Poll until the device's WireGuard config is applied, or timeout."""
    deadline = time.time() + timeout
    while True:
        enabled, msg = api.wireguard_status(device)
        if enabled:
            return True, msg
        if time.time() >= deadline:
            return False, f"not applied within {timeout}s ({msg})"
        time.sleep(interval)
