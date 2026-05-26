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
- **WireGuard fleet management** — enable/disable/status across many devices at
  once, and *wait* until the platform confirms each device is a live VPN peer.
  Works through the config API directly, so `fioctl` is **not required**.
- **Fan-out SSH/exec** — run a command (or open a shell) across a tag/group in
  parallel, over your Factory WireGuard tunnel.

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
```

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
