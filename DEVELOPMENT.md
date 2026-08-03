# Oculizer development guide

This document is the project's technical source of truth. It describes the current architecture, the QLC+ 5 target, the implementation roadmap, recorded decisions, and the rules that developers and coding agents must follow when continuing the work.

## Documentation language policy

All repository documentation must remain in English. This applies to `README.md`, `DEVELOPMENT.md`, code comments added as documentation, configuration guidance, roadmap entries, and implementation log entries.

User requests and development conversations may be written in French or any other language. Their language must not be copied into repository documentation: translate the relevant information into English before updating either Markdown file. Do not switch the documentation language to match the language of a request.

## Product objective

Build a hybrid system in which:

- Oculizer captures and analyzes music;
- the model predicts a semantic scene;
- the operator can manually override a scene;
- Oculizer sends events and a small number of continuous modulations over OSC;
- QLC+ 5 owns lighting functions, fixture patching, and DMX output.

During development, Oculizer and QLC+ run on the same Mac. In production, both will run locally on a Raspberry Pi 5 with Raspberry Pi OS. The default OSC address must therefore be `127.0.0.1`, while remaining configurable.

## Verified current state

### Implemented

- single-stream and dual-stream audio capture;
- mel-scaled FFT and adaptive normalization;
- prediction with `v1`, `v3`, `v4`, `v5`, and `vday`;
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

### Not implemented

- Oculizer OSC client;
- QLC+ OSC configuration;
- abstract lighting backend;
- `enttec`/`qlc-osc` command-line selection;
- Oculizer-scene to QLC+-function mappings;
- audio modulations sent to QLC+;
- QLC+ state feedback or synchronization;
- Raspberry Pi production services.

Never describe these items as available before they have been implemented and validated.

## Current architecture

```text
oculize.py / toggle.py
        │
        ├── SceneManager
        │     ├── scenes/*.json
        │     └── profile_fallbacks.json
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

## QLC+ 5 target architecture

```text
                 ┌─ events: scene, flash, blackout ──────┐
Audio → Oculizer ┤                                        ├→ OSC → QLC+ 5 → DMX
                 └─ values: master, bass, speed, etc. ───┘
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
- mapping between logical names and OSC paths.

### Proposed initial OSC contract

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

## Roadmap and validation gates

A step is complete only after implementation, relevant automated tests, and real QLC+ validation where required.

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
config/qlc_osc.json
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

Status: **not started**

- [ ] add a scene-to-OSC-path mapping;
- [ ] deactivate the previous scene before activating the new one;
- [ ] avoid duplicate commands when selection does not change;
- [ ] define behavior for an unmapped scene;
- [ ] implement `off` and blackout behavior;
- [ ] validate navigation, clicking, searching, and reloading.

Exit criterion: a complete manual session controls QLC+ without Oculizer opening a DMX interface.

### Phase 4 — Automatic prediction

Status: **not started**

- [ ] connect predicted transitions to the same backend;
- [ ] preserve smoothing and fallbacks;
- [ ] preserve manual override;
- [ ] log the requested scene, fallback, and activated QLC+ scene;
- [ ] send nothing when the logical state has not changed.

Exit criterion: automatic transitions and return from override are consistent in QLC+.

### Phase 5 — First continuous modulation

Status: **not started**

- [ ] begin only with `/oculizer/master`;
- [ ] normalize into `[0.0, 1.0]`;
- [ ] rate-limit output, initially targeting 20–30 Hz;
- [ ] add a change threshold and smoothing;
- [ ] send a safe value during shutdown;
- [ ] measure latency, CPU use, and regularity.

Exit criterion: a QLC+ slider follows audio without flicker or significant overhead.

### Phase 6 — Advanced modulations

Status: **not started**

- [ ] add bass, mid, and high one at a time;
- [ ] add speed or strobe only when the workspace consumes it;
- [ ] document the source, smoothing, and QLC+ destination of every value;
- [ ] remove any transmission without a validated artistic use.

### Phase 7 — Robustness and state

Status: **not started**

- [ ] define startup and shutdown policy for controlled functions;
- [ ] implement emergency blackout;
- [ ] add an optional heartbeat;
- [ ] define a strategy for lost UDP packets;
- [ ] add OSC feedback if necessary;
- [ ] keep logs and metrics concise;
- [ ] test abrupt shutdown and QLC+ restart behavior.

### Phase 8 — Raspberry Pi 5 production target

