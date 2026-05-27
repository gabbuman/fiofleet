from fiofleet import transport
from fiofleet.transport import Bastion, BastionTransport, LocalTransport


# --- fake paramiko surface ---

class _FakeStd:
    def __init__(self, data=b"", rc=0):
        self._data = data
        self.channel = self
        self._rc = rc

    def read(self):
        return self._data

    def recv_exit_status(self):
        return self._rc


class FakeClient:
    """Captures the command BastionTransport runs on the bastion."""

    def __init__(self, out=b"", err=b"", rc=0):
        self.commands = []
        self._out, self._err, self._rc = out, err, rc

    def exec_command(self, command, timeout=None):
        self.commands.append((command, timeout))
        return None, _FakeStd(self._out, self._rc), _FakeStd(self._err, self._rc)


def make_bt(**kw):
    bt = BastionTransport(Bastion("vpn.example", "ops"), **kw)
    return bt


# --- remote command construction ---

def test_device_cmd_key_auth_quotes_command():
    bt = make_bt(device_user="fio")
    cmd = bt._device_cmd("dev-eu-01", "systemctl is-active aktualizr-lite")
    assert cmd.startswith("ssh ")
    assert "fio@dev-eu-01" in cmd
    assert "StrictHostKeyChecking=accept-new" in cmd
    # the device command is quoted so the bastion shell passes it through intact
    assert "'systemctl is-active aktualizr-lite'" in cmd
    assert "sshpass" not in cmd  # no password -> key/agent auth


def test_device_cmd_password_uses_sshpass_env_not_args():
    bt = make_bt(device_user="fio", device_password="s3cret")
    cmd = bt._device_cmd("dev-eu-01", "uptime")
    # password travels via env for sshpass -e, never as a bare -p argument
    assert cmd.startswith("SSHPASS=")
    assert "sshpass -e ssh" in cmd
    assert "-p s3cret" not in cmd


def test_device_cmd_identity_and_tty():
    bt = make_bt(device_user="fio", device_key="/keys/dev")
    cmd = bt._device_cmd("d1", "ls", tty=True)
    assert "-tt" in cmd
    assert "-i /keys/dev" in cmd


# --- run() parses paramiko streams ---

def test_run_returns_rc_out_err():
    bt = make_bt()
    bt._client = FakeClient(out=b"hello\n", err=b"", rc=0)
    rc, out, err = bt.run("d1", "echo hello")
    assert (rc, out, err) == (0, "hello\n", "")
    # it ran our composed device command on the bastion
    assert bt._client.commands[0][0].startswith("ssh ")


def test_run_propagates_nonzero_and_stderr():
    bt = make_bt()
    bt._client = FakeClient(out=b"", err=b"boom\n", rc=3)
    rc, out, err = bt.run("d1", "false")
    assert rc == 3 and err == "boom\n"


# --- transport selection in the CLI builder ---

def test_build_transport_local_when_no_server(monkeypatch):
    from fiofleet import cli
    monkeypatch.setattr(cli.config, "load_server", lambda: None)
    t = cli.build_transport("fio", None, None, None, None, None, direct=False)
    assert isinstance(t, LocalTransport)
    assert t.user == "fio"


def test_build_transport_bastion_from_config(monkeypatch):
    from fiofleet import cli
    monkeypatch.setattr(cli.config, "load_server", lambda: {
        "host": "pi.local", "user": "user", "port": 22, "key": None,
        "password": "pw", "device_user": "fio", "device_password": None,
        "device_key": None,
    })
    t = cli.build_transport(None, None, None, None, None, None, direct=False)
    assert isinstance(t, BastionTransport)
    assert t.bastion.host == "pi.local"
    assert t.device_user == "fio"


def test_build_transport_direct_flag_forces_local(monkeypatch):
    from fiofleet import cli
    # even with a server saved, --direct must bypass it
    monkeypatch.setattr(cli.config, "load_server",
                        lambda: {"host": "pi.local", "user": "u", "port": 22,
                                 "key": None, "password": None, "device_user": "fio",
                                 "device_password": None, "device_key": None})
    t = cli.build_transport("fio", None, None, None, None, None, direct=True)
    assert isinstance(t, LocalTransport)


def test_build_transport_flag_overrides_config_server(monkeypatch):
    from fiofleet import cli
    monkeypatch.setattr(cli.config, "load_server", lambda: None)
    t = cli.build_transport("admin", None, "other.host", "ops", "/k", None, direct=False)
    assert isinstance(t, BastionTransport)
    assert t.bastion.host == "other.host"
    assert t.bastion.user == "ops"
    assert t.device_user == "admin"
