#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
EFFICIENTAT_URL="efficientat @ git+https://github.com/LandryBulls/EfficientAT.git@010b68e69d9f75d074eb8720ac06968c38352ac8"
PYTORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
PIP_NETWORK_OPTIONS=(
    --timeout "${OCULIZER_PIP_TIMEOUT:-300}"
    --retries "${OCULIZER_PIP_RETRIES:-20}"
    --resume-retries "${OCULIZER_PIP_RESUME_RETRIES:-50}"
)
INSTALL_ATTEMPTS="${OCULIZER_INSTALL_ATTEMPTS:-5}"

run_pip() {
    local attempt

    for ((attempt = 1; attempt <= INSTALL_ATTEMPTS; attempt++)); do
        if "$VENV_PYTHON" -m pip "$@"; then
            return 0
        fi

        if ((attempt == INSTALL_ATTEMPTS)); then
            echo "Error: pip failed after $INSTALL_ATTEMPTS attempts." >&2
            return 1
        fi

        echo
        echo "pip download interrupted; retrying ($((attempt + 1))/$INSTALL_ATTEMPTS)..."
        sleep $((attempt * 2))
    done
}

usage() {
    cat <<'EOF'
Usage: ./install.sh [--python COMMAND]

Create or update Oculizer's local Python environment and install all Python
dependencies. The default Python command is python3.

Options:
  --python COMMAND  Python interpreter used to create .venv
  -h, --help        Show this help
EOF
}

PYTHON_COMMAND="python3"
while (($#)); do
    case "$1" in
        --python)
            if (($# < 2)); then
                echo "Error: --python requires a command." >&2
                exit 2
            fi
            PYTHON_COMMAND="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$INSTALL_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: OCULIZER_INSTALL_ATTEMPTS must be a positive integer." >&2
    exit 2
fi

if ! command -v "$PYTHON_COMMAND" >/dev/null 2>&1; then
    echo "Error: Python command not found: $PYTHON_COMMAND" >&2
    exit 1
fi

if ! "$PYTHON_COMMAND" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    echo "Error: Oculizer requires Python 3.11 or newer." >&2
    exit 1
fi

cd "$SCRIPT_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Creating local Python environment..."
    "$PYTHON_COMMAND" -m venv "$VENV_DIR"
else
    echo "Updating existing local Python environment..."
fi

VENV_PYTHON="$VENV_DIR/bin/python"

echo "Installing Oculizer dependencies..."
run_pip install "${PIP_NETWORK_OPTIONS[@]}" --upgrade pip "setuptools<82" wheel

# PyPI's Linux PyTorch wheels can pull the CUDA runtime even on machines with
# no NVIDIA GPU. Install the official CPU builds first; the matching pins in
# requirements.txt then remain satisfied without downloading CUDA, cuDNN,
# NCCL, or Triton. macOS wheels are already CPU/Metal builds on PyPI.
if [[ "$(uname -s)" == "Linux" ]]; then
    echo "Installing CPU-only PyTorch packages..."
    run_pip install "${PIP_NETWORK_OPTIONS[@]}" \
        --index-url "$PYTORCH_CPU_INDEX" \
        torch==2.11.0 torchaudio==2.11.0 torchvision==0.26.0
fi

run_pip install "${PIP_NETWORK_OPTIONS[@]}" -r "$SCRIPT_DIR/requirements.txt"
run_pip install "${PIP_NETWORK_OPTIONS[@]}" --no-deps "$EFFICIENTAT_URL"

echo "Checking runtime dependencies..."
"$VENV_PYTHON" - <<'PY'
import efficientat
import torch
import torchaudio
import torchvision

expected = {
    "torch": (torch.__version__, "2.11.0"),
    "torchaudio": (torchaudio.__version__, "2.11.0"),
    "torchvision": (torchvision.__version__, "0.26.0"),
}
for package, (installed, required) in expected.items():
    if installed.split("+", 1)[0] != required:
        raise SystemExit(
            f"Error: {package} {installed} is installed; expected {required}."
        )

print("Runtime dependency check passed.")
PY

echo
echo "Oculizer installation complete."
echo "Start it with:"
echo "  ./.venv/bin/python oculize.py"
