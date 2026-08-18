# Oculizer Raspberry Pi service pack

This directory contains only the Oculizer service installation and lifecycle files. QLC+, its workspace, and its service are owned by a separate repository.

Run preflight and installation from the repository root:

```bash
chmod +x raspi_service_pack/install.sh
./raspi_service_pack/install.sh --check
sudo ./raspi_service_pack/install.sh
```

Git preserves the executable bit. The `chmod` command is harmless and also supports copies made through an archive or filesystem that discarded Unix modes.

## Installation options

The installer accepts deployment options both for the first installation and
when reconfiguring an existing service:

| Option | Accepted value | Default |
|---|---|---|
| `--audio-input SELECTOR` | An Oculizer device selector such as `default` | `default` |
| `--dynamic-control NAME` | `off` or a profile declared in `config/oculizer.json` | `normal` |
| `--service-user USER` | Existing Linux account used to run Oculizer | invoking sudo user, or `pi` |
| `--no-web` | Do not start the embedded Web child | Web enabled |
| `--web-bind ADDRESS` | Embedded Web listen address | `0.0.0.0` |
| `--web-port PORT` | Embedded Web TCP port | `8080` |
| `--check` | Validate the host without changing it | disabled |
| `--non-interactive` | Explicit automation marker; installation is already non-interactive | disabled |

Display the authoritative option list at any time:

```bash
./raspi_service_pack/install.sh --help
```

For example, install or reconfigure the native-only service:

```bash
sudo ./raspi_service_pack/install.sh --audio-input default --dynamic-control normal --service-user pi
```

After installation, open the embedded interface from the local network at
`http://raspberrypi.local:8080` or the Raspberry Pi address. It is owned by the
existing `oculizer.service`; no second systemd service is installed or exposed
to raspiLightGUI.

Every successful installation regenerates `/etc/oculizer/deployment.json`
from the options supplied on that invocation. Options that are omitted take
their documented defaults; they are not copied implicitly from the previous
configuration. The prior deployment file is retained as
`/etc/oculizer/deployment.json.previous`. Review the active configuration with:

```bash
cat /etc/oculizer/deployment.json
```

The service-owned Web editor treats this deployment file as the effective
source for `audio_input`, `web_enabled`, `web_bind`, and `web_port`. All other
editable application fields remain in the repository's
`config/oculizer.json`. Apply distributes changes between those files,
maintains a `.previous` backup for each source, and rejects stale edits if
either file changed. The installer grants the service account narrowly scoped
write ownership of `/etc/oculizer` for this purpose.

Reinstallation updates the environment, helpers, systemd unit, and deployment
configuration but deliberately preserves whether the service is currently
running and whether boot auto-start is enabled. Restart a running service
explicitly when the new configuration should take effect:

```bash
oculizer-service restart
```

Lighting connection, widget captions, routing, and scene metadata all come
from the repository's single `config/oculizer.json`. The deployment file holds
only machine-specific service values such as repository, account, audio input,
dynamic-control profile, and control socket. QLC+ Native startup remains
asynchronous, so the service can start before QLC+ and reconnect later.

The installer preserves the current running and boot-enabled states. Choose one operating mode afterward:

```bash
oculizer-service auto
```

or:

```bash
oculizer-service start
oculizer-service stop
```

QLC+ System Command functions can invoke these absolute commands without an interactive password:

```text
/usr/local/bin/oculizer-service start
/usr/local/bin/oculizer-service stop
```

Available lifecycle commands:

```text
oculizer-service start
oculizer-service stop
oculizer-service restart
oculizer-service status
oculizer-service logs
oculizer-service run-auto
oculizer-service auto
oculizer-service noauto
oculizer-service last-state
oculizer-service health
```

For a one-off start or restart without creating the Web child:

```bash
oculizer-service start --no-web
oculizer-service restart --no-web
```

For a persistent no-Web installation, reinstall with
`sudo ./raspi_service_pack/install.sh --no-web`. A normal later start clears
the one-off override and follows the installed deployment setting.

`oculizer-service auto` enables systemd boot startup. It is unrelated to `oculizerctl auto`, which resumes automatic scene prediction inside an already running process.

The installed `oculizerctl` automatically discovers one active Oculizer control
socket. It checks the environment override, this deployment's configured
systemd socket, the user's runtime directory, and the standard per-user `/tmp`
socket. If both a service and a foreground instance are running, select one
explicitly, for example:

```bash
oculizerctl --socket /run/oculizer/control.sock status
```

The client never guesses when multiple active runtimes are found.

## Shutdown and reboot behavior

The live audio source now requests a prompt PortAudio interrupt before the
Oculizer worker owns the final stream close. Native `abort`, `stop`, and `close`
stages are timed in the service log, and a blocked native audio call is bounded
so it cannot hold the Oculizer worker indefinitely.

The installed unit is also ordered after the selected service user's
`user@<uid>.service`. systemd reverses that ordering on shutdown, allowing
Oculizer to stop while the user's PipeWire/ALSA session is still available.

`TimeoutStopSec=30` intentionally remains unchanged during validation. After
installing this version, validate a normal stop first:

```bash
sudo systemctl stop oculizer.service
sudo journalctl -u oculizer.service -b -n 100 --no-pager
```

Then validate a real Raspberry Pi reboot/shutdown and inspect the previous boot:

```bash
sudo journalctl -b -1 -u oculizer.service --no-pager
```

The expected result is that Oculizer exits before the 30-second timeout and the
log shows which PortAudio shutdown stages completed. A `stream.close()` timeout
may still be reported if the native audio stack wedges; that condition is now
explicitly bounded so application shutdown can continue instead of waiting for
systemd's 30-second kill timeout.

On Raspberry Pi validation performed 2026-08-18, the normal stop completed in
2.820 seconds with `close()` returning normally. A second stop performed after
QLC+ was already absent completed in 4.096 seconds even though `stream.close()`
did not return within its 2.000-second native bound. In both cases systemd
reported successful deactivation and no SIGKILL occurred.

The detailed design, observed validation results, expected log messages, and
regression procedure are documented in
[`docs/raspberry_shutdown.md`](../docs/raspberry_shutdown.md).
