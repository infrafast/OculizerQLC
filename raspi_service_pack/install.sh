#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEFAULT_OUTPUT=qlc-websocket
DEFAULT_AUDIO_INPUT=default
DEFAULT_DYNAMIC_CONTROL=normal
CONFIG_DIR=/etc/oculizer
CONFIG_FILE=$CONFIG_DIR/deployment.json
HELPER_DIR=/usr/local/lib/oculizer-deploy
CONTROL_CLIENT=/usr/local/bin/oculizerctl
SERVICE_CLIENT=/usr/local/bin/oculizer-service
APP_UNIT=oculizer.service

output=$DEFAULT_OUTPUT
audio_input=$DEFAULT_AUDIO_INPUT
dynamic_control=$DEFAULT_DYNAMIC_CONTROL
service_user=${SUDO_USER:-pi}
check_only=false

usage() {
  cat <<'EOF'
Usage: sudo ./raspi_service_pack/install.sh [OPTIONS]

Install Oculizer as a Raspberry Pi systemd service.

Options:
  --output MODE             qlc-websocket (default) or qlc-osc
  --audio-input SELECTOR    Oculizer input selector (default: default)
  --dynamic-control NAME    Startup dynamic-control profile (default: normal)
  --service-user USER       Runtime account (default: invoking sudo user or pi)
  --check                   Validate the host and configuration without changes
  --non-interactive         Accepted for automation; installation is non-interactive
  -h, --help                Show this help
EOF
}

