# Oculizer

Oculizer is a music-reactive lighting controller. It analyzes audio in real time, automatically selects lighting scenes, and lets an operator take manual control when needed.

This fork supports two lighting outputs:

- direct DMX through an Enttec/DMXKing-compatible interface;
- QLC+ 5 through OSC, with QLC+ responsible for scenes, chasers, and DMX output.

It can use a live audio device or continuously loop an uncompressed PCM WAV file. Interactive and headless operation share the same live control commands.

Development architecture, implementation details, decisions, validation history, and the roadmap are maintained in [DEVELOPMENT.md](DEVELOPMENT.md).

## Requirements

- Python 3.8 or newer; Python 3.11 is recommended;
- Git and internet access during installation;
- PortAudio and an audio input for live capture;
- optionally, a virtual audio cable such as BlackHole on macOS;
- an Enttec-compatible interface only for direct-DMX operation;
- QLC+ 5 for the OSC output mode.

A CUDA-capable GPU can accelerate prediction but is not required. Initial model loading can take several seconds on a CPU.

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

On Windows, activate the environment with:

```text
.venv\Scripts\activate
```

Verify the model dependency:

```bash
python -c "import efficientat; print('EfficientAT: OK')"
```

## Configuration

The main user configuration files are:

- `config/oculizer.json`: audio input, analysis behavior, transition presets, silence, and speech settings;
- `config/qlc_config.json`: QLC+ OSC destination, global controls, and logical-scene mappings;
- `profiles/`: direct-DMX fixture profiles;
- `scenes/`: lighting scenes;
- `profile_fallbacks.json`: profile-specific scene substitutions.

Use another general configuration with `--config PATH`, or another QLC+ configuration with `--qlc-config PATH`.

### Audio input

List the inputs visible to Oculizer:

```bash
python oculize.py --list-devices
```

Select the default device, a recognized alias such as `blackhole` or `scarlett`, a full or partial device name, or a numeric index:

```bash
python oculize.py --input-device blackhole
python oculize.py --input-device "Microphone iMac"
python oculize.py --input-device 0
```

Device names are preferable to indexes because indexes can change after a restart.

### WAV input

On a host without an audio capture device, use an uncompressed PCM WAV file. It is played at real-time speed and loops continuously:

```bash
python oculize.py \
  --audio-file tests/fascination.wav \
  --output qlc-osc \
  --osc-dry-run
```

`--audio-file` cannot be combined with `--prediction-device`. MP3 and online streams are not currently supported.

## Interactive automatic operation

Example with one live audio stream:

```bash
python oculize.py \
  --profile garage2025 \
  --input-device blackhole \
  --single-stream
```

Example with separate FFT and prediction devices:

```bash
python oculize.py \
  --profile garage2025 \
  --input-device scarlett \
  --prediction-device blackhole
```

Useful options:

| Option | Purpose |
| --- | --- |
| `-p`, `--profile` | Direct-DMX fixture profile |
| `-i`, `--input-device` | Main audio input |
| `--prediction-device` | Separate prediction input |
| `--single-stream` | Use one input for FFT and prediction |
| `--audio-file PATH` | Loop a PCM WAV file instead of capturing audio |
| `--predictor-version VERSION` | Select the prediction model |
| `--scene-cache-size N` | Set prediction smoothing (default: `10`) |
| `--scene-rate-limit N/SECONDS` | Limit automatic scene changes in a rolling window |
| `--scene-throttle N/SECONDS` | Allow a burst, then progressively recover transition credits |
| `--scene-max-duration SECONDS` | Force automatic music scenes to rotate after a maximum duration (default: `40`) |
| `--output enttec|qlc-osc` | Select the lighting output |
| `--no-graph` | Hide the interactive RMS graph |
| `--list-devices` | List available audio inputs |

`v6` is the default predictor and is selected when `--predictor-version` is omitted. Use `--predictor-version v4` or `--predictor-version v5` only for explicit comparison or compatibility tests. `v5` uses the v4 scene mapping as an experimental starting point; because its clusters were trained separately, its scene assignments still require artistic validation. Earlier incomplete predictors have been removed from runtime selection, while their distinct scene mappings remain archived under `oculizer/scene_predictors/legacy_mappings/`.

### Train a concert-specific v6 predictor

v6 uses the faster v4 feature pipeline (1,920 EfficientAT dimensions plus 128 mean-MFCC dimensions) while training new clusters on the operator's own recordings. Put representative MP3, WAV, FLAC, M4A, AAC, or OGG recordings in one directory, then run:

