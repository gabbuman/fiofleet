import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from . import config
from .api import FoundriesAPI
from . import wireguard
from . import updates as updates_mod
from . import ssh as ssh_mod
from . import transport as transport_mod


def build_transport(user, identity_file, server, server_user, server_key,
                    device_password, direct):
    """Pick how device ssh is reached: directly, or hopped through the bastion.

    A bastion (the WireGuard server) is used when --server is given or a server
    is saved in config, unless --direct forces the local path. Flags override
    config; the device user is --user, else the saved device_user, else 'fio'.
    """
    cfg = None if direct else config.load_server()
    host = server or (cfg["host"] if cfg else None)
    device_user = user or (cfg["device_user"] if cfg else None) or "fio"

    if not host:
        return transport_mod.LocalTransport(user=device_user, identity_file=identity_file)

    bastion = transport_mod.Bastion(
        host=host,
        user=server_user or (cfg["user"] if cfg else None) or "root",
        port=(cfg["port"] if cfg else 22),
        key_filename=server_key or (cfg["key"] if cfg else None),
        password=(cfg["password"] if cfg else None),
    )
    return transport_mod.BastionTransport(
        bastion,
        device_user=device_user,
        device_password=device_password or (cfg["device_password"] if cfg else None),
        device_key=(cfg["device_key"] if cfg else None),
    )


def _bastion_opts(fn):
    """Shared --server/... flags for ssh and exec."""
    fn = click.option("--server", help="WireGuard-server bastion host to hop through "
                                       "(else uses saved config).")(fn)
    fn = click.option("--server-user", help="SSH user on the bastion.")(fn)
    fn = click.option("--server-key", help="SSH private key for the bastion.")(fn)
    fn = click.option("--device-password", help="Password for the device (sshpass on the bastion).")(fn)
    fn = click.option("--direct", is_flag=True, help="Skip the bastion; ssh straight from this host.")(fn)
    return fn


def _fanout(fn, items, parallel):
    """Run fn over items in parallel, returning results in input order."""
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(fn, it): i for i, it in enumerate(items)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
    return results


def get_api():
    token, factory, api_base = config.load()
    if not token or not factory:
        click.echo(
            "Missing config. Set FOUNDRIES_API_TOKEN and FOUNDRIES_FACTORY, "
            "or run: fiofleet config set",
            err=True,
        )
        sys.exit(1)
    return FoundriesAPI(token, factory, api_base=api_base)


def _fmt_tags(device):
    """The API returns `tag` as a string (e.g. "main"); tolerate a list too."""
    tag = device.get("tag") or device.get("tags") or []
    if isinstance(tag, str):
        return tag or "-"
    return ",".join(tag) or "-"


def resolve_targets(api, name, tag, group):
    """Resolve an explicit device name or a tag/group filter to device names."""
    if name:
        return [name]
    if tag or group:
        return [d["name"] for d in api.list_devices(tag=tag, group=group)]
    raise click.UsageError("Provide a device name, --tag, or --group.")


@click.group()
@click.version_option(package_name="fiofleet")
def cli():
    """fiofleet — bulk fleet operations for Foundries.io devices."""


# --- config ---

@cli.group(name="config")
def config_cmd():
    """Manage local config."""


@config_cmd.command("set")
@click.option("--token", prompt=True, hide_input=True)
@click.option("--factory", prompt=True)
@click.option("--api-base", default=config.DEFAULT_API_BASE, show_default=True)
def config_set(token, factory, api_base):
    """Store API token, factory, and API base locally."""
    path = config.save(token, factory, api_base)
    click.echo(f"Saved to {path}")


@config_cmd.command("set-server")
@click.option("--server", prompt="WireGuard server host", help="Bastion host (the VPN server).")
@click.option("--server-user", prompt="SSH user on the server", default="root")
@click.option("--server-key", default=None, help="SSH private key for the server.")
@click.option("--server-password", default=None, help="SSH password for the server (else key/agent).")
@click.option("--device-user", default="fio", show_default=True, help="SSH user on devices.")
@click.option("--device-password", default=None, help="Device password (sshpass), if not key-based.")
def config_set_server(server, server_user, server_key, server_password,
                      device_user, device_password):
    """Store the WireGuard-server bastion that ssh/exec hop through."""
    path = config.save_server(
        server=server, server_user=server_user, server_key=server_key,
        server_password=server_password, device_user=device_user,
        device_password=device_password,
    )
    click.echo(f"Saved server settings to {path}")


