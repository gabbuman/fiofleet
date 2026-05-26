"""SSH helpers.

These assume a route to the device already exists — i.e. you are running from
(or peered into) the Factory WireGuard server, where device names resolve to
their 10.42.42.x VPN addresses (the server keeps /etc/hosts in sync). fiofleet
does not manage the tunnel itself.
"""

import subprocess

DEFAULT_RUN_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
DEFAULT_SHELL_OPTS = ["-o", "StrictHostKeyChecking=accept-new"]


def _target(device, user):
    return f"{user}@{device}" if user else device


def _identity_opts(identity_file):
    return ["-i", identity_file] if identity_file else []


def run_command(device, command, user="fio", timeout=60, ssh_options=None, identity_file=None):
    """Run a command on a device over SSH. Returns (exit_code, stdout, stderr)."""
    opts = (ssh_options or DEFAULT_RUN_OPTS) + _identity_opts(identity_file)
    cmd = ["ssh", *opts, _target(device, user), command]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def interactive_shell(device, user="fio", ssh_options=None, identity_file=None):
    """Open an interactive SSH session to the device."""
    opts = (ssh_options or DEFAULT_SHELL_OPTS) + _identity_opts(identity_file)
    cmd = ["ssh", *opts, _target(device, user)]
    return subprocess.call(cmd)
