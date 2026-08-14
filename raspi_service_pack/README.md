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
| `--output MODE` | `qlc-websocket`, `qlc-native`, or `qlc-osc` | `qlc-websocket` |
| `--audio-input SELECTOR` | An Oculizer device selector such as `default` | `default` |
| `--dynamic-control NAME` | `off` or a profile declared in `config/oculizer.json` | `normal` |
| `--service-user USER` | Existing Linux account used to run Oculizer | invoking sudo user, or `pi` |
| `--check` | Validate the host without changing it | disabled |
| `--non-interactive` | Explicit automation marker; installation is already non-interactive | disabled |

Display the authoritative option list at any time:

```bash
./raspi_service_pack/install.sh --help
```

For example, install or reconfigure the service for QLC+ native output:

```bash
sudo ./raspi_service_pack/install.sh --output qlc-native --audio-input default --dynamic-control normal --service-user pi
```

Equivalent WebSocket and OSC examples are:

```bash
sudo ./raspi_service_pack/install.sh --output qlc-websocket --audio-input default --dynamic-control normal --service-user pi
sudo ./raspi_service_pack/install.sh --output qlc-osc --audio-input default --dynamic-control normal --service-user pi
```

Every successful installation regenerates `/etc/oculizer/deployment.json`
from the options supplied on that invocation. Options that are omitted take
their documented defaults; they are not copied implicitly from the previous
configuration. The prior deployment file is retained as
`/etc/oculizer/deployment.json.previous`. Review the active configuration with:

```bash
cat /etc/oculizer/deployment.json
```

Reinstallation updates the environment, helpers, systemd unit, and deployment
configuration but deliberately preserves whether the service is currently
running and whether boot auto-start is enabled. Restart a running service
explicitly when the new configuration should take effect:

```bash
oculizer-service restart
```

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

`oculizer-service auto` enables systemd boot startup. It is unrelated to `oculizerctl auto`, which resumes automatic scene prediction inside an already running process.
