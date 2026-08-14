# Oculizer development guide

This document is the project's technical source of truth. It describes the current architecture, the QLC+ 5 target, the implementation roadmap, recorded decisions, and the rules that developers and coding agents must follow when continuing the work.

## Documentation language policy

All repository documentation must remain in English. This applies to `README.md`, `DEVELOPMENT.md`, code comments added as documentation, configuration guidance, roadmap entries, and implementation log entries.

User requests and development conversations may be written in French or any other language. Their language must not be copied into repository documentation: translate the relevant information into English before updating either Markdown file. Do not switch the documentation language to match the language of a request.

## QLC+ Native-only product policy

Decision accepted on 2026-08-14: OculizerQLC will specialize around the QLC+ 5 native protocol. Phase 9 removes direct Enttec/DMX, OSC, and QLC+ WebSocket output from this repository. QLC+ remains the sole owner of fixtures, universes, functions, Virtual Console behavior, and final DMX output. Its Web Server may continue running for other applications, but Oculizer will neither configure nor consume it.

Coding agents must apply these rules from Phase 9 onward:

- implement lighting behavior once through the native QLC+ client; do not reintroduce a selectable backend or transport-parity layer;
- keep audio capture, fast events, inference, automatic routing, manual override, dynamic controls, normalized master/bass/mid/high production, runtime control, interactive/headless operation, WAV testing, and model selection independent from native network I/O;
- discover widget IDs, types, actions, ranges, Frames, and function associations from the active QLC+ project; persist only stable logical captions and explicit caption overrides;
- keep network queues and retained intentions bounded so QLC+ failure or authorization wait never blocks audio or inference;
- treat `--dry-run` as a complete network-free application mode rather than a transport-specific option;
- update `README.md` and this guide in the same change, including service and Raspberry Pi commands;
- warn before adding CPU- or memory-intensive behavior on the Raspberry Pi 5 target, and prefer deletion/factoring over compatibility shims;
- do not retain obsolete Enttec, OSC, WebSocket, fixture-profile, DMX-scene, or fallback code merely to preserve old command lines. This is an intentional breaking migration with an explicit rollback commit.

The historical transport-parity work remains valuable validation evidence below, but it is no longer a forward requirement.

## Product objective

Build a specialized system in which:

- Oculizer captures and analyzes music;
- the model predicts a semantic scene;
- the operator can manually override a scene;
- Oculizer sends scene-button intentions and a small number of continuous modulations through the QLC+ native protocol;
- QLC+ 5 owns lighting functions, fixture patching, and DMX output.

During development, Oculizer and QLC+ run on the same Mac. In production, both run locally on a Raspberry Pi 5 with Raspberry Pi OS. The native endpoint therefore defaults to `127.0.0.1:9998`, while remaining configurable.

## Verified pre-unplug checkpoint

Commit `ca9b38e` is the accepted, pushed rollback point before Phase 9. It has 224 passing automated tests and live Raspberry Pi validation for QLC+ native authorization, project inventory, scene buttons, Frame/SoloFrame behavior, enabled sliders, service and interactive runtime control, QLC+ restart/reconnect, and automatic control-socket discovery. The lists and diagrams below describe legacy capabilities still present at that checkpoint; Phase 9 deliberately removes the non-native subset.

### Implemented

- single-stream and dual-stream audio capture;
- mel-scaled FFT and adaptive normalization;
- prediction with the supported `v4` and experimental `v5` predictors, both including speech-aware AudioSet scores;
- configurable prediction smoothing cache;
- JSON scene loading and reloading;
- JSON fixture profiles;
- conversion of scene definitions into DMX value arrays;
- stateful effects and orchestrators;
- automatic curses interface;
- standalone and integrated manual selector;
- automatic following and manual override in the integrated selector;
- profile-specific forced substitutions and fallbacks;
- `--test` mode without a DMX controller or FFT stream;
- DMXKing/Enttec USB DMX Pro serial controller;
- startup progress messages.

### Historical limitations at the original baseline

- QLC+ state feedback or synchronization;
- external runtime control of the headless service;
- Raspberry Pi production service units.

These original limitations were subsequently implemented where recorded in the roadmap and implementation log. They are retained only as historical context.

## Legacy direct-DMX architecture scheduled for deletion

```text
oculize.py / toggle.py
        │
        ├── SceneManager
        │     ├── scenes/*.json
        │     └── profiles/profile_fallbacks.json
        │
        └── Oculizer thread
              ├── audio capture
              ├── EfficientAT/PCA/k-means predictor
              ├── process_light()
              ├── orchestrators and effects
              └── EnttecProController
                        └── serial port → complete DMX frame
```

Important entry points:

| File | Responsibility |
| --- | --- |
| `oculize.py` | CLI, automatic UI, integrated manual mode |
| `toggle.py` | standalone and reusable manual selector |
| `oculizer/light/control.py` | main thread, audio, prediction, and lighting output |
| `oculizer/light/mapping.py` | scene configuration and FFT to DMX conversion |
| `oculizer/light/effects.py` | stateful effects |
| `oculizer/light/orchestrators.py` | fixture-group coordination |
| `oculizer/light/enttec_controller.py` | serial DMX transport |
| `oculizer/scenes/scene_manager.py` | loading, compatibility, and fallbacks |
| `profiles/*.json` | installation fixture inventory |
| `scenes/*.json` | artistic behavior and reactivity |

### Output-related technical debt

`Oculizer` imports `EnttecProController` directly. `_load_controller()` creates internal fixture objects that write directly to `controller.dmx_data` and call `_send_dmx_packet()`. `turn_off_all_lights()` also manipulates this private buffer.

Consequences:

- transport and fixture models are coupled;
- multiple fixtures can cause multiple complete DMX frames per cycle;
- the global `n_channels` dictionary is used instead of profile `n_channels` values;
- replacing every DMX write with an OSC message would preserve the coupling and generate excessive traffic.

The migration must introduce an abstraction at the intent level (`activate_scene`, `set_parameter`, and `blackout`) instead of merely replacing the serial port with UDP.

## Accepted Phase 9 target architecture

```text
Live input or WAV
        │
        ▼
Audio analysis ──► silence / announcement priority events
        │
        ├────────► artistic scene inference (v4/v5/v6/future v7)
        │
        └────────► normalized master / bass / mid / high
                         │
                         ▼
                automatic/manual router
                         │
                         ▼
             QLC+ 5 native client (TCP 9998)
                         │
                         ▼
       QLC+ Virtual Console → Functions → patched DMX output
```

QLC+ must own:

- fixture and universe patching;
- Scenes, Chasers, and other Functions;
- the Virtual Console;
- final rendering and DMX output.

Oculizer must own:

- audio capture;
- prediction;
- operator override;
- conversion of selected audio features into normalized values;
- mapping between logical names and optional QLC+ caption overrides.

### Historical OSC contract — superseded by Phase 9

The following contract documents the migration path that proved the logical-intention architecture. It must not be carried into the native-only runtime or its configuration.

The exact contract must be tested in QLC+ before it is frozen. Proposed baseline:

```text
/oculizer/system/heartbeat       float 0.0 or 1.0
/oculizer/system/blackout        float 0.0 or 1.0
/oculizer/scenes/<scene>         float 0.0 or 1.0
/oculizer/master                 float 0.0 to 1.0
/oculizer/audio/bass             float 0.0 to 1.0
/oculizer/audio/mid              float 0.0 to 1.0
/oculizer/audio/high             float 0.0 to 1.0
/oculizer/effects/speed          float 0.0 to 1.0
/oculizer/effects/strobe         float 0.0 to 1.0
```

Proposed defaults:

```json
{
  "host": "127.0.0.1",
  "port": 7700,
  "send_rate_hz": 25,
  "change_threshold": 0.01
}
```

OSC paths will be assigned to external controls in the QLC+ Virtual Console. They must not contain internal numeric QLC+ IDs when stable logical names are possible.

## Historical roadmap and accepted validation gates

A step is complete only after implementation, relevant automated tests, and real QLC+ validation where required.

Phases 0 through 8b explain how the current rollback checkpoint was reached. They are not parallel forward paths. Any unchecked item that remains useful has been merged into the single Phase 9 roadmap below; otherwise it is superseded by the native-only decision.

### Phase 0 — Minimal OSC contract and QLC+ workspace

Status: **complete — validated with QLC+ 5.2.2 on macOS**

- [x] create a QLC+ 5 development workspace;
- [x] patch a local OSC input;
- [x] create an `Oculizer` input profile;
- [x] assign `/test` to a Virtual Console button;
- [x] attach the button to a minimal lighting function;
- [x] manually verify press and release values;
- [x] provide a dependency-free OSC test sender;
- [x] test OSC packet encoding and local UDP delivery;
- [x] record the temporary test target as `/test` on `127.0.0.1:7700`, using float values `1.0` and `0.0`;
- [x] record the behavior observed in QLC+ 5.2.2;
- [x] store the workspace and input profile in the repository.

Repository-side test command:

```bash
python scripts/send_osc_test.py --pulse 1
```

The `/test` path and script are intentionally limited to milestone validation. Do not place `/test` in the application namespace or turn the script into the application client; phase 1 must introduce the reusable client under `oculizer/light/`.

Exit criterion: an external OSC command starts and stops the test function deterministically.

### Phase 1 — Standalone OSC client

Status: **complete — validated with QLC+ 5.2.2 on macOS**

Proposed files:

```text
oculizer/light/osc_client.py
config/qlc_config.json
tests/test_osc_client.py
```

- [x] choose a dependency-free OSC implementation compatible with macOS and Linux ARM64;
- [x] implement `send`, `press`, `release`, `set_level`, `blackout`, and `close`;
- [x] validate and clamp values;
- [x] add dry-run mode;
- [x] ensure a missing QLC+ instance cannot block the audio loop;
- [x] test against a local UDP receiver;
- [x] test against QLC+ 5 on macOS.

Exit criterion: the same client passes UDP tests and controls the QLC+ button created in phase 0.

### Phase 2 — Interchangeable backend

Status: **complete — validated on macOS**

- [x] define the `LightingBackend` protocol;
- [x] wrap the current output in `EnttecBackend`;
- [x] create `QLCOscBackend`;
- [x] add `--output enttec|qlc-osc`;
- [x] add `--osc-dry-run` and host/port overrides;
- [x] never detect serial ports in OSC mode;
- [x] preserve existing Enttec behavior.

Minimum intent API:

```python
activate_scene(scene_name)
deactivate_scene(scene_name)
set_parameter(name, value)
blackout(enabled=True)
close()
```

Exit criterion: both backends start, stop, and pass their tests without opening hardware belonging to the other backend.

### Phase 3 — Manual scenes from `toggle.py`

Status: **complete — validated with QLC+ on macOS**

- [x] add a scene-to-OSC-path mapping;
- [x] make Enttec fixture profiles optional and unused in `qlc-osc` mode;
- [x] replace QLC+ profile filtering with a logical scene configuration containing no fixtures, DMX channels, or hardware addresses;
- [x] keep the default QLC+ launch free of a material-profile choice;
- [x] isolate scene activation from curses so the same command path can be called by a future headless service;
- [x] deactivate the previous scene before activating the new one;
- [x] avoid duplicate commands when selection does not change;
- [x] define behavior for an unmapped scene;
- [x] implement `off` and blackout behavior;
- [x] validate keyboard navigation, searching, and reloading.

Exit criterion: a complete manual session controls QLC+ without loading an Enttec fixture profile or opening a DMX interface, and the scene-command layer has no dependency on curses.

### Phase 4 — Automatic prediction

Status: **complete — validated with live audio and QLC+ on macOS**

- [x] connect predicted transitions to the same backend;
- [x] preserve smoothing and fallbacks;
- [x] preserve manual override;
- [x] log the requested scene, fallback, and activated QLC+ scene;
- [x] send nothing when the logical state has not changed;
- [x] provide a non-interactive application mode that starts prediction and QLC+ control without curses or terminal input;
- [x] handle `SIGTERM` and `SIGINT` with the same safe shutdown path.

Exit criterion: automatic transitions and return from override are consistent in QLC+.

### Phase 4b — Speech-aware semantic routing

Status: **complete — validated with live speech, music, and silence on macOS**

- [x] retain the 527 AudioSet logits already returned by EfficientAT instead of discarding them after embedding extraction;
- [x] aggregate relevant `Speech`, male/female/child speech, and speech-noise labels into a speech score;
- [x] treat `Singing` as music so vocals do not trigger announcement mode;
- [x] compare speech and music confidence using configurable thresholds and a minimum confidence margin;
- [x] require a configurable minimum speech duration and release duration to prevent rapid mode changes;
- [x] make the speech/announcement scene user-configurable;
- [x] define mixed speech-and-music behavior, initially preserving the current scene when confidence is ambiguous;
- [x] give manual override priority over speech routing, speech routing priority over ordinary music-scene prediction, and silence routing its explicitly documented priority;
- [x] log concise speech/music scores and routing decisions without logging every inference frame;
- [x] validate speech, music, and silence transitions with the local QLC+ workspace; singing and ambiguous mixtures retain conservative score-based routing.

Proposed routing order:

```text
manual override
    → configured silence policy
    → dominant clean speech / announcement scene
    → music and singing / normal scene prediction
    → ambiguous speech plus music / preserve current scene
```

Implementation decision: reuse the pretrained EfficientAT AudioSet classification head already evaluated by the current model. Do not add a second large model unless validation proves the existing logits insufficient.

Exit criterion: spoken announcements reliably select the configured scene without classifying singing as speech or destabilizing mixed music playback.

### Phase 5 — First continuous modulation

Status: **complete — validated with live audio and QLC+ on macOS**

- [x] begin only with `/oculizer/master`;
- [x] normalize into `[0.0, 1.0]`;
- [x] rate-limit output, initially targeting 20–30 Hz;
- [x] add a change threshold and smoothing;
- [x] send a safe value during shutdown;
- [x] validate latency and update regularity during live operation without observable performance issues.

Exit criterion: a QLC+ slider follows audio without flicker or significant overhead.

### Phase 6 — Advanced modulations

Status: **complete — bass, mid, and high validated with live audio and QLC+ on macOS**

- [x] add configurable bass, mid, and high extraction while enabling only bass for the first validation slice;
- [x] defer speed and strobe because the reference workspace does not consume them;
- [x] document the source, smoothing, and QLC+ destination of every value;
- [x] transmit only the validated master, bass, mid, and high controls.

First validation slice:

- [x] reuse the existing Mel spectrum without adding another FFT;
- [x] extract bass from 35–180 Hz, remove a slowly adapting baseline, and normalize transient energy to `[0.0, 1.0]`;
- [x] send `/oculizer/bass` at no more than 25 Hz with smoothing and change suppression;
- [x] send a safe zero at silence and shutdown;
- [x] map `/oculizer/bass` to one QLC+ control and validate its visual use with live audio;
- [x] calibrate the reference bass transient response from live input.

### Phase 7 — Robustness and state

Status: **complete — validated with live QLC+ lifecycle and restart tests on macOS**

- [x] define startup and shutdown policy for controlled functions;
- [x] implement emergency blackout;
- [x] defer heartbeat because QLC+ has no configured watchdog consumer and coupled service restart is cheaper;
- [x] periodically refresh absolute modulation values and prohibit blind retries of toggle actions;
- [x] defer OSC feedback by choosing coupled QLC+/Oculizer lifecycle recovery for production;
- [x] keep logs and metrics concise;
- [x] validate graceful interruption and QLC+ restart behavior, and document the hard-kill boundary.

### Phase 7b — Pluggable audio source and looped WAV input

Status: **complete — validated with looped WAV input and OSC dry-run on an audio-less Linux host**

Objective: allow the complete prediction and modulation pipeline to run on a host with no capture device by substituting a WAV file for live input, while establishing a small audio-source boundary that can support other source types later.

- [x] define a minimal `AudioSource` protocol for starting, stopping, joining, and delivering timestamped or real-time-paced audio chunks;
- [x] retain the existing `sounddevice` capture behavior behind a lazy device boundary;
- [x] implement a WAV-file source with the standard-library `wave` decoder;
- [x] add `--audio-file PATH` to select file input and loop it continuously by default for this development mode;
- [x] validate the WAV path, PCM format, channel layout, and readable audio frames before predictor startup;
- [x] convert channels and resample through the same established analysis path used by live capture;
- [x] pace file chunks against a monotonic clock so prediction, silence detection, speech routing, and continuous modulation operate at real-time speed;
- [x] reset prediction smoothing, semantic scores, transient baselines, and queued audio at each loop boundary;
- [x] preserve normal signal handling and bounded shutdown while sleeping or crossing a loop boundary;
- [x] support the interactive and headless applications plus OSC dry-run mode with no `sounddevice` device available;
- [x] add unit tests for source selection, PCM validation, channel conversion, looping, loop-boundary notification, and hardware isolation;
- [x] validate bounded frame streaming and real-time pacing without another model, analysis pass, queue, or unbounded file allocation.

Initial scope is intentionally limited to live capture through `sounddevice` and local PCM WAV playback. MP3 decoding and public network streams are not part of phase 7b. The abstraction may later accept implementations such as `SoundDeviceAudioSource`, `WavFileAudioSource`, and `OnlineStreamAudioSource`, but no network client, reconnect logic, compressed-audio decoder, or speculative dependency may be added until a concrete requirement is approved.

Design constraints:

- source implementations deliver audio only; they must not duplicate FFT, model inference, routing, or lighting logic;
- disk reads and pacing must remain outside the real-time analysis callback;
- buffering must be bounded, and a large WAV file must not be loaded completely into memory merely to enable looping;
- file mode must not import or initialize `sounddevice`, allowing it to work on hosts with no PortAudio library or capture device;
- source selection must be explicit and mutually exclusive: a configured live device and `--audio-file` cannot both control the same analysis stream;
- future compressed-file or online sources must reuse this boundary and meet the same chunk, timing, shutdown, and bounded-buffer requirements.

Exit criterion: `oculize.py --audio-file <file.wav> --output qlc-osc --osc-dry-run` runs the existing automatic scene, speech, silence, master, and frequency-band pipelines at real-time pace on an audio-less host, loops cleanly, and stops safely without importing or opening `sounddevice`.

### Phase 8a — Shared local runtime control and dynamic-control profiles

Status: **complete — implementation and live QLC+ operator validation accepted**

- [x] host the same configurable Unix-domain control socket from either `oculize.py` or `oculizer_service.py`, with exactly one runtime owning the socket at a time;
- [x] add `oculizerctl` commands for `auto`, `pause`, forced logical scenes, status, profile discovery, and atomic dynamic-profile selection;
- [x] expose configurable named transition profiles suitable for four operator buttons: `responsive`, `normal`, `calm`, and the reserved neutral `off` state;
- [x] make `oculizerctl dynamic-control <name>` apply cache, rate, and throttle as one validated atomic update, and add `oculizerctl dynamic-controls` to list resolved values;
- [x] keep profile values in normal Oculizer configuration rather than hard-coding artistic choices in the client or QLC+ workspace;
- [x] validate the documented QLC+ 5 `Engine.systemCommand` profile buttons against a live Virtual Console;
- [x] keep the interactive main-screen active profile synchronized with changes received through the socket on its normal render cadence;
- [x] add a monotonically increasing policy revision so an `l` editor opened before an external change cannot silently overwrite newer socket values;
- [x] make all updates atomic and thread-safe inside `AutomaticSceneRouter`, with validation before mutation and explicit reset semantics for cache, rolling-window history, and throttle credits;
- [x] validate malformed commands, concurrent clients, permissions, stale sockets, acknowledgements, and client disconnects locally;
- [x] document how a development terminal discovers the interactive runtime socket and how QLC+ buttons invoke dynamic-control profiles.

Exit criterion: while the interactive application or headless runtime is running, a second terminal and QLC+-originated actions can select modes, scenes, and named dynamic-control profiles; every active interactive display reflects external changes without restart or split state.

### Phase 8a.1 — Fast event detection and scene-transition separation

Status: **complete — automated/offline and live QLC+ operator validation accepted**

Objective: make detection latency independent from the operator's scene-change-frequency policy. The v6 predictor remains the source of artistic scene candidates and keeps its training-compatible four-second window. A lightweight priority path detects silence, audio recovery, and speech transitions without waiting for a v6 artistic classification. `responsive`, `normal`, and `calm` may accept ordinary music scenes at different rates, but they must observe priority events and stable candidate changes at the same latency.

Architecture and ownership:

- [x] add small timestamped priority-event records for `SUDDEN_SILENCE`, `AUDIO_RESUME`, `SPEECH_START`, and `SPEECH_END`;
- [x] feed the detector from the existing shared audio-source boundary without opening another capture stream or performing routing inside an audio callback;
- [x] send events to the shared `AutomaticSceneRouter`; the detector never issues QLC+, Enttec, or scene commands directly;
- [x] use the same detector, router, and runtime-control state in interactive and headless execution; curses remains presentation and operator control only;
- [x] preserve v6's EfficientAT/MFCC/scaler/PCA/KMeans artistic pipeline and make four seconds its code and configuration default where no explicit compatible value is supplied.

Low-cost silence slice:

- [x] reuse the existing RMS threshold, duration, and resume hysteresis instead of maintaining a second energy/baseline state machine;
- [x] keep silence evaluation bounded and allocation-free, using the RMS value already produced by audio capture;
- [x] route confirmed silence immediately after its configured duration and resume from fresh audio state without waiting for v6;
- [x] omit energy-rise/drop-triggered semantic inference: it added scheduling complexity and burst CPU load without being required for the accepted one-to-two-second response target.

Short semantic slice — accepted Raspberry Pi design:

- [x] keep exactly one EfficientAT model instance and one serialized inference execution path; do not run concurrent semantic and artistic inference;
- [x] run a semantic-only inference over a `2.0s` rolling window; evaluation on `mixvoicemusic.wav` showed shorter windows were too unstable for EfficientAT speech/music confidence;
- [x] use one fixed `1.0s` cadence both during music and announcements, targeting a predictable one-to-two-second response without burst inference;
- [x] short semantic work extracts only speech, singing, and music scores and skips MFCC, scaler, PCA, KMeans, and scene mapping;
- [x] do not create a second model instance, process, permanent high-rate inference loop, or new VAD dependency in this phase;
- [x] never interrupt a native inference already in progress and never build a separate semantic request queue;
- [x] retain configurable speech threshold, music margin, minimum duration, and release duration, targeting confident `SPEECH_START` routing within one to two seconds.

Router and dynamic-control semantics:

- [x] distinguish priority routes from ordinary music routes explicitly;
- [x] make confirmed silence and speech-start routes bypass scene cache, rate limit, throttle, maximum duration, and scene re-entry delay while retaining their own event-specific hysteresis;
- [x] treat audio resume and speech end as immediate release from the priority state, then require fresh candidate state before returning to music;
- [x] keep one small prediction-stability cache across dynamic-control profiles instead of using a large cache to make a show calm;
- [x] tune from measured tests to `responsive` cache `3`, rate `10/10`, throttle `4/1`; `normal` cache `15`, rate `3/15`, throttle `2/4`; and `calm` cache `5`, rate `2/20`, throttle `1/6`;
- [x] track raw prediction, latest prediction, stable candidate scene, and active scene as separate state;
- [x] when policy rejects an ordinary candidate, retain only the newest valid stable candidate and reconsider it when admission becomes available; never queue obsolete scene transitions;
- [x] distinguish in status and logs between a stable candidate deliberately retained by policy, a priority route, and an accepted ordinary transition; raw and stable values expose candidates still converging.

Configuration and observability:

- [x] strictly validate the semantic window and fixed interval under `audio.fast_detection.speech`;
- [x] expose active scene, raw prediction, stable candidate, last priority event, speech score, music score, route reason, block reason, and active dynamic-control profile;
- [x] ensure selecting a dynamic-control profile changes no detector threshold, window, or semantic polling frequency;
- [x] preserve existing silence thresholds/routes and speech thresholds/hysteresis as the single routing policy.

Implementation slices and validation gates:

1. Add validated semantic scheduling configuration, small priority-event records, and status fields.
2. Integrate priority silence/resume routing and prove it bypasses every ordinary policy.
3. Add a fixed-rate serialized semantic check using the existing EfficientAT instance, then integrate speech-start/end priority routing.
4. Separate stable candidates from active scenes, reduce profile caches, retain only the latest rejected candidate, and add block/reason observability.
5. Validate on synthetic signals and representative WAV/concert inputs before live QLC+ acceptance.

Automated acceptance must prove:

- [x] speech and silence bypass `responsive`, `normal`, and `calm` ordinary limits;
- [x] calm observes a stable candidate immediately while intentionally retaining the active scene;
- [x] responsive accepts stable candidates rapidly and alternating prediction jitter does not churn scenes;
- [x] rejected candidates do not reappear later as a stale queue;
- [x] speech release clears cached artistic evidence and resumes from fresh prediction state;
- [x] profile changes cannot alter fast-detector behavior because runtime policy mutation is confined to cache/rate/throttle fields;
- [x] v6 continues using a four-second artistic classification window;
- [x] interactive, headless, QLC+ OSC, Enttec, WAV, live audio, runtime control, and shutdown behavior remain covered.

Embedded-system impact gate: this phase targets a Raspberry Pi 5 with 8 GB RAM. The approved design avoids duplicate model memory and concurrent inference; the material risk is CPU scheduling and latency. Record short semantic inference time, v6 inference time, queue depth, process RSS, sustained CPU, temperature, and missed audio deadlines on macOS and later on Raspberry Pi. All queues and logs must remain bounded, and semantic work must degrade by delaying or replacing an obsolete request rather than accumulating work.

Exit criterion: all dynamic-control profiles detect priority events at essentially the same latency; silence follows its configured RMS hysteresis without waiting for v6; a confident music-to-speech transition selects `announcement` within one to two seconds; v6 retains its four-second artistic window; calm substantially reduces ordinary activations without hiding stable candidates; and status clearly explains every detected, blocked, priority, and accepted route.

Operator acceptance on 2026-08-12 confirmed the expected live behavior across `responsive`, `normal`, and `calm`: priority `silent` and `announcement` routing remains responsive and independent from ordinary transition filtering, music resumes from fresh prediction state, and `calm` reduces ordinary scene changes without delaying priority events. Raspberry Pi CPU, temperature, and missed-deadline measurements remain part of Phase 8b and do not block this completed phase.

#### Pre-next-phase decision checkpoint — possible fast-detection rollback

Status: **complete — retain the current fast-detection design; Raspberry Pi validation accepted**

