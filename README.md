# OculizerQLC

OculizerQLC is a music-reactive lighting controller based on the original Oculizer project available here: https://github.com/LandryBulls/Oculizer. It analyzes audio in real time, automatically selects lighting scenes, and lets an operator take manual control when needed.

This fork enrich and supports two lighting outputs:

- direct DMX through an Enttec/DMXKing-compatible interface;
- QLC+ 5 through OSC, with QLC+ responsible for scenes, chasers, and DMX output.

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/78e758ab-2950-49af-b60f-a08bb41b6d21" />


It can use a live audio device or continuously loop an uncompressed PCM WAV file for simulation and testing before live show. Interactive and headless operation share the same live control commands.

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

- `config/oculizer.json`: audio input, analysis behavior, dynamic-control profiles, silence, and speech settings;
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

### Silence and speech scenes

Oculizer can recognize sustained silence and dominant spoken voice independently of the normal music-scene prediction. Each event can activate a dedicated logical scene: for example, silence can select `off`, while speech can select `announcement` so that lighting remains suitable when someone speaks between songs.

Configure the two routes under `audio` in `config/oculizer.json`:

```json
{
  "audio": {
    "silence": {
      "enabled": true,
      "threshold": 0.001,
      "resume_threshold": 0.002,
      "duration_seconds": 2.0,
      "scene": "off"
    },
    "speech": {
      "enabled": true,
      "threshold": 0.55,
      "music_margin": 0.15,
      "minimum_duration_seconds": 0.5,
      "release_duration_seconds": 0.75,
      "scene": "announcement"
    }
  }
}
```

Change each `scene` value to any logical scene available to the selected output backend. Silence must remain below its threshold for the configured duration before its scene is activated. Speech routing uses confidence and timing margins to avoid switching on brief or ambiguous sounds, then returns automatically to music prediction when music becomes dominant again. Set the corresponding `enabled` value to `false` to disable either detector.

The following example processes `mixvoicemusic.wav`, which contains silence, spoken voice, and music. The scene markers include `off` during detected silence and `announcement` when speech becomes dominant, alongside the scenes selected for musical passages. It uses the neutral raw view because silence and speech are priority events rather than ordinary music transitions and do not require a dynamic-control comparison.

![Silence, speech, and music scene detection on mixvoicemusic.wav](docs/mixvoicemusic.svg)

This panel example was generated using following command:

```bash
python3 scripts/render_dynamic_control_comparison.py \
  tests/mixvoicemusic.wav \
  --output docs/mixvoicemusic.svg \
  --prediction-hop-seconds 2 \
  --raw-only
```

