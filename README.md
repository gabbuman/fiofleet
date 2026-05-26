# fiofleet

Bulk fleet operations for [Foundries.io](https://foundries.io) devices.

`fioctl` is great for single-device work. **fiofleet** is for when you have fifty
devices tagged `prod-eu`, you need to enable WireGuard on all of them, wait until
each one's config is actually applied, then run a diagnostic command across the
whole set. It's a thin, scriptable layer over the Foundries OTA API (and,
optionally, `fioctl`).

## Features

- **Device inventory** — list/show devices, filter by tag or group, with
  online/offline detection.
- **OTA update reports** — for a tag/group, show each device's last update
  broken down by stage (download → install), exactly *which stage failed* and
  the error the device reported, plus a fleet-level pass/fail summary. Drill
  into a single device's full update timeline with `ota stages`.
- **WireGuard fleet management** — enable/disable/status across many devices at
  once, and *wait* until the platform confirms each device is a live VPN peer.
  Works through the config API directly, so `fioctl` is **not required**.
- **Fan-out SSH/exec** — run a command (or open a shell) across a tag/group in
  parallel over your Factory WireGuard tunnel, and collect the results as JSON
  (`--json`) so you can drive scripts off them.

## Install

```
pip install fiofleet
```

Requires Python 3.9+. `fioctl` is optional — only needed if you pass
`--via-fioctl` to the WireGuard commands.

## Setup

```
fiofleet config set
# prompts for API token, factory name (and optionally an API base URL)
```

Or via env vars (these override the saved config):

```
export FOUNDRIES_API_TOKEN=...
export FOUNDRIES_FACTORY=my-factory
```

Get your API token at https://app.foundries.io/settings/tokens/.

## Commands

```
# Factories your token can see
fiofleet factories

# Devices
fiofleet devices list
fiofleet devices list --tag prod-eu --online-only
fiofleet devices show my-device-01

# OTA update reports
fiofleet ota report --tag prod-eu                   # last update per device + fleet summary
fiofleet ota report --tag prod-eu --failed-only     # just the devices that failed
fiofleet ota report --tag prod-eu --json            # structured, for dashboards/CI
fiofleet ota stages my-device-01                    # full stage timeline for one device

# WireGuard
fiofleet wg enable my-device-01
fiofleet wg enable --tag prod-eu --parallel 20      # enable + wait until applied
fiofleet wg status --tag prod-eu
fiofleet wg disable --tag prod-eu
fiofleet wg enable my-device-01 --via-fioctl        # delegate to fioctl instead

# SSH / exec (run from the Factory WireGuard server, where device names resolve)
fiofleet ssh my-device-01
fiofleet exec "uptime" --tag prod-eu
fiofleet exec "systemctl is-active aktualizr-lite" --tag prod-eu
fiofleet exec "fiotest" --tag prod-eu --json        # collect results as JSON
fiofleet exec "reboot" --tag prod-eu --strict       # non-zero exit if any device fails
```

A typical `ota report` looks like:

```
DEVICE                RESULT       FAILED@    TARGET                        WHEN
dev-us-01             FAILED       install    raspberrypi4-64-lmp-124       2026-05-21T08:14:02Z
    -> install: Installation failed: ostree pull error: Server returned HTTP 500
dev-eu-02             IN_PROGRESS  -          raspberrypi4-64-lmp-124       2026-05-21T08:13:55Z
dev-eu-01             SUCCESS      -          raspberrypi4-64-lmp-124       2026-05-20T22:01:10Z

Fleet summary (3 device(s)):
  FAILED       1   (install: 1)
  IN_PROGRESS  1
  SUCCESS      1
```

## How OTA reporting works

Each device posts a stream of [libaktualizr](https://docs.foundries.io/latest/reference-manual/ota/ota.html)
report events to the device-gateway as it updates
(`EcuDownloadStarted`/`Completed`, `EcuInstallationStarted`/`Applied`/`Completed`).
fiofleet reads that stream from the OTA API's per-device `updates` view — the
same history `fioctl` shows — and collapses it into two operator-facing stages,
**download** and **install**. A stage that reports `success=false` marks the
update `FAILED` at that stage and surfaces the `details` the device attached;
an update that reached `EcuInstallationApplied` but not `…Completed` is
`IN_PROGRESS` (applied, awaiting the post-reboot confirmation). No agent on the
device is required — it's all read from the API.

## How WireGuard works here

Enabling WireGuard on a device writes a `wireguard-client` config entry (the same
one `fioctl devices config wireguard enable` writes). The platform assigns the
device a `10.42.42.x` address; the device applies the change on its next check-in.

`fiofleet wg status` / `--wait` poll the Foundries
[`wireguard-ips`](https://docs.foundries.io/latest/reference-manual/remote-access/wireguard.html)
view — the same one the [Factory WireGuard server](https://github.com/foundriesio/factory-wireguard-server)
reads to learn its peers — so "applied" means the platform actually considers the
device a live VPN peer, not just that a config was queued.

For SSH to reach a device you need a route to it. That route lives on the Factory
WireGuard server (it keeps `/etc/hosts` in sync with device VPN IPs), so run
`fiofleet ssh`/`exec` from there — or from a host peered into the same VPN.
fiofleet runs `ssh`; it doesn't manage the tunnel itself.

## Development

```
pip install -e ".[dev]"
pytest
```

A local end-to-end harness (real Pi WireGuard server + containerised devices) lives
in [`harness/`](harness/README.md).

## License

Apache 2.0