```bash
python3 scripts/train_predictor_v6.py \
  --input /path/to/concert-recordings \
  --clusters 30 \
  --window-seconds 4 \
  --hop-seconds 2
```

The initial model remains unavailable to the application because every cluster provisionally maps to `party`. Open `oculizer/scene_predictors/v6/review/cluster_report.md`, listen to its representative excerpts, and edit `oculizer/scene_predictors/v6/scene_mapping.json`. Approve that complete mapping by rerunning the inexpensive statistical stage from the cached audio features:

```bash
python3 scripts/train_predictor_v6.py \
  --input /path/to/concert-recordings \
  --clusters 30 \
  --window-seconds 4 \
  --hop-seconds 2 \
  --reuse-features \
  --mapping oculizer/scene_predictors/v6/scene_mapping.json \
  --force
```

After approval, `--predictor-version v6` appears automatically. Keep `audio.prediction.window_seconds` equal to the v6 training window. The feature cache and review excerpts are local generated data and are ignored by Git; retain a backup until the mapping is final. Use `--max-windows-per-track` to prevent long recordings from dominating, and run `python3 scripts/train_predictor_v6.py --help` for all controls.

Interactive controls:

- `q`: quit;
- `r`: reload scenes;
- `l`: edit cache, rate limit, and throttle values live;
- `Ctrl+T`: open the scene selector;
- `Ctrl+O`: switch between manual override and automatic prediction from the integrated selector.

The main screen displays a scrolling RMS graph and scene-transition markers. Disable it when a simpler display is preferred:

```bash
python oculize.py --no-graph [other options]
```

The current cache, rate limit, and throttle are shown in the status area. An omitted rate limit or throttle is displayed as `Off`. In the `l` editor, use the arrow keys or `+`/`-` to change values, `0` to disable the selected optional policy, Enter to apply, and Escape to cancel.

Automatic music scenes are limited to 40 seconds by default. Override the global value at startup with, for example, `--scene-max-duration 20`. When a scene expires, Oculizer prefers a different mapped scene found in recent predictions and otherwise selects `ambient1`. The expired target cannot immediately re-enter, preventing rapid ping-pong. Silence, announcement, and manual overrides are exempt.

A scene can override the global duration by declaring a positive duration in its artistic definition under `scenes/`:

```json
{
  "name": "white_flicker",
  "max_duration_seconds": 8,
  "lights": []
}
```

When `max_duration_seconds` is absent, the global value is used. The example only illustrates the field; retain the scene's real `lights` definition.

The supplied v6 scene set applies an eight-second maximum to every scene with an active strobe declaration. Non-strobing racer/alternating effects and selected high-energy scenes use 15 seconds. Calmer v6 scenes inherit the global 40-second default. These limits are safety-oriented starting points and can be tuned in the corresponding `scenes/<name>.json` file.

## QLC+ OSC operation

Start automatic operation with QLC+:

```bash
python oculize.py \
  --output qlc-osc \
  --qlc-config config/qlc_config.json \
  --input-device blackhole
```

Override the OSC destination with `--osc-host HOST` and `--osc-port PORT`.

`config/qlc_config.json` contains every logical scene emitted by predictors v4 and v6 and derives each OSC address as `/oculizer/scenes/<scene-name>`. All 30 v6 scenes carry a temporary `"implemented": false` marker so the operator can track QLC+ widget creation. Oculizer deliberately ignores this marker; change it manually as the QLC+ project progresses. Predictor mappings and artistic scene filenames use the same canonical identifiers; historical aliases and misspellings have been normalized.

Test without sending UDP packets:

```bash
python oculize.py \
  --audio-file tests/fascination.wav \
  --output qlc-osc \
  --osc-dry-run
```

Hide selected paths from dry-run logs by repeating `--filter-osc`:

```bash
python oculize.py \
  --audio-file tests/fascination.wav \
  --output qlc-osc \
  --osc-dry-run \
  --filter-osc /oculizer/bass \
  --filter-osc /oculizer/mid \
  --filter-osc /oculizer/high
```

The reference QLC+ workspace is `qlc/qlc.qxw`, and its input profile is `qlc/Oculizer-OSC.qxi`. On macOS, install the profile in `~/Library/Application Support/QLC+/InputProfiles/` before opening the workspace.

## Direct-DMX dry run

Exercise the Enttec rendering path without a connected DMX interface:

```bash
python oculize.py \
  --profile garage2025 \
  --audio-file tests/fascination.wav \
  --output enttec \
  --dmx-dry-run
```

