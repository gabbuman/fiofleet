import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from . import config
from .api import FoundriesAPI
from . import wireguard
from . import ssh as ssh_mod


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


@config_cmd.command("show")
def config_show():
    token, factory, api_base = config.load()
    masked = (token[:6] + "..." + token[-4:]) if token else "(unset)"
    click.echo(f"factory:  {factory or '(unset)'}")
    click.echo(f"token:    {masked}")
    click.echo(f"api_base: {api_base}")


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


# --- ssh / exec ---

@cli.command("ssh")
@click.argument("device")
@click.option("--user", default="fio")
@click.option("-i", "--identity", "identity_file", help="SSH private key file.")
def ssh_cmd(device, user, identity_file):
    """Open an interactive SSH session to a device (requires wg tunnel up)."""
    sys.exit(ssh_mod.interactive_shell(device, user=user, identity_file=identity_file))


@cli.command("exec")
@click.argument("command")
@click.option("--name", help="Single device.")
@click.option("--tag")
@click.option("--group")
@click.option("--user", default="fio")
@click.option("-i", "--identity", "identity_file", help="SSH private key file.")
@click.option("--parallel", default=10, type=int)
@click.option("--timeout", default=60, type=int)
def exec_cmd(command, name, tag, group, user, identity_file, parallel, timeout):
    """Run a shell command on one or more devices (requires wg tunnel up)."""
    api = get_api()
    targets = resolve_targets(api, name, tag, group)
    click.echo(f"Running on {len(targets)} device(s): {command}")

    def worker(device):
        try:
            rc, out, err = ssh_mod.run_command(
                device, command, user=user, timeout=timeout, identity_file=identity_file
            )
            return device, rc, out, err
        except Exception as e:  # noqa: BLE001
            return device, -1, "", str(e)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for fut in as_completed([pool.submit(worker, t) for t in targets]):
            device, rc, out, err = fut.result()
            click.echo(f"\n--- {device} (exit {rc}) ---")
            if out:
                click.echo(out.rstrip())
            if err:
                click.echo(err.rstrip(), err=True)


if __name__ == "__main__":
    cli()
