from datetime import datetime, timezone, timedelta

from fiofleet.api import FoundriesAPI, WG_CLIENT_FILE, _parse_kv


def make_api():
    return FoundriesAPI(token="t", factory="f")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- helpers ---

def test_parse_kv_tolerates_indentation():
    kv = _parse_kv("endpoint=1.2.3.4:5555\n    server_address=10.42.42.1\n    pubkey=abc")
    assert kv == {"endpoint": "1.2.3.4:5555", "server_address": "10.42.42.1", "pubkey": "abc"}


# --- online detection ---

def test_is_online_recent():
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert make_api().is_online({"last-seen": iso(recent)}) is True


def test_is_online_stale():
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    assert make_api().is_online({"last-seen": iso(old)}) is False


def test_is_online_missing_or_bad():
    api = make_api()
    assert api.is_online({}) is False
    assert api.is_online({"last-seen": "not-a-date"}) is False


# --- address allocation (mirrors fioctl) ---

def test_find_vpn_address_first_free(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_server",
                        lambda: {"server_address": "10.42.42.1", "enabled": True})
    monkeypatch.setattr(api, "wireguard_ips", lambda: [])
    assert api.find_vpn_address() == "10.42.42.2"


def test_find_vpn_address_skips_used(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_server",
                        lambda: {"server_address": "10.42.42.1", "enabled": True})
    monkeypatch.setattr(api, "wireguard_ips",
                        lambda: [{"ip": "10.42.42.2"}, {"ip": "10.42.42.3"}])
    assert api.find_vpn_address() == "10.42.42.4"


def test_find_vpn_address_no_server(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_server", lambda: None)
    try:
        api.find_vpn_address()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "server" in str(e).lower()


# --- enable / disable ---

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append((path, payload))


def test_enable_requires_pubkey(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "", "pubkey": "", "enabled": False})
    try:
        api.enable_wireguard("dev1")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "public key" in str(e).lower()


def test_enable_allocates_address_and_writes(monkeypatch):
    api = make_api()
    rec = _Recorder()
    monkeypatch.setattr(api, "_patch", rec)
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "", "pubkey": "PUB", "enabled": False})
    monkeypatch.setattr(api, "find_vpn_address", lambda: "10.42.42.7")
    api.enable_wireguard("dev1")
    path, payload = rec.calls[0]
    assert path == "/devices/dev1/config/"
    f = payload["files"][0]
    assert f["name"] == WG_CLIENT_FILE and f["unencrypted"] is True
    assert "address=10.42.42.7" in f["value"]
    assert "pubkey=PUB" in f["value"]
    assert "enabled=0" not in f["value"]  # enabled is implicit


def test_enable_keeps_existing_address(monkeypatch):
    api = make_api()
    rec = _Recorder()
    monkeypatch.setattr(api, "_patch", rec)
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "10.42.42.5", "pubkey": "PUB", "enabled": True})
    api.enable_wireguard("dev1")
    _, payload = rec.calls[0]
    assert "address=10.42.42.5" in payload["files"][0]["value"]


def test_disable_writes_enabled_zero(monkeypatch):
    api = make_api()
    rec = _Recorder()
    monkeypatch.setattr(api, "_patch", rec)
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "10.42.42.5", "pubkey": "PUB", "enabled": True})
    api.disable_wireguard("dev1")
    _, payload = rec.calls[0]
    assert "enabled=0" in payload["files"][0]["value"]


# --- status ---

def test_status_peer_active(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_ips",
                        lambda: [{"name": "dev1", "ip": "10.42.42.2", "enabled": True}])
    enabled, msg = api.wireguard_status("dev1")
    assert enabled is True and "10.42.42.2" in msg


def test_status_falls_back_to_client_config(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_ips", lambda: [])
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "10.42.42.9", "pubkey": "P", "enabled": True})
    enabled, msg = api.wireguard_status("dev1")
    assert enabled is True and "10.42.42.9" in msg


def test_status_none(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "wireguard_ips", lambda: [])
    monkeypatch.setattr(api, "wireguard_client",
                        lambda n: {"address": "", "pubkey": "", "enabled": False})
    enabled, msg = api.wireguard_status("dev1")
    assert enabled is False and "no wireguard" in msg
