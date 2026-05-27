import os
from pathlib import Path

DEFAULT_API_BASE = "https://api.foundries.io/ota"

# Bastion (WireGuard-server jump host) keys, with their env-var overrides.
# Storing these lets `ssh`/`exec` run from any machine, hopping through the
# server that's actually on the device VPN. See transport.BastionTransport.
_SERVER_KEYS = {
    "server":          "FIOFLEET_SERVER",
    "server_user":     "FIOFLEET_SERVER_USER",
    "server_port":     "FIOFLEET_SERVER_PORT",
    "server_key":      "FIOFLEET_SERVER_KEY",
    "server_password": "FIOFLEET_SERVER_PASSWORD",
    "device_user":     "FIOFLEET_DEVICE_USER",
    "device_password": "FIOFLEET_DEVICE_PASSWORD",
    "device_key":      "FIOFLEET_DEVICE_KEY",
}


def config_path():
    """Location of the config file. Override the dir with FIOFLEET_CONFIG_DIR."""
    base = os.environ.get("FIOFLEET_CONFIG_DIR")
    if base:
        return Path(base) / "config"
    return Path.home() / ".config" / "fiofleet" / "config"


# kept as a module attribute for backwards-compat / easy reference
CONFIG_PATH = config_path()


def _read_all():
    """All key=value pairs from the config file as a dict (missing file -> {})."""
    path = config_path()
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            out[k] = v
    return out


def _write_all(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items() if v not in (None, "")]
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)  # contains a token / possibly a password
    except (OSError, NotImplementedError):
        # chmod is a no-op on some platforms (e.g. Windows); not fatal.
        pass
    return path


def load():
    """
    Resolve (token, factory, api_base).

    Precedence: environment variables, then the config file. This lets CI and
    one-off shells override a saved config without editing it.
    """
    token = os.environ.get("FOUNDRIES_API_TOKEN")
    factory = os.environ.get("FOUNDRIES_FACTORY")
    api_base = os.environ.get("FOUNDRIES_API_BASE")

    if not token or not factory or not api_base:
        data = _read_all()
        token = token or data.get("token")
        factory = factory or data.get("factory")
        api_base = api_base or data.get("api_base")

    return token, factory, api_base or DEFAULT_API_BASE


def save(token, factory, api_base=None):
    """Persist token/factory/api_base, preserving any saved server settings."""
    data = _read_all()
    data["token"] = token
    data["factory"] = factory
    if api_base and api_base != DEFAULT_API_BASE:
        data["api_base"] = api_base
    else:
        data.pop("api_base", None)
    return _write_all(data)


def load_server():
    """Resolve bastion settings (env over file) as a dict, or None if no host.

    Returns keys: host, user, port, key, password, device_user,
    device_password, device_key.
    """
    data = _read_all()
    resolved = {}
    for key, env in _SERVER_KEYS.items():
        resolved[key] = os.environ.get(env) or data.get(key)

    if not resolved.get("server"):
        return None
    return {
        "host":            resolved["server"],
        "user":            resolved.get("server_user") or "root",
        "port":            int(resolved.get("server_port") or 22),
        "key":             resolved.get("server_key"),
        "password":        resolved.get("server_password"),
        "device_user":     resolved.get("device_user") or "fio",
        "device_password": resolved.get("device_password"),
        "device_key":      resolved.get("device_key"),
    }


def save_server(**kwargs):
    """Merge given server_*/device_* keys into the config file (None = leave as is)."""
    data = _read_all()
    for key in _SERVER_KEYS:
        if kwargs.get(key) is not None:
            data[key] = kwargs[key]
    return _write_all(data)
