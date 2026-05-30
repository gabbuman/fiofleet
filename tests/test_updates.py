from fiofleet import updates


def ev(eid, t, success=None, details=None, target=None, kebab=False):
    """Build a synthetic aktualizr event. kebab=True uses the alternate wire shape."""
    body = {}
    if success is not None:
        body["success"] = success
    if details is not None:
        body["details"] = details
    if target is not None:
        body["targetName"] = target
    if kebab:
        out = {"event-type": {"id": eid}, "time": t}
        out.update(body)  # success/details at top level
        return out
    return {"eventType": {"id": eid}, "deviceTime": t, "event": body}


def success_stream():
    return [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z"),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=True),
        ev("EcuInstallationStarted", "2026-05-20T10:02:00Z"),
        ev("EcuInstallationApplied", "2026-05-20T10:03:00Z"),
        ev("EcuInstallationCompleted", "2026-05-20T10:05:00Z", success=True),
    ]


# --- result classification ---

def test_clean_success():
    s = updates.summarize({"correlation-id": "c1", "target": "lmp-123"}, success_stream())
    assert s["result"] == updates.RESULT_SUCCESS
    assert s["failed_stage"] is None
    assert s["error"] == ""
    by_stage = {st["stage"]: st["status"] for st in s["stages"]}
    assert by_stage == {"download": "ok", "install": "ok"}


def test_download_failure_captures_error():
    stream = [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z"),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=False,
           details="ostree pull: Server returned HTTP 404"),
    ]
    s = updates.summarize({"correlation-id": "c2"}, stream)
    assert s["result"] == updates.RESULT_FAILED
    assert s["failed_stage"] == "download"
    assert "404" in s["error"]
    # install never started
    assert s["stages"][1]["status"] == "-"


def test_install_failure_captures_error():
    stream = [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z"),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=True),
        ev("EcuInstallationStarted", "2026-05-20T10:02:00Z"),
        ev("EcuInstallationCompleted", "2026-05-20T10:03:00Z", success=False,
           details="Installation failed: package postinstall returned 1"),
    ]
    s = updates.summarize({"correlation-id": "c3"}, stream)
    assert s["result"] == updates.RESULT_FAILED
    assert s["failed_stage"] == "install"
    assert "postinstall" in s["error"]
    assert s["stages"][0]["status"] == "ok"  # download succeeded


def test_in_progress_when_applied_but_not_completed():
    stream = [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z"),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=True),
        ev("EcuInstallationStarted", "2026-05-20T10:02:00Z"),
        ev("EcuInstallationApplied", "2026-05-20T10:03:00Z"),  # awaiting reboot/confirm
    ]
    s = updates.summarize({"correlation-id": "c4"}, stream)
    assert s["result"] == updates.RESULT_IN_PROGRESS
    assert s["failed_stage"] is None
    assert s["stages"][1]["status"] == "applied"


def test_unknown_when_no_recognizable_events():
    s = updates.summarize({"correlation-id": "c5"}, [{"eventType": {"id": "DownloadProgressReport"}}])
    assert s["result"] == updates.RESULT_UNKNOWN


# --- robustness ---

def test_tolerates_kebab_and_toplevel_fields():
    stream = [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z", kebab=True),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=False,
           details="boom", kebab=True),
    ]
    s = updates.summarize({"correlationId": "c6"}, stream)
    assert s["correlation_id"] == "c6"
    assert s["result"] == updates.RESULT_FAILED
    assert s["failed_stage"] == "download"
    assert s["error"] == "boom"


def test_orders_events_by_time():
    # shuffled input — classification must still see install as the final stage
    stream = list(reversed(success_stream()))
    s = updates.summarize({}, stream)
    assert s["result"] == updates.RESULT_SUCCESS


def test_target_backfilled_from_events():
    stream = [
        ev("EcuDownloadStarted", "2026-05-20T10:00:00Z", target="lmp-from-event"),
        ev("EcuDownloadCompleted", "2026-05-20T10:01:00Z", success=True),
        ev("EcuInstallationStarted", "2026-05-20T10:02:00Z"),
        ev("EcuInstallationCompleted", "2026-05-20T10:03:00Z", success=True),
    ]
    s = updates.summarize({"correlation-id": "c7"}, stream)  # no target on the update
    assert s["target"] == "lmp-from-event"


# --- fleet aggregation ---

def test_fleet_summary_counts_and_failed_stages():
    summaries = [
        {"result": "SUCCESS", "failed_stage": None},
        {"result": "FAILED", "failed_stage": "install"},
        {"result": "FAILED", "failed_stage": "download"},
        {"result": "FAILED", "failed_stage": "install"},
        {"result": "IN_PROGRESS", "failed_stage": None},
    ]
    fs = updates.fleet_summary(summaries)
    assert fs["total"] == 5
    assert fs["by_result"] == {"SUCCESS": 1, "FAILED": 3, "IN_PROGRESS": 1}
    assert fs["failed_by_stage"] == {"install": 2, "download": 1}


# --- api-driven helpers ---

class FakeAPI:
    def __init__(self, updates_list, events_by_cid):
        self._updates = updates_list
        self._events = events_by_cid

    def list_updates(self, name, limit=10):
        return self._updates[:limit]

    def update_events(self, name, cid):
        return self._events.get(cid, [])


def test_latest_summarizes_most_recent():
    api = FakeAPI(
        [{"correlation-id": "newest", "target": "lmp-200"},
         {"correlation-id": "older", "target": "lmp-199"}],
        {"newest": success_stream()},
    )
    s = updates.latest(api, "dev1")
    assert s["device"] == "dev1"
    assert s["correlation_id"] == "newest"
    assert s["result"] == updates.RESULT_SUCCESS


def test_latest_handles_no_history():
    s = updates.latest(FakeAPI([], {}), "dev1")
    assert s["device"] == "dev1"
    assert s["result"] == updates.RESULT_UNKNOWN
    assert "no update history" in s["error"]


def test_history_returns_all_with_device_set():
    api = FakeAPI(
        [{"correlation-id": "a"}, {"correlation-id": "b"}],
        {"a": success_stream(), "b": []},
    )
    rows = updates.history(api, "dev1", limit=10)
    assert [r["correlation_id"] for r in rows] == ["a", "b"]
    assert all(r["device"] == "dev1" for r in rows)


def test_latest_attempt_at_returns_most_recent_match():
    # newest first; the target we want sits in the middle, with a later non-match on top
    api = FakeAPI(
        [
            {"correlation-id": "newer", "target": "raspberrypi4-64-lmp-130"},
            {"correlation-id": "match", "target": "raspberrypi4-64-lmp-124"},
            {"correlation-id": "older-match", "target": "raspberrypi4-64-lmp-124"},
        ],
        {"match": success_stream()},
    )
    s = updates.latest_attempt_at(api, "dev1", "lmp-124")
    assert s["correlation_id"] == "match"
    assert s["result"] == updates.RESULT_SUCCESS


def test_latest_attempt_at_returns_none_when_no_attempt():
    api = FakeAPI([{"correlation-id": "x", "target": "lmp-200"}], {})
    assert updates.latest_attempt_at(api, "dev1", "lmp-124") is None


def test_latest_attempt_at_matches_version_field():
    api = FakeAPI(
        [{"correlation-id": "v", "version": "124"}],
        {"v": success_stream()},
    )
    s = updates.latest_attempt_at(api, "dev1", "124")
    assert s is not None and s["correlation_id"] == "v"
