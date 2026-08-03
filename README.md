# Oculizer

Oculizer is a music-reactive lighting system. It analyzes an audio stream in real time, predicts a suitable mood with a classification model, and automatically selects a lighting scene. A manual mode lets the operator take control during a show.

The project is being migrated to a hybrid architecture with QLC+ 5: Oculizer will retain audio analysis and intelligent scene selection, while QLC+ will run scenes, chasers, and DMX outputs. OSC output is not implemented yet; the current version directly controls a DMX interface compatible with the Enttec USB DMX Pro protocol.

## Current features

- real-time audio capture with `sounddevice`;
- mel-scaled FFT analysis for light modulation;
- scene prediction using EfficientAT, PCA, and k-means;
- `v1`, `v3`, `v4`, `v5`, and `vday` predictors;
- single-stream and dual-stream audio modes;
- JSON-based scenes and fixture profiles;
- reactive effects, time-based effects, and group orchestrators;
- automatic and manual scene selection;
- profile-specific scene substitution through `profile_fallbacks.json`;
- prediction-only test mode without FFT or DMX hardware;
- direct DMX output through a DMXKing/Enttec-compatible interface.

## Intended use

Oculizer is designed to create a music-driven light show with three levels of control:

1. the predictor automatically selects a scene from the musical content;
2. FFT analysis modulates lighting parameters in real time;
3. the operator can manually select a scene and later return to automatic mode.

The QLC+ 5 target will preserve this logic while moving hardware patching, lighting functions, and DMX output into QLC+. Development currently takes place on macOS with a local QLC+ instance. The production target is a Raspberry Pi 5 running Raspberry Pi OS, with QLC+ 5 and Oculizer on the same machine.

## Current prerequisites

- Python 3.8 or newer;
- an audio input available through PortAudio;
- a virtual audio cable when required, such as BlackHole on macOS;
- an Enttec USB DMX Pro-compatible interface for the current output path;
- DMX fixtures addressed according to the selected profile.

A CUDA-capable GPU accelerates the model but is not required. Initial model loading can take several seconds on a CPU.

## Installation

```bash
git clone https://github.com/LandryBulls/Oculizer.git
cd Oculizer
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps \
  "efficientat @ git+https://github.com/LandryBulls/EfficientAT.git@010b68e69d9f75d074eb8720ac06968c38352ac8"
```

On Windows, activate the environment with `.venv\\Scripts\\activate`.

Python 3.11 is the recommended development version. EfficientAT is installed from the project's package-enabled GitHub fork because the upstream project is not distributed on PyPI. The fork is installed with `--no-deps` because its historical training metadata pins PyTorch versions that are unavailable on Python 3.11 and Apple Silicon; the compatible runtime libraries are installed by `requirements.txt`. Installation therefore requires Git and internet access.

Because the fork retains those historical metadata declarations, `pip check` reports EfficientAT dependency conflicts and missing training-only packages. This is expected for this installation method. Runtime validation should instead import `torch`, `torchaudio`, `torchvision`, and `efficientat`; Oculizer does not use the omitted training stack.

## Configuration

The main directories and configuration files are:

- `profiles/`: fixtures available in each installation;
- `scenes/`: lighting behavior and modulation definitions;
- `templates/`: scene examples;
- `config/audio_parameters.json`: audio capture and analysis parameters;
- `profile_fallbacks.json`: profile-specific scene substitutions.

List available audio devices:

```bash
python oculize.py --list-devices
```

Devices can be selected by alias (`blackhole`, `scarlett`, or `cable_output`) or, where supported, by index. Names are preferable because device indexes can change after a restart.

## Automatic operation

Example single-stream setup on macOS:

```bash
python oculize.py --profile garage2025 --input-device blackhole --single-stream
```

Example dual-stream setup:

```bash
python oculize.py \
  --profile garage2025 \
  --input-device scarlett \
  --prediction-device blackhole
```

Main options:

| Option | Purpose |
| --- | --- |
| `-p`, `--profile` | Fixture profile |
| `-i`, `--input-device` | Input used for FFT processing |
| `--prediction-device` | Separate input used by the predictor |
| `--single-stream` | Use one input for FFT and prediction |
| `--predictor-version` | Predictor version |
| `--scene-cache-size` | Prediction smoothing window |
| `--prediction-channels` | Audio channels used for prediction |
| `--average-dual-channels` | Average the first two FFT input channels |
| `--test` | Prediction only, without FFT or DMX |
| `--list-devices` | Display available audio inputs |

