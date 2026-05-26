import ipaddress

import requests
from datetime import datetime, timezone

from .config import DEFAULT_API_BASE

ONLINE_THRESHOLD_SECONDS = 600  # a device is "online" if seen in the last 10 min

# Per-device config file controlling WireGuard, and the factory-level file the
# VPN server publishes. These match what fioctl/fioconfig use, so fiofleet and
# the rest of the Foundries tooling stay interoperable.
WG_CLIENT_FILE = "wireguard-client"
WG_SERVER_FILE = "wireguard-server"
# OnChanged handler an LmP device runs to apply the config (no-op elsewhere).
WG_VPN_HANDLER = "/usr/share/fioconfig/handlers/factory-config-vpn"


def _parse_ts(value):
    """Parse an ISO-8601 timestamp from the API into an aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_kv(value):
    """Parse the `key=value` lines of a wireguard config file (tolerates indent)."""
    out = {}
    for line in (value or "").splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class FoundriesAPI:
    """Thin client over the Foundries.io OTA REST API.

    Auth is the personal API token (https://app.foundries.io/settings/tokens/),
    sent as the OSF-TOKEN header. Everything is scoped to a single factory.
    """

    def __init__(self, token, factory, api_base=DEFAULT_API_BASE, session=None):
        self.factory = factory
        self.api_base = api_base.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "OSF-TOKEN": token,
        })

    # --- low-level ---

    def _url(self, path):
        return f"{self.api_base}{path}"

    def _get(self, path, **params):
        r = self.session.get(self._url(path), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _patch(self, path, payload):
        r = self.session.patch(self._url(path), json=payload, timeout=30)
        r.raise_for_status()
        return r

    # --- factories ---

    def list_factories(self):
        return self._get("/factories/")

    def factory_config(self):
        data = self._get(f"/factories/{self.factory}/config/")
        return data.get("config", [])

    # --- devices ---

    def list_devices(self, tag=None, group=None):
        """List devices in the factory, optionally filtered by tag/group."""
        params = {"factory": self.factory, "page": 1, "limit": 1000}
        if tag:
            params["match_tag"] = tag
        if group:
            params["group"] = group
        data = self._get("/devices/", **params)
        return data.get("devices", [])

    def get_device(self, name):
        return self._get(f"/devices/{name}/")

    def get_device_configs(self, name):
        data = self._get(f"/devices/{name}/config/", page=1, limit=1000)
        return data.get("config", [])

    def is_online(self, device):
        """Take a device dict (from list_devices/get_device) and return a bool."""
        ts = _parse_ts(device.get("last-seen"))
        if ts is None:
            return False
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() <= ONLINE_THRESHOLD_SECONDS

    # --- ota updates ---

    def list_updates(self, name, limit=10):
        """Recent OTA updates for a device, newest first.

        Each item identifies one update attempt (a `correlation-id`) plus the
        target/version it was moving to. The per-stage events live behind
        `update_events`. Mirrors the data `fioctl devices list-updates` shows.
        """
        data = self._get(f"/devices/{name}/updates/", page=1, limit=limit)
        return data.get("updates", data) if isinstance(data, dict) else data

    def update_events(self, name, correlation_id):
        """The ordered aktualizr event stream for a single update.

        These are the libaktualizr report events (EcuDownloadStarted,
        EcuInstallationCompleted, ...) the device posted to the device-gateway.
        `updates.summarize` turns them into a staged pass/fail result.
        """
        data = self._get(f"/devices/{name}/updates/{correlation_id}/")
        if isinstance(data, dict):
            return data.get("events", data.get("Events", []))
        return data or []

    # --- wireguard ---

    def wireguard_server(self):
        """Parse the factory's wireguard-server config, or None if not enabled.

        Returns a dict with endpoint, server_address, pubkey, enabled.
        """
        for cfg in self.factory_config():
            for f in cfg.get("files", []):
                if f.get("name") == WG_SERVER_FILE:
                    kv = _parse_kv(f.get("value"))
                    kv["enabled"] = kv.get("enabled", "1") != "0"
                    return kv
        return None

    def wireguard_ips(self):
        """List of {name, pubkey, ip, enabled} — the live VPN peer view.

        This is the same endpoint the Factory WireGuard server reads to build its
        peer list, so it's the authoritative 'is this device on the VPN' source.
        """
        return self._get(f"/factories/{self.factory}/wireguard-ips/") or []

    def wireguard_client(self, name):
        """Parse a device's wireguard-client config into {address, pubkey, enabled}."""
        for cfg in self.get_device_configs(name):
            for f in cfg.get("files", []):
                if f.get("name") == WG_CLIENT_FILE:
                    kv = _parse_kv(f.get("value"))
                    return {
                        "address": kv.get("address", ""),
                        "pubkey": kv.get("pubkey", ""),
                        # absence of an `enabled` line means enabled (fioctl semantics)
                        "enabled": kv.get("enabled", "1") != "0",
                    }
        return {"address": "", "pubkey": "", "enabled": False}

    def find_vpn_address(self):
        """Pick the next free 10.42.42.x address (mirrors fioctl's allocator)."""
        server = self.wireguard_server()
        if not server or not server.get("enabled") or not server.get("server_address"):
            raise RuntimeError("No WireGuard server is configured for this factory")
        base = int(ipaddress.ip_address(server["server_address"]))
        used = {
            int(ipaddress.ip_address(i["ip"]))
            for i in self.wireguard_ips() if i.get("ip")
        }
        for cand in range(base + 1, base + 10000):
            if cand not in used and (cand & 0xFF) != 0:
                return str(ipaddress.ip_address(cand))
        raise RuntimeError("Unable to find a free VPN address")

    def _write_wireguard_client(self, name, address, pubkey, enabled, reason):
        value = f"address={address}\npubkey={pubkey}"
        if not enabled:
            value += "\nenabled=0"
        payload = {
            "reason": reason,
            "files": [{
                "name": WG_CLIENT_FILE,
                "unencrypted": True,
                "value": value,
                "on-changed": [WG_VPN_HANDLER],
            }],
        }
        return self._patch(f"/devices/{name}/config/", payload)

    def enable_wireguard(self, name):
        """Enable WireGuard on a device.

        The device must already have reported a public key (an on-device agent —
        fioconfig on LmP, or fiofleet's harness agent — generates the keypair and
        publishes the pubkey over its mTLS channel). We then allocate a VPN
        address and write the client config, exactly as `fioctl` does.
        """
        wcc = self.wireguard_client(name)
        if not wcc["pubkey"]:
            raise RuntimeError(
                f"{name} has not reported a WireGuard public key yet "
                "(the device generates it on first check-in)"
            )
        address = wcc["address"] or self.find_vpn_address()
        return self._write_wireguard_client(
            name, address, wcc["pubkey"], True, "fiofleet: enable wireguard"
        )

    def disable_wireguard(self, name):
        wcc = self.wireguard_client(name)
        return self._write_wireguard_client(
            name, wcc["address"], wcc["pubkey"], False, "fiofleet: disable wireguard"
        )

    def wireguard_status(self, name):
        """Return (enabled: bool, message: str) for a device's WireGuard."""
        for item in self.wireguard_ips():
            if item.get("name") == name:
                if item.get("enabled"):
                    return True, f"peer active at {item.get('ip')}"
                return False, "configured but disabled"
        wcc = self.wireguard_client(name)
        if wcc["pubkey"] and wcc["address"] and wcc["enabled"]:
            return True, f"enabled at {wcc['address']} (awaiting server sync)"
        if wcc["pubkey"]:
            return False, "pubkey reported, not enabled"
        return False, "no wireguard config found"
