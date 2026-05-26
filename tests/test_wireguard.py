from fiofleet import wireguard


class FakeAPI:
    def __init__(self, statuses):
        # statuses: list of (enabled, msg) returned on successive calls
        self._statuses = list(statuses)
        self.enabled_calls = []
        self.disabled_calls = []

    def enable_wireguard(self, device):
        self.enabled_calls.append(device)

    def disable_wireguard(self, device):
        self.disabled_calls.append(device)

    def wireguard_status(self, device):
        return self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]


def test_enable_disable_delegate_to_api():
    api = FakeAPI([(True, "ok")])
    wireguard.enable(api, "dev1")
    wireguard.disable(api, "dev1")
    assert api.enabled_calls == ["dev1"]
    assert api.disabled_calls == ["dev1"]


def test_wait_applied_succeeds_after_polling(monkeypatch):
    monkeypatch.setattr(wireguard.time, "sleep", lambda s: None)
    api = FakeAPI([(False, "pending"), (False, "pending"), (True, "peer active")])
    ok, msg = wireguard.wait_applied(api, "dev1", timeout=100, interval=1)
    assert ok is True
    assert "peer active" in msg


def test_wait_applied_times_out(monkeypatch):
    monkeypatch.setattr(wireguard.time, "sleep", lambda s: None)
    # time advances past the deadline on the second check
    t = {"v": 1000.0}
    monkeypatch.setattr(wireguard.time, "time", lambda: t["v"])

    def step(*a, **k):
        t["v"] += 60
        return (False, "pending")

    api = FakeAPI([(False, "pending")])
    monkeypatch.setattr(api, "wireguard_status", step)
    ok, msg = wireguard.wait_applied(api, "dev1", timeout=30, interval=1)
    assert ok is False
    assert "not applied" in msg
