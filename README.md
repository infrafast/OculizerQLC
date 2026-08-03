# Oculizer

Oculizer is a music-reactive lighting system. It analyzes an audio stream in real time, predicts a suitable mood with a classification model, and automatically selects a lighting scene. A manual mode lets the operator take control during a show.

The project is being migrated to a hybrid architecture with QLC+ 5: Oculizer retains audio analysis and intelligent scene selection, while QLC+ runs scenes, chasers, and DMX outputs. The application can start with either the existing Enttec-compatible direct-DMX backend or the QLC+ OSC backend. Automatic scene selection, silence handling, and speech-aware announcement routing are validated with QLC+ on macOS.

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
- direct DMX output through a DMXKing/Enttec-compatible interface;
- selectable `enttec` and `qlc-osc` lighting backends with OSC dry-run and destination overrides.

## Intended use

Oculizer is designed to create a music-driven light show with three levels of control:

1. the predictor automatically selects a scene from the musical content;
2. FFT analysis modulates lighting parameters in real time;
3. the operator can manually select a scene and later return to automatic mode.

The QLC+ 5 target will preserve this logic while moving hardware patching, lighting functions, and DMX output into QLC+. Development currently takes place on macOS with a local QLC+ instance. The production target is a Raspberry Pi 5 running Raspberry Pi OS, with QLC+ 5 and Oculizer on the same machine. Production operation will use a non-interactive Oculizer mode managed as a service; curses will remain an optional operator interface rather than a service dependency.

## Current prerequisites

- Python 3.8 or newer;
- an audio input available through PortAudio;
- a virtual audio cable when required, such as BlackHole on macOS;
- an Enttec USB DMX Pro-compatible interface only when using the `enttec` backend;
- DMX fixtures addressed according to the selected profile only when using the `enttec` backend.

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
- `config/oculizer.json`: general runtime configuration, including the audio input selector;
- `config/audio_parameters.json`: audio capture and analysis parameters;
- `config/qlc_config.json`: unified QLC+ OSC transport, global controls, and logical scene routing;
- `profile_fallbacks.json`: profile-specific scene substitutions.

List available audio devices:

```bash
python oculize.py --list-devices
```

By default, `config/oculizer.json` contains:

```json
{
  "audio": {
    "input_device": "default",
    "prediction": {
      "window_seconds": 2.0
    },
    "master_modulation": {
      "enabled": true,
      "parameter": "master",
      "rate_hz": 25.0,
      "input_floor": 0.001,
      "input_ceiling": 0.1,
      "smoothing_factor": 0.25,
      "change_threshold": 0.01,
      "silence_value": 0.0,
      "shutdown_value": 0.0
    },
    "silence": {
      "enabled": true,
      "threshold": 0.001,
      "resume_threshold": 0.002,
      "duration_seconds": 2.0,
      "scene": "off"
    }
  }
}
```

`default` selects the input exposed as default by the operating system through PortAudio. This keeps the same configuration portable across CoreAudio on macOS and the available PortAudio host API on Linux. The selector may instead be an alias (`blackhole`, `scarlett`, or `cable_output`), a full or partial device name, or a numeric index. Names are preferable to indexes because indexes can change after a restart.

The silence policy is evaluated before automatic prediction routing. Audio must remain at or below `threshold` for `duration_seconds` before the configured scene is selected. Normal prediction resumes only above `resume_threshold`, which provides hysteresis near the boundary. `scene` is user-selectable: it can be `off`, an ambient scene, a safety light, or any other logical scene present in both `scenes/` and `config/qlc_config.json`. Manual override always has priority over the silence policy.

`audio.prediction.window_seconds` controls the rolling analysis window. The responsive default is two seconds; increasing it improves temporal stability but delays transitions. Speech defaults use 0.5 seconds to enter announcement mode and 0.75 seconds to return to music.