@config_cmd.command("show")
def config_show():
    token, factory, api_base = config.load()
    masked = (token[:6] + "..." + token[-4:]) if token else "(unset)"
    click.echo(f"factory:  {factory or '(unset)'}")
    click.echo(f"token:    {masked}")
    click.echo(f"api_base: {api_base}")
    srv = config.load_server()
    if srv:
        click.echo(f"server:   {srv['user']}@{srv['host']}:{srv['port']}"
                   f"  (device user: {srv['device_user']})")
    else:
        click.echo("server:   (unset — ssh/exec run directly from this host)")


# --- factories ---

@cli.command("factories")
def factories():
    """List factories your token can access."""
    api = get_api()
    for f in api.list_factories():
        click.echo(f.get("name", f))


# --- devices ---

@cli.group()
def devices():
    """Query devices."""


@devices.command("list")
@click.option("--tag", help="Filter by tag.")
@click.option("--group", help="Filter by group.")
@click.option("--online-only", is_flag=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def devices_list(tag, group, online_only, as_json):
    api = get_api()
    devs = api.list_devices(tag=tag, group=group)
    if online_only:
        devs = [d for d in devs if api.is_online(d)]
    if as_json:
        click.echo(json.dumps(devs, indent=2))
        return
    if not devs:
        click.echo("(no devices)")
        return
    for d in devs:
        online = "online" if api.is_online(d) else "offline"
        click.echo(f"{d['name']}\t{online}\t{_fmt_tags(d)}\t{d.get('last-seen', '')}")


@devices.command("show")
@click.argument("name")
def devices_show(name):
    api = get_api()
    click.echo(json.dumps(api.get_device(name), indent=2))


# --- wireguard ---

@cli.group()
def wg():
    """Wireguard enable/disable/status."""


def _target_opts(fn):
    fn = click.argument("name", required=False)(fn)
    fn = click.option("--tag")(fn)
    fn = click.option("--group")(fn)
    return fn


@wg.command("enable")
@_target_opts
@click.option("--wait/--no-wait", default=True, help="Wait until applied.")
@click.option("--timeout", default=900, type=int, help="Wait timeout in seconds.")
@click.option("--parallel", default=10, type=int)
@click.option("--via-fioctl", is_flag=True, help="Use fioctl instead of the config API.")
@click.option("--fioctl", "fioctl_bin", default="fioctl", help="Path to fioctl binary.")
def wg_enable(name, tag, group, wait, timeout, parallel, via_fioctl, fioctl_bin):
    """Enable wireguard on one device or many (by --tag/--group)."""
    api = get_api()
    targets = resolve_targets(api, name, tag, group)
    click.echo(f"Enabling wireguard on {len(targets)} device(s)...")
    _run_wg_op(api, targets, "enable", wait, timeout, parallel, via_fioctl, fioctl_bin)


@wg.command("disable")
@_target_opts
@click.option("--parallel", default=10, type=int)
@click.option("--via-fioctl", is_flag=True, help="Use fioctl instead of the config API.")
@click.option("--fioctl", "fioctl_bin", default="fioctl")
def wg_disable(name, tag, group, parallel, via_fioctl, fioctl_bin):
    """Disable wireguard on one device or many."""
    api = get_api()
    targets = resolve_targets(api, name, tag, group)
    click.echo(f"Disabling wireguard on {len(targets)} device(s)...")
    _run_wg_op(api, targets, "disable", False, 0, parallel, via_fioctl, fioctl_bin)


@wg.command("status")
@_target_opts
def wg_status(name, tag, group):
    """Show wireguard status for a device, or a whole tag/group."""
    api = get_api()
    targets = resolve_targets(api, name, tag, group)
    for device in targets:
        enabled, msg = api.wireguard_status(device)
        state = "ENABLED" if enabled else "NOT APPLIED"
        click.echo(f"{device}\t{state}\t{msg}")


def _run_wg_op(api, targets, op, wait, timeout, parallel, via_fioctl, fioctl_bin):
    def worker(device):
        try:
            if op == "enable":
                if via_fioctl:
                    wireguard.fioctl_enable(device, fioctl=fioctl_bin)
                else:
                    wireguard.enable(api, device)
                if wait:
                    ok, msg = wireguard.wait_applied(api, device, timeout=timeout)
                    return device, ("OK" if ok else "TIMEOUT"), msg
                return device, "SENT", "enable queued"
            else:
                if via_fioctl:
                    wireguard.fioctl_disable(device, fioctl=fioctl_bin)
                else:
                    wireguard.disable(api, device)
                return device, "OK", "disable queued"
        except Exception as e:  # noqa: BLE001 — report per-device, keep going
            return device, "ERROR", str(e)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for fut in as_completed([pool.submit(worker, t) for t in targets]):
            device, status, msg = fut.result()
            click.echo(f"{device}\t{status}\t{msg}")


# --- ota update report ---

@cli.group()
def ota():
    """OTA update reporting — what each device's last update did, and where it failed."""


_RESULT_ORDER = ["FAILED", "IN_PROGRESS", "UNKNOWN", "SUCCESS"]


def _truncate(s, width):
    s = " ".join((s or "").split())  # collapse newlines/whitespace for one-line display
    return s if len(s) <= width else s[: max(0, width - 3)] + "..."


@ota.command("report")
@_target_opts
@click.option("--failed-only", is_flag=True, help="Only print devices whose last update failed.")
@click.option("--json", "as_json", is_flag=True, help="Output full structured results as JSON.")
@click.option("--parallel", default=10, type=int)
def ota_report(name, tag, group, failed_only, as_json, parallel):
    """Per-device report of the most recent OTA update, with the failed stage and error."""
    api = get_api()
    targets = resolve_targets(api, name, tag, group)

    def worker(device):
        try:
            return updates_mod.latest(api, device)
        except Exception as e:  # noqa: BLE001 — one bad device shouldn't sink the report
            s = updates_mod.summarize({}, [])
            s.update({"device": device, "result": "UNKNOWN", "error": f"lookup failed: {e}"})
            return s

    summaries = _fanout(worker, targets, parallel)
    summary = updates_mod.fleet_summary(summaries)

    if as_json:
        click.echo(json.dumps({"devices": summaries, "summary": summary}, indent=2))
        return

    shown = [s for s in summaries if s["result"] == "FAILED"] if failed_only else summaries
    shown = sorted(shown, key=lambda s: (_RESULT_ORDER.index(s["result"])
                                         if s["result"] in _RESULT_ORDER else 99, s["device"]))

    click.echo(f"{'DEVICE':<22}{'RESULT':<13}{'FAILED@':<11}{'TARGET':<30}WHEN")
    for s in shown:
        click.echo(
            f"{_truncate(s['device'], 21):<22}"
            f"{s['result']:<13}"
            f"{(s['failed_stage'] or '-'):<11}"
            f"{_truncate(s['target'] or '-', 29):<30}"
            f"{s['time'] or '-'}"
        )
        if s["result"] == "FAILED" and s["error"]:
            click.echo(f"    -> {s['failed_stage']}: {_truncate(s['error'], 100)}")

    click.echo(f"\nFleet summary ({summary['total']} device(s)):")
    for result in _RESULT_ORDER:
        n = summary["by_result"].get(result)
        if not n:
            continue
        extra = ""
        if result == "FAILED" and summary["failed_by_stage"]:
            extra = "   (" + ", ".join(f"{k}: {v}" for k, v in summary["failed_by_stage"].items()) + ")"
        click.echo(f"  {result:<13}{n}{extra}")


@ota.command("stages")
@click.argument("device")
@click.option("--limit", default=5, type=int, help="How many recent updates to show.")
@click.option("--json", "as_json", is_flag=True)
def ota_stages(device, limit, as_json):
    """Stage-by-stage timeline of a single device's recent updates."""
    api = get_api()
    summaries = updates_mod.history(api, device, limit=limit)
    if as_json:
        click.echo(json.dumps(summaries, indent=2))
        return
    if not summaries:
        click.echo(f"{device}: no update history")
        return
    click.echo(f"{device} — last {len(summaries)} update(s), newest first\n")
    for s in summaries:
        head = f"[{s['time'] or '?'}] {s['target'] or s['version'] or '?'}  {s['result']}"
        if s["failed_stage"]:
            head += f" at {s['failed_stage']}"
        click.echo(head)
        for st in s["stages"]:
            line = f"    {st['stage']:<10}{st['status']:<9}{st['time'] or ''}"
            if st["error"]:
                line += f"   {_truncate(st['error'], 90)}"
            click.echo(line)
        click.echo("")


# --- ssh / exec ---

@cli.command("ssh")
@click.argument("device")
@click.option("--user", default=None, help="Device SSH user (default: fio / saved device_user).")
@click.option("-i", "--identity", "identity_file", help="SSH private key file (direct mode).")
@_bastion_opts
def ssh_cmd(device, user, identity_file, server, server_user, server_key, device_password, direct):
    """Open an interactive SSH session to a device.

    By default this hops through the saved WireGuard-server bastion, so it works
    from any machine; pass --direct to ssh straight from this host instead.
    """
    transport = build_transport(user, identity_file, server, server_user,
                                server_key, device_password, direct)
    try:
        sys.exit(transport.interactive(device))
    finally:
        transport.close()


@cli.command("exec")
@click.argument("command")
@click.option("--name", help="Single device.")
@click.option("--tag")
@click.option("--group")
@click.option("--user", default=None, help="Device SSH user (default: fio / saved device_user).")
@click.option("-i", "--identity", "identity_file", help="SSH private key file (direct mode).")
@click.option("--parallel", default=10, type=int)
@click.option("--timeout", default=60, type=int)
@click.option("--json", "as_json", is_flag=True, help="Emit one JSON array of per-device results.")
@click.option("--strict", is_flag=True, help="Exit non-zero if any device returns non-zero.")
@_bastion_opts
def exec_cmd(command, name, tag, group, user, identity_file, parallel, timeout,
             as_json, strict, server, server_user, server_key, device_password, direct):
    """Run a shell command on one or more devices.

    By default this hops through the saved WireGuard-server bastion (so it runs
    from any machine); pass --direct to ssh straight from this host. Results are
    collected, not just streamed: `--json` emits a machine-readable array
    (device, exit_code, ok, stdout, stderr) so you can drive scripts off the
    outcome, and `--strict` makes the whole run exit non-zero if any device
    failed.
    """
    api = get_api()
    targets = resolve_targets(api, name, tag, group)
    transport = build_transport(user, identity_file, server, server_user,
                                server_key, device_password, direct)
    if not as_json:
        click.echo(f"Running on {len(targets)} device(s): {command}")

    def worker(device):
        try:
            rc, out, err = transport.run(device, command, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — report per-device, keep going
            rc, out, err = -1, "", str(e)
        return {"device": device, "exit_code": rc, "ok": rc == 0, "stdout": out, "stderr": err}

    try:
        results = _fanout(worker, targets, parallel)
    finally:
        transport.close()
    failures = [r for r in results if not r["ok"]]

    if as_json:
        click.echo(json.dumps(results, indent=2))
    else:
        for r in results:
            click.echo(f"\n--- {r['device']} (exit {r['exit_code']}) ---")
            if r["stdout"]:
                click.echo(r["stdout"].rstrip())
            if r["stderr"]:
                click.echo(r["stderr"].rstrip(), err=True)
        click.echo(
            f"\n{len(results) - len(failures)}/{len(results)} ok"
            + (f", {len(failures)} failed: {', '.join(r['device'] for r in failures)}" if failures else ""),
            err=True,
        )

    if strict and failures:
        sys.exit(1)


if __name__ == "__main__":
    cli()
