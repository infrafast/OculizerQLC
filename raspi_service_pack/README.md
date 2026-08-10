# Oculizer Raspberry Pi service pack

This directory contains only the Oculizer service installation and lifecycle files. QLC+, its workspace, and its service are owned by a separate repository.

Run preflight and installation from the repository root:

```bash
./raspi_service_pack/install.sh --check
sudo ./raspi_service_pack/install.sh
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