Please note that since it is a single panel, profiles where first removed from oculizer.json config file (see

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
| `--dynamic-control PROFILE` | Apply a configured dynamics profile; see [Dynamic control](#dynamic-control) (default: `off`) |
| `--scene-max-duration SECONDS` | Set the automatic scene-duration base before ±30% per-activation variation (default: `40`) |
| `--output enttec|qlc-osc` | Select the lighting output |
| `--no-graph` | Hide the interactive RMS graph |
| `--list-devices` | List available audio inputs |

System can be used different trained predictors. `v6` is the default predictor and is selected when `--predictor-version` is omitted. Use `--predictor-version v4` or `--predictor-version v5` only for explicit comparison or compatibility tests. `v5` uses the v4 scene mapping as an experimental starting point; because its clusters were trained separately, its scene assignments still require artistic validation. Earlier incomplete predictors have been removed from runtime selection, while their distinct scene mappings remain archived under `oculizer/scene_predictors/legacy_mappings/`.

### Train a concert-specific predictor

You can regenerate `v6` from your own concert recordings. The process first groups acoustically similar four-second excerpts into clusters; it cannot decide what those clusters should look like on stage. You complete that artistic step by listening to representative excerpts and assigning an existing Oculizer scene to every cluster.

Cluster numbers have no permanent meaning: cluster `7` is not intrinsically a calm or energetic cluster, and its meaning can change when the corpus, cluster count, or training options change.

#### 1. Prepare the recording corpus

Place representative MP3, WAV, FLAC, M4A, AAC, or OGG recordings in one directory. Include the different songs, intensities, transitions, and atmospheres expected during a show. Avoid unnecessary duplicates; use `--max-windows-per-track` if a long recording would otherwise dominate the corpus.

The command below intentionally replaces the current v6 model, so back it up first if it must be retained.

#### 2. Extract features and create the clusters

```bash
python3 scripts/train_predictor_v6.py \
  --input /path/to/concert-recordings \
  --clusters 30 \
  --window-seconds 4 \
  --hop-seconds 2 \
  --representatives 8 \
  --force
```

This produces the model files and a review workspace under `oculizer/scene_predictors/v6/review/`. The model deliberately remains unavailable at runtime at this point because its provisional mapping assigns every cluster to `party`.

#### 3. Perform the artistic interpretation

Open `oculizer/scene_predictors/v6/review/cluster_report.md`. For each cluster:

1. Listen to all its files in `review/excerpts/`, not only the first one. The source names and the RMS, speech, singing, and music measurements in the report are useful clues, but they are not artistic decisions.
2. Identify what the excerpts have in common: for example energy, density, rhythm, mood, colour, or the suitability of a strobe effect.
3. Choose the existing scene that should represent that musical character. Use its exact logical name from the scene definitions under `scenes/`.
4. Edit the corresponding value in `oculizer/scene_predictors/v6/scene_mapping.json`.

For example:

```json
{
  "0": "chill_blue",
  "1": "electric",
  "2": "pink_strobe_pulse"
}
```

Every cluster from `0` to `29` must occur exactly once. Several clusters may deliberately use the same scene, but no value may be empty and no cluster may be omitted or added. Speech and silence are detected separately at runtime and use the dedicated `announcement` and `off` routes; do not try to encode those two events solely through the musical cluster mapping.

This review is normally iterative: when a cluster is ambiguous, replay all its excerpts and choose the scene whose behaviour is safest and most coherent across the whole group, rather than the scene that best matches one isolated excerpt.

#### 4. Approve and finalize the mapping

Rerun the statistical stage from the cached features. Keep the same cluster and window settings used above:

```bash
python3 scripts/train_predictor_v6.py \
  --input /path/to/concert-recordings \
  --clusters 30 \
  --window-seconds 4 \
  --hop-seconds 2 \
  --representatives 8 \
  --reuse-features \
  --mapping oculizer/scene_predictors/v6/scene_mapping.json \
  --force
```

This validates that the mapping is complete, marks the model as reviewed, and enables `--predictor-version v6`. Reusing the feature cache avoids decoding every recording and running feature extraction again.

#### 5. Test the completed predictor

Test it without lighting hardware before using it in a show:

```bash
python3 oculize.py \
  --audio-file tests/fascination.wav \
  --output qlc-osc \
  --osc-dry-run \
  --predictor-version v6 \
  --scene-cache-size 10
```

Check that the selected scenes remain artistically appropriate across several representative tracks. If an assignment is unsatisfactory, edit `scene_mapping.json` and repeat step 4; feature extraction is not required again.

Keep `audio.prediction.window_seconds` equal to the window used for training (`4` in this example). Also ensure every mapped scene exists in the selected output configuration, especially the QLC+ mapping. The feature cache and review excerpts are generated locally and ignored by Git, so retain a backup until the model and artistic mapping are final. Run `python3 scripts/train_predictor_v6.py --help` for less common controls.

Interactive controls:

- `q`: quit;
- `r`: reload scenes;
- `l`: select a dynamic-control profile live;
- `Ctrl+T`: open the scene selector;
- `Ctrl+O`: switch between manual override and automatic prediction from the integrated selector.

The main screen displays a scrolling RMS graph and scene-transition markers. Disable it when a simpler display is preferred:

```bash
python oculize.py --no-graph [other options]
```

Otherwise the main screen looks like:
<img width="2481" height="1428" alt="image" src="https://github.com/user-attachments/assets/f26856ab-65e4-43f4-94d6-788b151dddf4" />

The milestones correspondent to scenes changes and colors and icons resemble those of the scenes available in the scene control screen (CTRL+T).

Automatic music scene duration uses 40 seconds as its default base. Override the global base at startup with, for example, `--scene-max-duration 20`. On every automatic activation, Oculizer draws one effective duration uniformly within ±30% of the scene-specific or global base and keeps that value stable for the complete activation. A base of 8 seconds therefore produces 5.6–10.4 seconds, while the default base produces 28–52 seconds. When that duration expires, Oculizer prefers a different mapped scene found in recent predictions and otherwise selects `ambient1`. This safety replacement bypasses the active profile's transition admission but is recorded in its internal budgets; the expired prediction holds that one replacement until a genuinely different prediction arrives, and the expired target cannot immediately re-enter. Silence, announcement, and manual overrides are exempt.

A scene can override the global duration by declaring a positive duration in its artistic definition under `scenes/`:

```json
{
  "name": "white_flicker",
  "max_duration_seconds": 8,
  "lights": []
}
```

When `max_duration_seconds` is absent, the global value is used. The example only illustrates the field; retain the scene's real `lights` definition.

The supplied v6 scene set applies an eight-second duration base to every scene with an active strobe declaration. Non-strobing racer/alternating effects and selected high-energy scenes use a 15-second base. Calmer v6 scenes inherit the global 40-second base. The ±30% variation makes these safety-oriented rotations less mechanical; tune the bases in the corresponding `scenes/<name>.json` file.

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

### Real-time audio controls

In addition to selecting scenes, Oculizer can continuously send four normalized values from `0` to `1` for use inside QLC+:

| OSC path | Signal | Typical QLC+ use |
| --- | --- | --- |
| `/oculizer/master` | Overall audio RMS/level | Grand master, scene brightness, or a dimmer group |
| `/oculizer/bass` | Low-frequency energy | Bass pulses, fixture intensity, or effect speed |
| `/oculizer/mid` | Mid-frequency energy | Color, movement, or secondary intensity |
| `/oculizer/high` | High-frequency energy | Sparkle, strobe depth, or fast effects |

Enable or disable the overall level under `audio.master_modulation`, and the frequency controls under `audio.frequency_modulation` in `config/oculizer.json`:

```json
"master_modulation": {
  "enabled": true
},
"frequency_modulation": {
  "enabled": true,
  "bands": {
    "bass": { "enabled": true },
    "mid":  { "enabled": false },
    "high": { "enabled": false }
  }
}
```

Set `frequency_modulation.enabled` to `false` to disable all three bands, or change one band's `enabled` value independently. The supplied configuration enables `master` and `bass` but leaves `mid` and `high` disabled. Keep the other tuning fields already present in the configuration when editing these abbreviated examples.

On the QLC+ side, enable an OSC input listening on the same port as `config/qlc_config.json` (`7700` by default), create a Virtual Console slider for each signal you want to use, and assign its external input with QLC+'s input auto-detection while Oculizer is running. Then connect that slider to the desired master, dimmer, fixture group, or effect parameter. The OSC paths can be changed under `controls` in `config/qlc_config.json`. These continuous controls apply to the QLC+ OSC backend; direct Enttec output does not consume them.

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

<img width="2409" height="480" alt="image" src="https://github.com/user-attachments/assets/9d2d9bd2-8fa4-41ba-85d0-bef644d62b63" />


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
python3 oculizerctl.py dynamic-controls
python3 oculizerctl.py dynamic-control responsive
python3 oculizerctl.py dynamic-control normal
python3 oculizerctl.py dynamic-control calm
python3 oculizerctl.py dynamic-control off
```

The default socket is `/tmp/oculizer-<uid>.sock`. Start the application with `--control-socket PATH` to use another path, then place `--socket PATH` before the `oculizerctl.py` subcommand. Use `--no-control-socket` to disable external control.

`pause` suspends automatic processing and activates blackout. `auto` clears pause or manual override and resumes automatic operation. `scene NAME` forces a configured logical scene. Live changes last until the application restarts and do not rewrite configuration files.

## Dynamic control
The engine responsivity can be controlled using a dynamic parameter. This is used when you want to calm down the scene or unleash a very color full show. 
Use `--dynamic-control PROFILE` at startup, press `l` in the interactive interface, or run `oculizerctl dynamic-control PROFILE` from another terminal. The active profile is shown in the status area and changes received through the control socket appear there automatically.

Named profiles can be added, adjusted, or removed under `control.dynamic_controls` in `config/oculizer.json`. An empty object is valid and leaves `off` as the only available profile. Selecting a named profile applies its complete tuple, including its cache value, so it takes precedence over `--scene-cache-size` while active. Starting without `--dynamic-control` selects the reserved `off` state: it restores the startup cache value and disables transition filtering.

The following values are recommended starting points when those named profiles are configured:

| Profile | Cache | Throttle | Rate limit | Behavior |
| --- | ---: | ---: | ---: | --- |
| `responsive` | `3` | `4/1` | `10/10` | Fast response and generous bursts |
| `normal` | `15` | `2/4` | `4/15` | Stable general-purpose behavior with restrained transitions |
| `calm` | `35` | `Off` | `2/20` | Very strong smoothing with at most two changes per rolling 20-second window, without a fixed recovery cadence |
| `off` | startup value | `Off` | `Off` | Restore startup smoothing and leave predictions unrestricted |

`off` is the least restricted profile, but it is not always the fastest. It keeps the normal startup cache (`10` by default), whereas `responsive` uses a shorter cache (`3`) and can therefore react sooner. In exchange, `responsive` retains generous safeguards against unusually rapid or sustained changes. If the predictions are already stable enough to remain below those safeguards, `off` and `responsive` can select the same scenes and produce the same number of changes.

The comparison below demonstrates that case: both produce 72 changes. Its raw predictions are sampled every two seconds, so they do not arrive quickly enough to reach the generous `responsive` limit of ten changes per ten seconds. The shorter cache can still move a transition by a fraction of a second, but that small difference is difficult to see when the complete five-minute track is compressed into one graph. A more unstable track or a shorter inference hop will make the protection provided by `responsive` more visible.

### Visual comparison

The following image replays the same RMS curve and raw v6 predictions through the neutral `off` state and every dynamic-control profile currently declared in `config/oculizer.json`. A colored dot or gray symbol marks the active scene at startup and each subsequent transition. The comparison is illustrative rather than a live-performance benchmark: model inference is sampled every two seconds for practical documentation generation, while routing, cache smoothing, silence, speech, scene-duration, rate, and throttle behavior are simulated every 0.1 seconds.

![Dynamic-control profiles compared on fascination.wav](docs/dynamic_control_comparison.svg)

Regenerate the image after changing a predictor, scene rules, or dynamic-control profiles:

```bash
python3 scripts/render_dynamic_control_comparison.py \
  tests/fascination.wav \
  --output docs/dynamic_control_comparison.svg \
  --prediction-hop-seconds 2
```

The script accepts any PCM WAV and automatically creates one panel for `off` plus one panel for every configured profile. Use `--config`, `--predictor-version`, `--prediction-hop-seconds`, `--simulation-step-seconds`, `--off-cache-size`, `--seed`, or `--width` when a different comparison is required. Smaller prediction hops are closer to the intended live inference cadence but take proportionally longer to compute.

### QLC+ buttons

QLC+ 5 Virtual Console buttons can call the installed client through script functions such as:

```javascript
Engine.systemCommand("/usr/local/bin/oculizerctl dynamic-control responsive");
Engine.systemCommand("/usr/local/bin/oculizerctl dynamic-control normal");
Engine.systemCommand("/usr/local/bin/oculizerctl dynamic-control calm");
Engine.systemCommand("/usr/local/bin/oculizerctl dynamic-control off");
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