The current Phase 8a.1 design deliberately separates priority semantic events from ordinary artistic prediction. Music scenes still come from the v6 predictor using its training-compatible four-second window. Sustained silence is detected from the already available RMS value using a configurable entry threshold, minimum duration, and higher resume threshold. Speech uses the existing single EfficientAT instance through a serialized semantic-only pass over the latest two seconds of audio at a fixed one-second cadence. Confirmed `silent` and `announcement` routes bypass the ordinary scene cache, rate limit, throttle, maximum-duration rule, and re-entry delay. On release, stale artistic evidence is cleared so music resumes only from fresh predictions. This targets a practical one-to-two-second response without a second model, concurrent inference, an additional audio stream, a new VAD dependency, or an unbounded work queue.

Before continuing beyond the current Phase 8b validation work or beginning another feature milestone, explicitly decide whether to retain this design or roll part of it back. A rollback must be surgical and must not be implemented as a broad Git revert: it must preserve the `silent` scene name, OSC/WebSocket transport parity, QLC+ mappings, runtime-control behavior, dynamic-control profiles, the v6 default model, service deployment work, and all unrelated fixes.

Rollback candidates, in increasing order of impact:

1. **Retain the current design (preferred baseline).** Keep RMS silence detection and the two-second/one-second-cadence semantic announcement path. Select this when Raspberry Pi measurements remain bounded and live false-positive/oscillation behavior is acceptable.
2. **Roll back only fast announcement detection.** Remove or disable the additional semantic-only schedule and derive speech routing from the normal four-second v6/EfficientAT pass again. Keep the lightweight RMS `silent`/resume path as a priority route. This reduces semantic inference frequency and code paths, but announcement entry can return toward the former approximately 2.5-second latency and may approach one complete artistic window in adverse alignment.
3. **Roll back all priority-path separation.** Route silence and speech only through the former slow prediction lifecycle. This is the simplest behavior but is not recommended: it re-couples safety/semantic events to artistic-window latency and dynamic scene admission, recreating the original reason for Phase 8a.1.

Use measured evidence rather than subjective code-size concerns alone. Consider rollback candidate 2 if the Raspberry Pi 5 shows sustained excessive CPU or temperature, repeated missed audio deadlines, growing audio queue depth, materially delayed v6 inference, or unacceptable `announcement` false positives/oscillation that threshold and hysteresis tuning cannot correct. Do not roll back merely because semantic inference adds some measurable load: the accepted requirement is reliable routing within one to two seconds, not minimum possible CPU at the expense of operational behavior. Candidate 3 requires a separate operator decision acknowledging the loss of priority-event latency guarantees.

The before/after decision protocol must remain reproducible:

- replay `tests/mixvoicemusic.wav` against the operator-authored `tests/mixvoicemusic.md` timeline using the same v6 model, four-second artistic window, one-second artistic hop, 0.1-second router simulation step, and seed zero;
- preserve and compare `reports/phase_8a1_baseline_mixvoicemusic.{json,svg}` and `reports/phase_8a1_post_mixvoicemusic.{json,svg}` rather than overwriting either reference;
- measure announcement entry/release latency, false `announcement` intervals, `silent` entry/resume latency, ordinary scene recovery, transition counts per dynamic-control profile, semantic and artistic inference duration, maximum audio queue depth, process RSS, sustained CPU, temperature, and missed audio deadlines;
- repeat a live voice-only, music-only, silence-to-voice, voice-to-music, and mixed voice/music validation through both OSC and WebSocket;
- require fresh artistic evidence after every priority release in every retained option, unless a future decision explicitly accepts the stale-scene flash regression;
- record the selected option, measurements, configuration, commit, and operator acceptance in this roadmap before starting the next feature phase.

Decision recorded on 2026-08-13: operator testing on the Raspberry Pi confirmed that disabling fast detection did not materially reduce the dominant CPU load. The actual bottleneck was the artistic predictor's former `0.1s` scheduling interval. After changing that interval to the accepted `1.0s` production cadence, functional behavior, priority silence/announcement response, CPU load, queue behavior, and service operation were accepted. Retain rollback candidate 1: the current fast-detection design remains in production. The historical rollback alternatives above remain decision context only and are no longer pending work.

### Phase 8a.2 — QLC+ 5 WebSocket Virtual Console backend

Status: **complete — automated and live QLC+ 5.2.2 operator validation accepted**

Objective: add a second QLC+ 5 transport selected with `--output qlc-websocket`, while retaining `--output qlc-osc` as a fully supported backend. Both transports implement activation-only scene routing and the same normalized master/bass/mid/high controls. `silent` is an ordinary configured scene route; neither transport owns a separate implicit blackout policy. Oculizer requests only the target button activation, while the QLC+ workspace owner chooses layering or exclusivity with Frames and Solo Frames. This milestone does not affect prediction, audio analysis, Enttec output, or the Phase 8a local Unix control socket used by `oculizerctl.py`.

Protocol research gate — complete before writing transport code:

- [x] read the official QLC+ 5 WebSocket API documentation applicable to the deployed QLC+ version;
- [x] inspect the matching QLC+ source in `mcallegari/qlcplus` to verify message names, payload formats, widget enumeration, button actuation, error behavior, and connection lifecycle;
- [x] record direct documentation/source references and the verified QLC+ version in the implementation log;
- [x] determine from verified behavior whether a button action is a press-only command or requires a release, and test the exact sequence automatically; visible-console confirmation remains in the manual gate;
- [x] rediscover widget IDs from the current inventory after every connection/reload; automatic reconnect is explicitly deferred and connection loss fails clearly.

First implementation slice — buttons and normalized sliders:

- [x] add `qlc-websocket` to interactive and headless output selection without changing the existing `qlc-osc` or `enttec` contracts;
- [x] place OSC and WebSocket behind the existing `LightingBackend` boundary and share the existing logical `SceneMap` resolution;
- [x] open one bounded WebSocket connection to the configured QLC+ endpoint and close it deterministically during normal shutdown and startup failure;
- [x] retrieve `/vc.json` after every connection and construct an in-memory `caption -> widget` index recursively;
- [x] discover Virtual Console sliders by normalized caption and map each normalized `0..1` control value to its advertised QLC+ range;
- [x] store optional captions or logical routing intentions in configuration, never WebSocket widget IDs; rediscover IDs after every new connection;
- [x] resolve a complete caption after normalizing case plus spaces/underscores/hyphens, without partial/fuzzy matching, and reject collisions after normalization;
- [x] fail explicitly when a requested configured caption is absent rather than silently targeting another widget, while allowing an incomplete workspace to start;
- [x] inspect every discovered button's actual QLC+ action type and emit its verified gesture: state-aware activation for Toggle/Blackout, press/release for Flash, and one press for Stop All;
- [x] treat that protocol sequence as one activation gesture, never as a request to deactivate the previously commanded scene;
- [x] stop explicitly toggling the previous scene during ordinary scene changes in both QLC+ transports; allow Frames to layer and Solo Frames to enforce exclusivity;
- [x] preserve configurable `silent`, `announcement`, and fallback routing; WebSocket resolves every route by caption and derives behavior from QLC+, while OSC alone consumes `OSCaction` and `OSCPath`;
- [x] define `active_scene` for QLC+ transports as the last scene command successfully issued by Oculizer;
- [x] avoid sending an implicit scene-deactivation or blackout command during shutdown;
- [x] provide a WebSocket dry-run that validates configuration, logs intended captions, and opens no network connection;
- [x] support configuration reload by parsing new routing and replacing widget inventory from `/vc.json` before replacing the active scene map;
- [x] contain synchronous bounded protocol parsing, connection state, and transport errors outside audio callbacks with no work queue;
- [x] fail clearly on connection loss; automatic reconnect is an explicitly documented limitation of this slice.

Explicitly out of scope for this milestone:

- XY pads, Cue Lists, direct function control, fixture/channel editing, and other advanced QLC+ WebSocket capabilities;
- replacement or removal of OSC;
- changes to Enttec, audio sources, FFT, prediction models, scene-selection algorithms, or the Phase 8a Unix-domain control protocol;
- persistent widget IDs or speculative protocol messages not verified against QLC+ 5 documentation or source.

Automated validation gate — no live QLC+ dependency:

- [x] test widget inventory parsing, caption lookup, unique-caption enforcement, missing captions, malformed messages, and all supported discovered button actions;
- [x] test the verified button command sequence and last-command transitions with a deterministic fake WebSocket peer;
- [x] prove ordinary scene changes emit one target activation and no previous-scene deactivation in both OSC and WebSocket intent tests;
- [x] test `silent`, `announcement`, fallback, blackout policy, clean close, and connection failure; reload is transactional and live rediscovery remains in the manual gate;
- [x] prove dry-run opens no socket and emits the intended logical/caption actions;
- [x] run regression coverage showing OSC transport, Enttec, automatic routing, and `oculizerctl.py` remain operational after the intentional QLC+ activation-only routing change;
- [x] compile and exercise CLI parsing plus backend construction for interactive, headless, and standalone entry points with the new output choice.

Manual QLC+ 5 validation gate:

- [x] connect to the real QLC+ 5.2.2 instance and confirm widget discovery after opening the current workspace;
- [x] confirm every configured scene caption resolves to exactly one normalized button caption and collision failures are actionable;
- [x] confirm in an ordinary Frame that successive Oculizer commands can leave functions layered, and in a Solo Frame that activating a new button stops the previous function and updates visible button state;
- [x] confirm explicit `silent`, `announcement`, fallback, reload, and shutdown behavior without relying on implicit previous-scene deactivation;
- [x] restart QLC+ or reload the workspace and prove that rediscovery replaces stale widget IDs;
- [x] run the same representative scene sequence through OSC and WebSocket and compare functional lighting behavior;
- [x] document the validated QLC+ build, known limitations, files changed, tests added/executed, and recommendations for future advanced-widget support.

Embedded-system impact gate: measure idle connection cost, message latency, memory, reconnect behavior, and log volume. The backend must add no audio-analysis work and must remain suitable for the Phase 8b Raspberry Pi 5 service design.

Exit criterion: `--output qlc-websocket` discovers Virtual Console buttons by unique caption and both QLC+ transports issue activation-only scene intentions, allowing ordinary Frames to layer functions and Solo Frames to enforce exclusivity, while special routes, fallback, reload, dry-run, shutdown, OSC/Enttec regressions, and non-persistence of widget IDs are validated.

Operator acceptance on 2026-08-12 confirmed the complete manual gate on QLC+ 5.2.2: configured captions resolve correctly, ordinary and Solo Frames retain their intended layering/exclusivity responsibilities, `silent`, `announcement`, fallback, reload, shutdown, and widget rediscovery behave as specified, and representative OSC and WebSocket scene sequences are functionally equivalent. The validated `master` and `bass` sliders cover the controls enabled by the reference configuration; `mid` and `high` remain available but require corresponding QLC+ widgets before operators enable them. Automatic reconnect, authenticated WebSocket access, and advanced widgets remain documented future extensions rather than completion requirements for this phase.

QLC+ action policy: `OSCaction` and `OSCPath` are consumed exclusively by the OSC backend. WebSocket ignores both fields, resolves every logical route by `caption` (defaulting to the logical name), and derives the correct gesture from the actual QLC+ button action type: Toggle and Blackout are state-aware activations, Flash is a press/release, and Stop All is one momentary press. Persistent activation errors must be logged once per distinct error rather than on every audio update tick. Obsolete routing keys `action` and `path` are rejected with a migration error to prevent cross-transport ambiguity.

The logical scene name `silent` has no special transport action. Its shipped configuration explicitly uses `OSCaction: "pushButton"` with its configured `OSCPath`; this means one `1.0` press, the configured pulse delay, then one `0.0` release. WebSocket behavior remains fully discovered from QLC+. The former `OSCaction` values `off` and `toggle` are rejected as semantically misleading. Keep `OSCaction` explicit in the reference configuration so future gestures such as held push, multi-press, or bounded-value commands can extend the OSC schema without relying on scene-name semantics.

#### 2026-08-07 — QLC+ 5.2.2 protocol research and first implementation

Verified target and sources:

