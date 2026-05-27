"""How a device SSH command actually gets there.

Two transports behind one interface (``run`` / ``interactive`` / ``close``):

* ``LocalTransport`` — run ``ssh`` straight from this host. Only works if this
  host is already on (or peered into) the device VPN, e.g. you're sitting on
  the Factory WireGuard server. This is the original behaviour.

* ``BastionTransport`` — the jump-host model. The operator's machine does not
  need to be on the VPN at all: fiofleet opens a paramiko SSH connection to the
  WireGuard server (the bastion) and runs the device ``ssh`` *there*, where the
  10.42.42.x route and the device host keys live. Device authentication
  therefore happens on the server (its key, or a password via ``sshpass``),
  exactly as an admin SSH-ing into the box by hand would do — so the tool runs
  from anywhere with reach to the server.

The device command is quoted once (``shlex.quote``) so it survives the
bastion shell intact and is parsed only by the device's shell.
"""

import shlex
import sys
import threading

from . import ssh as _local

# Applied to the device-side ssh (run on the bastion). accept-new trusts a
# device host key the first time (the server is the trust anchor here).
_DEVICE_SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]


class LocalTransport:
    """Run ssh directly from this host (must reach the device VPN itself)."""

    def __init__(self, user="fio", identity_file=None):
        self.user = user
        self.identity_file = identity_file

    def run(self, device, command, timeout=60):
        return _local.run_command(
            device, command, user=self.user, timeout=timeout,
            identity_file=self.identity_file,
        )

    def interactive(self, device):
        return _local.interactive_shell(
            device, user=self.user, identity_file=self.identity_file,
        )

    def close(self):
        pass


class Bastion:
    """Connection details for the WireGuard server we hop through."""

    def __init__(self, host, user, port=22, key_filename=None, password=None):
        self.host = host
        self.user = user
        self.port = int(port or 22)
        self.key_filename = key_filename
        self.password = password


class BastionTransport:
    """Run device ssh from the WireGuard server, reached over paramiko."""

    def __init__(self, bastion, device_user="fio", device_password=None,
                 device_key=None, connect_timeout=15):
        self.bastion = bastion
        self.device_user = device_user
        self.device_password = device_password
        self.device_key = device_key
        self.connect_timeout = connect_timeout
        self._client = None
        self._lock = threading.Lock()

    # --- bastion connection (shared across parallel device commands) ---

    def _connect(self):
        with self._lock:
            if self._client is None:
                import paramiko
                client = paramiko.SSHClient()
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    self.bastion.host,
                    port=self.bastion.port,
                    username=self.bastion.user,
                    password=self.bastion.password,
                    key_filename=self.bastion.key_filename,
                    timeout=self.connect_timeout,
                    allow_agent=True,
                    look_for_keys=True,
                )
                self._client = client
        return self._client

    # --- build the command that runs *on the bastion* ---

    def _device_cmd(self, device, command=None, tty=False):
        opts = (["-tt"] if tty else []) + list(_DEVICE_SSH_OPTS)
        if self.device_key:
            opts += ["-i", self.device_key]
        parts = ["ssh", *opts, f"{self.device_user}@{device}"]
        if command is not None:
            parts.append(shlex.quote(command))  # parsed only by the device shell
        remote = " ".join(parts)
        if self.device_password:
            # Pass the password via env (sshpass -e), not -p, so it doesn't show
            # in the bastion's process list.
            remote = f"SSHPASS={shlex.quote(self.device_password)} sshpass -e " + remote
        return remote

    # --- interface ---

    def run(self, device, command, timeout=60):
        client = self._connect()
        _stdin, stdout, stderr = client.exec_command(self._device_cmd(device, command),
                                                      timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

    def interactive(self, device):
        """Best-effort interactive session to a device, hopped through the bastion.

        Line-buffered and portable (no termios), so it works from Windows too;
        the bulk `exec` path is the primary fan-out interface.
        """
        client = self._connect()
        chan = client.get_transport().open_session()
        chan.get_pty()
        chan.exec_command(self._device_cmd(device, tty=True))

        def pump_out():
            while True:
                data = chan.recv(4096)
                if not data:
                    break
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()

        reader = threading.Thread(target=pump_out, daemon=True)
        reader.start()
        try:
            while not chan.exit_status_ready():
                line = sys.stdin.readline()
                if not line:
                    chan.shutdown_write()
                    break
                chan.send(line)
        except (EOFError, KeyboardInterrupt):
            pass
        reader.join(timeout=1)
        return chan.recv_exit_status()

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