`audio.master_modulation` maps live input RMS to the QLC+ control named by `parameter`. The reference `master` control is `/oculizer/master` under `controls` in `config/qlc_config.json`. RMS values between `input_floor` and `input_ceiling` are normalized to `[0.0, 1.0]`, smoothed, rate-limited to 25 Hz, and deduplicated using `change_threshold`. Silence and clean shutdown send the configured safe value `0.0`. In QLC+ 5, the reference slider is a Grand Master in Reduce mode applied to Intensity channels only.

`audio.frequency_modulation` derives bass (35–180 Hz), mid (180–2,000 Hz), and high (2,000–8,000 Hz) energy from the Mel spectrum already computed by the audio callback. Each band has an independent OSC parameter, frequency range, normalization floor and ceiling, response mode, and enable switch. Bass and mid use `transient` response: a slowly adapting baseline is removed so musical accents create peaks while sustained energy recedes. High uses `level` response so cymbals, hi-hats, and sustained high-frequency content remain represented for their full duration. `baseline_smoothing` controls the baseline only for transient bands. Bass, mid, and high are enabled at `/oculizer/bass`, `/oculizer/mid`, and `/oculizer/high`. All enabled bands share rate limiting, smoothing, change suppression, and safe silence/shutdown values.

The command line overrides the configuration. For example, use BlackHole for an Enttec-backed launch:

```bash
python toggle.py --input blackhole --output enttec
```

Or select another input by name or index:

```bash
python toggle.py --input "Microphone iMac" --output enttec
python toggle.py --input 0 --output enttec
```

An alternative general configuration can be supplied with `--config PATH`.

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

For QLC+, no fixture profile or audio input is required:

```bash
python toggle.py --output qlc-osc --qlc-config config/qlc_config.json
```

Only logical scenes declared under `routing.scenes` in `config/qlc_config.json` are displayed. The reference mapping exposes `party`, which pulses the validated `/test` QLC+ toggle button, `announcement`, and `off`. The reference QLC+ buttons must be off before starting because logical state tracking has no OSC state feedback yet.

The `off` action also sends `/blackout 1.0` after deactivating the tracked toggle. The next ordinary scene activation sends `/blackout 0.0` before pulsing its control. QLC+ must therefore map `/blackout` to an appropriate blackout control.

The mapping stays under `config/` because it routes semantic scenes into a deployment-specific QLC+ workspace; files under `scenes/` describe Oculizer's artistic scene semantics.

## Non-interactive automatic operation

Run prediction and QLC+ scene routing without curses or terminal input:

```bash
python oculizer_service.py --output qlc-osc --input-device blackhole --predictor-version v4 --qlc-config config/qlc_config.json
```

Omit `--input-device` to use `config/oculizer.json`. `SIGINT` and `SIGTERM` use the same clean shutdown path, making this entry point suitable for later systemd supervision.

Service output uses explicit carriage-return line endings for readable terminal logs. Verbose third-party model dumps are captured at debug level; normal startup reports only concise predictor progress and warnings.

Unmapped predictions resolve explicitly to `party`. Requested and resolved scenes are logged, while different predictions resolving to the same active target send no duplicate OSC pulse. Expand `routing.scenes` in `config/qlc_config.json` as real QLC+ functions are added.

Once silence is active, heavy model inference is suspended and queued audio is discarded. RMS monitoring continues in the audio callback; inference resumes from fresh audio only after the level crosses `resume_threshold`. One prediction already in progress may finish immediately after silence activation, but periodic silent `wave` classifications and queue-depth growth then stop.

Speech-aware routing reuses EfficientAT's existing AudioSet outputs to distinguish dominant spoken announcements from music and singing. It supports a configurable announcement scene, confidence thresholds, timing hysteresis, and conservative behavior for ambiguous speech mixed with music. It does not require a separate microphone input or second large model.

Speech-aware routing is configured under `audio.speech` in `config/oculizer.json`. The default policy requires speech confidence `0.55`, a `0.15` lead over music, 0.5 seconds of stable speech, and a 0.75-second release. It routes to the logical `announcement` scene at `/oculizer/scenes/announcement`; singing contributes to music rather than speech. While speech is being confirmed, or while neither speech nor music is dominant, the router preserves the current QLC+ scene. It releases `announcement` only after dominant music remains stable for the configured release duration, preventing short pauses in speech from leaking cluster scenes such as `wave` into the output.

