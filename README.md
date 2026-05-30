# fiofleet

Bulk fleet operations for [Foundries.io](https://foundries.io) devices.

`fioctl` is great for single-device work. **fiofleet** is designed for when you have a large fleet of
devices, and want to enable/disable wireguard vpn and run ssh commands remotely en masse.
It's a thin, scriptable layer over the Foundries OTA API (and, optionally, `fioctl`).

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
  parallel, and collect the results as JSON (`--json`) so you can drive scripts
  off them. Runs from *any* machine: fiofleet hops through your Factory
  WireGuard server (a bastion) and SSHes to the devices from there, so the
  operator doesn't need to be on the VPN.

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

To use `ssh`/`exec` from a machine that isn't on the device VPN, point fiofleet
at your Factory WireGuard server (the bastion it hops through):

```
fiofleet config set-server --server vpn.example.com --server-user ops
# add --device-password ... if your devices use password (sshpass) auth
```

**Connecting to the server with an OpenSSH key** (e.g. an Azure VM running the
Factory WireGuard server — the same `.pem`/OpenSSH key you'd use for `ssh -i`):

```
fiofleet config set-server \
  --server my-fio-vpn.eastus.cloudapp.azure.com \
  --server-user azureuser \
  --server-key ~/.ssh/azure-fio-vpn.pem \
  --device-user fio
# device auth happens on the server: add --device-password ... if devices need
# sshpass, otherwise omit (the server's own key reaches the devices).
```

`--server-key` is passed through to paramiko — anything `ssh -i` would accept
works (`.pem`, `~/.ssh/id_ed25519`, …). Omit it to fall back to your SSH agent
/ default keys, then password.

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
fiofleet ota report --tag prod-eu --target lmp-124  # every device that attempted target lmp-124
fiofleet ota report --tag prod-eu --target lmp-124 --failed-only   # …and which of them failed
fiofleet ota stages my-device-01                    # full stage timeline for one device

# WireGuard
fiofleet wg enable my-device-01
fiofleet wg enable --tag prod-eu --parallel 20      # enable + wait until applied
fiofleet wg status --tag prod-eu
fiofleet wg disable --tag prod-eu
fiofleet wg enable my-device-01 --via-fioctl        # delegate to fioctl instead

# SSH / exec (hops through the configured WireGuard-server bastion by default)
fiofleet ssh my-device-01
fiofleet exec "uptime" --tag prod-eu
fiofleet exec "systemctl is-active aktualizr-lite" --tag prod-eu
fiofleet exec "fiotest" --tag prod-eu --json        # collect results as JSON
fiofleet exec "reboot" --tag prod-eu --strict       # non-zero exit if any device fails
fiofleet exec "uptime" --tag prod-eu --server vpn.example.com   # ad-hoc bastion
fiofleet exec "uptime" --name dev-01 --direct       # already on the VPN; skip the hop
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

Pass `--target X` to scope the report to one rollout: each device's update
history is searched (newest first) for an update whose target/version contains
`X`, and only devices that *actually attempted* it appear in the output —
their most recent attempt, with the same SUCCESS/FAILED/IN_PROGRESS verdict
and failing-stage detail.

## How WireGuard works here

Enabling WireGuard on a device writes a `wireguard-client` config entry (the same
one `fioctl devices config wireguard enable` writes). The platform assigns the
device a `10.42.42.x` address; the device applies the change on its next check-in.

`fiofleet wg status` / `--wait` poll the Foundries
[`wireguard-ips`](https://docs.foundries.io/latest/reference-manual/remote-access/wireguard.html)
view — the same one the [Factory WireGuard server](https://github.com/foundriesio/factory-wireguard-server)
reads to learn its peers — so "applied" means the platform actually considers the
device a live VPN peer, not just that a config was queued.

## How ssh/exec reach a device (the jump-host model)

A route to a device only exists on the Factory WireGuard server — it's peered
into the VPN and keeps `/etc/hosts` in sync with device VPN IPs. Rather than
require you to be on that box, fiofleet treats it as a **bastion**: it opens an
SSH connection to the server (via [paramiko](https://www.paramiko.org/)) and
runs the device `ssh` *there*. So:

```
your laptop ──SSH──► WireGuard server ──SSH──► device (10.42.42.x)
 (anywhere)          (on the VPN)              (fio@…)
```

Device authentication therefore happens on the server — using the server's key,
or a password via `sshpass` (`--device-password`) — exactly as an admin SSHing
into the box by hand would. Configure the bastion once with
`fiofleet config set-server` (or pass `--server` ad hoc); pass `--direct` to
skip it when you're already on the VPN. fiofleet runs `ssh`; it doesn't manage
the tunnel itself.

## Development

```
pip install -e ".[dev]"
pytest
```

A local end-to-end harness (real Pi WireGuard server + containerised devices) lives
in [`harness/`](harness/README.md).

## License

Apache 2.0