The dry run prints a maximum of three changed-channel summaries per second. Hide all DMX frame summaries with `--filter-dmx`:

```bash
python oculize.py \
  --profile garage2025 \
  --audio-file tests/fascination.wav \
  --output enttec \
  --dmx-dry-run \
  --filter-dmx
```

## Manual scene selection

Run the standalone direct-DMX selector:

```bash
python toggle.py --profile garage2025 --input blackhole
```

Run the standalone QLC+ selector:

```bash
python toggle.py --output qlc-osc --qlc-config config/qlc_config.json
```

Selector controls:

- arrow keys: move through the scene grid;
- Enter: activate a scene;
- type letters: search by prefix;
- Escape: clear the search;
- `Ctrl+R`: reload scenes and QLC+ mappings;
- `Ctrl+T`: return to the automatic screen when using the integrated selector;
- `Ctrl+Q`: quit.

## Headless operation

Run automatic prediction and QLC+ routing without curses:

```bash
python oculizer_service.py \
  --output qlc-osc \
  --input-device blackhole \
  --qlc-config config/qlc_config.json
```

The process handles `SIGINT` and `SIGTERM` cleanly. Raspberry Pi and systemd installation are covered by the next development phase and are not yet documented as production-ready.

## Live runtime control

Interactive and headless operation expose the same local control socket. From another terminal:

```bash
python3 oculizerctl.py status
python3 oculizerctl.py auto
python3 oculizerctl.py pause
python3 oculizerctl.py scene wave
python3 oculizerctl.py limits
python3 oculizerctl.py limits --cache 7 --rate 6/10 --throttle 3/2
python3 oculizerctl.py limits --rate off --throttle off
python3 oculizerctl.py presets
python3 oculizerctl.py preset responsive
python3 oculizerctl.py preset normal
python3 oculizerctl.py preset calm
python3 oculizerctl.py preset reset
```

The default socket is `/tmp/oculizer-<uid>.sock`. Start the application with `--control-socket PATH` to use another path, then place `--socket PATH` before the `oculizerctl.py` subcommand. Use `--no-control-socket` to disable external control.

`pause` suspends automatic processing and activates blackout. `auto` clears pause or manual override and resumes automatic operation. `scene NAME` forces a configured logical scene. Live changes last until the application restarts and do not rewrite configuration files.

### Operator presets

The supplied presets are starting points that can be adjusted under `control.presets` in `config/oculizer.json`:

| Preset | Cache | Throttle | Rate limit | Behavior |
| --- | ---: | ---: | ---: | --- |
| `responsive` | `3` | `4/1` | `10/10` | Fast response and generous bursts |
| `normal` | `15` | `2/4` | `4/15` | Stable general-purpose behavior with restrained transitions |
| `calm` | `35` | `1/10` | `2/20` | Very strong smoothing and long, relaxed scene holds |
| `reset` | startup value | `Off` | `Off` | Restore startup smoothing and disable both limits |

QLC+ 5 Virtual Console buttons can call the installed client through script functions such as:

```javascript
Engine.systemCommand("/usr/local/bin/oculizerctl preset responsive");
Engine.systemCommand("/usr/local/bin/oculizerctl preset normal");
Engine.systemCommand("/usr/local/bin/oculizerctl preset calm");
Engine.systemCommand("/usr/local/bin/oculizerctl preset reset");
```

The `/usr/local/bin/oculizerctl` installation path will be provided by the Raspberry Pi deployment phase. During development, use the absolute paths to Python and `oculizerctl.py`.

## Test mode

Run prediction without FFT or DMX output:

```bash
python oculize.py --test --profile mobile
```

## Troubleshooting

- No audio input: run `python oculize.py --list-devices`, or use `--audio-file` on an audio-less host.
- `PortAudio library not found`: install the system PortAudio library before using live capture; WAV mode does not require a capture device.
- No DMX interface: use `--output enttec --dmx-dry-run` for a hardware-free test.
- Slow or apparently blank startup: model loading can take several seconds on a CPU.
- Excessive OSC or DMX dry-run logs: use repeatable `--filter-osc PATH` or `--filter-dmx`.
- Dark or substituted scene: check the active profile, `profile_fallbacks.json`, and QLC+ mappings.
- Runtime details and errors: inspect `oculizer.log`.
- Installation cannot find EfficientAT on PyPI: run the separate EfficientAT installation command shown above.

Display all supported command-line options with:

```bash
python oculize.py --help
```

## License and credits

The project is distributed under the MIT License. Audio prediction relies on EfficientAT, librosa, PyTorch, and scikit-learn.
