import os
from pathlib import Path

DEFAULT_API_BASE = "https://api.foundries.io/ota"


def config_path():
    """Location of the config file. Override the dir with FIOFLEET_CONFIG_DIR."""
    base = os.environ.get("FIOFLEET_CONFIG_DIR")
    if base:
        return Path(base) / "config"
    return Path.home() / ".config" / "fiofleet" / "config"


# kept as a module attribute for backwards-compat / easy reference
CONFIG_PATH = config_path()


def load():
    """
    Resolve (token, factory, api_base).

    Precedence: environment variables, then the config file. This lets CI and
    one-off shells override a saved config without editing it.
    """
    token = os.environ.get("FOUNDRIES_API_TOKEN")
    factory = os.environ.get("FOUNDRIES_FACTORY")
    api_base = os.environ.get("FOUNDRIES_API_BASE")

    path = config_path()
    if (not token or not factory or not api_base) and path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            if k == "token" and not token:
                token = v
            elif k == "factory" and not factory:
                factory = v
            elif k == "api_base" and not api_base:
                api_base = v

    return token, factory, api_base or DEFAULT_API_BASE


def save(token, factory, api_base=None):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"token={token}", f"factory={factory}"]
    if api_base and api_base != DEFAULT_API_BASE:
        lines.append(f"api_base={api_base}")
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        # chmod is a no-op on some platforms (e.g. Windows); not fatal.
        pass
    return path
