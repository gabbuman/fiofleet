# fiofleet test harness

A real, local end-to-end environment for exercising `fiofleet` against an actual
Foundries.io factory — no production hardware required. It was used to validate
every `fiofleet` feature (device inventory, WireGuard fleet management, and
SSH/exec fan-out) end to end.

## Topology

```
 Control host (dev)            WireGuard server                Device containers
 ──────────────────           (e.g. Raspberry Pi)             (Docker)
 fiofleet (API ops) ──API──► api.foundries.io ◄── token
                              factory-wireguard.py            fioup register → factory
                              UDP :5555, 10.42.42.1     ◄──── wg + sshd (fio user)
                              fiofleet ssh/exec run HERE  wg   wg-agent.py brings up
                              /etc/hosts ← device IPs    tunnel  factory-vpn0 → server
```

The devices are **not** flashed LmP images — they're generic Linux containers
running the real `fioup` client (the supported path for a `fioup`/`arm64-linux`
factory). Because `fioup` is container-only and doesn't ship `fioconfig`,
[`wg-agent.py`](wg-agent.py) stands in for fioconfig's WireGuard handler:
it generates the device keypair, reports the pubkey over the device's mTLS
channel, and brings up the tunnel with plain `wg`/`ip`.

## 1. WireGuard server (once)

On a Linux host with a stable, reachable UDP endpoint (a Raspberry Pi works well):

```bash
sudo apt-get install -y wireguard wireguard-tools python3-requests git
git clone https://github.com/foundriesio/factory-wireguard-server.git
cd factory-wireguard-server
wg genkey | sudo tee server.key >/dev/null && sudo chmod 600 server.key
# --intf-name is required when the factory name is >12 chars.
sudo python3 factory-wireguard.py \
    -t "$FOUNDRIES_API_TOKEN" -f "$FOUNDRIES_FACTORY" -n fiochoc -k server.key \
    enable -e <SERVER_LAN_IP> -p 5555 --no-check-ip
```

This registers the server's endpoint + pubkey into the factory config and starts
a systemd unit that keeps peers in sync from the `wireguard-ips` view.

> Note: on a NAT-free LAN you don't need the script's `iptables` MASQUERADE
> PostUp rules (and recent versions ship them with an unfinished `-o TODO`
> egress interface). For SSH *from* the server *to* devices, plain point-to-point
> WireGuard is enough — drop those `PostUp`/`PostDown` iptables lines.

## 2. Devices (Docker)

From this directory, on a host with Docker:

```bash
export FOUNDRIES_FACTORY=<your-factory>
export PI_PUBKEY="$(ssh <vpn-server> cat ~/.ssh/id_ed25519.pub)"  # server's key, for ssh fio@device

./run-devices.sh build
./run-devices.sh start    dev-eu-01 dev-us-01
./run-devices.sh register dev-eu-01     # approve the code at app.foundries.io/activate/
./run-devices.sh register dev-us-01
```

## 3. Enable WireGuard + connect

```bash
# From the control host (talks to the API):
fiofleet wg enable --tag main           # allocates 10.42.42.x and writes device config

# Bring up the device tunnels (reports pubkey, waits for the address, connects):
./run-devices.sh connect dev-eu-01 dev-us-01
```

## 4. Drive the fleet (from the WireGuard server)

`ssh`/`exec` must run where the route to devices lives — the VPN server:

```bash
fiofleet devices list --tag main --online-only
fiofleet wg status --tag main
fiofleet exec "uname -srm; hostname" --tag main
fiofleet ssh dev-eu-01
```

## Teardown

```bash
./run-devices.sh reset                  # remove the device containers
# On the server: sudo systemctl disable --now factory-vpn-<factory>.service
```