Without `--input`, `toggle.py` uses the selector from `config/oculizer.json`, which defaults to the operating-system input. Inspect the inputs visible to the current Python environment with:

```bash
python toggle.py --list-devices
```

Controls:

- arrow keys: move through the grid;
- `Enter`: activate a scene;
- typing letters: search by prefix;
- `Escape`: clear the search;
- `Ctrl+R`: reload scenes and the QLC+ logical mapping;
- `Ctrl+T`: return to the automatic interface when it launched the selector;
- `Ctrl+Q`: quit.

The standalone selector is intentionally keyboard-only. Terminal mouse protocols vary across macOS integrated terminals, Linux consoles, and SSH sessions and can otherwise expose raw escape sequences in the interface.

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

- Check devices with `python oculize.py --list-devices` or `python toggle.py --list-devices`.
- Use `--test` to isolate prediction from lighting hardware.
- A startup pause can be caused by EfficientAT loading on the CPU.
- Check `oculizer.log` for scene changes, substitutions, and audio errors.
- If a scene remains dark, verify that its fixture names exist in the active profile.
- If installation reports that no `efficientat` distribution exists, use both installation commands above; `efficientat` is not available from PyPI.

## Project status

Direct DMX output remains available. OSC transport, interchangeable backends, manual and automatic QLC+ selection, configurable silence behavior, stabilized speech-aware announcement routing, the headless runtime, continuous Grand Master modulation, and bass, mid, and high modulation are validated with live audio on macOS. Robustness and state handling is the active milestone.

The milestone-0 QLC+ connection can be checked after configuring the test control in QLC+:

```bash
python scripts/send_osc_test.py --pulse 1
```

This sends `/test` with value `1.0`, waits one second, and sends `0.0` to `127.0.0.1:7700`. It is a temporary connection diagnostic, not part of the application OSC namespace or backend.

The QLC+ integration is configured in `config/qlc_config.json`. Its `transport`, `controls`, and `routing` sections define the UDP destination, global commands such as blackout, and semantic scene mappings. The OSC client provides normalized float messages, press/release helpers, level controls, dry-run operation, and non-blocking UDP error handling. Select it from either application entry point with:

```bash
python toggle.py --output qlc-osc
python oculize.py --output qlc-osc
```

Use `--qlc-config PATH` to select another unified QLC+ configuration. `--osc-host HOST` and `--osc-port PORT` override its destination, while `--osc-dry-run` exercises startup and shutdown without sending packets. Selecting `qlc-osc` never loads a fixture profile or initializes audio or serial DMX hardware in the standalone manual selector.

The standalone `toggle.py` selector does not open an audio stream in `qlc-osc` mode because manual selection has no audio consumer. Audio capture remains enabled for direct reactive Enttec rendering and for automatic scene prediction in `oculize.py`.

The validated QLC+ 5.2.2 reference workspace is stored in `qlc/qlc.qxw`. Its OSC input profile is stored separately in `qlc/Oculizer-OSC.qxi`, because QLC+ workspaces reference input profiles by name rather than embedding them. On macOS, install the profile in `~/Library/Application Support/QLC+/InputProfiles/` before opening the workspace on a new system. This workspace is a test reference, not a hard-coded runtime default. A future launcher will require a configurable path to the `.qxw` workspace that QLC+ must load. The production profile location and launch mechanism for Raspberry Pi OS will be finalized during the deployment milestone.

Technical tracking, the roadmap, and contributor instructions are maintained in [DEVELOPMENT.md](DEVELOPMENT.md).

## License and credits

The project is distributed under the MIT License.

Audio prediction relies on EfficientAT, librosa, PyTorch, and scikit-learn. The current output path uses pyserial and the Enttec USB DMX Pro protocol.
