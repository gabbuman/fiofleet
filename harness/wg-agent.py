#!/usr/bin/env python3
"""Device-side WireGuard agent for fiofleet's test harness.

Stands in for fioconfig on a non-LmP (fioup) device. It:

  report : generate a WireGuard keypair (/var/sota/wg-priv) and report the public
           key to the device gateway over the device's mTLS channel — the same
           thing fioconfig does, so the platform will let fiofleet allocate a VPN
           address for it.
  up     : read the assigned address + the factory's wireguard-server config from
           the gateway and bring up the `factory-vpn0` tunnel with plain wg/ip
           (no NetworkManager).
  run    : report, wait until fiofleet has enabled WireGuard (an address appears),
           then up. Re-runnable.

Auth/material all comes from /var/sota (client.pem, pkey.pem, root.crt, sota.toml),
created by `fioup register`.
"""
import os
import subprocess
import sys
import time
import tomllib

import requests

SOTA = os.environ.get("SOTA_DIR", "/var/sota")
PRIV = os.path.join(SOTA, "wg-priv")
IFACE = os.environ.get("WG_IFACE", "factory-vpn0")


def gateway():
    with open(os.path.join(SOTA, "sota.toml"), "rb") as f:
        return tomllib.load(f)["tls"]["server"]


def mtls():
    return dict(
        cert=(os.path.join(SOTA, "client.pem"), os.path.join(SOTA, "pkey.pem")),
        verify=os.path.join(SOTA, "root.crt"),
    )


def parse_kv(value):
    out = {}
    for line in (value or "").splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def run(*args, **kw):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw)


def ensure_key():
    if not os.path.exists(PRIV):
        os.makedirs(SOTA, exist_ok=True)
        old = os.umask(0o077)
        try:
            key = run("wg", "genkey").stdout.strip()
            with open(PRIV, "w") as f:
                f.write(key + "\n")
        finally:
            os.umask(old)
    priv = open(PRIV).read().strip()
    pub = run("wg", "pubkey", input=priv + "\n").stdout.strip()
    return pub


def get_config():
    r = requests.get(f"{gateway()}/config", timeout=30, **mtls())
    r.raise_for_status()
    return r.json()


def client_cfg():
    return parse_kv(get_config().get("wireguard-client", {}).get("Value", ""))


def report(pub):
    """Merge our pubkey into wireguard-client (preserving any assigned address)."""
    cur = client_cfg()
    if cur.get("pubkey") == pub:
        print(f"pubkey already reported ({pub})")
        return
    cur["pubkey"] = pub
    value = "\n".join(f"{k}={v}" for k, v in cur.items())
    body = {
        "reason": "fiofleet harness: report wireguard pubkey",
        "files": [{"name": "wireguard-client", "unencrypted": True, "value": value}],
    }
    r = requests.patch(f"{gateway()}/config", json=body, timeout=30, **mtls())
    r.raise_for_status()
    print(f"reported pubkey {pub}")


def up():
    cfg = get_config()
    client = parse_kv(cfg.get("wireguard-client", {}).get("Value", ""))
    server = parse_kv(cfg.get("wireguard-server", {}).get("Value", ""))
    addr = client.get("address")
    if not addr:
        sys.exit("no VPN address assigned yet — run `fiofleet wg enable` first")
    if client.get("enabled", "1") == "0":
        sys.exit("wireguard is disabled for this device")
    for k in ("endpoint", "pubkey", "server_address"):
        if not server.get(k):
            sys.exit(f"wireguard-server config missing '{k}'")

    subprocess.run(["ip", "link", "del", IFACE], capture_output=True)
    run("ip", "link", "add", IFACE, "type", "wireguard")
    run("wg", "set", IFACE,
        "private-key", PRIV,
        "peer", server["pubkey"],
        "endpoint", server["endpoint"],
        "allowed-ips", "10.42.42.0/24",
        "persistent-keepalive", "25")
    run("ip", "address", "add", f"{addr}/24", "dev", IFACE)
    run("ip", "link", "set", IFACE, "up")
    print(f"{IFACE} up: {addr} -> server {server['endpoint']} ({server['server_address']})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    pub = ensure_key()
    if cmd == "report":
        report(pub)
    elif cmd == "up":
        up()
    elif cmd == "run":
        report(pub)
        print("waiting for fiofleet to enable wireguard (address assignment)...")
        for _ in range(60):
            if client_cfg().get("address"):
                break
            time.sleep(5)
        up()
    elif cmd == "pubkey":
        print(pub)
    else:
        sys.exit(f"usage: wg-agent.py [report|up|run|pubkey]; got {cmd!r}")


if __name__ == "__main__":
    main()
