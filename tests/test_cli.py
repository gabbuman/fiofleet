import json

from click.testing import CliRunner

from fiofleet import cli
from fiofleet.api import FoundriesAPI


def _auth(monkeypatch):
    monkeypatch.setenv("FOUNDRIES_API_TOKEN", "t")
    monkeypatch.setenv("FOUNDRIES_FACTORY", "f")


# --- exec: structured results ---

def test_exec_json_collects_per_device_results(monkeypatch):
    _auth(monkeypatch)
    outputs = {
        "dev1": (0, "hello\n", ""),
        "dev2": (1, "", "boom\n"),
    }
    monkeypatch.setattr(cli.ssh_mod, "run_command",
                        lambda device, command, **kw: outputs[device])
    # resolve a fixed target set without hitting the API
    monkeypatch.setattr(cli, "resolve_targets", lambda *a, **k: ["dev1", "dev2"])

    result = CliRunner().invoke(cli.cli, ["exec", "echo hi", "--tag", "x", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    by_dev = {r["device"]: r for r in data}
    assert by_dev["dev1"]["ok"] is True and by_dev["dev1"]["exit_code"] == 0
    assert by_dev["dev2"]["ok"] is False and "boom" in by_dev["dev2"]["stderr"]


def test_exec_strict_exits_nonzero_on_failure(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(cli.ssh_mod, "run_command", lambda device, command, **kw: (2, "", "nope"))
    monkeypatch.setattr(cli, "resolve_targets", lambda *a, **k: ["dev1"])
    result = CliRunner().invoke(cli.cli, ["exec", "false", "--name", "dev1", "--strict"])
    assert result.exit_code == 1


# --- ota report ---

def _events_success():
    return [
        {"eventType": {"id": "EcuDownloadStarted"}, "deviceTime": "2026-05-20T10:00:00Z", "event": {}},
        {"eventType": {"id": "EcuDownloadCompleted"}, "deviceTime": "2026-05-20T10:01:00Z",
         "event": {"success": True}},
        {"eventType": {"id": "EcuInstallationStarted"}, "deviceTime": "2026-05-20T10:02:00Z", "event": {}},
        {"eventType": {"id": "EcuInstallationCompleted"}, "deviceTime": "2026-05-20T10:03:00Z",
         "event": {"success": True}},
    ]


def _events_install_fail():
    return [
        {"eventType": {"id": "EcuDownloadStarted"}, "deviceTime": "2026-05-20T10:00:00Z", "event": {}},
        {"eventType": {"id": "EcuDownloadCompleted"}, "deviceTime": "2026-05-20T10:01:00Z",
         "event": {"success": True}},
        {"eventType": {"id": "EcuInstallationStarted"}, "deviceTime": "2026-05-20T10:02:00Z", "event": {}},
        {"eventType": {"id": "EcuInstallationCompleted"}, "deviceTime": "2026-05-20T10:03:00Z",
         "event": {"success": False, "details": "rollback: kernel panic on boot"}},
    ]


def test_ota_report_json_includes_fleet_summary(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(cli, "resolve_targets", lambda *a, **k: ["dev1", "dev2"])
    updates_by_dev = {
        "dev1": [{"correlation-id": "c1", "target": "lmp-1"}],
        "dev2": [{"correlation-id": "c2", "target": "lmp-1"}],
    }
    events_by_cid = {"c1": _events_success(), "c2": _events_install_fail()}
    monkeypatch.setattr(FoundriesAPI, "list_updates",
                        lambda self, name, limit=10: updates_by_dev[name])
    monkeypatch.setattr(FoundriesAPI, "update_events",
                        lambda self, name, cid: events_by_cid[cid])

    result = CliRunner().invoke(cli.cli, ["ota", "report", "--tag", "x", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["summary"]["by_result"] == {"SUCCESS": 1, "FAILED": 1}
    assert data["summary"]["failed_by_stage"] == {"install": 1}
    failed = [d for d in data["devices"] if d["result"] == "FAILED"][0]
    assert failed["device"] == "dev2"
    assert "kernel panic" in failed["error"]


def test_ota_report_failed_only_text(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(cli, "resolve_targets", lambda *a, **k: ["dev1", "dev2"])
    updates_by_dev = {
        "dev1": [{"correlation-id": "c1", "target": "lmp-1"}],
        "dev2": [{"correlation-id": "c2", "target": "lmp-1"}],
    }
    events_by_cid = {"c1": _events_success(), "c2": _events_install_fail()}
    monkeypatch.setattr(FoundriesAPI, "list_updates",
                        lambda self, name, limit=10: updates_by_dev[name])
    monkeypatch.setattr(FoundriesAPI, "update_events",
                        lambda self, name, cid: events_by_cid[cid])

    result = CliRunner().invoke(cli.cli, ["ota", "report", "--tag", "x", "--failed-only"])
    assert result.exit_code == 0
    assert "dev2" in result.output
    assert "dev1" not in result.output.split("Fleet summary")[0]  # not in the device rows
    assert "kernel panic" in result.output