fail() {
  echo "install.sh: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --output) (($# >= 2)) || fail "--output requires a value"; output=$2; shift 2 ;;
    --audio-input) (($# >= 2)) || fail "--audio-input requires a value"; audio_input=$2; shift 2 ;;
    --dynamic-control) (($# >= 2)) || fail "--dynamic-control requires a value"; dynamic_control=$2; shift 2 ;;
    --service-user) (($# >= 2)) || fail "--service-user requires a value"; service_user=$2; shift 2 ;;
    --check) check_only=true; shift ;;
    --non-interactive) shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

[[ $output == qlc-websocket || $output == qlc-osc ]] || fail "--output must be qlc-websocket or qlc-osc"
[[ $(uname -m) == aarch64 || $(uname -m) == arm64 ]] || fail "this installer currently requires Linux ARM64"
[[ -r /etc/os-release ]] || fail "cannot identify the operating system"
grep -qE '^(ID=debian|ID=raspbian)$' /etc/os-release || fail "Debian or Raspberry Pi OS is required"
id "$service_user" >/dev/null 2>&1 || fail "service user '$service_user' does not exist"
service_home=$(getent passwd "$service_user" | cut -d: -f6)
[[ -n $service_home && -d $service_home ]] || fail "service user '$service_user' has no usable home directory"
service_group=$(id -gn "$service_user")
service_uid=$(id -u "$service_user")

[[ -r $REPO_ROOT/config/oculizer.json ]] || fail "missing config/oculizer.json"
[[ -r $REPO_ROOT/config/qlc_config.json ]] || fail "missing config/qlc_config.json"
[[ -f $REPO_ROOT/oculizer/scene_predictors/v6/.ready ]] || fail "the v6 predictor is not marked ready"

echo "Oculizer Raspberry Pi installation"
echo "  repository:      $REPO_ROOT"
echo "  service user:    $service_user"
echo "  output:          $output"
echo "  audio input:     $audio_input"
echo "  dynamic control: $dynamic_control"

if $check_only; then
  command -v python3 >/dev/null || fail "python3 is missing"
  python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version'
  echo "Preflight passed; no changes made."
  exit 0
fi

[[ $EUID -eq 0 ]] || fail "installation requires sudo (use --check for read-only preflight)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git sudo python3 python3-venv python3-dev build-essential portaudio19-dev libsndfile1-dev ffmpeg

[[ -x $REPO_ROOT/install.sh ]] || fail "missing executable install.sh"
if [[ -d $REPO_ROOT/.venv ]]; then
  chown -R "$service_user:$service_group" "$REPO_ROOT/.venv"
fi
sudo -u "$service_user" -H "$REPO_ROOT/install.sh" --python python3
"$REPO_ROOT/.venv/bin/python" -c 'import efficientat, numpy, scipy, torch; print(f"Validated Python stack: numpy={numpy.__version__} scipy={scipy.__version__} torch={torch.__version__}")'

install -d -m 0755 "$CONFIG_DIR" "$HELPER_DIR"
if [[ -e $CONFIG_FILE ]]; then
  cp -a "$CONFIG_FILE" "$CONFIG_FILE.previous"
fi
python3 - "$CONFIG_FILE" "$REPO_ROOT" "$service_user" "$output" "$audio_input" "$dynamic_control" <<'PY'
import json
import os
import sys

path, repository, user, output, audio_input, dynamic_control = sys.argv[1:]
payload = {
    "repository": repository,
    "service_user": user,
    "output": output,
    "audio_input": audio_input,
    "dynamic_control": dynamic_control,
    "control_socket": "/run/oculizer/control.sock",
    "qlc_port": 9999,
}
temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
os.chmod(temporary, 0o644)
os.replace(temporary, path)
PY

install -m 0755 "$SCRIPT_DIR/run_oculizer.py" "$HELPER_DIR/run_oculizer.py"
install -m 0755 "$SCRIPT_DIR/wait_for_qlc.py" "$HELPER_DIR/wait_for_qlc.py"
install -m 0755 "$SCRIPT_DIR/oculizerctl-wrapper" "$CONTROL_CLIENT"
install -m 0755 "$SCRIPT_DIR/oculizer-service" "$SERVICE_CLIENT"
python3 - "$SCRIPT_DIR/systemd/oculizer.service" "/etc/systemd/system/$APP_UNIT" "$service_user" "$service_group" "$service_home" "$service_uid" <<'PY'
import pathlib, sys
source, destination, user, group, home, uid = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding="utf-8")
text = text.replace("@SERVICE_USER@", user).replace("@SERVICE_GROUP@", group).replace("@SERVICE_HOME@", home).replace("@SERVICE_UID@", uid)
pathlib.Path(destination).write_text(text, encoding="utf-8")
PY
chmod 0644 "/etc/systemd/system/$APP_UNIT"
systemd-analyze verify "/etc/systemd/system/$APP_UNIT"

python3 - "/etc/sudoers.d/oculizer-service" "$service_user" <<'PY'
import pathlib, sys
path, user = sys.argv[1:]
commands = (
    "/usr/bin/systemctl start oculizer.service",
    "/usr/bin/systemctl stop oculizer.service",
    "/usr/bin/systemctl restart oculizer.service",
    "/usr/bin/systemctl enable --now oculizer.service",
    "/usr/bin/systemctl disable oculizer.service",
)
text = "Cmnd_Alias OCULIZER_SERVICE = " + ", ".join(commands) + "\n"
text += f"{user} ALL=(root) NOPASSWD: OCULIZER_SERVICE\n"
pathlib.Path(path).write_text(text, encoding="utf-8")
PY
chmod 0440 /etc/sudoers.d/oculizer-service
visudo -cf /etc/sudoers.d/oculizer-service

for group in audio dialout; do
  getent group "$group" >/dev/null && usermod -a -G "$group" "$service_user"
done
loginctl enable-linger "$service_user"

systemctl daemon-reload

echo "Installation complete."
echo "Configuration: $CONFIG_FILE"
echo "Status: sudo systemctl status $APP_UNIT"
echo "Logs: sudo journalctl -u $APP_UNIT -f"
echo "Control: oculizerctl status"
echo "Manual start: oculizer-service start"
echo "Boot auto-start: oculizer-service auto"