- repository workspaces identify their creator as QLC+ `5.2.2`, so implementation was checked against the official `mcallegari/qlcplus` tag [`QLC+_5.2.2`](https://github.com/mcallegari/qlcplus/tree/QLC%2B_5.2.2), commit `87a7cdedc00b01cf9f882176d1194d38229bcc43`;
- official [Web Interface documentation](https://docs.qlcplus.org/v5/advanced/web-interface) requires `-w`, defaults to port `9999`, and documents optional HTTP Basic authentication;
- official [Web API documentation](https://docs.qlcplus.org/v5/advanced/web-interface/web-api) specifies `/qlcplusWS`, `/vc.json`, pipe-separated messages, widget state values, and direct widget control;
- source `webaccess/src/webaccess-qml.cpp` verifies recursive widget APIs, button status `0/127/255`, direct `<widgetID>|<value>` dispatch, and `VCButton::requestStateChange(value > 0)`;
- source `qmlui/virtualconsole/vcbutton.cpp` verifies that Toggle ignores the pressed value and toggles on every request; consequently press-plus-release would toggle twice and is incorrect;
- source `webaccess/res/webaccess-v5.js` verifies inventory retrieval after connection and one-second reconnect behavior in the official browser, while this first embedded backend intentionally fails clearly rather than owning an automatic retry loop.

Implementation decisions:

- use one synchronous `websocket-client` connection and bounded request timeouts; no background thread, queue, duplicate model, or audio-path work is added;
- retrieve the language-independent `typeId`, caption, action type, and recursive widget hierarchy from `/vc.json`; reject unsupported widget/action types, duplicate captions, missing captions, malformed data, and invalid states;
- derive every button gesture from its discovered action type; query Toggle/Blackout state before activation, pulse Flash with `255` then `0`, and send one `255` press for Stop All;
- keep numeric IDs memory-only and replace the inventory after reconnect/reload;
- retain OSC fader support and route the same normalized master/bass/mid/high values to WebSocket sliders discovered by caption and current range;
- use no automatic reconnect in this slice. The runtime reports transport failure and requires restart/reload, preventing stale IDs or an unbounded retry loop on Raspberry Pi.

Manual gate remaining: enable web access in the local QLC+ 5.2.2 instance, align exact captions, validate Normal/Solo Frame behavior and visible button state, reload/restart QLC+ to prove ID rediscovery, and compare the same sequence with OSC.

Live discovery adjustment:

- the first real `/vc.json` inventory showed uppercase labels with spaces (`AMBIENT1`, `WHITE FAIRIES`) while logical scene names are lowercase and underscore-separated;
- caption lookup now normalizes case and removes spaces, underscores, and hyphens while retaining complete-name matching; collisions such as `AMBIENT 1` and `ambient_1` are rejected;
- the real QLC+ 5.2.2 connection successfully resolved and activated logical `ambient1` as widget `AMBIENT1` using the single-message protocol.
- the operator confirmed that the `AMBIENT1` button visibly activated in the real Virtual Console.
- live messages set the discovered `master` slider (runtime ID 3) to 111/255 and the `bass` slider (runtime ID 4) to 0/255; IDs are never persisted;
- the current reference workspace has no exact `mid` or `high` slider, so those controls are implemented and automatically tested but await live validation after the widgets are added;
- the historical QLC+ startup/global blackout shortcut and OSC `paths.blackout` setting were removed. Both transports route logical `silent` like every other configured scene; pause and shutdown do not send master, band, scene, or blackout changes;
- WebSocket modulation reuses the existing master/frequency values and update rates, adding no FFT, inference, worker, or queue. Repeated missing-slider errors are suppressed per control to keep embedded log volume bounded.

Automated validation:

- 176 tests pass across WebSocket button/slider protocol, configuration/backend behavior, normalized-caption collision handling, OSC, Enttec, automatic routing, runtime control, CLI parsing, dry-run, and shutdown;
- interactive, headless, and standalone help expose `qlc-websocket` plus generic `--qlc-host`, `--qlc-port`, and `--qlc-dry-run` options while retaining the OSC aliases;
- a repository-config dry-run activates `ambient1`, `announcement`, and `silent` as caption intentions without opening a socket;
- compilation, JSON validation, and whitespace checks pass;
- authenticated web access, automatic reconnect, and advanced widgets remain explicitly deferred.

#### 2026-08-10 — Silence route renamed to `silent`

- renamed the sustained-silence semantic scene from `off` to `silent` across runtime defaults, the reference configuration, scene and fallback data, QLC+ OSC/WebSocket routing, tests, and current documentation;
- assigned the ordinary OSC push-button route `/oculizer/scenes/silent`; WebSocket continues resolving the logical name as the normalized QLC+ caption `silent`;
- retained `off` exclusively as the reserved neutral dynamic-control profile (`--dynamic-control off`), which is unrelated to audio silence;
- retained old `off` labels in archived concert/comparison reports as historical output rather than rewriting past observations;
- migration requires renaming the QLC+ Virtual Console widget to `Silent` (or an equivalent normalized caption) and relearning its OSC input with the new address.

### Phase 8a.3 — QLC+ 5 native network protocol evaluation and backend decision

Status: **implementation in progress against QLC+ commit `984f0e7` — Raspberry Pi authorization, inventory, button, enabled-slider, reconnect, coexistence, service, and runtime-control discovery validation accepted; protocol hardening, extended parity, macOS, and endurance gates remain**

Rollback point before the native work: Git commit [`22bb33f`](https://github.com/LandryBulls/OculizerQLC/commit/22bb33ffa4f447f1d1c1f3f9cd6a84d83c40f032). The validated caption POC has now been absorbed into the isolated `oculizer/light/qlc_native.py` implementation; reverting to this commit removes the complete experiment and backend.

Objective: now that QLC+ can run its Native Server concurrently with its required Web Server, implement a staged replacement for Oculizer's `qlc-websocket` transport using the QLC+ 5 Tardis network protocol on UDP `9997` and TCP `9998`. Keep `qlc-websocket` available only as a migration and validation baseline until native parity is proven; do not send through both transports simultaneously. After all automated and live gates pass, remove the WebSocket output from Oculizer while leaving QLC+'s Web Server running for other operator tools and integrations. The `qlc-osc` and Enttec backends remain supported. Native transport must preserve the current logical routing contract, automatic widget-ID discovery, activation-only scene behavior, QLC+ Frame/Solo Frame ownership, and normalized `master`, `bass`, `mid`, and `high` controls. Its additional value is access to the authoritative workspace transfer and broader Virtual Console actions, including carefully bounded semantic status feedback.

Current evidence and pre-evaluation:

- the repository reference documents the QLC+ 5.2.2 packet format, SimpleCrypt compatibility, authentication, project transfer, live action codes, and security limitations in `docs/QLC+ 5 native network protocol reference.md`;
- the Python proof of concept in `docs/QLC5 native network protocol early tests summary.md` has successfully completed UDP discovery, TCP connection, authentication, project transfer handling, and a real `VCWidgetCaption` change to `HELLO FROM OCULIZER`;
- upstream QLC+ commit [`984f0e7`](https://github.com/mcallegari/qlcplus/commit/984f0e75e48c7c19a56581b82c5e5895285135c7) makes Web and Native servers independent, replaces the exclusive server type with a bit mask, adds the combinable `--remote` command-line option, and lets the GUI start or stop each server independently;
- the same upstream commit assigns explicit ranges to action families. Native control must use the new grouped live codes, notably `VCButtonSetPressed = 0xF200` and `VCSliderSetValue = 0xF300`, rather than the earlier positional `0xF004`/`0xF005` values documented by the initial proof of concept;
- technical feasibility is therefore established, but a production client is a medium-to-high complexity addition rather than a small variation of the WebSocket client;
- native project transfer supplies the complete authoritative workspace XML. Its Virtual Console representation contains the IDs, captions, concrete widget elements, button configuration, slider limits, and Frame hierarchy required to reproduce current `/vc.json` caption/type discovery without WebSocket coupling;
- native inventory is functionally equivalent for Oculizer routing, though operationally more complex than the already-parsed JSON: it requires authentication, bounded project reassembly, safe XML parsing, and atomic inventory refresh;
- Phase 8a.3 may therefore resume. Keep the validated WebSocket backend during implementation and parity testing, then replace it with `qlc-native` as the QLC control backend only after the complete automated and live gates below pass. QLC+'s Web Server remains enabled for the operator's other integrations even when Oculizer communicates through Native Server.

Upstream dependency resolution:

- the tested QLC+ GUI permits selection of either the Native Server or the Web Server, but not both;
- saving a project with Native Server selected does not preserve native availability when QLC+ is subsequently launched with `--web`: the command-line option overrides the saved server selection;
- those observations remain valid for the older tested build but are superseded by QLC+ commit `984f0e7`;
- the new GUI exposes independent Web and Native controls, saved workspace configuration stores `Native|Web`, and the new `--remote` option can be combined with `--web` to force both servers from the command line;
- exact QLC+ builds containing this commit must still pass live macOS and Raspberry Pi coexistence tests before the WebSocket backend is removed.

#### Widget discovery and stable logical routing

The native project transfer is the preferred runtime source of widget metadata:

- [x] authenticate and reassemble the bounded `NetProjectTransfer` XML exactly, including projects whose size is an exact multiple of 8192 bytes;
- [x] parse only the Virtual Console metadata required for routing: widget ID, caption, concrete widget type, button action type, slider range/mode/style, immediate parent Frame or SoloFrame, caption path, and relevant function association;
- [x] build the same normalized complete-caption index used by WebSocket, reject collisions, and keep numeric widget IDs in memory only;
- [x] resolve every scene and continuous control from its logical name or configured `caption`; never persist a native widget ID in `qlc_config.json`;
- [x] rediscover the complete inventory after every new native session or project transfer and invalidate all prior IDs atomically;
- [ ] consume relevant inbound widget create/delete/caption/type/range actions to update the inventory safely, or explicitly require a bounded rediscovery/reconnect when live project editing is detected;
- [ ] retain optional offline `.qxw` parsing only as a configuration-validation tool. A file path may be stale, may not match the workspace currently open in QLC+, and must not be the default authority at runtime;
- [x] do not make the native backend depend on WebSocket `/vc.json`. WebSocket inventory may be evaluated as an explicit diagnostic fallback, but mixing two transports would add lifecycle and failure coupling and is excluded from the first native slice.

The transferred XML is untrusted network input. Enforce a configurable maximum project size, bounded buffers, strict element/attribute validation, and parser behavior that does not resolve external entities. A malformed, incomplete, oversized, or incompatible project must fail before any live action is sent.

#### Widget type and command semantics

Runtime-discovered QLC+ type information is the preferred authority. Configuration may optionally declare an expected type as a safety assertion, but must not duplicate volatile IDs or silently override the actual widget type:

- [x] retain the transport-neutral logical `caption` for WebSocket and native lookup; native-only discovered metadata remains session inventory rather than duplicated configuration;
- [ ] validate an optional expected widget class/action against the transferred project and fail explicitly on mismatch;
- [ ] verify the exact section types and values for grouped `VCButtonSetPressed` (`0xF200`) and every supported button action on the target QLC+ build before implementing control;
- [x] preserve the activation-only business contract: Oculizer requests the target button activation and never deactivates the previous scene merely because another scene was selected;
- [x] let ordinary Frames layer functions and Solo Frames enforce exclusivity, exactly as validated for WebSocket;
- [ ] route `silent`, `announcement`, fallbacks, and all ordinary scenes through the same configured caption resolution without implicit blackout semantics;
- [x] use Virtual Console button actions rather than `FunctionStart`/`FunctionStop` for normal scene routing, because direct function control would bypass button state and Frame/Solo Frame behavior;
- [x] define `active_scene` as the last logical command successfully issued by Oculizer, not as authoritative QLC+ global state;
- [x] issue no implicit scene stop or blackout on shutdown.

#### Continuous master and frequency controls

- [x] implement grouped `VCSliderSetValue` (`0xF300`) for the existing normalized `master`, `bass`, `mid`, and `high` values;
- [x] discover each slider's current ID and range from the transferred project and map Oculizer's `[0.0, 1.0]` value into that range with the same clamping behavior as WebSocket;
- [x] preserve the current modulation enable/disable configuration, smoothing, refresh rate, change threshold, and safe-value policy without adding an FFT or analysis pass;
- [x] coalesce replaceable slider updates in a bounded transport queue so a slow TCP session cannot block the audio, prediction, routing, or runtime-control threads and cannot accumulate stale values;
- [x] prioritize discrete scene commands over replaceable modulation updates while preserving command order and avoiding starvation;
- [x] suppress repeated identical transport errors and bound reconnect-state log volume on Raspberry Pi; long-session volume remains part of the final endurance gate.

`master`, `bass`, `mid`, and `high` remain ordinary utility sliders in QLC+. The native backend must not reinterpret their artistic purpose or create widgets/functions in the live workspace.

#### Native session and coexistence caveats

- [x] implement correct bounded TCP stream framing for split and coalesced packets and encrypted-payload lengths; reject malformed packets and deterministically close/reconnect instead of attempting unsafe in-stream resynchronization across encrypted payloads;
- [ ] reproduce the tested QLC+ 5 SimpleCrypt CRC, compression, and native-endian float behavior exactly, with protocol code isolated and covered by fixed binary vectors;
- [ ] handle the authentication approval prompt and timeout explicitly. A cold headless boot cannot be considered autonomous until repeated or pre-authorized client behavior has been tested on the deployed QLC+ build;
- [x] account for systemd `Type=simple`: keep audio and prediction running while an event-driven network thread waits without a timeout, expose `waiting-for-qlc-authorization` through logs and `oculizerctl status`, and retain only bounded latest-value intentions until authorization completes;
- [x] make the deployment readiness helper non-blocking for `qlc-native`: `ExecStartPre` now succeeds without probing TCP `9998`, allowing Oculizer to start first and its bounded reconnect loop to attach when QLC+ appears; WebSocket retains its bounded readiness probe and OSC retains its startup delay;
- [x] validate late QLC+ startup and QLC+ restart under systemd, including fresh GUI authorization, project/inventory replacement, bounded reconnect logs, and return to `ready` without restarting Oculizer; an idle broken connection is still discovered on the next outbound command rather than immediately;
- [ ] detect disconnect through TCP closure because `NetPoll`/`NetPollReply` are declared but not implemented, then use bounded reconnect/backoff and obtain a fresh project before resuming output;
- [ ] pin and validate the QLC+ action-code table against each supported QLC+ build. Positional opcode changes can silently execute the wrong action and must be treated as a compatibility boundary. Ask upstream to assign explicit stable numeric values, append new actions without renumbering existing values, and expose a protocol/version or capability handshake that lets third-party clients reject incompatible peers before sending actions;
- [x] establish the current coexistence behavior: the tested GUI allows only one server type, and launching a native-configured project with `--web` overrides the saved selection and makes the Web server authoritative;
- [x] obtain an upstream resolution defining simultaneous Web and native operation: commit `984f0e7` makes both server types independent in saved configuration and adds combinable `--web` and `--remote` command-line switches;
- [ ] if coexistence is accepted upstream, validate a build exposing both TCP `9998` and WebSocket/HTTP `9999` concurrently on macOS and Raspberry Pi through unambiguous GUI settings or combinable CLI switches;
- [ ] before allowing `--output qlc-native`, verify native readiness while independently confirming that QLC+'s Web Server remains available for external consumers. Oculizer need not open or use a WebSocket connection in native mode, and failure of the native connection must not remove or disrupt the QLC+ Web Server;
- [ ] bind native ports to localhost or a trusted show network only. The hard-coded replayable key, ineffective server password, advisory access mask, weak SimpleCrypt cipher, and unauthenticated CRC provide no meaningful network security;
- [ ] ensure a second QLC+ peer cannot cause Oculizer to edit fixtures, functions, or workspace structure. The client emits only the approved live-control and narrowly scoped feedback actions.

Frequent status updates through `VCWidgetCaption`, colors, or fonts require special caution: these opcodes are editing actions below `0xF000`, may mark the workspace modified, enter undo/replication paths, and create unnecessary traffic. The first native slice must keep semantic feedback optional, low-rate, change-only, and limited to explicitly configured status widgets. It must never update captions at audio or modulation frequency. If QLC+ cannot provide a non-editing status action, scene/button/slider parity may be accepted while continuous semantic feedback remains deferred.

#### Implementation boundary

If the research gate is accepted, add an isolated native protocol/client module and a `QLCNativeBackend` behind the existing `LightingBackend` interface. Reuse `SceneMap`, logical captions, fallback handling, modulation producers, automatic routing, and runtime control. Do not place encryption, TCP reads, project parsing, reconnect logic, or QLC+-specific action codes in audio callbacks, `AutomaticSceneRouter`, curses, or predictor code.

Suggested implementation slices:

1. fixed-vector packet/section/SimpleCrypt codec tests and bounded TCP framing;
2. discovery, authentication, project transfer, safe XML inventory, and dry-run/offline inspection;
3. button activation with Frame/Solo Frame parity and special-route validation;
4. coalesced slider transport for `master`, `bass`, `mid`, and `high`;
5. reconnect, live inventory invalidation, service readiness, resource measurements, and optional semantic feedback;
6. explicit comparison of OSC, WebSocket, and native behavior before deciding whether all three remain supported.

Automated validation gate:

- [x] cover known binary packets, encryption/decryption, truncated/coalesced TCP data, authentication replies, exact-multiple project chunks, corrupt/oversized XML, and disconnects;
- [ ] cover caption normalization/collisions, widget-type mismatch, inventory replacement, missing widgets, all supported button actions, slider ranges, bounded coalescing, error suppression, and reconnect state reset;
- [ ] prove scene changes remain activation-only and no transport failure blocks audio or prediction;
- [ ] prove dry-run and offline validation open no UDP/TCP/WebSocket connection;
- [ ] run all OSC, WebSocket, Enttec, automatic routing, runtime-control, interactive, headless, and deployment regressions.

Manual QLC+ 5 validation gate:

- [x] validate native discovery/authentication/project transfer on the Raspberry Pi QLC+ build containing commit `984f0e7`;
- [ ] repeat native discovery/authentication/project transfer validation on the target macOS QLC+ build;
- [x] validate automatic caption/type/ID discovery without reading hard-coded IDs on Raspberry Pi;
- [x] validate ordinary and Solo Frame scene activation on Raspberry Pi;
- [ ] validate the extended `silent`, `announcement`, fallback, reload, shutdown, and widget-ID-change matrix;
- [x] validate the currently enabled continuous controls on Raspberry Pi; `mid` and `high` remain conditional on corresponding QLC+ widgets;
- [x] validate simultaneous QLC+ Web and Native server availability on Raspberry Pi with the commit `984f0e7` build launched using `--web --remote`;
- [ ] compare representative OSC, WebSocket, and native scene/modulation sequences;
- [ ] measure latency, CPU, RSS, queue depth, reconnect behavior, log volume, and long-session stability on Raspberry Pi.

Decision criterion: native implementation may now proceed because upstream provides explicit simultaneous Web and Native server operation. During migration, Oculizer selects either `qlc-websocket` or `qlc-native`, never both. Native must preserve the relevant validated lighting behavior, discover IDs and widget types without persistent numeric configuration, keep continuous controls bounded, survive project/session changes safely, and pass the live macOS and Raspberry Pi coexistence/parity gates. Once accepted, remove `qlc-websocket` from Oculizer and keep QLC+'s independently running Web Server for external consumers.

Operator acceptance recorded on 2026-08-13: the first Phase 8a.3 Raspberry Pi slice is accepted against QLC+ commit `984f0e7`. Simultaneous `--web --remote` operation, non-blocking authorization wait, transition to native readiness, project-derived widget routing, scene-button activation, Solo Frame ownership, and enabled continuous controls behaved as expected. This validates the architecture and permits the remaining hardening work; it does not yet remove the WebSocket migration fallback or close the long-session/reconnect/macOS gates.

Service-start integration on 2026-08-14 made native readiness fully asynchronous. The systemd `ExecStartPre` helper no longer probes or waits for native TCP port `9998`; Oculizer becomes active immediately and `QLCNativeClient` owns late connection, authorization, and bounded reconnection. This preserves the external service-management contract used by [`infrafast/raspiLightGUI`](https://github.com/infrafast/raspiLightGUI) at commit `f0785b4`: that UI reads `ActiveState`, `NRestarts`, and `is-enabled` directly from systemd and invokes only the stable `oculizer-service start|stop|restart|auto|noauto` commands. Consequently, `UP`, `AUTO`, and `MANUAL` continue to describe process and boot state; native protocol readiness remains a separate application-level `lighting_state` available through `oculizerctl status`. The GUI's existing dependency rule still refuses a manual Oculizer start or restart until `qlcplus.service` is `UP`, but cold-start ordering no longer makes Oculizer fail merely because the native endpoint or authorization is not ready yet.

Live Raspberry Pi validation on 2026-08-14 confirmed this integration contract. `oculizer-service start` made `oculizer.service` visible as `UP` in raspiLightGUI while the native client remained awaiting authorization. Audio capture and v6 prediction continued normally during the wait, with typical inference around `450–500ms` inside the configured one-second interval and bounded queue observations around `13–16` chunks. After operator authorization, the same uninterrupted service session reported `authorization accepted`, `downloading-project`, and `ready`; no Oculizer restart was required.

The same live session then validated QLC+ loss and recovery. Stopping QLC+ caused the native client to detect refusal, clear its session inventory, enter `disconnected`, and retry at the configured two-second cadence while audio analysis and prediction continued. After QLC+ restarted, the unchanged Oculizer process connected to a new native session, waited for fresh operator authorization, downloaded the current project again, rebuilt its widget inventory, and returned to `ready`. Reconnect observability was subsequently bounded without changing the two-second network cadence: repeated `connecting` and `disconnected` messages are emitted at most once every 30 seconds during one outage, while authorization, project download, recovery to `ready`, and the first state/error of every later outage remain immediately visible. Raspberry Pi operator validation accepted this rate-limited logging behavior on 2026-08-14.

Post-reconnect control validation on 2026-08-14 confirmed that `oculizerctl` scene requests still activate the intended native QLC+ buttons and that the enabled `master` and `bass` continuous controls still drive their sliders after inventory replacement. `mid` and `high` share the same discovered-caption, range-mapping, and coalesced-send implementation and remain accepted by automated coverage, but were not visually exercised because the corresponding live widgets were not part of this operator test. They must not be described as manually validated until those widgets are tested.

Interactive native startup was corrected on 2026-08-14 after Raspberry Pi exposed that `main()` supplied `qlc_encryption_key` to an `AudioOculizerController` signature that did not yet accept it. The controller now accepts and forwards the value to `Oculizer`, and native output receives the same backend scene-catalog restriction as OSC and WebSocket. A regression test constructs the interactive native controller with an explicit key and verifies both forwarding and scene restriction; the already-correct headless path is unchanged.

The service-pack user documentation was also expanded on 2026-08-14 with the complete installer option contract, native/WebSocket/OSC examples, reconfiguration behavior, deployment-file inspection, backup location, and explicit restart requirement. It records the important installer rule that omitted options revert to documented defaults rather than inheriting values from the previous `/etc/oculizer/deployment.json`.

WAV startup validation was hardened on 2026-08-14 after a duplicated relative path produced a full headless traceback after the native transport had already begun connecting. Both interactive and headless argument parsers now expand and resolve `--audio-file`, reject a missing file before initializing QLC+, audio, or inference, and report the exact absolute path through the normal concise CLI error. The headless entry point also converts expected `ValueError`/`OSError` construction failures into one startup error and exit status `2`, while unexpected programming errors still retain their traceback. Regression coverage verifies both entry points and the headless fallback.

Control-client connection diagnostics were clarified on 2026-08-14. `oculizerctl` reports the exact socket path it attempted and distinguishes a missing socket, a path with no listening Oculizer process, and a response timeout. Application-level command failures remain separate.

Socket selection was then centralized in `oculizerctl.py`. Unless `--socket` is supplied, the client builds an ordered, de-duplicated candidate list from `OCULIZER_CONTROL_SOCKET`, `/etc/oculizer/deployment.json`, `XDG_RUNTIME_DIR`, and the standard per-user `/tmp` path. It performs one short Unix-socket connection probe per candidate: exactly one listener is selected, no listeners produce a diagnostic listing every attempted path, and multiple listeners are treated as ambiguous and require `--socket PATH`. The installed wrapper no longer forces the production path, so the same rules cover systemd and foreground runtimes. Process-name inspection is deliberately excluded because names, interpreters, containers, and multiple instances make it unreliable. Discovery adds only a few local connections when a control command starts; it adds no background polling, persistent memory, or load to the embedded runtime.

Operator acceptance on 2026-08-14 validated automatic socket discovery with both the installed systemd service and a foreground interactive runtime on Raspberry Pi. Normal single-runtime commands selected the listener without `--socket`; explicit selection remains the required deterministic behavior when more than one runtime is active. This closes the runtime-control discovery slice without changing the service lifecycle contract exposed to raspiLightGUI.

Native protocol hardening on 2026-08-14 replaced ad-hoc packet reads with one bounded packet decoder that consumes exactly the encrypted length advertised by each header, so fragmented and coalesced TCP delivery require no accumulating stream buffer. The decoder now rejects truncated headers and required sections, invalid booleans and UTF-8 in fields it consumes, undefined SimpleCrypt flags, CRC/SHA-1/size inconsistencies, and compressed payloads that exceed a fixed one-megabyte plaintext ceiling. Malformed encrypted packets cause the existing bounded reconnect path rather than byte-pattern resynchronization, because the protocol exposes no authenticated framing and scanning ciphertext for `0xE686` could execute a falsely aligned action. Project transfer validates sequence shape, declared and actual sizes, chunk types, exact-8192-byte completion, and XML before replacing inventory atomically. Virtual Console discovery ignores lookalike XML outside `VirtualConsole`, rejects invalid slider ranges, accepts QLC+'s local `<!DOCTYPE Workspace>` marker, and refuses entity, external, or extended DTD declarations. These checks run only on native network input and add no audio pass, worker, polling loop, or persistent buffer.

The first Raspberry Pi run of that hardening exposed an over-strict XML policy: the standard QLC+ workspace declaration `<!DOCTYPE Workspace>` was rejected, so every otherwise successful authorization reached project download, disconnected, and prompted for authorization again. The parser now explicitly permits only that inert local marker while continuing to reject declarations capable of defining or loading content. A regression fixture uses the real QLC+ prologue. This was a project-validation failure, not an authentication or service-lifecycle defect.

Forward-compatibility policy was refined immediately afterward. Framing, decryption, allocation bounds, and required fields remain strict, but semantic extensions no longer break an otherwise safe session. The client decrypts and skips unknown opcodes without attempting to decode their sections; for authentication and project-transfer opcodes it decodes only the required leading fields and ignores trailing future sections. SimpleCrypt's already-declared SHA-1 integrity flag is accepted in addition to QLC+'s current CRC default, while contradictory or undefined flags remain errors. Unknown opcodes are visible at debug level only, preventing log noise. Regression packets include deliberately unknown section encodings after known fields, proving they are never interpreted. The full suite now contains 221 passing tests.

Native inventory semantics were completed against the QLC+ source pinned at commit `984f0e7` on 2026-08-14. Each discovered button now carries its action type and optional function ID; each slider carries its real `LowLimit`/`HighLimit`, mode, widget style, and optional controlled function. Both retain their immediate Frame/SoloFrame metadata and nested caption path. Optional evolving metadata is best-effort and cannot reject an otherwise usable widget, while the numeric ID and concrete button/slider kind required to send an action remain validated. Ready sessions reject a caption resolved to the wrong widget kind before queueing output. Button delivery follows QLC+ semantics rather than applying one generic pulse: Toggle, Blackout, StopAll, absent/default, and future actions receive a press only, while Flash receives a press followed by the configured scene pulse delay and release. This preserves SoloFrame behavior and avoids toggling Toggle or Blackout twice. The transport-neutral JSON remains unchanged because these facts come authoritatively from the transferred project. The full suite now contains 224 passing tests.

### Phase 8b — Raspberry Pi 5 production target

Status: **complete — functional, service, audio, resource, and operator validation accepted on Raspberry Pi 5**

Initial target baseline recorded on 2026-08-10:

- Raspberry Pi 5 with 8 GB RAM and 2 GB swap;
- Debian 13.5 (`trixie`) on native `arm64`/`aarch64`;
- system Python 3.13.5;
- Debian 13 provides no packaged Python 3.11 or 3.12 alternative, so Python 3.13 in a project-local virtual environment is the preferred validation target;
- QLC+ 5.2.2 available as `/usr/bin/qlcplus-qml`;
- repository cloned from `origin/main` with commit `2fdf1d0` as the initial deployment baseline;
- approximately 7.3 GiB memory available at idle during the initial inventory.
- initial CPU temperature reported as 47.7 degrees Celsius.

The validated installer uses the system Python 3.13 in a project-local virtual environment, selects the compatible NumPy line, and installs the official CPU-only PyTorch ARM64 packages. It must remain the supported installation path so generic PyPI resolution cannot reintroduce CUDA dependencies or incompatible package ownership.

- [x] validate every dependency on Linux ARM64;
- [x] remove assumptions about macOS paths or devices;
- [x] prepare reproducible installation;
- [x] install only the Oculizer systemd service; QLC+, its workspace, and its service remain exclusively owned by the separate QLC+ deployment repository;
- [x] run Oculizer through its non-interactive mode with no TTY requirement;
- [x] route remote scene commands through `AutomaticSceneRouter` and the existing `change_scene()` path rather than bypassing scene state;
- [x] install and permission the Phase 8a control socket and client for the production service user;
- [x] configure service user, working directory, environment, logs, and graceful stop behavior;
- [x] configure Oculizer restart behavior and a bounded passive readiness check for the independently managed QLC+ endpoint;
- [x] validate audio on Raspberry Pi OS;
- [x] validate the one-second artistic inference cadence against the previous CPU-saturated runtime, including CPU, queue depth, prediction latency, priority-event latency, and subjective scene response;
- [x] monitor temperature, CPU, RAM, and latency during a long session;
- [x] document operation and incident recovery.

Final criterion accepted by the operator on 2026-08-13: installation, manual and automatic service lifecycle, cold-start operation, live audio, QLC+ WebSocket control, `oculizerctl`, foreground diagnostics, CPU/resource behavior, and sustained runtime operation were functionally validated on the target Raspberry Pi 5.

#### 2026-08-12 — Raspberry Pi prediction-cadence experiment

Observed baseline before this change:

- the Raspberry Pi service process consumed approximately `201–237%` CPU, equivalent to slightly more than two cores, while QLC+ consumed about `7%`;
- v6 artistic predictions took approximately `500–545ms` each and advanced by ten predictions in roughly five seconds;
- the runtime hard-coded a `0.1s` minimum prediction interval, so each completed inference was followed almost immediately by another and the audio queue remained around `16` chunks with an observed maximum of `23`;
- disabling `audio.fast_detection` changed little, demonstrating that the serialized short speech checks were not the dominant load.

Experimental implementation:

- added validated `audio.prediction.interval_seconds`, defaulting to `1.0s`, independently from the training-compatible `4.0s` rolling window;
- passed the configured cadence through both interactive and headless runtimes and log the effective window, interval, and cache history at predictor startup;
- replaced the obsolete fixed `500ms` slow-prediction warning with a cadence-relative warning at 80% of the configured interval and a critical diagnostic when inference exceeds the full interval;
- replaced queue warnings above ten chunks, which were normal while inference was running, with sustained pressure detection at 80% of the bounded queue and a critical diagnostic at 95%; warnings use `⚠️` and critical conditions use `🛑` for immediate operator recognition;
- retained the existing cache values because the accepted Phase 8a.1 reference simulations and dynamic-control calibration already used a one-second artistic prediction hop; dividing the caches would change the validated profile behavior rather than preserve it;
- retained fast silence and announcement routing unchanged so the experiment isolates artistic-inference cadence from priority-event behavior.

Restoration point: Git tag `pre-prediction-cadence-raspi` identifies commit `a391db3611a3998a89f92f90e70ff225f909b7b7`, the clean repository state immediately before this experiment. If target validation rejects the new cadence, restore that revision on a dedicated branch or revert the cadence commit; do not use a destructive reset on a working deployment.

Acceptance result: the operator confirmed that the one-second cadence materially improved CPU behavior without unacceptable loss of responsiveness. Prediction duration remained typically around `450–550ms` inside its `1000ms` budget, queue depth remained bounded, and the functional behavior of priority `silent`/`announcement` routing plus `responsive`, `normal`, and `calm` was accepted. The experiment is retained; the restoration tag remains historical insurance rather than pending rollback work.

Foreground service diagnostics now announce the QLC+ readiness endpoint and
timeout before `run-auto` waits. An unavailable WebSocket endpoint produces a
visible error and prevents Oculizer startup; the same readiness helper writes
its progress and failure reason into the systemd journal when used by
`ExecStartPre`.

#### 2026-08-10 — First reproducible installer candidate

- added an idempotent `raspi_service_pack/install.sh` entry point with a read-only `--check` mode; installation deliberately preserves the existing running and boot-enabled states;
- added Python 3.13-aware NumPy requirements while retaining NumPy 1.x for the validated Python 3.11 macOS environment;
- install Debian packages, create the repository-local virtual environment, install the pinned EfficientAT fork, and validate imports before writing service state;
- store Oculizer deployment choices in `/etc/oculizer/deployment.json` and back up the prior file on reinstall;
- install only `oculizer.service`, with a bounded passive port-9999 readiness gate before WebSocket Oculizer starts;
- explicitly exclude QLC+ packages, workspace paths, process lifecycle, and systemd units because a separate repository owns that installation;
- install `/usr/local/bin/oculizerctl` against the production control socket `/run/oculizer/control.sock`;
- install `/usr/local/bin/oculizer-service` with the LiveStageAssistant service-pack lifecycle contract: `start`, `stop`, `restart`, `status`, `logs`, foreground `run-auto`, boot `auto`, `noauto`, `last-state`, and `health`;
- leave both current process state and boot enablement unchanged during installation; the operator explicitly chooses `oculizer-service auto` or manual operation;
- install a validated sudoers rule restricted to lifecycle operations on `oculizer.service`, allowing QLC+ System Command functions running as the service account to start or stop Oculizer without an interactive password prompt;
- keep lifecycle `oculizer-service auto` distinct from runtime `oculizerctl auto`, which resumes automatic prediction inside an already running process;
- export the service user's XDG runtime and D-Bus paths and enable linger so the system service can discover the same PipeWire/PulseAudio devices before an interactive login;
- support both `qlc-websocket` and `qlc-osc` from the same installer and shared runtime, consistent with the transport parity policy;
- add automated deployment tests for installer/lifecycle shell syntax, manual/boot command exposure, absence of QLC+ lifecycle ownership, and generated headless arguments.

Local validation: the complete test suite passes on macOS Python 3.11, shell syntax and Python compilation pass, JSON remains valid, and the working tree passes whitespace checks. Target validation is still required for Python 3.13 ARM64 wheels, NumPy 2 compatibility with saved scikit-learn artefacts, connectivity to the independently managed QLC+ instance, audio-device selection, systemd lifecycle, and resource behavior. Do not mark the installation or service checklist items complete until those checks pass on the Raspberry Pi.

Raspberry Pi interactive-start correction:

- fixed `oculize.py` referencing the function-local `default_prediction_device` after argument parsing;
- retain the platform prediction default on the parsed namespace so Windows dual-stream compatibility no longer depends on an inaccessible local variable;
- make Linux/Raspberry Pi use one OS-default input stream unless the operator explicitly configures a separate prediction device, matching the production audio policy and avoiding the obsolete `cable_output` assumption.

### Shared runtime control contract (Phase 8a)

Interactive operation remains supported independently of the production service. Running `oculize.py --output qlc-osc` keeps automatic prediction active and allows the operator to enter the integrated scene selector with `Ctrl+T`. Selecting a scene enables the existing manual override, and leaving the override returns routing to automatic prediction. The standalone `toggle.py --output qlc-osc` remains a manual-only controller and must not be run concurrently with the service because both processes would maintain independent QLC+ toggle state.

The interactive and production runtimes must expose the same routing intentions without requiring input from their owning terminal. Implement a local Unix-domain control socket with a configurable path. Interactive development may use a user-writable runtime location; the production service defaults to a service-owned location under `/run/oculizer/`. Provide a small command-line client, provisionally named `oculizerctl`, with these commands:

```text
oculizerctl auto
oculizerctl pause
oculizerctl scene <logical-scene-name>
oculizerctl status
oculizerctl dynamic-controls
oculizerctl dynamic-control responsive
oculizerctl dynamic-control normal
oculizerctl dynamic-control calm
oculizerctl dynamic-control off
```

The service has three mutually exclusive operator modes:

- `auto`: clear any manual override, clear the paused state, and resume routing from fresh audio;
- `scene <name>`: enter manual override and activate the requested logical scene through the normal configured fallback and QLC+ mapping path;
- `pause`: suspend prediction routing, discard queued prediction audio, and stop automatic routing/modulation updates without changing any QLC+ scene, blackout state, master, or frequency control. Returning to `auto` must resume from fresh audio state so a stale prediction cannot flash a scene.

`status` must report at least the operator mode, requested manual scene if any, resolved active scene, blackout state, whether the audio worker is healthy, and the active dynamic-control profile with its resolved cache and internal transition-policy diagnostics. Commands must return a clear success or error response and must not silently accept an unknown scene, an unmapped scene, or an unknown profile.

Live control changes are process-local and must not rewrite configuration files. Selecting a profile applies its cache and both internal policies atomically after validating the configuration; any failure preserves the complete previous state. Changing profiles preserves the newest applicable cache entries, clears rolling-window history, and refills throttle credits. A service restart restores `--dynamic-control`, which defaults to the reserved neutral `off` state rather than replaying a live adjustment.

Named dynamic controls are configuration aliases for one complete cache/rate/throttle tuple. Applying a profile uses the same atomic router API as the interactive `l` selector. QLC+ buttons, another terminal, and future automation must invoke this command rather than writing router fields directly. The interactive display reads live control state every render, so external updates become visible automatically. A policy revision guards the modal selector: if socket state changes while it is open, applying its stale snapshot must fail visibly instead of overwriting newer values.

POSIX signals remain reserved for process lifecycle (`SIGINT` and `SIGTERM`) and possibly a future configuration reload. They are not the scene-control protocol: signals cannot carry arbitrary logical scene names, provide acknowledgements, or report current state. The local socket avoids opening a network port, supports access control through filesystem ownership and permissions, and can later be wrapped by a web, GPIO, MIDI, or home-automation interface without duplicating routing logic.

Control state is initially process-local and is not restored after a crash or reboot. A restarted service follows deterministic safe startup and then enters `auto`; it must never replay a stale forced scene from a leftover socket or state file. Persistent operator state may be reconsidered only with an explicit safe-start policy.

<a id="phase-9-native-only"></a>

## Forward roadmap — Phase 9: native-only specialization

Status: **Complete — native-only architecture accepted on Raspberry Pi production target**

This is the only forward implementation path. It absorbs the useful remaining Phase 8a.3 gates and supersedes all plans to maintain multiple lighting transports. The implementation may remove legacy code aggressively after each migration gate is proven, but must not alter artistic inference behavior or the accepted native QLC+ runtime contract.

### Rollback point

The complete pre-unplug reference is pushed commit [`ca9b38e`](https://github.com/infrafast/OculizerQLC/commit/ca9b38e), “L’inventaire natif QLC+ est maintenant complété.” It contains the operator-accepted native inventory behavior and 224-test baseline. Before the first deletion commit, create the annotated tag `pre-native-only-unplug` at this exact commit and push it. If a milestone regresses inference, fast events, native scene/slider control, service lifecycle, or sustained Raspberry Pi operation and cannot be corrected within the milestone, restore this commit rather than partially resurrecting individual legacy backends.

### Scope and deletion boundary

Remove all Oculizer-owned lighting output alternatives and their exclusive assets:

- direct Enttec/DMX controller, virtual controller, fixture objects, DMX mapping/rendering, effects and orchestrators that exist only to generate fixture channels;
- QLC+ OSC client, packet sender/test script, OSC paths/actions/configuration, and OSC-specific tests and CLI aliases;
- QLC+ WebSocket client/backend, WebSocket dependency and transport tests; QLC+'s independently managed Web Server remains untouched for other consumers;
- `profiles/`, `profiles/profile_fallbacks.json`, `oculizer/custom_profiles/`, profile selection/defaults, fallback-generation scripts, and profile compatibility tests;
- legacy DMX fixture payloads under `scenes/` after every scene description, reviewed design behavior, and duration override has been migrated and verified;
- backend selection constants/factories, `--output`, `--profile`, `--qlc-config`, `--osc-*`, `--filter-osc`, `--dmx-*`, `--filter-dmx`, and equivalent installer/deployment fields;
- `PyDMXControl`, `pyserial`, and `websocket-client` when an import audit proves no retained feature uses them;
- standalone utilities whose only purpose was choosing or exercising a legacy backend. Preserve manual control itself through the integrated selector and `oculizerctl`; either simplify `toggle.py` to a native-only tool with a demonstrated distinct use or remove it.

Do not delete prediction model mappings, model artefacts, training/analysis scripts, audio sources, fast event detection, automatic routing policies, runtime control, curses/RMS visualization, dynamic-control reports, or WAV test references merely because their historical tests used a legacy dry-run transport.

### Target configuration and CLI

`config/oculizer.json` becomes the only application configuration. Move only the surviving semantics from `config/qlc_config.json` under a validated top-level `lighting` object, provisionally:

```json
{
  "lighting": {
    "native": {
      "host": "127.0.0.1",
      "port": 9998,
      "encryption_key": "",
      "reconnect_seconds": 2.0,
      "maximum_project_size": 16777216
    },
    "controls": {
      "master": "master",
      "bass": "bass",
      "mid": "mid",
      "high": "high"
    },
    "routing": {
      "pulse_seconds": 0.1,
      "fallback_scene": "ambient1",
      "caption_overrides": {}
    },
    "scene_metadata": {
      "wave": {
        "description": "All blue RGB lights with a slow sine wave pattern",
        "design_behavior": "normal"
      },
      "bass_hopper_blue": {
        "description": "User-facing artistic description migrated from the legacy scene",
        "design_behavior": "responsive",
        "max_duration_seconds": 8
      }
    }
  }
}
```

The final schema may improve these names during implementation, but it must follow these rules:

- remove `transport`, `websocket`, every `OSCPath`/`OSCaction`, and the duplicated routing registry; retain a compact `scene_metadata` catalog for design guidance rather than transport authorization;
- use the selected predictor mapping as the authority for predicted logical scene names, plus configured silence/speech scenes and explicit manual additions where needed;
- derive the normal QLC+ caption from the logical name and store only exceptions in `caption_overrides`;
- migrate the description of all 127 current scenes and all 23 `max_duration_seconds` overrides before deleting the legacy scene JSON files, and prove exact before/after equality with a migration test;
- assign every migrated scene exactly one user-facing `design_behavior` value: `static` for a deliberately fixed look, `normal` for an autonomous/time-based animation or movement, and `responsive` for a design intended to react materially to audio/modulation values;
- derive the initial behavior proposal from the current modulators, orchestrator, energy/strobe/movement values, and description, then require one explicit human-review pass because many legacy scenes mix fixed, time-driven, and MFFT-driven fixtures;
- keep `description` and `design_behavior` informational: they guide the QLC+ designer and must not silently alter prediction, transition rate, dynamic-control selection, slider modulation, or scene duration;
- preserve the global default scene duration and randomized anti-monopoly policy;
- replace `--qlc-dry-run` with `--dry-run`. Dry-run must construct the normal native-only application, emit logical button/slider intentions, open no native or other network socket, and remain usable with live or WAV audio;
- remove `--output` rather than silently accepting ignored values. Other supported audio, predictor/model, dynamic-control, interactive/headless, graph, WAV, control-socket, and service options remain operational;
- remove `--qlc-config`; `--config PATH` selects the complete configuration. Native connection overrides may retain clear `--qlc-*` names or adopt unambiguous native names, but must have one documented spelling and one implementation.

### Caveats and risk controls

1. **Hidden coupling in `SceneManager`.** Native routing still reads legacy scene files for catalog membership and duration overrides. Migrate catalog and duration policy first, introduce a lightweight logical scene registry, and only then remove `SceneManager`'s fixture/profile behavior and `scenes/`.
2. **Missing or renamed QLC+ widgets.** Removing the explicit scene list means the active project inventory becomes the lighting authority. Validate every predicted/special/manual caption when inventory becomes ready, report missing and duplicate targets without crashing audio, apply only the configured fallback where safe, and expose inventory diagnostics through status or logs.
3. **Native protocol availability.** With no OSC/WebSocket fallback, authorization, opcode compatibility, and QLC+ restart behavior are production-critical. Retain bounded intentions, asynchronous connection, forward-compatible parsing, pinned action codes, reconnect tests, and explicit `lighting_state`; never block inference while QLC+ is unavailable.
4. **Intentional command/config break.** Existing service deployments contain `output` and launch with `--qlc-config`. Update installer, helper, unit, help, examples, and `/etc/oculizer/deployment.json` generation atomically. Back up the old deployment file, reject removed CLI flags clearly, and require service reinstallation before restart.
5. **Loss or misinterpretation of useful metadata during deletion.** All 127 scene files contain descriptions and 23 contain duration overrides; predictor mappings contain the semantic cluster assignments and must remain. Generate and test the complete compact metadata catalog before deletion. Behavior classification needs human review and is advisory only: using `responsive` as a routing or dynamic-control input would introduce an inference regression. Retain human analysis reports when they document inference behavior even after fixture payloads are removed.
6. **Interactive/manual regression.** Removing profiles and backend selection must not remove the curses graph, integrated selector, manual override, `oculizerctl scene`, pause/auto, or configuration reload. Replace their scene source with the native logical registry and active inventory.
7. **Dry-run regression.** Native inventory is unavailable without a connection. Dry-run validates configuration and emitted logical captions, not live widget existence; document that boundary and cover it without network mocks that accidentally connect.
8. **Inference and v7 isolation.** Training and runtime predictors output logical labels and do not need a lighting transport. Keep `scripts/train_predictor_v6.py` and the future v7 workflow free of native imports. Run fixed WAV/model comparisons before and after the unplug and require identical raw predictions, priority silence/speech decisions, transition timing, and scene-duration policy for the same seed/configuration.
9. **Embedded resources.** This phase should reduce installation size, imports, branches, and maintenance cost. Do not replace deleted backends with new polling, duplicate FFT work, an unbounded inventory, or a compatibility framework. Measure Raspberry Pi CPU/RSS and queue depth against the rollback baseline.
10. **Historical tests and documentation.** A lower test count after deleting backend-only tests is not itself a regression. Preserve or replace every test that protects retained behavior, then remove obsolete assertions and rewrite current user documentation while keeping the implementation log as historical evidence.

### Milestone 9.1 — configuration and runtime cutover

- [x] add the validated `lighting` section to `oculizer.json` and an exact migration test for native settings, captions, fallback, pulse timing, controls, all 127 descriptions, all 127 reviewed `design_behavior` values, and all 23 duration overrides;
- [x] make native QLC+ the unconditional lighting path in interactive and headless runtimes;
- [x] replace the DMX-oriented `SceneManager` dependency with a lightweight logical scene registry derived from compact metadata and native routing;
- [x] make `--config` and `--dry-run` the single configuration/dry-run contract while preserving every non-backend runtime option;
- [x] compare v4/v6 raw predictions and the accepted fast-event/dynamic-control WAV references against commit `ca9b38e`.

Validation gate: macOS dry-run and live Native operation must pass for automatic scenes, `silent`, `announcement`, fallback, manual override, master/bass/mid/high, pause/auto, reload, WAV input, and clean shutdown before deletion begins.

### Milestone 9.2 — legacy unplug and code reduction

- [x] delete Enttec/DMX, OSC, and WebSocket implementations, assets, profiles, legacy scene payloads, dependencies, scripts, CLI branches, factories, and backend-only tests listed in scope;
- [x] collapse `LightingBackend`/factory indirection into a focused native lighting controller while retaining clean separation between routing intent and network I/O;
- [x] move caption normalization out of the deleted WebSocket module and retain native protocol isolation;
- [x] remove dead fixture/effect/orchestrator imports and prove that no retained module imports deleted packages or reads deleted folders;
- [x] remove `toggle.py`; integrated selector and `oculizerctl` retain manual control without a second competing runtime.

Validation gate: the retained automated suite passes; a repository search finds no runtime Enttec, OSC, WebSocket, fixture-profile, `OSCPath`, `OSCaction`, or output-selection dependency outside historical documentation; raw inference comparison remains identical.

### Milestone 9.3 — deployment, documentation, and production acceptance

- [x] make the Raspberry Pi installer native-only, remove `--output`, generate the new deployment schema, preserve backup/permissions/systemd/raspiLightGUI lifecycle compatibility, and update every wrapper and readiness helper;
- [x] reinstall over the accepted Raspberry Pi deployment and validate manual/automatic service operation, `oculizerctl`, QLC+ late start/restart/authorization, inventory refresh, logs, and foreground diagnostics;
- [x] replace all current README commands and troubleshooting guidance with the native-only CLI and configuration; keep historical details only in this implementation log;
- [x] publish the simplified local workflow diagram showing Audio → Oculizer inference/router → QLC+ Native → Virtual Console/Functions/DMX;
- [x] measure CPU, RSS, queue depth, startup time, reconnect log volume, and a sustained live/WAV session against the rollback checkpoint.

Final acceptance gate: the operator validates macOS interactive use and Raspberry Pi service use, including representative concert audio, silence/speech transitions, manual/runtime control, continuous sliders, QLC+ restart, and raspiLightGUI status. If accepted, native-only becomes the supported product and the rollback tag is retained as historical recovery. If rejected for a fundamental reason, return to `pre-native-only-unplug`/`ca9b38e` as a whole.

## Forward roadmap — Phase 10: single audio-input simplification

Status: **Complete — one-input audio architecture accepted on macOS and Raspberry Pi**

Phase 10 removes the single-stream/dual-stream concept completely. Oculizer will have one live audio capture device, selected only by `--input-device` or `audio.input_device` when the CLI override is absent. The same captured samples must continue to feed FFT/reactivity, RMS and frequency modulation, prediction buffering, EfficientAT inference, and silence/speech routing exactly as in the Phase 9 accepted single-input path.

### Restore point and invariant baseline

The annotated tag `phase9-native-only-accepted` at commit `5b08601` is the restore point before this refactor. It contains the accepted Raspberry Pi 5 native-only service, interactive runtime, QLC+ reconnect behavior, and resource measurements. If the cleanup changes prediction results, timing policy, priority routing, modulation, native lighting behavior, or service lifecycle and cannot be corrected within this milestone, restore that tag as a whole rather than reintroducing selected dual-stream fragments.

The behavioral baseline to preserve is:

- exactly one live `SoundDeviceAudioSource` and one PortAudio capture stream;
- `--input-device` resolves `default`, a portable alias, a full/partial device name, or an input-device index through the existing main-device resolver;
- the capture callback performs the existing source-rate-to-analysis-rate conversion before both prediction queue insertion and mel-spectrum calculation;
- the prediction cache remains expressed at the existing shared analysis sample rate and is resampled to the predictor's 48 kHz input immediately before EfficientAT semantic scoring or scene inference;
- prediction windowing, cadence, bounded queue, freshness flush, cache voting, fast silence/speech decisions, WAV loop reset, pause/resume behavior, and QLC+ intentions remain unchanged;
- `--audio-file` remains the alternative to a live device and continues through the same downstream shared analysis/prediction path without opening PortAudio.

### Milestone 10.1 — remove separate prediction capture

- [x] remove interactive `--single-stream`, `--dual-stream`, `--prediction-device`, and `--prediction-channels` options and all platform-specific defaults, argument state, help text, startup selection, and stream-mode logging associated with them;
- [x] remove headless/service `--prediction-device` and `--prediction-channels`, their validation/conversion, and constructor forwarding;
- [x] reduce `AudioOculizerController`, `main`, and `Oculizer` constructor signatures to one input device and remove `dual_stream`, `scene_prediction_device`, `prediction_channels_spec`, `prediction_channel_indices`, and `prediction_stream` state;
- [x] delete the separate prediction-device resolver, prediction-channel parser, prediction-only callback, device-specific channel auto-detection, 48 kHz secondary `InputStream`, duplicate cleanup, and every branch that starts or stops that stream;
- [x] always start the existing prediction worker beside the one shared live/WAV source when prediction is enabled, and always feed its bounded queue from the existing shared capture callback;
- [x] simplify interactive diagnostics to report the one input or WAV source without a `SINGLE`, `DUAL`, or generic stream-mode label;
- [x] keep `--test` only as the existing lighting-disabled diagnostic mode, but run its live audio through the same one-input capture/FFT/prediction pipeline instead of its historical prediction-only secondary-stream branch;
- [x] retain `--average-dual-channels` in this milestone: despite its historical name, it selects and averages channels 1–2 of the one input device and is not a second stream. Renaming or redesigning input-channel selection would be a separate user-facing change and could alter FFT/prediction samples;
- [x] remove or rewrite tests that protect dual-stream behavior, add structural tests proving one source/callback feeds both consumers, and retain coverage for live input selection, WAV input, sample-rate conversion, buffering, prediction, fast events, modulation, clean shutdown, interactive CLI, headless CLI, service deployment, and QLC+ Native integration;
- [x] update `README.md`, current architecture descriptions, CLI examples/help, developer instructions, and service documentation so no supported workflow or current description exposes a stream mode or separate prediction input;
- [x] run dead-code and repository searches proving no runtime `single_stream`, `dual_stream`, `scene_prediction_device`, `prediction_stream`, `prediction_channels`, `--single-stream`, `--dual-stream`, `--prediction-device`, or `--prediction-channels` dependency remains outside historical implementation records.

### Risks, caveats, and controls

1. **Sample-rate equivalence.** The accepted shared path captures at the device's native rate, converts to the analysis rate, queues those converted samples, then converts the prediction window to 48 kHz. Removing the secondary path must not “optimize” or relocate either conversion: doing so could change model inputs, RMS thresholds, FFT bands, latency, and scene selections.
2. **Channel semantics.** The removed `--prediction-channels` selected channels only from a separate device. The retained default shared path flattens channel 1 unless `--average-dual-channels` opens and averages channels 1–2. Do not silently replace this with averaging every available channel or a new channel abstraction.
3. **Diagnostic `--test` mode.** Its current implementation depends entirely on the secondary prediction stream and skips the normal FFT stream. It must be folded onto the shared capture pipeline while retaining disabled lighting output. This is the only intentional internal execution-path change beyond deletion and needs focused live/mock coverage.
4. **WAV input.** WAV already follows the shared queue and must remain device-independent. Removing the `audio-file`/prediction-device conflict check is correct because the conflicting option disappears, but missing/invalid WAV errors, real-time looping, temporal-state reset, and interactive display must remain covered.
5. **Shutdown ownership.** The accepted CoreAudio/PortAudio rule is that the runtime thread owns stream closure. Delete secondary-stream cleanup without changing `SoundDeviceAudioSource` ownership, or the prior macOS double-close crash could return.
6. **CLI compatibility is deliberately broken.** Removed flags must fail clearly as unknown arguments rather than being accepted and ignored. Update service examples and verify the installed Raspberry Pi launcher supplies only `--input-device`; the deployment JSON field `audio_input` and `audio.input_device` configuration remain valid.
7. **Misleading historical names.** `average_dual_channels` refers to two channels in one stream, not dual streams. Keeping it minimizes scope and preserves audio samples; document that distinction. A future rename may use a compatibility window, but it is not required for this milestone.
8. **Inference isolation.** Predictor implementations and training scripts do not own audio capture and should not need modification. Any change inside v4/v6 prediction algorithms, model artefacts, clustering, semantic scoring, or routing policy is out of scope and should be treated as a regression risk.
9. **Embedded performance.** The cleanup should remove one possible PortAudio stream and dead branches without adding any worker, queue, copy, FFT, resampler, or abstraction. Raspberry Pi CPU/RSS should remain equal or improve; a material regression blocks acceptance.

### Validation gate

Before implementation, record the clean Phase 9 test baseline. During implementation, use focused tests first and then run the complete retained suite. Acceptance requires:

- both CLI help screens expose `--input-device` and none of the four removed options;
- removed options are rejected, not silently ignored;
- a mocked live device opens exactly one `SoundDeviceAudioSource` and no direct secondary `sd.InputStream`;
- one callback block feeds both the prediction queue and the existing FFT/RMS state with the same post-conversion samples;
- fixed WAV/model comparisons preserve raw v4/v6 predictions, fast silence/speech decisions, scene transition timing, and modulation behavior;
- interactive live input, interactive WAV input, headless foreground operation, systemd service operation, `oculizerctl`, clean `Ctrl+C`/SIGTERM shutdown, and QLC+ Native control pass on their relevant platforms;
- Raspberry Pi 5 queue depth remains bounded and sustained CPU, RSS, temperature, and inference time do not regress materially from the Phase 9 acceptance reference (151% CPU, 834,288 KiB RSS, 24 threads, approximately 460 ms average inference, queue 13–16 with maximum 17, 55.4 degrees Celsius, no throttling).

No implementation work begins until the operator explicitly approves Milestone 10.1.

Operator decision: Milestone 10.1 was approved on 2026-08-14. Two-channel averaging remains opt-in because enabling it by default would change the accepted channel-1 signal, require two input channels, and break valid mono/default Raspberry Pi devices. This milestone must preserve the current default while retaining `--average-dual-channels` for interfaces whose first two channels should deliberately be combined.

## Forward roadmap — Phase 11: lightweight Web control and observability

Status: **Accepted on Raspberry Pi 5 — 2026-08-14**

Phase 11 adds a small Web application embedded in the headless Oculizer lifecycle. It provides configuration editing, runtime control, predictions, bounded logs, and an optional RMS/scene timeline without executing HTTP request handling in the audio/inference process. The interactive `oculize.py` terminal remains self-contained and does not start a Web server.

The Web component is an Oculizer-owned child process, not an independent application or systemd service. Headless Oculizer starts, supervises, and stops it unless `--no-web` is selected. The child communicates through an extended bounded control-socket API and validated configuration operations. This process boundary contains ordinary Web crashes, blocking handlers, and memory faults without adding a second deployment lifecycle; an operating-system-level fatal condition affecting the whole host remains outside this guarantee. A Web-child failure must be logged and restarted with bounded backoff without interrupting audio capture, EfficientAT inference, scene routing, or QLC+ Native control.

### Restore point and invariants

Before implementation, create an annotated `phase10-one-input-accepted` tag at accepted commit `4b98607`. Phase 11 must not change audio capture, resampling, FFT/reactivity, prediction buffering or algorithms, silence/speech detection, scene-transition policy, dynamic-control calculations, QLC+ Native behavior, or the accepted service lifecycle.

The Web process must add no audio stream, FFT, resampler, inference call, continuous high-rate worker, or unbounded history. If isolation cannot prevent a material Raspberry Pi 5 resource or timing regression, disable optional telemetry first; do not weaken inference to accommodate the UI.

### Configuration and live-apply contract

- expose a curated operator set: dynamic-control preset; silence and speech thresholds/timings/scenes; fast speech timing; prediction window/cadence; automatic maximum scene duration; and master plus bass/mid/high modulation settings and frequency bounds;
- define one shared field catalog containing JSON path, type, units, explanation, hard bounds, recommended bounds, and hot-applicable versus restart-required status. Server validation and UI help/tooltips must derive from it;
- treat browser validation as convenience only. The server must reject unknown/read-only fields, validate types and cross-field relationships through the existing configuration loader, cap request sizes, and return field-specific errors without changing memory or disk;
- make **Apply configuration** transactional: validate the complete draft, atomically persist it with a recoverable backup, hot-apply supported fields, and report the exact restart-required fields. Failed application must retain previous runtime values and must not leave a partial file;
- do not promise live application for startup-owned values. Audio input is restart-required in the first version because safely changing it would require PortAudio teardown/reopen, device/channel/rate validation, temporal-buffer reset, and failure recovery inside the running audio engine. Predictor/model loading, unsafe prediction-buffer/window changes, native connection settings, and Web bind settings also remain restart-required unless focused implementation and tests prove otherwise;
- after a successful save containing restart-required fields, inspect the selected runtime's reported launch mode and offer the appropriate action. A systemd runtime exits cleanly into its existing restart policy after confirmation; a local headless runtime shows the exact recorded executable, arguments, and working directory needed for manual relaunch. Send the HTTP result before restarting, tolerate the temporary disconnect, and reconnect the page automatically;
- use `0.75 s` as the initial Raspberry Pi recommended minimum for prediction or fast-detection intervals, not as a universal hard rule. Review every exposed field individually and provide meaningful hard bounds plus narrower recommended bounds wherever operational evidence or algorithm constraints justify them; input controls and mouseover help must show the same metadata;
- never return the QLC+ encryption key to the browser. Secret editing is excluded from the first milestone.

The Web child controls only the headless runtime that owns it. Do not expose a runtime selector: service and local headless launches intentionally share application configuration, while only the installed service has a deployment overlay. Group overlay-backed values in a collapsed **Service startup** section and show both trusted resolved paths. Never accept an arbitrary filesystem path from HTTP. `oculize.py` neither hosts nor appears in the Web interface.

### Shared telemetry and low-resource UI

- factor a terminal-independent immutable runtime snapshot. Curses and Web views may both format it, avoiding duplicate interpretation of prediction, current/resolved scene, route reason, RMS, inference time, queue depth, controls, mode, audio health, and QLC+ state;
- extend the bounded Unix control protocol with compatible read-only telemetry and recent-log commands. Preserve payload limits, permissions, timeouts, `oculizerctl`, and raspiLightGUI status meanings;
- attach a bounded in-memory logging handler to Oculizer rather than scraping journald. Redact secrets and expose no arbitrary file;
- draw the timeline in browser Canvas from already computed RMS/scene samples. Initially sample and poll at no more than 1–2 Hz, retain only 30–60 seconds, and cap every response;
- make graph telemetry configurable and switchable. Disabling it stops history sampling and graph requests while status, predictions, controls, and logs remain available;
- use bounded HTTP polling initially. Do not add WebSocket/SSE unless measurements prove polling inadequate.

### Security, deployment, and maintenance caveats

1. **Network exposure.** LAN mode is intentionally unauthenticated and must not add a password prompt or prominent UI warning. Bind to the configured interface and retain Host/origin validation, request/body/time limits, and no directory serving. Keep the security assumption as concise technical documentation: deployment is intended for the operator-controlled local network, not direct Internet exposure.
2. **Privileges and restart.** Run the Web child unprivileged and grant no unrestricted `sudo` or systemd control. Service restart is a confirmed clean runtime exit handled by the existing systemd restart policy. Local headless mode reports its recorded relaunch command rather than executing an unsafe re-exec.
3. **Concurrent writers.** Serialize updates, include a configuration revision/hash, and reject stale submissions. Web apply, runtime reload, and manual edits must not silently overwrite each other.
4. **Separation.** Configuration mutation belongs in a focused configuration service, telemetry in the runtime/control layer, and HTTP handlers only validate, invoke, and format bounded results.
5. **Dependencies.** Prefer Python's standard-library HTTP server and static vanilla HTML/CSS/JavaScript unless testing proves its isolation or shutdown unsuitable. Avoid a large framework/frontend toolchain for this bounded embedded UI.
6. **Compatibility.** Preserve `oculizerctl`, raspiLightGUI status, curses operation, service helpers, deployment JSON, socket discovery, and manual configuration editing.
7. **Embedded lifecycle with process isolation.** Do not create `oculizer-web.service`. Headless Oculizer alone owns the Web child, reaps it, applies bounded restart backoff, and terminates it during shutdown. `--no-web` prevents child creation entirely. A child crash must not restart or stop Oculizer; an Oculizer stop/restart naturally removes and recreates the child. Preserve `oculizer.service` as the sole status authority observed by raspiLightGUI.

### Milestone 11.1 — shared schema, atomic configuration, and telemetry API

- [x] create the Phase 10 restoration tag and record clean test/resource baselines;
- [x] introduce the shared editable-field catalog and field/cross-field validation without changing configuration semantics;
- [x] implement revision-aware atomic persistence, backup, rollback, and hot-versus-restart-required reporting;
- [x] factor the shared runtime snapshot and bounded redacted log ring;
- [x] extend the control socket compatibly and prove existing `oculizerctl` and raspiLightGUI status behavior unchanged;
- [x] test concurrency, stale edits, invalid configuration, partial failure, payload limits, permissions, and secret redaction.

Validation gate: apply and rollback against foreground headless and systemd runtimes; invalid or concurrent edits change nothing; predictions, routing, QLC+ output, existing controls, and service status remain identical.

### Milestone 11.2 — embedded isolated Web application

- [x] add the Oculizer-owned HTTP child process, bounded supervision/backoff, responsive UI, collapsed logical configuration groups, explanations/tooltips, hard/recommended limits, field errors, and hot/restart-required badges;
- [x] implement unauthenticated apply, pause/auto/manual-scene controls, runtime/prediction view, and bounded log view through the shared services only;
- [x] add launch-mode/capability reporting and a restart workflow: controlled confirmed systemd exit/restart or the exact local-headless manual relaunch command;
- [x] add the optional Canvas RMS/scene timeline with bounded low-rate sampling and persistent enable/disable setting;
- [x] test Host/origin checks, malformed requests, disconnected runtime behavior, timeouts, config identity, Web-child crash/restart without disturbing Oculizer, and Web disablement with `--no-web`;
- [x] validate acceptable Web-enabled and Web-disabled resource behavior on Raspberry Pi 5 without inference, queue, thermal, or service-health regression; exact comparative figures were not retained for this acceptance run.

Validation gate: validate from a second LAN device, including help, bounds, rejected edits, hot apply, restart-required reporting, prediction/log freshness, graph disablement, and uninterrupted lighting during Web refresh/restart.

### Milestone 11.3 — Raspberry Pi service-pack integration and documentation

- [x] extend the single existing service launcher and deployment configuration with Web bind/port/enabled settings; do not install another unit or add a second status surface;
- [x] add canonical `--no-web` headless/service-helper behavior for a deliberately Oculizer-only run, while keeping Web-child failure isolated from the audio engine;
- [x] preserve uninstall, upgrade, backup, ownership, state restoration, absolute-path behavior, and raspiLightGUI compatibility for the single unit;
- [x] document LAN/local use, target semantics, hot versus restart-required fields, confirmed restart/reconnect behavior, recovery, and graph resource controls in `README.md`;
- [x] record the implemented architecture, automated validation, and qualitative Raspberry Pi resource acceptance here in English.

Final acceptance was recorded on 2026-08-14 after Raspberry Pi service and LAN operation, Web controls and telemetry, bounded history, native QLC+ continuity, interactive-mode compatibility, and accepted resource behavior were validated. Terminating only the Web child caused the supervisor to recreate it while the same `oculizer.service` process remained healthy, confirming the required isolation and recovery behavior.

Operator decisions recorded on 2026-08-14: the Web child controls its owning headless runtime and exposes the shared application configuration plus a collapsed service-only deployment section when applicable; field limits are reviewed individually and `0.75 s` is a Raspberry Pi cadence recommendation/example; LAN control deliberately has neither password nor prominent warning; hot-safe fields apply immediately while audio-device and other startup-owned changes are saved with a launch-mode-aware restart proposal; the Web server is an isolated child embedded in the headless Oculizer lifecycle, never an independent service, and is omitted completely with `--no-web`. Phase 11 implementation may begin from Milestone 11.1.

## Implementation log

Add an entry for every meaningful change. Use an ISO date and separate delivered behavior, validation, and remaining work.

### 2026-08-14 — Phase 11 embedded Web implementation accepted on Raspberry Pi 5

Delivered behavior:

- created annotated restoration tag `phase10-one-input-accepted` at accepted commit `4b98607` before changing runtime behavior;
- added one shared 64-field operator catalog with descriptions, hard bounds, recommended ranges, units, and hot/restart ownership; neither QLC+ encryption data nor the full scene catalog is exposed;
- added revision-aware configuration reads and atomic validated writes with stale-edit rejection, recoverable `.previous` backup, runtime rollback, and exact hot/restart-required results;
- hot-applied silence, speech, master, frequency-band, Web-graph, and global scene-duration settings while retaining audio device, prediction scheduling/model, listener, and connection settings as restart-owned;
- added compatible telemetry, bounded/redacted log-tail, configuration, apply, and restart commands to the existing owner-only Unix control socket, including bounded response enforcement;
- factored terminal-independent runtime status for scene, raw/stable prediction, route reason, RMS, queue depth, dynamic controls, audio health, QLC+ state, configuration identity, and launch mode;
- added the headless-owned lightweight Web child with no independent systemd unit, bounded one-second supervision checks, delayed restart backoff, clean ownership shutdown, and complete `--no-web` omission;
- added the responsive vanilla HTML/CSS/Canvas interface with pause/auto/manual scene and dynamic-profile controls, collapsed logical configuration sections/tooltips, field errors, restart reporting, current input-device inventory, recent logs, and a browser-only bounded 60-second RMS/scene graph;
- added a confirmed service restart path that lets the existing systemd `Restart=always` policy relaunch cleanly after the HTTP acknowledgement; local headless targets display their exact executable/argument command instead of unsafe automatic re-exec;
- extended the single Raspberry Pi service deployment with Web enabled/bind/port settings plus persistent installer and one-off `oculizer-service start|restart|run-auto --no-web` controls; no second service/status surface was introduced;
- made package exports lazy so the lightweight Web child does not import Torch, librosa, predictors, or the audio engine merely to use the control-socket client.

Validation performed:

- the complete retained suite passes: 199 tests;
- focused coverage includes atomic persistence and backup, cross-field validation, stale revision conflict, unknown/secret rejection, runtime/file rollback, hot policy replacement, response and request limits, concurrent control clients, Host/origin checks, static/API behavior, Web-child crash/restart isolation, `--no-web`, service launcher generation, shell syntax, and existing interactive/service CLI behavior;
- the public schema is about 19.5 KiB, safely below the 64 KiB control-response limit;
- `git diff --check`, Python compilation, installer/helper shell syntax, and both CLI help surfaces pass;
- no audio input, resampler, FFT, prediction worker, model, routing algorithm, QLC+ controller, or inference timing setting was added or changed by the Web child.

Post-validation UI adjustments:

- restored the interactive RMS graph by decoupling its fixed nine-row terminal log allocation from the Web telemetry buffer, whose expansion to 50 retained messages had accidentally consumed the curses graph area;
- bounded interactive curses rendering against narrow, short, and concurrently resized terminals, preventing `addwstr() returned ERR` from escaping into and corrupting the macOS terminal display;
- made Auto, Select scene, and Pause a mutually exclusive persisted mode indicator driven by authoritative runtime status, with pressed styling and `aria-pressed` accessibility state;
- grouped all 64 editable fields into 12 logical native `<details>` sections, opening only Audio input and Silence detection initially to keep the page compact;
- removed the runtime selector after live validation confirmed that service and local headless modes intentionally share `config/oculizer.json`; each Web child now presents only its owning runtime, with service-only values isolated in the deployment section;
- added an on-demand PortAudio input inventory to the owning runtime and render `audio.input_device` as a dropdown containing only input-capable devices plus the OS default selector; device enumeration runs only during explicit configuration loading and adds no continuous work;
- record graph scene labels only on actual transitions, so a surviving current scene is not relabeled at the moving left edge after its original transition leaves the 60-second window;
- added focused section classification, input-device inventory, single-runtime rendering, collapsible-rendering, and transition-label regression coverage before rerunning the complete suite.

Service configuration-source correction:

- the installed launcher now identifies `/etc/oculizer/deployment.json` explicitly to the runtime configuration store;
- an installed service overlays effective `audio.input_device`, Web enabled, bind, and port values from deployment keys, while a local headless launch reads those fields from its application JSON;
- one Apply partitions changed fields between deployment and application documents, validates the complete application configuration, writes `.previous` backups, replaces both files under one lock, rolls both back after any persistence/live-apply failure, and computes its stale-edit revision across both byte streams;
- the Web schema labels every field as `service deployment` or `application config` and the header lists both resolved paths;
- the installer makes `/etc/oculizer` and `deployment.json` writable only by the configured service account/group (`0750` directory, `0640` file), enabling atomic same-directory replacement without broad permissions;
- added dual-source routing, backup, effective-value, schema-source, and combined-revision tests. Raspberry Pi deployments must rerun the installer before testing this correction.

Operator acceptance:

- Raspberry Pi 5 service and browser operation, live configuration behavior, native QLC+ continuity, raspiLightGUI service status, and interactive macOS operation were accepted;
- terminating the supervised Web child briefly removed browser access, then produced a new child without restarting or disrupting the owning Oculizer service;
- the operator accepted the observed resource behavior without retaining a paired numerical Web-enabled/Web-disabled measurement set; future performance investigations should capture both sets rather than treating this qualitative acceptance as a benchmark.

### 2026-08-14 — Milestone 10.1 one-input audio implementation

Delivered behavior:

- removed all interactive and headless single/dual-stream selection and separate prediction-device/channel CLI options;
- reduced the controller and engine constructors to one `input_device`, one `SoundDeviceAudioSource`, one capture callback, one bounded prediction queue, and the existing prediction worker;
- removed the secondary PortAudio callback/stream, channel parser, device resolver, auto-detection, state, startup branches, cleanup, and stream-mode display/logging;
- folded lighting-disabled `--test` operation onto the normal shared capture/FFT/prediction path;
- removed the unused standalone `AudioListener` and `RealTimeScenePredictor` capture implementations and their package exports, leaving `oculizer.audio.sources.SoundDeviceAudioSource` as the only live `sd.InputStream` owner;
- retained channel 1 as the default and retained opt-in `--average-dual-channels` for deliberate channel 1–2 averaging;
- preserved WAV input, sample-rate conversion points, prediction buffering, EfficientAT processing, fast priority events, routing, modulation, and QLC+ Native behavior.

Validation performed:

- recorded a clean pre-change baseline of 176 tests and completed the refactored suite with 176 passing tests; obsolete dual-mode tests were replaced by CLI rejection, one-source construction, and shared post-resampling sample-flow coverage;
- both help surfaces expose `--input-device` and no removed stream/prediction-device option; all four removed options are explicitly rejected by both entry points;
- compile and dead-code searches confirm one retained runtime `sd.InputStream`, owned only by `SoundDeviceAudioSource`, with removed identifiers present only in rejection tests and historical records;
- the v6 `mixvoicemusic.wav` comparison exactly reproduced all 42 raw predictions and all 44 fast semantic predictions from the accepted retained report;
- a direct comparison against `phase9-native-only-accepted` exactly reproduced all 42 v4 raw predictions, all 44 v4 fast semantic predictions, and the complete deterministic routing profile output.

Final acceptance:

- the operator validated interactive operation on macOS after the refactor, completing the live/WAV and clean-shutdown gate;
- automated parity, macOS interactive behavior, Raspberry Pi service behavior, and embedded resource measurements are accepted;
- Phase 10 is complete. `phase9-native-only-accepted` remains the pre-refactor restoration point.

Raspberry Pi live validation:

- the reinstalled service opened only the configured `default` input, capturing channel 1 at 44.1 kHz and retaining the accepted 16 kHz shared analysis rate;
- the log explicitly confirmed that scene prediction uses the shared audio input and that only the existing prediction worker started;
- v6 retained its four-second window and one-second interval, with observed inference of 470.4–483.8 ms;
- queue depth remained stable at 15 with a maximum of 16, matching the Phase 9 operating range, and automatic prediction continued normally;
- no stream-mode, secondary-device, prediction-channel, or second-stream activity appeared.

Raspberry Pi resource validation after the refactor:

- at 1 minute 52 seconds of service uptime, CPU was 145%, versus 151% at the Phase 9 reference;
- RSS was 832,288 KiB (10.0%), versus 834,288 KiB (10.1%); virtual size was 3,692,768 KiB and the process retained 24 threads;
- temperature was 53.2 degrees Celsius, versus 55.4 degrees Celsius at the reference, with `get_throttled=0x0` in both cases;
- the small improvements are consistent with code-path reduction but are not claimed as a benchmark because uptime, ambient conditions, and input content differ;
- no embedded CPU, memory, threading, queue, thermal, or throttling regression was observed. Raspberry Pi acceptance is complete.

### 2026-08-14 — User dynamic-control visualization restored

- restored the concise dynamic-control comparison to the user README after the native-only documentation rewrite had dropped it;
- retained the existing `fascination.wav` v6 graph because Phase 10 reproduced the accepted raw predictions and complete deterministic routing outputs exactly, so regenerating it would not change the result;
- documented only the user-facing distinction: identical predictions yield progressively fewer ordinary scene changes from `responsive` to `normal` to `calm`, while `silent` and `announcement` remain priority routes;
- corrected the adjacent runtime-control example to the current `dynamic-control calm` command.

### 2026-08-14 — Interactive WAV display compatibility

Implemented:

- restored a uniform optional-normalizer diagnostic contract across live and WAV audio sources;
- made the terminal display tolerate audio engines without an adaptive normalizer;
- retained identical WAV inference, QLC+ Native output, service behavior, and audio processing costs.

Validation:

- added regression coverage proving that a WAV-backed `Oculizer` explicitly exposes no normalizer instead of raising during display refresh;
- focused audio-source and terminal-UI tests pass;
- the operator confirmed on Raspberry Pi that the interactive WAV display remains stable and no longer reports a missing `normalizer` attribute.

### 2026-08-14 — Phase 9 Raspberry Pi production acceptance

Validated on a Raspberry Pi 5 with 8 GB RAM:

- the native-only service remained healthy through startup, QLC+ loss, reconnect, reauthorization, project refresh, and continued inference;
- after more than five minutes, the process used about 151% CPU, 834,288 KiB RSS (10.1% of system memory), and 24 threads;
- v6 inference stayed between 424.8 and 489.5 ms in the measured window;
- prediction queue depth remained bounded between 13 and 16, with a session maximum of 17 and no upward drift or queue-pressure warning;
- the Raspberry Pi remained at 55.4 degrees Celsius with `get_throttled=0x0`; system load remained moderate at 2.27/2.67/2.25;
- no warning or error was present in the sustained five-minute sample.

Outcome:

- Milestone 9.3 and Phase 9 are accepted;
- QLC+ Native is the sole supported lighting integration;
- retain the `pre-native-only-unplug` rollback tag as a historical recovery point, not as an active alternative architecture.

### 2026-08-14 — Milestone 9.1 native configuration cutover

Delivered behavior:

- created and pushed the annotated rollback tag `pre-native-only-unplug` at accepted commit `ca9b38e` before changing runtime behavior;
- generated `lighting.native`, native widget captions, fallback/pulse routing, and compact metadata for all 127 logical scenes directly in `config/oculizer.json`;
- preserved all 127 legacy descriptions and all 23 explicit duration overrides exactly, and assigned reviewed advisory behaviors: seven `static`, 25 `normal`, and 95 `responsive`;
- added strict parsing for the native-only application schema, including captions, fallback integrity, behavior vocabulary, pulse limits, and duration limits;
- made both interactive and headless launchers default to QLC+ Native, made a custom `--config` file authoritative for lighting too, and exposed `--dry-run` while retaining hidden legacy selection flags until the live validation gate;
- moved automatic duration lookup to compact native metadata with the legacy scene payload retained as a temporary compatibility fallback.

Validation performed:

- exact migration tests cover native settings, captions, controls, descriptions, durations, absence of transport fields, and deterministic behavior classification;
- native construction from `config/oculizer.json` reached dry-run ready state with 127 routes and no network connection;
- the complete transitional suite passes: 230 tests.
- corrected interactive SIGINT cleanup after Raspberry Pi/Python 3.13 showed that a repeated Ctrl+C could interrupt the native client's bounded thread join and print a traceback; interactive cleanup now temporarily ignores further SIGINT, the native client independently tolerates an interrupted join, and normal shutdown semantics remain unchanged on macOS and Linux.

Remaining work: perform the first macOS live Native gate, replace the DMX-oriented scene registry, run fixed inference comparisons, then begin the approved legacy deletion only after operator confirmation.

### 2026-08-14 — Milestone 9.1 logical registry and inference gate

Delivered behavior:

- replaced `SceneManager` with `LogicalSceneRegistry` in both native interactive and headless construction; the native runtime now loads 127 compact metadata entries and no fixture, orchestrator, effect, profile fallback, or DMX scene payload;
- preserved the existing scene-dictionary interface needed by the selector, status, automatic router, and runtime control, including current selection across an atomic configuration reload;
- changed interactive `r` to reload both compact scene metadata and native QLC+ routing/inventory instead of rereading only legacy DMX files;
- changed the dynamic-control comparison tool to obtain its scene catalog and 23 duration overrides from `config/oculizer.json`.

Validation performed:

- operator accepted the first live native gate for automatic scenes, priority silence/speech, manual control, continuous sliders, pause, and interactive operation;
- added registry tests for compact loading, duration preservation, selection, unknown scenes, successful reload, and rollback on invalid configuration;
- confirmed the complete v4/v6 predictor directories, audio pipeline, automatic router, fast detection, modulation code, and audio parameters are byte-identical to rollback commit `ca9b38e`;
- reproduced all 42 v6 raw predictions and all 44 fast semantic predictions from the accepted `mixvoicemusic.wav` reference exactly; historical profile-policy differences predate Phase 9 and reflect the later accepted preset calibration;
- completed real v4 and v6 inference runs over the 42-window reference without error; a current `fascination.wav` run also completed, but was not claimed as parity because the local untracked WAV differs in duration from the retained report source;
- the complete transitional suite passes: 234 tests before the additional metadata-source regression.

Remaining work: complete the CLI cutover and begin Milestone 9.2 deletion. The next operator gate occurs after the native-only code reduction, before service-pack migration.

### 2026-08-14 — Native-only unplug and deployment cutover

Delivered behavior:

- removed 168 approved legacy files in the first deletion pass, including all 127 DMX scene payloads, 13 fixture profiles, custom fixture assets, Enttec/virtual DMX, OSC, WebSocket, fallback generation, backend factories, standalone `toggle.py`, and backend-only tests;
- removed the separate `config/qlc_config.json`; `config/oculizer.json` is now the sole application configuration and the retained 127-scene metadata catalog is covered independently;
- replaced interchangeable backend inheritance with a focused `NativeLightingController` over the isolated `QLCNativeClient`, and moved caption normalization into a transport-neutral utility;
- removed output/profile/config transport selection from both public CLIs. Only `--config`, `--dry-run`, and explicit `--qlc-host`/`--qlc-port`/`--qlc-encryptionkey` native overrides remain;
- removed `pyserial`, `PyDMXControl`, and `websocket-client` from runtime dependencies;
- removed unused adaptive DMX normalization/MFFT queue work while preserving the mel spectrum consumed by master/frequency modulation, reducing bounded CPU and memory work rather than adding any embedded overhead;
- migrated the service installer, launcher, readiness helper, deployment JSON schema, tests, and service-pack documentation atomically to native-only operation; systemd and raspiLightGUI lifecycle commands are unchanged;
- replaced the user README with a native-only guide covering macOS, dry-run/WAV, QLC+ authorization and widgets, unified configuration, runtime control, Raspberry Pi installation, and troubleshooting.

Validation performed:

- retained native/controller/config/catalog/automatic/audio/fast-event/modulation/control-socket/deployment/inference tests pass: 176 tests;
- shell syntax and installer help validate with no output/backend option;
- both application help surfaces contain no output/profile/OSC/WebSocket/DMX choices;
- a complete v6 headless dry-run over `mixvoicemusic.wav` emitted master/bass values, routed music and `announcement`, and stopped cleanly on SIGINT;
- repository runtime/config/install search finds no retained legacy transport or fixture dependency; references remaining in this file are historical implementation records.

Remaining work: reinstall and run the native-only service pack on Raspberry Pi, repeat the live QLC+ scene/slider/reconnect/control/clean-shutdown gate, and collect production CPU/RSS/queue observations before final acceptance.

### 2026-08-14 — Native-only specialization approved and planned

Decision and evaluation:

- accepted QLC+ Native as the only future lighting integration and superseded the multi-backend parity policy;
- selected pushed commit `ca9b38e` as the complete rollback checkpoint and required a `pre-native-only-unplug` tag before destructive implementation;
- inventoried the deletion boundary across Enttec/DMX, OSC, WebSocket, 13 profile files, eight custom-profile assets, 127 legacy scene JSON files, backend selection, service deployment, dependencies, scripts, and backend-only tests;
- identified that all 127 legacy scene files supply user-facing descriptions and 23 supply native scene-duration overrides, so compact metadata migration, behavior classification/review, and exact parity tests must precede deletion;
- confirmed that predictor training/mappings, including a future v7, operate on logical scene labels and need no lighting transport, but required fixed inference comparisons to prevent accidental coupling regressions;
- consolidated all useful remaining roadmap work into the single three-milestone Phase 9 path with configuration/runtime, unplug/reduction, and deployment/acceptance gates;
- replaced the remote multi-output README image with the repository-owned native-only SVG workflow.

Validation performed at planning time:

- repository-wide dependency and CLI inventory;
- current configuration/scene/profile asset counts, confirmation that all 127 scenes have descriptions, modulator/type inventory for later behavior classification, and duration-override extraction;
- confirmation that the rollback commit is pushed on `origin/main`;
- XML validation of the new workflow asset and Markdown link checks remain part of the documentation handoff below.

Remaining work: Phase 9 implementation has not started. Current commands and service configuration remain valid until Milestone 9.1 deliberately changes them.

### 2026-08-13 — Phase 8b Raspberry Pi production acceptance

Delivered and accepted behavior:

- validated the supported installer on Raspberry Pi 5 / Debian 13 ARM64 with Python 3.13, CPU-only PyTorch, compatible scientific dependencies, and a repository-local virtual environment;
- validated foreground/headless execution, live audio, WAV diagnostics, QLC+ WebSocket output, runtime control, manual and automatic systemd lifecycle, readiness failure reporting, logging, graceful shutdown, and cold-start operation;
- retained the one-second artistic inference cadence after it materially reduced the former CPU-saturated behavior while preserving accepted priority silence/announcement routing and dynamic-control responsiveness;
- retained QLC+ ownership in its separate deployment repository and confirmed that this service pack installs and manages only Oculizer.

Operator validation:

- the operator confirmed completion of all Phase 8b functional, service, audio, CPU/resource, and sustained-operation tests on the target Raspberry Pi;
- the Phase 8a.1 rollback checkpoint is resolved in favor of retaining the current fast-detection design;
- Phase 8a.3 remains independently deferred because the tested QLC+ Web and Native servers cannot coexist; it does not block the accepted WebSocket-based Raspberry Pi production target.

Remaining work: none for Phase 8b. Future native-protocol work remains conditional on an upstream-supported simultaneous Web/Native server configuration.

### 2026-08-12 — Removed repository-local QLC+ workspace

Delivered behavior:

- removed the `qlc/` directory, including the reference workspace, its backup, and the legacy OSC input profile;
- removed the current README instruction that treated those files as the user-facing QLC+ reference;
- retained historical validation entries below as development history only.

Decision:

- QLC+, its workspace, input profiles, fixture patching, and deployment lifecycle are owned by the separate QLC+ project and are no longer duplicated in this repository;
- Oculizer retains only its transport configuration and OSC/WebSocket clients.

Validation:

- confirmed no runtime module, test, script, or Oculizer service installer reads files from `qlc/`;
- removed the resulting empty directory and checked repository whitespace.

Remaining work: maintain transport examples without reintroducing a QLC+ workspace into this repository.

### 2026-08-12 — Removed unused staged scenes

Delivered behavior:

- removed the unreferenced `scenes_staging/` directory and its six historical scene drafts;
- retained `scenes/` as the sole source of runtime scene definitions.

Validation:

- confirmed no runtime module, test, script, installer, or documentation referenced the staging directory;
- removed the resulting empty directory and checked repository whitespace.

Remaining work: none.

### 2026-08-12 — Removed unused development notebooks

Delivered behavior:

- removed the four historical notebooks under `notebooks/`, which were no longer referenced by runtime code, tests, scripts, installation, or documentation;
- removed the obsolete `notebooks/` ignore rule so a future accidental reintroduction remains visible to Git.

Validation:

- confirmed no repository component depended on the notebooks;
- removed the resulting empty directory and checked repository whitespace.

Remaining work: none.

### 2026-08-12 — Removed unused scene templates

Delivered behavior:

- removed the root `templates/` directory and its ten historical scene examples;
- retained `scenes/` as the sole runtime scene-definition directory.

Validation:

- confirmed no application module, test, script, installer, or documentation referenced `templates/`;
- checked repository whitespace.

Remaining work: none.

### 2026-08-12 — Removed obsolete Etna launcher

Delivered behavior:

- removed the unreferenced root `etna` shell launcher, which depended on an obsolete Conda environment and hard-coded audio arguments;
- retained `profiles/etna.json` and its fallback mappings because they are profile configuration, not part of the removed launcher.

Validation:

- confirmed no documentation, test, service, or application path referenced the launcher;
- checked repository whitespace.

Remaining work: the separate historical Etna fallback targets noted below still require an explicit decision if the profile is used again.

### 2026-08-12 — Consolidated profile-fallback tests

Delivered behavior:

- removed the three overlapping root-level manual scripts `test_fallbacks.py`, `test_fallbacks_simple.py`, and `test_profile_fallbacks.py`;
- replaced them with assertion-based `unittest` coverage in `tests/test_profile_fallbacks.py`;
- covered JSON validity, mobile scene-reference integrity, the 31 mobile mappings, actual mobile substitution, absence of garage mappings, fixture compatibility, and loading outside the repository working directory;
- updated the developer test/tool inventory to use the standard test command.

Validation:

- ran the focused profile-fallback module and the complete discovered test suite;
- compiled the new test and checked repository whitespace.

Remaining work: none.

Historical note: the unrelated `etna` mapping still names the absent targets `rainbow`, `orange_bass_pulse`, and `strobe`. Retargeting those entries requires an explicit artistic decision and was not folded into this test-organization change.

### 2026-08-12 — Profile fallback configuration organization

Delivered behavior:

- moved `profile_fallbacks.json` from the repository root to `profiles/profile_fallbacks.json`, alongside the fixture profiles it specializes;
- updated `SceneManager` to resolve the mapping from the repository location independently of the process working directory;
- moved the fallback generator to `scripts/generate_fallbacks.py` and made its input/output paths independent of the process working directory;
- updated the historical validation path to use the new location;
- updated all current user and developer documentation references.

Validation:

- confirmed the moved JSON is unchanged and remains valid;
- validated actual fallback loading and scene substitution through the existing fallback scripts;
- compiled the modified Python files and checked repository whitespace.

Remaining work: none.

### 2026-08-12 — Scene-analysis tool and report organization

Delivered behavior:

- moved the standalone generator from `analyze_scenes.py` to `scripts/analyze_scenes.py`;
- moved its generated `scene_analysis.json` output into `reports/`;
- made repository and output paths independent from the caller's current working directory;
- updated the documented maintenance command and historical report path.

Validation:

- ran the relocated generator successfully against all 127 current scene definitions;
- confirmed that the regenerated report has the same scene count and summary statistics as the retained report;
- compiled the relocated script and checked repository whitespace.

Remaining work: none.

### 2026-08-12 — Simplified local installation entry point

Delivered behavior:

- removed the unused legacy `setup.py`, which duplicated `requirements.txt` but was not used by the documented or Raspberry Pi installation paths;
- added an idempotent root `install.sh` that validates Python 3.11+, creates or reuses `.venv`, installs the requirements and pinned EfficientAT dependency, and prints the direct launch command;
- configured long socket timeouts, connection retries, and resumable-download retries for large ARM64 PyTorch wheels, with `OCULIZER_PIP_TIMEOUT`, `OCULIZER_PIP_RETRIES`, and `OCULIZER_PIP_RESUME_RETRIES` overrides;
- reduced the local user installation procedure to `./install.sh` after cloning and removed the need to activate the virtual environment before launching Oculizer.

Validation:

- validated shell syntax and the installer help/error paths without modifying the existing environment;
- checked documentation whitespace with `git diff --check`.

Remaining work: retain `requirements.txt` as the shared dependency source for local and Raspberry Pi installation, and keep platform system-package installation in the dedicated Raspberry Pi service pack.

### 2026-08-12 — Native QLC+ protocol pre-evaluation and Phase 8a.3 gate

Delivered documentation:

- reviewed the complete native-protocol reference and successful Python/QLC+ proof of concept under their renamed `docs/` paths;
- inserted Phase 8a.3 before completion of the Raspberry Pi production phase without changing the existing Phase 8b scope;
- selected native project transfer XML as the preferred authoritative runtime inventory, with `.qxw` parsing limited to offline validation and WebSocket inventory excluded from the initial native dependency chain;
- specified discovery of IDs, captions, widget/action types, slider ranges, and Frame ownership without persistent numeric configuration;
- preserved activation-only button routing, QLC+ Frame/Solo Frame responsibility, and the existing normalized master/bass/mid/high modulation contract;
- recorded TCP framing, SimpleCrypt, authentication approval, project-size, opcode compatibility, security, reconnect, bounded-queue, semantic-feedback, and Web/native coexistence caveats;
- made implementation conditional on automated, live QLC+, and Raspberry Pi gates rather than treating the caption proof of concept as a production backend.

Validation:

- reconciled the proposal with the existing OSC and WebSocket backends, `LightingBackend`, `SceneMap`, logical caption configuration, runtime control, and Phase 8b service constraints;
- checked the roadmap update with `git diff --check`;
- operator testing resolved the conflicting coexistence claims for the current build: the GUI exposes mutually exclusive Web/Native selection, and `--web` overrides a saved Native Server choice;
- converted future coexistence into an upstream dependency requiring explicit, combinable server controls before any production design relies on both protocols;
- recorded an upstream compatibility request for stable explicit action-code values and a protocol/capability handshake so third-party clients can reject incompatible opcode tables safely.

Remaining work: await upstream clarification on QLC+ server coexistence and opcode compatibility. Continue Phase 8a.3 only if QLC+ can keep both servers running; Oculizer itself will continue selecting a single backend. If QLC+ keeps the servers mutually exclusive, defer native implementation indefinitely because the Web Server is independently required by other operator integrations.

### 2026-08-06 — Unified dynamic-control profiles

Delivered behavior:

- replaced the public `--scene-rate-limit` and `--scene-throttle` options with one `--dynamic-control PROFILE` option in interactive and headless entry points;
- made `off` the reserved default, restoring startup cache smoothing while applying no transition rate or throttle policy;
- moved configurable profile definitions to `control.dynamic_controls`, retaining rate and throttle only as internal profile implementation details;
- replaced socket/client `limits`, `preset`, and `presets` commands with `dynamic-control NAME` and `dynamic-controls`;
- replaced the interactive field editor with a configured-profile selector and synchronized its active name with external socket changes;
- updated supplied QLC+ scripts to invoke the unified command.
- added a reusable WAV comparison renderer that performs one predictor pass, replays identical RMS/prediction data through `off` and every configured profile, reuses the terminal scene-identity algorithm, and writes a dependency-free vector SVG for the user documentation;
- generated the checked-in README comparison from `tests/fascination.wav` with v6 and a two-second offline inference hop, while retaining 0.1-second routing and RMS simulation.
- allowed `control.dynamic_controls` to be empty in production, leaving the reserved `off` state as the sole valid profile while continuing to reject unknown profile selections;

Compatibility decision: the removed CLI and socket commands intentionally have no aliases. Custom behavior is expressed by adding or editing a named profile in `config/oculizer.json` and selecting it as one atomic policy.

Validation:

- `python3 -m unittest discover -s tests`: 146 tests passed, including visual-identity, dynamic-profile simulation, and SVG-content coverage;
- strict compilation and `--help` checks passed for interactive, headless, and control-client entry points;
- a real headless WAV/QLC-OSC dry run started with `calm`, reported cache `35` and rate `2/20`, accepted a live switch to `normal`, then switched to `off` with cache `10` and both policies disabled before clean shutdown.

### 2026-08-06 — Non-mechanical calm routing and strict scene expiry

Delivered behavior:

- removed the `1/10` token throttle from the `calm` preset while retaining cache `35` and the rolling rate limit `2/20`;
- preserved the calm preset's low average transition rate but allowed its two permitted changes to occur according to musical predictions instead of releasing exactly one credit every ten seconds;
- made replacements selected after `max_duration_seconds` bypass both scene throttle and rolling rate admission, then record the forced transition in both budgets so the safety action is immediate without enabling a subsequent unbounded burst;
- retained one selected replacement while the expired target remains the dominant blocked prediction, allowing only a genuinely different unblocked prediction to re-enter the ordinary preset policy;
- interpreted every scene-specific or global duration as a base and drew one stable effective duration per automatic activation from a uniform ±30% range: 8 becomes 5.6–10.4 seconds, 15 becomes 10.5–19.5 seconds, and the default 40 becomes 28–52 seconds;
- retained the expired-target re-entry block and left silence, announcement, manual override, and the ordinary `normal`/`responsive` transition parameters unchanged.

Rationale and embedded impact:

- the live calm log showed a sustained sequence of accepted changes almost exactly ten seconds apart because `throttle=1/10` had no burst capacity and predictions consumed every credit immediately;
- the same log showed an 8-second-base scene remaining active for roughly 24 seconds when its replacement was held by the throttle;
- the first strict-expiry revision then exposed repeated cache-alternative bypasses, producing five scene changes in roughly 1.1 seconds; holding one replacement and charging forced changes to the budgets closes that path;
- the change removes token-bucket work in calm mode and stores one duration float plus existing target state, with no additional audio, thread, or network cost.

Validation:

- added a regression with a fully exhausted `1/60` throttle and `1/60` rate window proving a five-second expiry still switches immediately, is charged to both budgets, and holds its one replacement while the expired prediction persists;
- added deterministic tests for the 8-second override and 40-second global jitter bounds plus one-draw-per-activation stability;
- updated shipped and built-in preset tests and passed 51 focused automatic-routing, runtime-configuration, runtime-control, and scene-limit tests;
- compiled the modified router and configuration modules and checked the patch for whitespace errors.

Remaining work: validate the revised `calm` preset against a live concert-length input and confirm that strict expiry plus rolling-window bursts feel natural in QLC+.

### 2026-08-06 — QLC+ WebSocket backend specification integrated

Roadmap decision:

- added the WebSocket milestone before the Raspberry Pi production phase, preserving Phase 8b numbering and its deployment scope; it was subsequently renumbered from Phase 8a.1 to Phase 8a.2 when fast event detection became the preceding milestone;
- retained OSC as a fully supported QLC+ transport and limited the first WebSocket slice to Virtual Console button discovery and actuation behind `LightingBackend`;
- adopted activation-only scene intent for both QLC+ transports: Oculizer must not deactivate the prior scene during an ordinary change, leaving overlap to ordinary Frames and exclusivity to Solo Frames; any required wire-level release remains part of one activation gesture rather than a second routing intention;
- made official QLC+ 5 documentation and matching source inspection a mandatory pre-implementation gate, including verification of exact messages, button press/release semantics, widget discovery, and connection lifecycle;
- prohibited persisted widget IDs and required per-connection unique-caption discovery, explicit duplicate/missing-caption errors, configurable `off` and `announcement` routes, bounded transport behavior, and a network-free dry-run;
- separated automated fake-peer coverage from live QLC+ 5 validation and deferred advanced widgets until the button backend is proven;
- preserved Enttec, audio/prediction behavior, OSC availability, and the Phase 8a `oculizerctl.py` Unix socket; the current OSC previous-scene pulse is now explicitly scheduled for replacement and parity validation in Phase 8a.2.

Validation:

- reconciled the supplied specification with the existing `LightingBackend`, OSC special-routing behavior, shared runtime-control contract, and Phase 8b embedded constraints;
- recorded every uncertain protocol detail as a documentation/source-backed validation gate rather than assuming a wire format;
- checked Markdown formatting and roadmap ordering after integration.

Remaining work: complete live macOS/QLC+ validation of Phase 8a.1, then execute Phase 8a.2 research, implementation, automated validation, and manual QLC+ 5 parity testing before starting Phase 8b.

### 2026-08-07 — Fast event detection strategy integrated

Roadmap decision:

- inserted Phase 8a.1 before the WebSocket transport milestone and renumbered that transport milestone to Phase 8a.2 without changing Phase 8b deployment scope;
- preserved the v6 four-second artistic classifier and separated fast audio events, candidate stability, and ordinary scene-admission policy;
- accepted a continuous lightweight energy detector plus one serialized EfficientAT execution path: energy edges trigger urgent short semantic work, a low-rate watchdog covers speech without an edge, and active speech receives faster release checks;
- prohibited duplicate EfficientAT instances, concurrent inference, an unbounded semantic queue, and a speculative VAD dependency;
- made silence and speech priority routes independent from dynamic-control policies while initially limiting energy rise/drop events to observable, non-routing hints;
- planned small stability caches for all named profiles and explicit raw/stable/active scene state so calm can detect immediately while changing scenes deliberately infrequently;
- recorded staged implementation, automated acceptance, representative-audio validation, and Raspberry Pi 5 CPU/latency measurement gates.

Documentation decision:

- consolidated the accepted standalone strategy into this technical source of truth and removed its source Markdown file;
- kept all repository documentation in English as required by the project policy.

Remaining work: the strategy has now been implemented; retain this entry as the accepted design record and use the implementation report below for measured results. Do not begin Phase 8a.2 until the operator accepts the live Phase 8a.1 behavior.

### 2026-08-07 — Phase 8a.1 voice/music baseline protocol

Implemented:

- extended the existing dynamic-control comparison tool with an optional deterministic JSON statistics output;
- recorded source duration, predictor/window/hop/simulation settings, random seed, raw v6 predictions, per-profile policy values, ordered scene intervals, transition counts, and per-scene wall-clock duration/percentage;
- selected `tests/mixvoicemusic.wav` and the operator-authored `tests/mixvoicemusic.md` timeline as the fixed qualitative and quantitative before/after reference for Phase 8a.1.

Comparison rule:

- generate the pre-implementation baseline with v6, its configured four-second window, a one-second inference hop, a 0.1-second router step, and seed zero;
- after Phase 8a.1, rerun the same source and settings through the updated analyzer, preserving the baseline artefact rather than overwriting it;
- compare scene occupancy and transition timing against the human-described silence/noise, solo singing, speech, dry-guitar, and full-music regions, with particular attention to announcement entry latency and fresh music recovery.

Baseline result:

- retained `reports/phase_8a1_baseline_mixvoicemusic.json` and `.svg` as the machine-readable and visual pre-implementation artefacts;
- all four profiles first routed `announcement` at `16.5s`, approximately `2.5s` after the human reference marks speech beginning at `14s`, and retained it until `25.8s` across the two speech regions;
- no profile activated a scene before `12.0s`; this exposes the existing large-cache/startup latency separately from priority speech latency;
- raw/off and responsive produced nine ordinary scene changes, normal seven, and calm four, confirming that admission policy already changes show activity while the shared slow semantic path determines announcement timing.

Validation:

- all 42 four-second v6 inference windows completed and both baseline artefacts were generated;
- the focused statistics/SVG tests pass;
- the complete suite passes: 149 tests; one local-socket startup race failed on the first aggregate run, then passed both in isolation and in the immediate complete rerun.

### 2026-08-07 — Phase 8a.1 fast event implementation

Implemented:

- added a bounded EMA-based fast-energy detector for silence, recovery, and structural rise/drop hints, sharing the existing audio capture and router;
- added one replaceable semantic-work request and a low-rate watchdog on the existing prediction thread and EfficientAT instance, with no second model, process, inference thread, or unbounded queue;
- selected a two-second semantic window after shorter windows proved unstable for speech/music discrimination, while retaining the training-compatible four-second v6 artistic window;
- made silence and speech priority routes bypass ordinary dynamic-control admission policies, and cleared artistic evidence after speech so music recovery requires a fresh prediction;
- separated raw prediction, stable candidate, active scene, fast-event state, semantic scores, routing reason, and policy-block reason in runtime status;
- reduced profile stability caches and kept calmness in rate/throttle policy: responsive `3 / 12-per-10s / 5-per-0.75s`, normal `5 / 6-per-15s / 3-per-2s`, and calm `5 / 2-per-20s / 1-per-6s`;
- extended the deterministic WAV analyzer with the fast semantic timeline and preserved separate baseline and post-implementation JSON/SVG artefacts.

Reference comparison (`tests/mixvoicemusic.wav`, v6, four-second artistic windows, one-second artistic hop, 0.1-second simulation step, seed zero):

- baseline `announcement` began at `16.5s`; Phase 8a.1 begins it at `14.5s`, reducing entry latency against the human `14.0s` speech marker from `2.5s` to `0.5s` for every profile;
- Phase 8a.1 retains `announcement` through `27.0s`; the final human speech region ends at `24.0s`, and the deliberate two-second speech-release hysteresis plus semantic sampling explains the tail;
- first ordinary routing moves from `12.0s` to `9.5s`; post-implementation scene-change counts are raw/off `9`, responsive `9`, normal `7`, and calm `3`;
- retained `reports/phase_8a1_baseline_mixvoicemusic.{json,svg}` and `reports/phase_8a1_post_mixvoicemusic.{json,svg}` for exact inspection.

Embedded-resource observations on the macOS development machine:

- a two-second semantic-only EfficientAT call averaged about `113ms`; a four-second full v6 artistic call averaged about `266ms` in the same process;
- observed maximum resident memory was about `946MB` (`902MiB`) with one loaded model; Phase 8a.1 adds only small bounded state and does not duplicate model memory;
- the live WAV/headless smoke test remained responsive, reported a bounded audio queue with an observed maximum depth of `11`, emitted fast energy events, and shut down cooperatively with `Ctrl+C`;
- sustained CPU, temperature, and missed-deadline measurements remain mandatory on the Raspberry Pi 5 during Phase 8b because macOS timings cannot establish target thermal behavior.

Validation:

- focused fast-event, routing, configuration, analyzer, and predictor tests pass;
- the complete suite passes: 157 tests;
- deterministic post-implementation JSON and SVG reports were regenerated after fresh-prediction recovery behavior was aligned with runtime behavior;
- live QLC+ scene behavior and subjective transition quality remain the operator acceptance gate before Phase 8a.2.

### 2026-08-07 — Phase 8a.1 embedded compromise and simplification

Decision:

- clarified that the product target is a reliable one-to-two-second announcement response, not sub-second event reaction;
- replaced event-triggered and speech-active semantic scheduling with one fixed semantic check every `1.0s` over the latest `2.0s` of audio;
- increased speech confirmation from `0.5s` to `1.0s`, requiring two coherent fixed-cadence observations and preventing isolated semantic errors from selecting `announcement`;
- removed the EMA energy-rise/drop detector, energy-triggered work requests, active-speech cadence state, related configuration fields, and energy diagnostics;
- retained the inexpensive existing RMS silence detector, the same EfficientAT model instance, the same prediction thread, priority routing, and fresh artistic evidence after speech release.

Reference comparison:

- preserved the original `phase_8a1_baseline` and first `phase_8a1_post` JSON/SVG artefacts unchanged;
- added `reports/phase_8a1_simplified_mixvoicemusic.{json,svg}` for the accepted compromise;
- baseline announcement entry is `16.5s`, the initial high-frequency design is `14.5s`, and the simplified design is `15.0s` against the human `14.0s` reference;
- the simplified result therefore remains within the accepted one-to-two-second entry latency and produces no additional false announcement activation later in the file; the configured release hysteresis deliberately bridges the short gap between the two speech regions and extends beyond their end;
- scene-change counts remain raw/off `9`, responsive `9`, normal `7`, and calm `3`, matching the first post-implementation result;
- the deterministic reference uses `44` semantic checks instead of `87`, approximately halving semantic inference work for this file without changing model memory.

Embedded impact:

- fixed-rate scheduling removes inference bursts and makes CPU/thermal demand more predictable on Raspberry Pi 5;
- deleting event-driven state reduces code and configuration surface as well as maintenance risk;
- resident memory remains dominated by PyTorch and the single EfficientAT/v6 model, so this compromise primarily improves CPU use rather than maximum RSS.

Validation:

- focused routing, configuration, predictor, event-record, and comparison-tool tests pass;
- the complete suite passes: 155 tests;
- the simplified reference JSON and SVG were generated with the same WAV, v6 model, four-second artistic window, one-second artistic hop, 0.1-second simulation step, and seed zero as the retained references.
- regenerated `docs/dynamic_control_comparison.svg` from `tests/fascination.wav` using the documented two-second artistic hop and the simplified one-second semantic cadence; retained the operator's renamed `dynamic_control_comparisonOLD.svg`, and stored exact statistics in `reports/dynamic_control_comparison_fascination.json`;
- the initial simplified fascination comparison produced 73 changes for raw/off, responsive, and normal, versus 30 for calm, exposing that normal no longer provided a meaningful intermediate behavior.

### 2026-08-07 — Dynamic-control profile retuning after simplification

Implemented:

- restored conservative safeguards for `responsive` while keeping its cache at `3`: rate `10/10s` and throttle `4/1s`;
- changed `normal` to cache `15`, rate `3/15s`, and throttle `2/4s` so it again provides a distinct middle position;
- retained the accepted `calm` values: cache `5`, rate `2/20s`, and throttle `1/6s`;
- updated both the shipped JSON and code defaults, then regenerated the fascination SVG and machine-readable statistics.

Measured result with the documented fascination protocol:

- raw/off: `73` changes;
- responsive: `73` changes;
- normal: `52` changes;
- calm: `30` changes;
- no `announcement` or `off` priority route was activated.

Decision: the `73 → 52 → 30` progression was accepted by the operator. Responsive intentionally remains close to off; normal now retains scenes materially longer without approaching calm.

Reference refresh:

- the operator replaced `tests/fascination.wav` with a version containing introductory silence and speech and removed the old comparison SVG;
- regenerated the user-facing SVG and JSON with the unchanged v6/two-second-hop protocol;
- the longer reference produces `80 / 80 / 55 / 34` changes for raw/off, responsive, normal, and calm respectively;
- all profiles route identically through priority segments: `off` at `2.0–18.0s`, `announcement` at `18.0–19.3s`, `off` at `19.3–24.0s`, and a second `off` interval at `47.0–56.0s`;
- the result preserves the intended profile ordering and demonstrates that speech/silence latency is independent from ordinary transition policy.

### 2026-08-04 — Concert-specific v6 training pipeline

Delivered behavior:

- added an offline trainer that discovers common compressed/lossless audio formats, decodes them at 48 kHz mono, creates configurable overlapping windows, filters low-RMS silence, and evenly caps each track's contribution;
- reused the faster v4 feature contract: 1,920 EfficientAT dimensions plus 128 mean-MFCC dimensions (2,048 total), while retaining speech, singing, and music review metadata from EfficientAT;
- added a reusable compressed feature cache so cluster count, PCA dimension, and artistic mapping can be iterated without rerunning neural feature extraction;
- trained deterministic `StandardScaler`, randomized PCA, and KMeans artefacts in runtime-compatible float64 precision;
- generated cluster counts, mean RMS/speech/music values, representative PCM WAV excerpts, CSV/Markdown review reports, model metadata, and a provisional complete scene mapping;
- added a v6 runtime loader inheriting the tested v4 feature implementation, while keeping v6 hidden from both CLIs until a complete mapping is explicitly supplied and the `.ready` marker is generated;
- ignored the large, source-specific feature cache and review directory while retaining the small reproducible trainer and model-loader code.

Validation:

- compiled the trainer, v4/v6 loaders, predictor registry, and both application entry points;
- passed focused tests for window sampling, strict mapping completeness, feature-cache compatibility, and incomplete-v6 rejection;
- ran the original pipeline smoke test on six repository WAV recordings, then repeated it after switching v6 to the 2,048-dimensional v4 contract;
- loaded the final v4-based artefacts through the real v6 runtime and predicted cluster 1 from a four-second `hotel.wav` window while retaining speech, singing, and music scores;
- detected and corrected a float32 training/runtime mismatch during the smoke test before documenting the workflow.

Remaining work: train the real model on the operator's representative concert corpus, review every cluster mapping, compare cluster counts (initially 20–40), and validate transitions on complete shows before production use.

Runtime-loader correction after the first approved model:

- made the inherited v4 loader receive the v6 module directory explicitly; without this override it combined v6 filenames with the v4 directory and looked for the nonexistent `v4/pca.pkl`;
- made registry tests follow the presence of the complete approved artefact set rather than assume that v6 is permanently absent;
- loaded the operator's approved 30-cluster model through the default v6 constructor and predicted `chill_blue`/cluster 29 from `tests/fascination.wav`, with AudioSet score routing intact.

### 2026-08-04 — Retired incomplete predictor versions

Delivered behavior:

- removed the unsupported v1, v2, v3, and vday predictor implementations and model artefacts; the runtime, interactive CLI, service CLI, and direct compatibility import now expose only the speech-aware v4 and v5 predictors, with v4 as the default;
- preserved the exact v1, v3, and vday cluster-to-scene dictionaries under `oculizer/scene_predictors/legacy_mappings/` before removing their models; v2 had no predictor or scene mapping to preserve;
- documented that vday's archived mapping is directly index-compatible with v4's byte-identical clustering artefacts, whereas v1 and v3 mappings require experimental adaptation because their cluster models differ;
- added regression coverage for the supported-version contract, default predictor, rejection of v1, and completeness of every archived mapping.

Validation:

- compared every archived JSON dictionary with its source before deletion: v1 100/100, v3 120/120, and vday 100/100 entries were identical;
- confirmed both application help screens advertise only `{v4,v5}` and v4 as the interactive default;
- ran 137 unit tests successfully and compiled the modified runtime modules;
- ran real four-second inference from `tests/mixvoicemusic.wav` after deletion: v4 returned `swamp`/cluster 86 and v5 returned `pink_speedracer`/cluster 27, with speech, singing, and music scores populated by both.

Remaining work: archived mappings may be evaluated against v4/v5 later as explicit artistic experiments; they are not selectable predictor versions.

### 2026-08-04 — User-focused README cleanup

Delivered behavior:

- reduced `README.md` to prerequisites, executable installation commands, configuration entry points, launch examples, operator controls, runtime presets, and troubleshooting;
- removed internal RMS/Braille rendering mechanics, concurrency and buffer details, OSC/DMX implementation commentary, installation rationale, validation history, and roadmap narration from the user guide;
- retained the corresponding engineering history and design detail in this development guide;
- strengthened the documentation policy for developers and coding agents so future implementation explanations remain in `DEVELOPMENT.md`.

Validation:

- checked every remaining README section for a direct user task or operational reference;
- verified Markdown whitespace with `git diff --check`;
- confirmed that the RMS graph remains documented by its visible purpose and `--no-graph` control, without its internal rendering design.

Remaining work: none. Apply the documentation policy to all future changes.

### 2026-08-04 — Stronger RMS graph glyphs

Delivered behavior:

- applied the curses bold attribute to every scene-colored RMS graph glyph, improving curve visibility without changing scene identities or selector styling;
- retained the existing terminal-dependent color fallback and introduced no additional sampling, analysis, or rendering pass.

Validation:

- compiled the interactive entry point with `SyntaxWarning` promoted to an error;
- ran the focused RMS graph tests and checked the patch with `git diff --check`.

Remaining work: visually confirm the result in the operator's terminal; terminals may render `A_BOLD` as a heavier glyph, a brighter color, or both.

### 2026-08-04 — Unified scene-cache default

Delivered behavior:

- changed the implicit `--scene-cache-size` value to `10` for interactive, headless, WAV, live-device, and test operation on every platform;
- removed test-mode platform overrides so an explicitly supplied cache size is preserved;
- aligned the core constructor fallback and configured `reset` preset with the new default, while runtime preset resolution continues to restore the actual startup value.

Validation:

- verified both CLI defaults and explicit-value preservation with focused tests;
- ran the automatic-routing, runtime-configuration, and runtime-control tests;
- compiled the modified entry points and core controller with `SyntaxWarning` promoted to an error.

Remaining work: none; operators can still select any valid value from `1` to `100` explicitly or at runtime.

### 2026-08-04 — Experimental v5 scene mapping and speech scores

Delivered behavior:

- replaced all 100 v5 `placeholder` targets with the complete v4 cluster-number mapping, making v5 return valid logical scene names;
- retained and aggregated v5's already-computed EfficientAT logits into the same `speech`, `singing`, and `music` scores used by v4 semantic announcement routing;
- kept singing classified as music rather than speech and added no extra neural inference;
- marked the mapping as experimental by design: v5 has distinct scaler, PCA, and KMeans artefacts, so equal cluster numbers do not establish artistic equivalence with v4.

Validation:

- verified exact 100-entry v4/v5 mapping equality and the absence of `placeholder` with a focused test;
- ran the shared v4/v5 AudioSet score aggregation test;
- performed a real v5 inference on a four-second WAV window and confirmed a valid `(scene, cluster)` result plus populated speech/music scores;
- compiled the modified predictor and test with `SyntaxWarning` promoted to an error.

Remaining work: evaluate v5 cluster distribution on the reference WAV corpus and replace the provisional mapping with assignments based on representative samples from each v5 cluster.

### 2026-08-04 — Phase 8a shared runtime control

Delivered behavior:

- added one bounded, owner-only Unix-domain control socket shared by the interactive and headless runtimes, with stale-socket recovery and active-owner protection;
- added `oculizerctl` commands for status, automatic operation, prediction-only pause, forced scenes, atomic live limits, preset discovery, and preset application;
- centralized operator state in `RuntimeControl`, so terminal keys, external commands, and the future service integration use the same scene-routing and safety paths;
- added configurable `responsive`, `normal`, `calm`, and `reset` presets, live prediction-cache resizing, atomic policy revisions, and stale interactive-editor conflict detection;
- made the interactive header follow externally changed mode and transition values without a restart;
- documented development-terminal and QLC+ 5 `Engine.systemCommand` invocation examples.

Validation:

- `python3 -m unittest discover -s tests`: 131 tests passed, including malformed requests, concurrent clients, permissions, stale sockets, ordinary-file preservation, policy conflicts, live cache resizing, and headless socket integration;
- strict Python compilation completed successfully for the modified entry points and runtime modules;
- a real headless WAV/QLC-OSC dry run accepted `status`, `preset normal`, `pause`, `auto`, and `scene wave` from a second process, then removed its socket during a clean interrupt;
- a real curses/WAV/QLC-OSC dry run accepted `preset calm` from a second terminal, displayed cache `15`, rate `4/15`, throttle `2/3`, and revision `1`, then exited cleanly and removed its socket;
- official QLC+ 5 documentation was checked for Virtual Console button functions and detached `Engine.systemCommand` execution.

Acceptance: the operator confirmed on 2026-08-04 that the live QLC+ control workflow behaves as expected. Phase 8a has no remaining work. Raspberry Pi installation paths, service-user ownership, and the production `/run/oculizer/` socket belong to phase 8b.

### 2026-08-04 — Final automatic scene transition limits and throttle

Implemented:

- added optional `--scene-rate-limit MAX/SECONDS` rolling-window limiting to both interactive and headless entry points;
- added optional `--scene-throttle BURST/RECOVERY_SECONDS` token-bucket protection independently of prediction cache smoothing;
- placed both policies in `AutomaticSceneRouter` immediately before the common `change_scene()` call, making them independent of Enttec, QLC+, or future output backends;
- initialized the throttle with a full burst allowance, consumed one credit only for each successful ordinary music change, and recovered credits continuously at the configured rate without imposing a fixed interval between transitions;
- counted only successful ordinary music changes after target resolution and deduplication, and continuously reconsidered the latest prediction instead of queueing stale blocked transitions;
- allowed manual selection and auto resumption, silence entry and recovery, and announcement entry and release to bypass both limits;
- left both policies disabled when their options are omitted, preserving existing runtime behavior;
- added a small read-only startup interpreter to the interactive loading screen: it recognizes rate-only, throttle-only, complementary, burst-capped, redundant, and unusually slow configurations, and emits at most three concise lines without changing user values;
- documented the ten supported interpretation cases and representative commands in `README.md`.
- added an always-visible main-screen summary after AGC for cache size, rolling-limit usage, and currently available throttle credits, using `Off` for policies not supplied at startup;
- added the `l` live-control editor with bounded keyboard adjustment, explicit disable, cancel, and atomic apply actions;
- added a central thread-safe reconfiguration and status API to `AutomaticSceneRouter` for reuse by the future Phase 8a Unix-socket controller;
- added live prediction-cache resizing under the existing prediction lock, preserving the newest cached labels and recomputing their mode;
- standardized interactive, CLI, and future control validation at cache/count `1–100` and time `0.5–300` seconds.

Validated:

- covered burst consumption, progressive token recovery, rolling-window expiry, latest-prediction selection, manual override and release, and silence priority with deterministic-clock tests;
- covered each interpreter branch and its loading-screen presentation with focused tests;
- verified both CLI help surfaces, strict compilation, and the complete automated test suite.
- covered atomic reconfiguration, invalid-value rollback, runtime-budget resets, editor bounds and defaults, and thread-safe cache resizing with focused tests.

Remaining work: tune deployment values from live shows; `--scene-rate-limit 6/10 --scene-throttle 3/2` is an example rather than a hard-coded default.

### 2026-08-04 — Bounded interactive RMS history graph

Implemented:

- added a default-on 30-second scrolling RMS graph to the unused center area of the interactive curses display;
- sampled the already-computed `current_audio_rms` scalar at 10 Hz into a bounded 301-entry deque, without adding work to the audio callback;
- added elapsed-time labels and adaptive RMS vertical scaling;
- rendered every RMS sample as a marker colored for the scene active at that sample time, preserving scene transitions in the scrolling history;
- rendered graph markers only in their scene color instead of first drawing a neutral marker and recoloring it, preventing grey remnants on some curses terminals;
- formatted the right-hand elapsed-time label as `MM'SS"` and removed the redundant left-hand seconds label;
- averaged samples that map to the same terminal column and retained the latest scene for that column, guaranteeing a single colored RMS point per horizontal position;
- compacted audio/profile/stream/predictor status onto one separator-delimited line and current/predicted/latest scene status onto a second line;
- compacted optional cluster and AGC diagnostics onto a third line, moving the graph top to row 4 and recovering several vertical plot rows;
- fixed the RMS ordinate to the absolute normalized range `0.0–1.0` instead of rescaling it from the current visible peak;
- fixed the scrolling time window at 30 seconds from startup instead of stretching early samples across the available width;
- permanently reserved nine bottom log rows, including initially empty slots, so log arrivals cannot resize the graph;
- decoupled 20 Hz keyboard polling, 10 Hz RMS sampling, and 4 Hz terminal rendering;
- added immediate renders for user interaction and terminal resize events;
- replaced forced full-screen clears with curses differential `erase()` plus `noutrefresh()`/`doupdate()` rendering to reduce flicker and SSH terminal traffic;
- applied the GUI's black background and performed one physical clear during curses initialization, preventing untouched terminal cells from retaining the terminal's original background color;
- moved that physical-screen synchronization after controller construction, ensuring direct DMX/predictor startup output cannot scroll the physical terminal after curses has established its differential-screen baseline;
- enabled `logging.captureWarnings(True)` before curses starts, routing `warnings.warn()` output into the file and UI log handlers instead of stderr, where multi-line warnings would scroll and desynchronize the physical terminal;
- anchored RMS aggregation buckets to startup time instead of continuously reprojection the moving window, keeping historical point membership and averaged values stable;
- changed scrolling to advance the complete curve by exactly one terminal column at each fixed bucket boundary;
- added an immediate common curses loading screen showing the selected lighting backend, audio source, profile, and predictor before heavyweight construction begins;
- captured legacy constructor stdout/stderr into application logs so initialization messages cannot overwrite the loading screen or desynchronize curses;
- replaced coarse one-character RMS markers with Unicode Braille cells, providing a virtual 2-by-4 subpixel grid per terminal character;
- interpolated adjacent RMS buckets into a continuous terminal line and assigned each Braille cell the most recent scene color represented in that cell;
- overlaid the derived scene marker on the initial RMS sample and the first sample after each actual scene transition, while assigning scrolling timestamps only to real transitions;
- changed Braille scrolling from subpixel steps to complete-cell steps, keeping the two horizontal subpixels grouped into the same glyph for its entire visible lifetime;
- replaced one-y-value-per-subcolumn interpolation with a full Bresenham-style line rasterizer, filling intermediate vertical pixels so steep RMS changes remain visibly connected;
- added scrolling `MM'SS"` labels below scene-transition markers while keeping the total elapsed counter fixed at the right edge;
- retained the newest transition timestamp when labels overlap and enforced a two-space gap between visible labels and the fixed counter;
- added the same stable scene-color markers beside names in both the integrated and standalone scene selectors, which act as the graph legend;
- replaced arbitrary scene colors with a centralized, name-derived visual identity: recognized color words and aliases select the matching family, a stable hash selects one of four shades, and names without a color word receive a stable gray Unicode shape instead of a dot;
- shared that identity between Braille graph coloring, transition overlays, and both scene selectors; the mapping has no static scene list and therefore applies automatically after dynamic scene additions or reloads, with a safe ANSI fallback on terminals without 256-color support;
- added `--no-graph`, which preserves the static display and centers an activation hint in the graph area;
- kept the graph entirely outside `Oculizer`, `HeadlessOculizerService`, and `oculizer_service.py` so Phase 8b service operation remains headless and unaffected;
- preserved all nine recent log lines when space permits and reduced only the visible log tail on short terminals to reserve a usable graph, without affecting the complete file log;
- handled terminals still too small for the graph by leaving the constrained center area untouched.

Validated:

- added focused tests for sampling rate, bounded memory, axes, plotting, and small-terminal behavior;
- compiled the interactive entry point and graph module with `SyntaxWarning` promoted to an error;
- ran the complete automated test suite.

Remaining work: validate the chosen refresh rate and terminal layout on the target Raspberry Pi during Phase 8b. The bounded UI-only design is expected to have negligible memory use and no material effect on audio or DMX latency.

### 2026-08-04 — Static main terminal display

Implemented:

- removed the central ASCII logo and animated skulls from the main curses display;
- removed random glitch particles and their decorative character set;
- removed the unused scanline and flicker implementation and associated state and color pairs;
- retained the textual status, current/predicted scene, AGC, log, and keyboard-control areas.

Validated:

- confirmed no animation or decorative-symbol code remains in the interactive entry point;
- compiled the interactive entry point with `SyntaxWarning` promoted to an error;
- ran the complete automated test suite.

Remaining work: assess a bounded scrolling RMS history graph with scene-state indication before implementing it.

### 2026-08-04 — Virtual Enttec DMX dry-run

Implemented:

- added `--dmx-dry-run` to the interactive, headless, and standalone manual entry points for `--output enttec`;
- added an Enttec-compatible virtual controller with the same 513-value universe buffer and channel, multi-channel, packet, blackout, and close operations used by the existing fixture renderers;
- bypassed serial-port discovery and connection while retaining profile loading, fixture construction, scene rendering, effects, and orchestrators;
- limited summaries to three per second and logged only channel values changed since the preceding summary;
- added `--filter-dmx` (and the `--filter-DMX` alias) to suppress all virtual frame summaries when only lifecycle logging is wanted;
- forced a final virtual blackout summary during clean shutdown.

Validated:

- constructed all 19 fixtures in `garage2025` while making serial discovery fail if called;
- streamed `tests/fascination.wav` through predictor v4 and the existing direct-DMX render loop;
- observed rate-limited changed-channel summaries across automatic scene transitions;
- confirmed signal-driven shutdown ends with zero values for every still-active channel;
- added focused virtual-controller rate-limit, log-suppression, final-blackout, and hardware-isolation tests.

Remaining work: none. Full-universe logging and a configurable dry-run log rate are intentionally deferred because the changed-channel summaries provide the required diagnostic surface with substantially less output.

### 2026-08-04 — Exact OSC dry-run log filtering

Implemented:

- added the repeatable `--filter-osc PATH` option to the interactive, headless, and standalone manual entry points;
- suppress exact matching OSC paths from dry-run informational logs without changing client success, state tracking, or packet transmission behavior;
- validate every filter as an OSC address and reject malformed paths during backend construction.

Validated:

- confirmed that multiple filters accumulate;
- confirmed that `/oculizer/bass` can be hidden while `/oculizer/master` remains visible;
- retained the complete OSC client, backend, routing, WAV-source, and local UDP regression suite.

Remaining work: none. Prefix or wildcard filtering is intentionally unsupported until a concrete need exists.

### 2026-08-04 — Phase 7b WAV-source planning

Decision:

- insert a pluggable audio-source milestone before Raspberry Pi deployment;
- implement only the existing live `sounddevice` source and a real-time-paced, continuously looped PCM WAV source in the first slice;
- keep audio analysis, prediction, semantic routing, and lighting behavior independent of the selected source;
- explicitly defer MP3 files and public online streams while keeping the source contract extensible enough to add them later;
- require bounded streaming rather than loading an entire file into memory.

Validated:

- confirmed that the current audio-less development host can import the application after installing PortAudio but exposes no input device;
- confirmed that the current runtime is coupled to `sounddevice` capture and therefore cannot exercise the main pipeline on that host;
- reviewed the existing single-stream, dual-stream, prediction, modulation, and shutdown responsibilities to define the phase boundary.

Implemented:

- added the `AudioSource` protocol and a threaded `WavFileAudioSource` using bounded standard-library PCM frame reads;
- added `--audio-file PATH` to the interactive and headless entry points;
- made WAV input mono, real-time paced, continuously looped, and resampled through the existing single-stream callback;
- made capture-device discovery lazy so file mode neither imports nor opens `sounddevice`;
- reset queued prediction data, prediction smoothing, semantic scores, current raw prediction, and modulation smoothing and transient baselines at loop boundaries;
- added a portable Numba cache location for containerized/read-only Python installations.

Validated:

- identified `tests/fascination.wav` as uncompressed stereo 16-bit PCM at 44.1 kHz with 14,810,544 frames;
- ran the file through predictor v4, automatic scene routing, speech/silence policy, and master, bass, mid, and high modulation using QLC+ OSC dry-run;
- observed real predictions and logical transitions between `ambient1` and `wave` targets;
- confirmed clean `SIGINT` shutdown with continuous safe zeros, scene deactivation, and blackout;
- started the interactive curses application with the WAV source on the audio-less host and exited cleanly with `q`;
- passed 62 automated tests, including local UDP delivery, live-source lifecycle adaptation, WAV validation, stereo-to-mono conversion, looping, hardware isolation, and existing routing and backend regressions;
- bounded memory to one source block plus the existing fixed-size analysis and prediction queues; steady-state predictor CPU remains the dominant cost and no additional analysis pass was introduced.

Remaining work: none for phase 7b. MP3 and public online streams remain deferred; phases 8a and 8b follow.

### 2026-08-04 — Headless operator-control design

Decision:

- retain the existing interactive QLC+ workflow while adding service-safe control as a separate interface;
- use a local Unix-domain socket and acknowledged CLI commands instead of encoding scene choices as POSIX signals;
- define `auto`, forced `scene`, and prediction-only `pause` as the initial service modes;
- make pause leave all lighting state untouched and make auto resume from fresh audio state;
- keep all commands on the existing automatic router and scene-command path so interactive and headless control share state semantics.

Validated:

- inspected the current integrated selector, manual-override router, headless runtime, and QLC+ backend command path;
- confirmed that interactive QLC+ selection already supports forcing a logical scene and returning to automatic prediction;
- confirmed that the headless runtime currently has lifecycle signal handling but no external operator-control endpoint.

Remaining work: implement and test the shared control endpoint and CLI during phase 8a; the contract documented above is not yet available at runtime.

### 2026-08-03 — Documentation consolidation

Implemented:

- consolidated repository documentation into `README.md` and `DEVELOPMENT.md`;
- separated user documentation from the technical source of truth;
- recorded the hybrid QLC+ 5 architecture and roadmap;
- recorded macOS development and Raspberry Pi production targets;
- translated all repository documentation into English;
- established English as the permanent documentation language regardless of request language.

Validated:

- inventoried existing Markdown files;
- checked CLI options and primary code integration points;
- confirmed that only the two canonical Markdown files remain.

Remaining work: begin phase 0 with the QLC+ test workspace.

### 2026-08-03 — Milestone 0 repository test sender

Implemented:

- added `scripts/send_osc_test.py`, a dependency-free OSC float sender;
- established `/test`, `127.0.0.1`, UDP port `7700`, and float press/release values as the temporary milestone contract;
- added pulse mode for deterministic press/release testing;
- added isolated OSC encoding and local UDP delivery tests.

Validated:

- QLC+ 5.2.2 is installed on the macOS development system;
- OSC message encoding uses padded OSC strings, the `,f` type tag, and a big-endian float;
- local UDP delivery succeeds without QLC+ or DMX hardware.

At this point the repository-side sender was complete; the QLC+ workspace validation was completed in the following log entry.

### 2026-08-03 — Milestone 0 completed

Implemented:

- added the validated QLC+ 5.2.2 reference workspace as `qlc/qlc.qxw`;
- added the portable OSC input profile as `qlc/Oculizer-OSC.qxi`;
- removed an accidental duplicate `/test` mapping from the Virtual Console frame while retaining the button mapping;
- documented the macOS input-profile installation location.

Validated:

- QLC+ listens locally on UDP port `7700`;
- the input-profile wizard detects `/test` as channel `15948`;
- the Virtual Console button maps channel `15948` to the `lyre red` test Scene;
- `python scripts/send_osc_test.py --pulse 1` controls the button successfully;
- the workspace and input profile are valid XML.

Decision:

- `/test` remains a temporary milestone-only address. Permanent application commands will use the `/oculizer/...` namespace.

Next: phase 1, the reusable standalone OSC client.

### 2026-08-03 — Reference workspace location and runtime-path requirement

Implemented:

- moved the milestone workspace to `qlc/qlc.qxw` next to its input profile;
- designated it as a test and development reference rather than a production default;
- added a deployment requirement for a configurable `.qxw` workspace path.

Decision:

- the future launcher must receive the workspace path through configuration or a command-line option, validate it, and pass it to QLC+ at startup;
- application code must not hard-code `qlc/qlc.qxw` or any machine-specific absolute path.

### 2026-08-03 — Phase 1 reusable OSC client implementation

Implemented:

- added `oculizer/light/osc_client.py` with a reusable, thread-safe UDP client;
- added the original standalone `config/qlc_osc.json` transport file, later consolidated into `config/qlc_config.json`;
- implemented OSC float encoding, normalized value clamping, press/release, level, blackout, dry-run, context-manager, and idempotent close behavior;
- made UDP send failures return `False` after logging instead of propagating into real-time application code;
- kept the milestone `/test` script self-contained so it can run by path without installing the Oculizer package;
- selected a standard-library implementation to avoid an unnecessary runtime dependency on macOS and Linux ARM64.

Validated:

- 10 unit and loopback UDP tests pass;
- configuration loading and validation pass;
- OSC encoding, clamping, dry-run, close behavior, and four-message UDP delivery pass;
- Python compilation and default configuration construction pass.

Validation targets:

- none; the client has been validated against the `/test` control in QLC+ 5.2.2.

Observed QLC+ button semantics:

- a Virtual Console button configured as `Toggle Function on/off` toggles only on a press event (`1.0`);
- the release event (`0.0`) ends the physical input gesture but does not stop the Function;
- switching the Function off requires a second complete press/release gesture, not a release message by itself;
- phase 2 must model a `pulse` gesture separately from desired logical scene state and must avoid assuming that `0.0` means “deactivate” for toggle widgets.

### 2026-08-03 — Phase 1 QLC+ validation completed

Validated:

- the standalone OSC client configuration successfully controlled the `/test` Virtual Console button before configuration consolidation;
- `press('/test')` toggles the attached Function on;
- `release('/test')` is correctly treated by QLC+ as button release and does not toggle the Function off;
- a second complete press/release pulse toggles the active Function off;
- the observed behavior matches the button's `Toggle Function on/off` configuration.

Decision:

- OSC transport retains distinct `press` and `release` primitives;
- the future QLC+ backend will implement a complete button pulse and explicit logical-state tracking for toggle widgets.

### 2026-08-03 — Reproducible EfficientAT installation

Implemented:

- restored the package-enabled `LandryBulls/EfficientAT` fork used by this codebase;
- pinned the dependency installation to commit `010b68e69d9f75d074eb8720ac06968c38352ac8`;
- removed the invalid `efficientat>=0.0.1` PyPI requirement;
- pinned the official compatible macOS trio `torch==2.11.0`, `torchaudio==2.11.0`, and `torchvision==0.26.0` through the project requirements;
- documented the separate `--no-deps` installation required to bypass the fork's obsolete training dependency pins;
- documented Python 3.11 and the Git requirement.

Validated:

- confirmed from repository history that this fork was the intended predictor dependency;
- confirmed that the upstream EfficientAT repository is not an installable Python package;
- confirmed that the pinned fork revision exists remotely.
- confirmed that the fork's package metadata pins PyTorch 1.13 and other historical training dependencies that are not compatible with Python 3.11 on Apple Silicon.
- confirmed the selected PyTorch trio against the official compatibility table.
- installed the complete runtime into `.venv` on Apple Silicon;
- validated EfficientAT imports and API exports;
- ran the mel frontend and an untrained DyMN forward pass successfully;
- imported all five Oculizer predictor implementations successfully;
- documented that `pip check` is not a valid acceptance test for the `--no-deps` fork installation because it evaluates the fork's obsolete training metadata.

### 2026-08-03 — Local virtual environment exclusion

Implemented:

- added `.venv/` to `.gitignore` so the repository-local Python environment and installed packages cannot be committed.

### 2026-08-03 — Phase 2 interchangeable lighting backend

Implemented:

- introduced the `LightingBackend` intent API and `DisabledBackend`, `EnttecBackend`, and `QLCOscBackend` implementations;
- retained the existing direct fixture-rendering loop exclusively for the Enttec backend;
- connected backend selection to both `oculize.py` and `toggle.py` with `--output enttec|qlc-osc`;
- added OSC configuration-path, host, port, and dry-run command-line overrides;
- made the QLC+ backend skip all serial-controller initialization and direct fixture rendering;
- routed shutdown and blackout through the selected backend;
- retained `enttec` as the default to preserve existing startup behavior.

Validated:

- `python -m py_compile` passes for both entry points and all modified lighting modules;
- 14 focused tests pass across the OSC sender, OSC client, and backend layers;
- adapter tests cover Enttec blackout and idempotent close behavior;
- QLC+ tests cover intent parameters, blackout, close, configuration overrides, and socket-free dry-run mode;
- an Oculizer construction test makes Enttec initialization raise immediately and confirms it is never called in `qlc-osc` mode.

Decision:

- QLC+ scene activation and toggle-state tracking remain intentionally inactive until phase 3 supplies an explicit logical scene-to-OSC mapping. Selecting `qlc-osc` in phase 2 validates backend isolation but does not yet change QLC+ scenes.

Next: phase 3, connect manual scene changes from `toggle.py` through a configured OSC scene mapping.

### 2026-08-03 — macOS toggle audio-device startup correction

Implemented:

- aligned the standalone toggle's macOS audio default with `oculize.py` by selecting the `blackhole` alias instead of assuming a connected Scarlett interface;
- added `toggle.py --list-devices` so audio discovery can be checked without entering curses mode.

Validated:

- Core Audio exposes `BlackHole 2ch` with two input channels on the development Mac;
- the former `scarlett` default did not match any connected input device.

This machine-specific default was subsequently replaced by the portable runtime configuration described in the next entry.

### 2026-08-03 — Portable audio input configuration

Implemented:

- added the general `config/oculizer.json` runtime configuration with `audio.input_device` set to `default`;
- established command-line input selection as an override of the general configuration;
- implemented portable OS-default resolution through PortAudio's active host API;
- accepted stable aliases, full or partial device names, and numeric indexes;
- connected the configuration to both `toggle.py` and `oculize.py` through `--config PATH`;
- kept QLC+ transport settings isolated from the general Oculizer runtime configuration.

Validation:

- the development Mac resolves `default` to the CoreAudio default input;
- BlackHole resolves by alias and full name;
- configuration loading, default fallback, validation, aliases, names, and indexes have focused unit coverage.

Decision:

- runtime code does not select CoreAudio, ALSA, PulseAudio, or another Linux audio layer explicitly. PortAudio and the operating system own that selection, which keeps application configuration portable to Raspberry Pi OS.

### 2026-08-03 — Standalone toggle terminal cleanup

Implemented:

- removed raw terminal mouse-movement tracking from `toggle.py`;
- routed audio callback and stream diagnostics through `oculizer.log` instead of writing tracebacks into the curses terminal;
- configured the standalone selector with a file-only logging handler before curses starts.

Cause:

- the raw `1003` mouse-tracking mode can expose terminal escape sequences as visible garbage when terminal and curses mouse handling differ.

Validation:

- the standalone selector no longer emits raw mouse-mode escape sequences;
- keyboard navigation remains enabled.

Follow-up validation showed that click and wheel protocols were also exposed as raw sequences by the development terminal. Mouse handling was therefore disabled completely. The standalone selector is keyboard-only to remain predictable in integrated terminals, Linux consoles, and SSH sessions.

### 2026-08-03 — Phase 2 manual-selector audio isolation

Implemented:

- prevented `toggle.py` in `qlc-osc` mode from resolving or opening an unused audio input;
- retained audio capture when direct Enttec rendering or scene prediction actually consumes it;
- kept the Oculizer worker alive for manual scene events and clean shutdown without creating a PortAudio stream.

Cause:

- the phase-2 manual QLC+ selector opened BlackHole at the FFT sample rate despite having no OSC audio modulation or prediction consumer;
- CoreAudio terminated the process natively with a bus error shortly after this unnecessary stream opened.

Validation requirement:

- QLC+ manual mode must start, remain stable, and stop without probing audio or serial hardware;
- focused tests make both audio-device and Enttec initialization fail if either is attempted in this mode.

Manual validation:

- the QLC+ selector starts successfully on macOS with an explicit QLC+ configuration;
- the operator can navigate the keyboard-only scene grid, select an item, and confirm it with `Enter`;
- `Ctrl+T` returns from the selector and the application shuts down without opening audio or direct-DMX hardware;
- no QLC+ scene change is expected yet because scene-to-OSC mapping begins in phase 3.

### 2026-08-03 — QLC+ profile separation and headless-service requirements

Roadmap decision:

- phase 3 will stop loading Enttec fixture profiles in `qlc-osc` mode and introduce a hardware-independent logical scene mapping;
- the QLC+ launch path will not ask the operator to choose a DMX fixture profile;
- scene commands will be separated from curses so manual and automatic callers share the same backend behavior;
- phase 4 will provide a non-interactive runtime with signal-aware safe shutdown;
- phase 8b will package that runtime and QLC+ as ordered Raspberry Pi systemd services with no TTY requirement.

Rationale:

- QLC+ owns fixtures, patching, DMX channels, and hardware addresses in the hybrid architecture;
- a production Raspberry Pi must start and recover without an interactive terminal or operator input.

### 2026-08-03 — Phase 3 manual QLC+ scene implementation

Implemented:

- added validated logical scene-map parsing in `oculizer/light/scene_map.py`; its original standalone mapping was later consolidated into `config/qlc_config.json`;
- mapped the reference logical `party` scene to the already validated `/test` QLC+ toggle and added the logical `off` action;
- implemented complete configurable press/release pulses, previous-scene deactivation, logical active-state tracking, duplicate suppression, unmapped-scene handling, off, and blackout behavior in `QLCOscBackend`;
- made `Oculizer.change_scene()` the curses-independent scene command path and preserved SceneManager state when output activation fails;
- stopped loading fixture profiles in QLC+ mode and retained platform profile defaults only for Enttec;
- restricted the QLC+ selector to mapped logical scenes and made `Ctrl+R` reload both scene files and the logical mapping;
- corrected SceneManager reloads to use their resolved scene-directory path rather than the process working directory;
- exposed an explicit mapping path from both application entry points; this was later replaced by the unified `--qlc-config PATH` option.

Validated:

- 23 focused tests pass across mapping validation, backend transitions, OSC transport, configuration, hardware isolation, and shutdown;
- Python compilation passes for both entry points and all phase-3 modules;
- tests verify that a transition pulses the previous toggle before the next toggle, a duplicate selection sends nothing, `off` clears the tracked scene, and an unmapped scene preserves state;
- tests verify that QLC+ mode loads neither an Enttec fixture profile nor audio or serial hardware;
- CLI tests verify that QLC+ leaves the fixture profile unset while Enttec retains its macOS default.

Known integration boundary:

- the original reference workspace provided only `/test`; the current default fallback is `ambient1` at `/oculizer/scenes/ambient1`, while `off` follows its own configured OSC path like an ordinary scene intent;
- QLC+ toggle state is assumed to be off at application startup because OSC feedback is deferred to the state/robustness phase.

Remaining validation:

- none; the operator confirmed that `party`, duplicate suppression, `off`, and reload behave as documented against QLC+ on macOS.

Naming decision:

- the mapping remains under `config/` because it routes logical scenes into a deployment-specific QLC+ workspace;
- routing configuration remains distinct from artistic scene definitions under `scenes/` and now lives under `routing` in `config/qlc_config.json`;
- manual selection in `toggle.py` is both an integration test surface and an operator override; phase 4 will drive the same command layer automatically from audio predictions.

### 2026-08-03 — Phase 4 automatic routing and headless runtime

Implemented:

- added `AutomaticSceneRouter`, a curses-independent coordinator for smoothed predictions, resolved output targets, duplicate suppression, and manual override;
- connected the existing interactive automatic loop and integrated selector to the same `Oculizer.change_scene()` path used by phase 3;
- added explicit logical fallback routing; it now resolves all currently unmapped predictions to `ambient1` at `/oculizer/scenes/ambient1`;
- retained the predictor's existing cache smoothing and corrected single-stream prediction resampling to use the actual capture sample rate;
- opened the primary audio stream at the device's native sample rate and resampled into the configured 16 kHz analysis rate, avoiding assumptions about CoreAudio or Linux device rates;
- added `oculizer_service.py` and `HeadlessOculizerService` for non-interactive prediction and QLC+ control;
- added clean `SIGINT` and `SIGTERM` handling with shared worker shutdown and bounded join behavior;
- removed mouse protocol handling from the integrated selector and retained keyboard override controls.

Validated:

- 31 focused tests pass across automatic fallback routing, deduplication, manual override and return, headless clean stop, unexpected worker exit, phase-3 mapping, hardware isolation, and OSC UDP transport;
- compilation passes for both interactive entry points, the headless entry point, and all routing modules;
- the headless CLI exposes configuration, audio, predictor, OSC, and scene-map overrides without importing curses runtime behavior.

Known integration boundary:

- `wave` is explicitly routed to `/oculizer/scenes/wave`; all other semantic predictions without an explicit routing entry resolve to the `ambient1` QLC+ control;
- the reference QLC+ workspace still exposes only one active test function, so richer semantic transitions require additional QLC+ functions and mappings.

Manual validation:

- the headless runtime starts with BlackHole, loads predictor v4, and produces live predictions;
- automatic output routing activates the expected reference QLC+ control without duplicate pulses;
- sustained silence activates the configured `silent` scene;
- signal-driven shutdown returns cleanly to the terminal;
- terminal logs remain readable after the CRLF and predictor-output cleanup.

### 2026-08-03 — Headless log readability

Implemented:

- configured the headless entry point with explicit CRLF line endings so every terminal log returns to column zero;
- captured direct stdout/stderr emitted by historical predictor implementations and EfficientAT model construction;
- retained captured third-party details at debug level and kept failures available in error logs without printing the complete neural-network representation during normal startup.

Observed cause:

- the development terminal treated line-feed-only output as a vertical move without a carriage return, producing progressively indented lines;
- EfficientAT also printed the full DyMN architecture directly to stdout, making the startup output unnecessarily large.

### 2026-08-03 — Configurable silence routing

Implemented:

- added a validated `audio.silence` policy to `config/oculizer.json`;
- made the silence scene user-selectable rather than hard-coding blackout or the then-current `off` name;
- added a configurable entry threshold, minimum duration, and higher resume threshold for hysteresis;
- measured RMS continuously on both single-stream and separate prediction inputs;
- gave manual override priority over silence routing and silence routing priority over raw model classification;
- connected the policy to both interactive automatic operation and the headless service.
- suspended heavy inference, cleared smoothing state, and drained queued audio while silence is active;
- resumed inference from fresh audio only after crossing the configured resume threshold.

Decision:

- an inference already in progress may emit one final raw label after silence activation, but subsequent silent classification is suspended;
- a custom silence scene must exist as an Oculizer semantic scene and have a QLC+ mapping for the deployment.

### 2026-08-03 — Speech-aware routing roadmap decision

Decision:

- add phase 4b for spoken-announcement detection when speech and music share the same input mix;
- reuse the 527 AudioSet logits already produced by EfficientAT, including `Speech`, speech subcategories, `Singing`, and `Music`;
- classify singing as music, use configurable confidence and timing hysteresis, and preserve the current scene for ambiguous mixed content;
- keep the announcement scene configurable and retain manual override as the highest-priority operator action.

Rationale:

- the current clustering path discards the AudioSet logits even though the loaded model has already computed them;
- reusing those outputs should add little inference overhead and avoids requiring a separate microphone feed or another large model.

### 2026-08-03 — Phase 4 live validation completed

Validated:

- live BlackHole input and predictor v4 operate through the non-interactive service;
- QLC+ receives the automatically resolved scene without repeated toggle commands;
- silence routing selects `silent` while raw silent classifications no longer control lighting output;
- the operator accepted the phase-4 behavior and authorized progression to phase 4b.

### 2026-08-03 — Phase 4b AudioSet score extraction

Implemented:

- retained predictor-v4 AudioSet probabilities alongside the existing embedding and cluster result;
- exposed aggregated `speech`, `singing`, and `music` scores to the Oculizer runtime;
- aggregated singing into the music score rather than the speech score;
- resolved class indexes from EfficientAT's packaged label names instead of hard-coding numeric positions.

This score extraction was subsequently connected to configurable confidence, margin, duration, release, and announcement-scene routing in the automatic coordinator.

Live-validation correction:

- reset speech-active timing and cached AudioSet scores across silence transitions;
- require a fresh post-silence inference window and the complete configured speech duration before activating `announcement`;
- prevent stale pre-silence speech confidence from activating the announcement scene immediately after audio resumes.

### 2026-08-03 — Historical explicit QLC+ blackout for the former `off` route

Implemented:

- changed the configured blackout OSC address to `/blackout`;
- initially made the former logical `off` route assert blackout; this historical policy was later superseded by the transport-neutral configured-path/caption behavior documented in Phase 8a.2 and the route was renamed `silent` on 2026-08-10;
- made the next ordinary scene clear blackout before activating its QLC+ control;
- tracked blackout state locally to avoid leaving QLC+ blacked out after music resumes.

### 2026-08-03 — Responsive prediction timing

Implemented:

- made the rolling prediction window configurable as `audio.prediction.window_seconds`;
- changed the default window from four seconds to two seconds;
- changed speech entry from one second to 0.5 seconds and release from two seconds to 0.75 seconds;
- sized the audio cache at the actual source sample rate before inference-time resampling.

Tradeoff: shorter windows improve voice-to-music transitions but can reduce classification stability. Operators can increase the window and timing values per deployment.

Validated:

- confirmed that no `.venv` file is tracked by Git;
- confirmed that Git ignores the complete `.venv` tree.

### 2026-08-03 — Phase 4b live validation and unified QLC+ configuration

Implemented:

- consolidated OSC transport, global QLC+ controls, and logical scene routing into `config/qlc_config.json`;
- added the validated `transport`, `controls`, and `routing` configuration sections;
- replaced `--osc-config` and `--scene-map` with the single `--qlc-config PATH` option in all application entry points;
- retained host, port, and dry-run command-line overrides;
- removed the two superseded QLC+ JSON files so there is one deployment configuration source;
- added unified-configuration parsing and validation tests.

Live validation:

- the operator confirmed working transitions between announcement, music, and silence with QLC+ on macOS;
- phase 4b is complete and continuous master modulation is now the next implementation phase.

### 2026-08-03 — Speech transition stability correction

Observed:

- a voice-only recording repeatedly produced low-level `wave` cluster predictions;
- the router briefly activated their `party` fallback before confirming speech and whenever speech confidence dipped between phrases;
- music confidence remained near zero during those dips, so the scene changes were not supported by semantic evidence.

Implemented:

- hold the current scene while dominant speech is completing its entry duration;
- preserve `announcement` across ambiguous or low-confidence voice windows;
- begin the configured release timer only when music is dominant over speech;
- allow ordinary cluster scenes only when music is dominant;
- added regression coverage for pre-announcement cluster leakage and speech-gap oscillation.

Validated:

- the operator replayed the voice-only recording and confirmed that the correction substantially eliminates `party` leakage before `announcement` and between spoken phrases;
- phase 4b, including speech-transition stabilization, is accepted as complete.

### 2026-08-03 — Phase 5 master modulation implementation

Implemented:

- added a configurable RMS-to-master modulation pipeline independent of scene prediction;
- normalized the configured RMS floor and ceiling into `[0.0, 1.0]`;
- added exponential smoothing, a change threshold, and a configurable rate capped at 60 Hz with a 25 Hz reference setting;
- send `silence_value` immediately below the RMS floor and `shutdown_value` before closing the lighting backend;
- added `/oculizer/master` to the unified QLC+ global controls;
- connected modulation to both the headless service and interactive automatic runtime;
- added focused tests for normalization, smoothing, rate limiting, deduplication, safe shutdown, configuration validation, and configurable OSC paths.

QLC+ contract:

- the operator created and mapped a QLC+ 5 Grand Master slider to `/oculizer/master`;
- the recommended widget uses Reduce mode and affects Intensity channels only.

Remaining validation:

- the operator confirmed that the QLC+ Grand Master follows live audio smoothly, uses the configured safe behavior, and performs as expected;
- no further calibration was required with the reference values;
- phase 5 is complete and phase 6 frequency-band modulation is now active.

### 2026-08-03 — Phase 6 bass modulation slice

Implemented:

- retained the unscaled Mel spectrum already computed by the audio callback and exposed it to the modulation layer without network I/O in the callback;
- added configurable bass, mid, and high ranges with independent enable switches and normalization bounds;
- added a shared rate limit, smoothing factor, change threshold, silence value, and shutdown value;
- initially enabled only the 20–250 Hz bass slice for the first live validation, then narrowed it during calibration as recorded below;
- added `/oculizer/bass`, `/oculizer/mid`, and `/oculizer/high` to unified QLC+ controls while keeping mid and high transmission disabled;
- connected band modulation to headless and interactive automatic operation;
- added unit coverage for configuration validation, enabled-band selection, normalization, and safe shutdown.

Embedded-resource decision:

- phase 6 reuses the 128-bin Mel spectrum already produced by the audio callback and does not add another FFT, audio stream, model, thread, or queue;
- band extraction scans only the existing small spectrum at the configured modulation rate, currently 25 Hz;
- future analysis or model additions require an explicit CPU and memory tradeoff review against the Raspberry Pi 5 production target before implementation.

Remaining validation:

- enable and validate the mid band as the next isolated modulation slice;
- keep high disabled until mid has a concrete and validated artistic use.

Live calibration correction:

- the initial 20–250 Hz absolute-level response detected kicks but remained elevated while other instruments were audible;
- narrowed the bass range to 35–180 Hz;
- changed the bass response to transient mode, which subtracts a slowly adapting energy baseline before normalization;
- retained level mode for the disabled mid and high bands;
- added regression coverage confirming that sustained bass energy produces a decreasing output after its initial transient.

Live validation:

- the operator confirmed that `/oculizer/bass` follows kicks accurately after transient-mode calibration;
- the bass fader no longer remains excessively elevated during the rest of the music;
- the bass slice is accepted as complete.

### 2026-08-03 — Phase 6 mid validation slice

Implemented:

- enabled the existing 180–2,000 Hz mid band at `/oculizer/mid` after the operator created its QLC+ fader;
- retained level response for the initial validation so the fader represents sustained mid-band energy;
- reused the same Mel spectrum, update loop, and state dictionaries as bass, adding no FFT, audio stream, model, thread, or queue;
- the incremental runtime cost is limited to one normalization, smoothing, threshold comparison, and optional OSC float message per 40 ms update.

Remaining validation:

- confirm the mid fader's usable range and stability with live music;
- tune its normalization bounds or response mode only if the observed behavior requires it.

Live calibration correction:

- the initial absolute-level mid response remained almost continuously at 100% because the broad 180–2,000 Hz range contains energy from most instruments;
- changed mid to the existing transient response without adding spectral work or runtime infrastructure;
- raised the mid transient ceiling from `0.02` to `0.1` to provide headroom for its wider band;
- retained the same low-cost slowly adapting baseline used by bass;
- validation must confirm that mid accents now use the fader range without remaining saturated.

Live validation:

- the operator confirmed that transient response prevents `/oculizer/mid` from remaining at 100% and provides a useful live response;
- the mid slice is accepted as complete;
- `SIGINT` shutdown was rechecked during the same live session and exits correctly after graceful cleanup.

The high band remained disabled at this point and was enabled only after its QLC+ fader was created, as recorded below.

### 2026-08-03 — Phase 6 high validation slice

Implemented:

- enabled the existing 2,000–8,000 Hz high band after the operator created its QLC+ fader;
- selected transient response and a `0.1` normalization ceiling from the start to avoid the broad-band saturation observed during mid validation;
- reused the same spectrum and modulation loop, adding only one small state entry and at most one thresholded OSC float message per 40 ms update;
- added no FFT, model, audio stream, thread, or queue.

Initial validation targets:

- confirm that cymbals, hi-hats, consonants, and other high-frequency accents produce a useful range without sustained saturation;
- calibrate the high ceiling or baseline only if required by live behavior.

Live calibration correction:

- synthetic checks confirmed that 3–7 kHz tones are correctly isolated in the high band;
- sustained tones above approximately 3.5 kHz appeared to cut out because transient mode intentionally absorbed continuous energy into its slow baseline;
- changed only high to level response so sustained cymbals and high-frequency tones remain represented;
- raised the high ceiling from `0.1` to `0.5` to accommodate absolute high-band energy without immediate saturation;
- this changes only arithmetic and configuration; it adds no embedded CPU or memory infrastructure.

Second live calibration:

- level mode correctly retained sustained high-frequency content, but the `0.5` ceiling used too little of the fader range with the test input;
- reduced the high ceiling to `0.1`, increasing sensitivity by a factor of five;
- a sustained test tone is expected to produce a stable level, while musical high-frequency transients should create the visible movement.

Live validation:

- after lowering the high level ceiling to `0.1`, the operator confirmed a useful and responsive fader range;
- `/oculizer/bass`, `/oculizer/mid`, and `/oculizer/high` are all accepted with live audio and QLC+;
- phase 6 is complete without adding another FFT, model, audio stream, thread, or queue;
- phase 7 robustness and state handling is now active.

### 2026-08-03 — Phase 7 deterministic lifecycle and UDP recovery

Implemented:

- assert QLC+ blackout immediately when the OSC backend is initialized;
- send safe zero values for master, bass, mid, and high before starting audio processing;
- on shutdown, send continuous safe values, deactivate the locally tracked toggle scene, assert blackout, and then close the OSC socket;
- make backend shutdown idempotent;
- periodically resend unchanged absolute fader values using configurable one-second refresh intervals;
- retain change thresholds and 25 Hz rate limits for ordinary modulation updates.

UDP policy:

- absolute fader packets are safe to refresh and recover automatically after an isolated UDP loss;
- toggle scene packets are not retried blindly because a duplicate can invert QLC+ state;
- a heartbeat alone cannot prove or restore toggle state after QLC+ restarts;
- full scene recovery therefore requires explicit QLC+ state feedback or a future idempotent scene-control contract.

Embedded cost:

- no new thread, timer, socket, queue, or analysis pass was added;
- the reference configuration adds at most four unchanged OSC float messages per second;
- state consists only of last-send timestamps for the four continuous controls.

Remaining validation:

- verify startup blackout, initial zero values, active-scene deactivation, and shutdown blackout with live QLC+;
- restart QLC+ while Oculizer remains active and record which absolute controls recover;
- decide whether toggle-scene feedback is required after observing the restart boundary.

Live validation and final policy:

- startup blackout and initial zero values behave as designed;
- live master, bass, mid, and high controls recover within the periodic refresh window;
- graceful `SIGINT` shutdown resets continuous controls, deactivates the tracked toggle, and asserts blackout;
- after QLC+ restarts while Oculizer remains active, absolute controls recover but toggle scenes do not, matching the documented UDP boundary;
- do not add a heartbeat or feedback loop without a concrete QLC+ consumer;
- in Raspberry Pi production, systemd must couple the QLC+ and Oculizer lifecycles so a QLC+ restart also restarts Oculizer and reapplies deterministic startup state;
- an uncatchable hard kill cannot run application cleanup; service restart must restore safety immediately, while complete host power loss is handled by loss of QLC+/DMX output;
- phase 7 is accepted as complete; phase 8a control work precedes phase 8b deployment.

## Instructions for developers and coding agents

### Before making changes

1. Read `README.md` and `DEVELOPMENT.md` in full.
2. Check `git status --short` and preserve user changes.
3. Identify the active phase and its exit criterion.
4. Inspect the actual code: this document records intent but does not authorize inventing an absent API.
5. For QLC+ 5, verify uncertain behavior against official documentation or a reproducible local test.
6. Keep every repository documentation update in English, even when the user gives instructions in French or another language.

### During implementation

- work on one phase or independently testable slice at a time;
- treat Raspberry Pi 5 resource limits as a permanent design constraint, even during macOS development;
- reuse already computed audio features and shared buffers before adding another analysis pass, model, thread, queue, or high-frequency timer;
- keep CPU, memory, allocation rate, latency, and thermal impact proportional to the feature's validated artistic value;
- identify resource-intensive proposals before implementation, explain the expected CPU and memory tradeoffs to the operator, and request approval when a meaningful compromise is required;
- provide a lower-cost alternative whenever a proposed implementation could materially affect embedded performance;
- record significant resource decisions and measurements in this document;
- retain Enttec output until its removal is explicitly approved;
- do not move QLC+-specific logic into `mapping.py`;
- keep network transport out of the audio callback;
- never block the audio callback on network or disk I/O;
- centralize defaults in configuration;
- use `pathlib` for local paths;
- do not hard-code absolute user paths;
- consider Linux ARM64 when selecting dependencies;
- type and clamp OSC values;
- rate-limit and deduplicate continuous output;
- define safe values and cleanup behavior for shutdown;
- add unit tests that do not require QLC+ or hardware;
- isolate integration tests requiring QLC+ or DMX;
- update both canonical Markdown files as implementation progresses;
- write every documentation change in English regardless of the conversation language.

### Minimum validation

Depending on the change, run:

- focused unit tests;
- existing fallback tests;
- Python compilation or import checks for modified files;
- dry-run tests;
- QLC+ macOS tests for OSC milestones;
- a check that OSC mode never opens a DMX port;
- clean-shutdown and safe-value checks;
- CPU and memory observations for new continuous processing, additional models, audio-analysis passes, or background workers, with Raspberry Pi 5 as the production reference.

Do not mark a roadmap checkbox complete without recording the executed test and result in the implementation log.

### Documentation maintenance

- `README.md` documents only behavior users can actually run.
- `DEVELOPMENT.md` owns architecture, decisions, roadmap, and the implementation log.
- For developers and coding agents: keep installation commands, configuration entry points, operator controls, and troubleshooting steps in `README.md`; put installation rationale, internal algorithms, implementation mechanics, architectural explanations, trade-offs, and validation evidence in `DEVELOPMENT.md`.
- Do not explain how a user-facing feature is implemented in `README.md` when the user only needs to know what it does and how to enable, configure, or disable it.
- update documentation in the same change as the corresponding implementation;
- move a feature from “not implemented” to “implemented” in both documents when its code has been validated;
- update roadmap status and checkboxes as work progresses;
- add an implementation-log entry with tests and results for every meaningful delivery;
- do not create one-off Markdown summaries; integrate information into the appropriate canonical file;
- when a decision changes, update the architecture and log instead of retaining contradictory versions;
- all documentation must remain in English, even when requirements are provided in French or another language.

### 2026-08-05 — Bounded automatic scene duration

Implemented:

- added `--scene-max-duration SECONDS` to interactive and headless runtimes, initially with a 30-second default later raised to 40 seconds, and range validation from 0.5 to 3600 seconds;
- added optional per-scene `max_duration_seconds` overrides in `scenes/<name>.json`, with an absent field inheriting the global runtime value;
- limited only ordinary automatic music scenes, leaving manual override, silence, and announcement safety routes exempt;
- selected the most recent distinct mapped prediction when a scene expires, falling back deterministically to `ambient1` rather than choosing an unrelated random scene;
- blocked the expired resolved output target for ten seconds to prevent immediate ping-pong, including when several predictions share one fallback target;
- reused the existing bounded prediction cache and monotonic routing clock, adding no FFT, model, worker, or unbounded history.

Embedded-system impact:

- negligible CPU and memory cost: one short reverse scan of the existing prediction cache only when routing evaluates an expired or temporarily blocked target, plus a small bounded timestamp dictionary;
- no additional audio processing or network traffic is produced until an actual replacement scene is activated.

Validation:

- focused automatic-routing tests cover recent mapped replacement, deterministic `ambient1` fallback, re-entry blocking, and per-scene duration override;
- interactive and headless entry points compile and advertise the new option.
- the complete test suite passes: 146 tests, including local UDP OSC coverage.

### 2026-08-05 — Complete v4 QLC+ routing catalog

Implemented:

- expanded `config/qlc_config.json` to every unique logical scene name emitted by the v4 mapping, now 48 after canonical-name normalization;
- derived every toggle address consistently as `/oculizer/scenes/<scene-name>` while preserving the dedicated silence and `announcement` routes; the silence route was later renamed `silent`;
- marked the 17 names shared by v4 and the approved v6 mapping with the temporary metadata field `"implemented": false` for manual QLC+ widget tracking;
- intentionally left `implemented` outside runtime behavior: the scene-map parser ignores unknown metadata; OSC routing is determined by `OSCaction` and `OSCPath`, while WebSocket routing is determined by caption and discovered QLC+ type;
- normalized the historical `disodream` and `full` identifiers in the subsequent scene-consistency audit.

Validation:

- JSON syntax validation passes;
- set comparison confirms zero missing or extra v4 routing names, all 17 v4/v6 intersections marked, and no v4-only scene marked as v6-shared.
- 20 focused QLC configuration, mapping, backend, and CLI tests pass.

### 2026-08-05 — Cross-version scene-name consistency audit

Implemented:

- normalized `disodream` to `discodream` and `full` to `fullstrobe` in v4, v5, and the archived vday mapping;
- normalized `laserstrobe` to `laser_strobe` in the archived v3 mapping;
- corrected internal scene names so `scenes/smut.json` declares `smut` and `scenes/rockville_example.json` declares `rockville_example`;
- added the missing `bass_hopper_red` artistic definition required by the archived v3 mapping, deriving its established hopper behavior from `bass_hopper_blue` with a red/orange palette;
- synchronized the complete v4 QLC+ routing catalog by removing the obsolete `disodream` entry and replacing `full` with `fullstrobe` and its canonical OSC path;
- regenerated `reports/scene_analysis.json` from all 127 scene definitions.

Validation:

- every predictor mapping JSON, including archived mappings, now resolves exclusively to an existing `scenes/*.json` file;
- every scene file's stem now matches its internal `name` field;
- the QLC+ catalog exactly matches all 48 unique canonical v4 names and retains all 17 v4/v6 tracking markers.
- the complete suite passes: 146 tests, including local UDP OSC coverage.

### 2026-08-05 — Complete v6 QLC+ routing catalog

Implemented:

- added the 13 v6-only scene names missing from the v4-based QLC+ catalog;
- derived their OSC paths consistently as `/oculizer/scenes/<scene-name>`;
- applied the temporary boolean metadata field `"implemented": false` to all 30 scenes emitted by v6, including the 17 names shared with v4;
- retained `implemented` as operator-only tracking metadata ignored by runtime parsing and routing.

Validation:

- the QLC+ catalog now covers the complete 61-scene union of v4 and v6;
- set checks confirm zero missing v4 or v6 names, canonical paths for every entry, all 30 v6 flags present, and a corresponding artistic scene file for every mapped name.
- 20 focused QLC configuration, scene-map, backend, and CLI tests pass.

### 2026-08-05 — v6 scene-duration safety policy

Implemented:

- assigned `max_duration_seconds: 8` to all 17 v6 scenes whose recursive artistic definition contains an active `strobe`, `panel_strobe`, or `bar_strobe` value;
- assigned `max_duration_seconds: 15` to the non-strobing alternating racers `green_speedracer`, `orb_racer`, `red_speedracer`, and `white_speedracer`, plus the high-energy `discolaser` and `discodream` scenes;
- left the remaining seven calmer v6 scenes without an override so they inherit the global duration, now 40 seconds;
- corrected the misleading `wave` description from “RGB strobes” to “RGB lights” because every strobe value in that slow scene is explicitly zero;
- added a static policy test that discovers active strobe keys recursively, including fixture-specific nested fields, and guards both the 8-second and reviewed 15-second sets.

Embedded-system impact:

- none beyond the already implemented duration router; these are load-time JSON values and add no continuous computation.

Validation:

- all scene JSON files parse successfully;
- the two focused v6 duration-policy tests pass.
- the complete suite passes: 148 tests, including local socket and UDP OSC coverage.

### 2026-08-05 — Calmer transition presets and longer default scenes

Implemented:

- changed `normal` from cache `10`, rate `6/10`, throttle `3/2` to cache `15`, rate `4/15`, throttle `2/4` in the shipped configuration;
- changed `calm` from cache `25`, rate `4/15`, throttle `2/3` to cache `35`, rate `2/20`, throttle `1/10`, eliminating its initial multi-change burst and allowing only one recovered transition credit every ten seconds;
- aligned the built-in fallback preset definitions with the shipped configuration so missing configuration and normal operation retain the same policy hierarchy;
- raised the global automatic scene maximum from 30 to 40 seconds in the core router plus interactive and headless CLI defaults;
- retained all explicit 8-second strobe and 15-second energetic-scene safety overrides.

Rationale and embedded impact:

- the concert log showed effective scene activations roughly every three seconds under the previous policies, with many average scene stays below three seconds;
- the new values reduce churn through existing bounded counters and a slightly larger deque of scene-name references; they add no inference, FFT, thread, timer, or network work and have negligible Raspberry Pi 5 memory impact.

Validation:

- 51 focused runtime-configuration, runtime-control, transition-limit, CLI, and automatic-routing tests pass;
- both interactive and headless help output report the new 40-second default;
- the complete suite passes: 148 tests, including local socket and UDP OSC coverage.

### 2026-08-05 — Cross-platform native audio shutdown ownership

Implemented:

- separated an audio source stop request from native stream destruction;
- made the Oculizer runtime thread the sole owner responsible for stopping and closing live PortAudio streams;
- removed caller-thread closure of the prediction stream during `SIGINT` shutdown;
- retained immediate cooperative cancellation for WAV sources;
- added regression coverage proving that a stop request cannot close either live capture stream from the calling thread.

Rationale and platform impact:

- macOS crash diagnostics showed a `free_tiny_botch` abort inside PortAudio `CloseStream` while `Ctrl+C` shutdown allowed the controller thread and runtime thread to close the same stream concurrently;
- the ownership rule is backend-neutral and applies equally to CoreAudio on macOS and PortAudio-hosted ALSA or PulseAudio on Raspberry Pi OS;
- the change adds no processing thread, polling loop, or steady-state CPU/memory cost.

Validation:

- the complete suite passes: 150 tests, including the native-stream ownership regressions.

### 2026-08-05 — v6 default predictor

Implemented:

- made v6 the default predictor for the interactive application, headless service, controller constructor, predictor registry, and compatibility import;
- retained `--predictor-version v4` and `--predictor-version v5` as explicit comparison and compatibility choices;
- updated user-facing examples to omit the predictor option where the v6 default is intended;
- added regression coverage for both CLI defaults and the predictor registry default.

Deployment note:

- the approved v6 artefact set and `.ready` marker must remain installed on both macOS and Raspberry Pi deployments; startup fails explicitly rather than silently falling back to a different model if the selected artefacts are unavailable.

Validation:

- both CLI help screens report v6 as the default;
- the complete suite passes: 151 tests.

### Expected completion report

At the end of a work slice, record or report:

```text
Result
- delivered behavior

Files
- created or modified files

Validation
- commands run and results

Decisions
- structural choices or assumptions

Next
- next validation gate
```

## Existing tests and tools

Run the profile-fallback regression coverage through the standard test suite:

```bash
python -m unittest tests.test_profile_fallbacks
```

The repository also contains maintenance and reporting tools:

```bash
python scripts/analyze_scenes.py
python scripts/generate_fallbacks.py
```

These tools are not part of the test suite. `scripts/fix_pickle_files.py` is a one-off maintenance tool for serialized models, not a standard test.

## Open decisions

- final shape of the QLC+ OSC input profile;
- exact press/release or toggle semantics for buttons;
- location and version-control policy for the `.qxw` workspace;
- state-feedback strategy;
- long-term retention of the Enttec backend;
- production audio device and routing on Raspberry Pi;
- graphical or headless QLC+ operation on the target.

Resolve these decisions when they become necessary, then record the outcome in this document.
