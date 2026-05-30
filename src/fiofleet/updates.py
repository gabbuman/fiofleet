"""OTA update reporting.

Turns a device's raw aktualizr event stream into a *staged* pass/fail report:
which step of the update each device reached, whether it failed, and the error
the device reported when it did.

A Foundries.io OTA update moves through a fixed sequence of libaktualizr events
(posted by the device to the device-gateway and surfaced by the OTA API):

    EcuDownloadStarted   -> EcuDownloadCompleted(success)        # download stage
    EcuInstallationStarted -> EcuInstallationApplied             # install stage,
                           -> EcuInstallationCompleted(success)  #   reboot-then-confirm

We collapse those into two stages the operator actually cares about —
**download** and **install** — and report the first one that failed plus the
`details` string the device attached to the failing event.

Event field names are read defensively (camelCase `eventType`/`deviceTime`,
kebab `event-type`, nested `event`/`detail` bodies) so the same code works
across aktualizr-lite / fioup reporters without pinning one wire format. The
live `harness/` is what validates the exact shape against a real factory.
"""

from datetime import datetime, timezone

# Operator-facing stages, in order.
STAGES = ("download", "install")

# aktualizr event id -> (stage, phase). Unlisted events (e.g. progress reports)
# are ignored.
_EVENT_MAP = {
    "EcuDownloadStarted":       ("download", "started"),
    "EcuDownloadCompleted":     ("download", "completed"),
    "EcuInstallationStarted":   ("install", "started"),
    "EcuInstallationApplied":   ("install", "applied"),
    "EcuInstallationCompleted": ("install", "completed"),
}

RESULT_SUCCESS = "SUCCESS"
RESULT_FAILED = "FAILED"
RESULT_IN_PROGRESS = "IN_PROGRESS"
RESULT_UNKNOWN = "UNKNOWN"


# --- defensive event accessors ---

def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_id(ev):
    et = ev.get("eventType") or ev.get("event-type") or ev.get("type") or ev.get("id")
    if isinstance(et, dict):
        return et.get("id") or et.get("Id") or ""
    return et or ""


def _event_body(ev):
    body = ev.get("event") or ev.get("detail") or ev.get("Detail")
    return body if isinstance(body, dict) else {}


def _event_time(ev):
    return (
        ev.get("deviceTime") or ev.get("device-time")
        or ev.get("time") or ev.get("Time") or ""
    )


def _event_success(ev):
    """Tri-state: True/False for *Completed events, None for started/applied."""
    body = _event_body(ev)
    if "success" in body:
        return body["success"]
    if "success" in ev:
        return ev["success"]
    return None


def _event_details(ev):
    body = _event_body(ev)
    for k in ("details", "Details", "detail", "message", "reason"):
        if body.get(k):
            return str(body[k]).strip()
    for k in ("details", "message"):
        if ev.get(k):
            return str(ev[k]).strip()
    return ""


def _event_target(ev):
    body = _event_body(ev)
    return body.get("targetName") or body.get("target") or body.get("version") or ""


def _ordered(events):
    """Events in chronological order; stable for ties / missing timestamps."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(list(events or []), key=lambda ev: _parse_ts(_event_time(ev)) or floor)


# --- summarization ---

def _empty_stages():
    return {s: {"stage": s, "status": "-", "time": "", "error": ""} for s in STAGES}


def summarize(update, events):
    """Collapse one update's events into a staged result dict.

    Returns: correlation_id, target, version, time, result (SUCCESS / FAILED /
    IN_PROGRESS / UNKNOWN), failed_stage, error, and a per-stage breakdown.
    """
    update = update or {}
    cid = update.get("correlation-id") or update.get("correlationId") or ""
    target = update.get("target") or update.get("targetName") or ""
    version = str(update.get("version") or "")
    when = update.get("time") or update.get("deviceTime") or ""

    stages = _empty_stages()
    seen = False
    for ev in _ordered(events):
        mapped = _EVENT_MAP.get(_event_id(ev))
        if not mapped:
            continue
        seen = True
        stage, phase = mapped
        rec = stages[stage]
        rec["time"] = _event_time(ev) or rec["time"]
        target = target or _event_target(ev)
        if phase == "started":
            if rec["status"] == "-":
                rec["status"] = "started"
        elif phase == "applied":
            rec["status"] = "applied"  # written, awaiting reboot/confirmation
        elif phase == "completed":
            if _event_success(ev) is False:
                rec["status"] = "failed"
                rec["error"] = _event_details(ev) or "reported success=false"
            else:
                rec["status"] = "ok"

    failed_stage = next((s for s in STAGES if stages[s]["status"] == "failed"), None)
    if failed_stage:
        result, error = RESULT_FAILED, stages[failed_stage]["error"]
    elif stages["install"]["status"] == "ok":
        result, error = RESULT_SUCCESS, ""
    elif not seen:
        result, error = RESULT_UNKNOWN, ""
    else:
        result, error = RESULT_IN_PROGRESS, ""

    return {
        "correlation_id": cid,
        "target": target,
        "version": version,
        "time": when,
        "result": result,
        "failed_stage": failed_stage,
        "error": error,
        "stages": [stages[s] for s in STAGES],
    }


def _summarize_one(api, name, update):
    cid = update.get("correlation-id") or update.get("correlationId")
    events = api.update_events(name, cid) if cid else []
    summary = summarize(update, events)
    summary["device"] = name
    return summary


def _empty_summary(name, note):
    s = summarize({}, [])
    s["device"] = name
    s["error"] = note
    return s


def latest(api, name):
    """Summarize a device's most recent update (UNKNOWN if it has none)."""
    updates = api.list_updates(name, limit=1)
    if not updates:
        return _empty_summary(name, "no update history")
    return _summarize_one(api, name, updates[0])


def history(api, name, limit=10):
    """Summaries for a device's recent updates, newest first."""
    return [_summarize_one(api, name, u) for u in api.list_updates(name, limit=limit)]


def _matches_target(update, needle):
    """Case-insensitive substring match against target/version on an update row."""
    needle = (needle or "").lower()
    if not needle:
        return True
    for key in ("target", "targetName", "version"):
        v = update.get(key)
        if v and needle in str(v).lower():
            return True
    return False


def latest_attempt_at(api, name, target, search_limit=50):
    """Most recent update where the device attempted ``target`` (substring match).

    Returns None if the device never tried it within the searched history. Use
    this to answer "who tried target X, and how did it go?" — every match is a
    real attempt, so the result is SUCCESS / FAILED / IN_PROGRESS / UNKNOWN as
    usual, never the "no update history" sentinel.
    """
    for u in api.list_updates(name, limit=search_limit):
        if _matches_target(u, target):
            return _summarize_one(api, name, u)
    return None


def fleet_summary(summaries):
    """Aggregate per-device summaries into counts by result + failed-stage tally."""
    by_result = {}
    failed_by_stage = {}
    for s in summaries:
        by_result[s["result"]] = by_result.get(s["result"], 0) + 1
        if s["result"] == RESULT_FAILED and s.get("failed_stage"):
            failed_by_stage[s["failed_stage"]] = failed_by_stage.get(s["failed_stage"], 0) + 1
    return {
        "total": len(summaries),
        "by_result": by_result,
        "failed_by_stage": failed_by_stage,
    }