Status: **not started**

- [ ] validate every dependency on Linux ARM64;
- [ ] remove assumptions about macOS paths or devices;
- [ ] prepare reproducible installation;
- [ ] create separate systemd services for QLC+ and Oculizer;
- [ ] order startup and configure restart policies;
- [ ] accept the QLC+ `.qxw` workspace path through configuration or a command-line option;
- [ ] validate the configured workspace path before starting QLC+;
- [ ] load the configured QLC+ workspace automatically without assuming the reference workspace;
- [ ] validate audio on Raspberry Pi OS;
- [ ] monitor temperature, CPU, RAM, and latency during a long session;
- [ ] document operation and incident recovery.

Final criterion: a cold Raspberry Pi restart reaches an operational lighting system without local intervention.

## Implementation log

Add an entry for every meaningful change. Use an ISO date and separate delivered behavior, validation, and remaining work.

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
- added `config/qlc_osc.json` with loopback defaults and a namespaced blackout path;
- implemented OSC float encoding, normalized value clamping, press/release, level, blackout, dry-run, context-manager, and idempotent close behavior;
- made UDP send failures return `False` after logging instead of propagating into real-time application code;
- kept the milestone `/test` script self-contained so it can run by path without installing the Oculizer package;
- selected a standard-library implementation to avoid an unnecessary runtime dependency on macOS and Linux ARM64.

Validated:

- 10 unit and loopback UDP tests pass;
- configuration loading and validation pass;
- OSC encoding, clamping, dry-run, close behavior, and four-message UDP delivery pass;
- Python compilation and default configuration construction pass.

Remaining validation:

- none; the client has been validated against the `/test` control in QLC+ 5.2.2.

Observed QLC+ button semantics:

- a Virtual Console button configured as `Toggle Function on/off` toggles only on a press event (`1.0`);
- the release event (`0.0`) ends the physical input gesture but does not stop the Function;
- switching the Function off requires a second complete press/release gesture, not a release message by itself;
- phase 2 must model a `pulse` gesture separately from desired logical scene state and must avoid assuming that `0.0` means “deactivate” for toggle widgets.

### 2026-08-03 — Phase 1 QLC+ validation completed

Validated:

- `OscClient.from_file('config/qlc_osc.json')` successfully controls the `/test` Virtual Console button;
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
- kept QLC+ transport settings isolated in `config/qlc_osc.json`.

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

- `python toggle.py --output qlc-osc --osc-config config/qlc_osc.json` starts successfully on macOS;
- the operator can navigate the keyboard-only scene grid, select an item, and confirm it with `Enter`;
- `Ctrl+T` returns from the selector and the application shuts down without opening audio or direct-DMX hardware;
- no QLC+ scene change is expected yet because scene-to-OSC mapping begins in phase 3.

Validated:

- confirmed that no `.venv` file is tracked by Git;
- confirmed that Git ignores the complete `.venv` tree.

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
- clean-shutdown and safe-value checks.

Do not mark a roadmap checkbox complete without recording the executed test and result in the implementation log.

### Documentation maintenance

- `README.md` documents only behavior users can actually run.
- `DEVELOPMENT.md` owns architecture, decisions, roadmap, and the implementation log.
- update documentation in the same change as the corresponding implementation;
- move a feature from “not implemented” to “implemented” in both documents when its code has been validated;
- update roadmap status and checkboxes as work progresses;
- add an implementation-log entry with tests and results for every meaningful delivery;
- do not create one-off Markdown summaries; integrate information into the appropriate canonical file;
- when a decision changes, update the architecture and log instead of retaining contradictory versions;
- all documentation must remain in English, even when requirements are provided in French or another language.

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

The repository contains several historical validation scripts:

```bash
python test_fallbacks_simple.py
python test_fallbacks.py
python test_profile_fallbacks.py
python analyze_scenes.py
python generate_fallbacks.py
```

Before treating them as a complete suite, check their dependencies and behavior in the current environment. `scripts/fix_pickle_files.py` is a one-off maintenance tool for serialized models, not a standard test.

## Open decisions

- final shape of the QLC+ OSC input profile;
- exact press/release or toggle semantics for buttons;
- location and version-control policy for the `.qxw` workspace;
- state-feedback strategy;
- long-term retention of the Enttec backend;
- production audio device and routing on Raspberry Pi;
- graphical or headless QLC+ operation on the target.

Resolve these decisions when they become necessary, then record the outcome in this document.