During operation:

- `q` quits the application;
- `r` reloads scenes;
- `Ctrl+T` opens the integrated manual selector.

## Manual scene selection

Run the standalone selector with:

```bash
python toggle.py --profile garage2025 --input blackhole
```

Controls:

- arrow keys: move through the grid;
- `Enter` or mouse click: activate a scene;
- typing letters: search by prefix;
- `Escape`: clear the search;
- `Ctrl+R`: reload scenes;
- `Ctrl+T`: return to the automatic interface when it launched the selector;
- `Ctrl+Q`: quit.

In the integrated selector, predictions continue in the background. A manual selection enables override mode; `Ctrl+O` switches between manual override and automatic prediction following.

## Test mode

```bash
python oculize.py --test --profile mobile --predictor-version v4
```

This mode:

- does not initialize the DMX controller;
- does not start the FFT stream;
- keeps the audio capture required by the predictor;
- validates the model and scene transitions without lighting hardware.

## Scenes and profiles

A scene associates fixtures with a modulator. Simplified example:

```json
{
  "name": "party",
  "description": "Reactive scene",
  "type": "effect",
  "lights": [
    {
      "name": "rgb1",
      "type": "rgb",
      "modulator": "mfft",
      "mfft_range": [0, 20],
      "power_range": [0, 2],
      "brightness_range": [0, 255],
      "color": "red",
      "strobe": 0
    }
  ]
}
```

Main modulators:

- `mfft`: reacts to a frequency range;
- `time`: evolves periodically without relying on audio level;
- `bool`: discrete or random selection and triggering.

Orchestrators coordinate multiple fixtures. For example, the `hopper` type selects active fixtures from an audio trigger. Exact parameters currently depend on the fixture type and the functions implemented in `oculizer/light/mapping.py`, `effects.py`, and `orchestrators.py`.

### Scene substitutions

`profile_fallbacks.json` contains `requested scene → played scene` mappings for each profile. Every declared mapping is applied even when the original scene is technically compatible. This also supports installation-specific artistic preferences.

Available tools:

```bash
python analyze_scenes.py
python generate_fallbacks.py
python test_fallbacks_simple.py
```

## Troubleshooting

- Check devices with `python oculize.py --list-devices`.
- Use `--test` to isolate prediction from lighting hardware.
- A startup pause can be caused by EfficientAT loading on the CPU.
- Check `oculizer.log` for scene changes, substitutions, and audio errors.
- If a scene remains dark, verify that its fixture names exist in the active profile.
- If installation reports that no `efficientat` distribution exists, use both installation commands above; `efficientat` is not available from PyPI.

## Project status

Direct DMX output works but is tightly coupled to the Enttec controller. The QLC+ 5 migration is planned as testable milestones: OSC transport, interchangeable backends, manual selection, automatic prediction, and continuous audio modulation.

The milestone-0 QLC+ connection can be checked after configuring the test control in QLC+:

```bash
python scripts/send_osc_test.py --pulse 1
```

This sends `/test` with value `1.0`, waits one second, and sends `0.0` to `127.0.0.1:7700`. It is a temporary connection diagnostic, not part of the application OSC namespace or backend.

The reusable OSC transport is configured in `config/qlc_osc.json`. It currently provides normalized float messages, press/release helpers, level controls, blackout, dry-run operation, and non-blocking UDP error handling. It is not connected to `oculize.py` or `toggle.py` yet; backend integration belongs to the next milestone.

The validated QLC+ 5.2.2 reference workspace is stored in `qlc/qlc.qxw`. Its OSC input profile is stored separately in `qlc/Oculizer-OSC.qxi`, because QLC+ workspaces reference input profiles by name rather than embedding them. On macOS, install the profile in `~/Library/Application Support/QLC+/InputProfiles/` before opening the workspace on a new system. This workspace is a test reference, not a hard-coded runtime default. A future launcher will require a configurable path to the `.qxw` workspace that QLC+ must load. The production profile location and launch mechanism for Raspberry Pi OS will be finalized during the deployment milestone.

Technical tracking, the roadmap, and contributor instructions are maintained in [DEVELOPMENT.md](DEVELOPMENT.md).

## License and credits

The project is distributed under the MIT License.

Audio prediction relies on EfficientAT, librosa, PyTorch, and scikit-learn. The current output path uses pyserial and the Enttec USB DMX Pro protocol.
