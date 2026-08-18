# Raspberry Pi shutdown lifecycle

## Purpose

`oculizer.service` must stop promptly during a normal service stop and during a full Raspberry Pi reboot or shutdown, even if the PipeWire/ALSA/PortAudio stack is disappearing at the same time.

The original failure mode was a roughly 30-second shutdown delay. Oculizer received `SIGTERM`, its prediction thread and QLC+ Native client stopped, but the main Oculizer audio worker could remain blocked in native PortAudio teardown. systemd then waited for the configured `TimeoutStopSec=30` before sending `SIGKILL`.

## Live audio shutdown design

`SoundDeviceAudioSource` keeps a single owner for native stream closure. This is important because closing the same PortAudio stream from two callers can create native double-close races on other platforms.

The lifecycle is now:

1. `Oculizer.stop()` clears the runtime flag and calls `audio_source.request_stop()`.
2. For a live `sounddevice` source, `request_stop()` starts one daemon helper that calls `stream.abort()` to interrupt capture promptly. It does not call `stream.close()`.
3. The Oculizer worker remains the lifecycle owner. When its context manager exits, `SoundDeviceAudioSource.stop()` waits a bounded time for the interrupt and then performs the final native close on one helper thread.
4. A normal context-manager exit that was not preceded by `request_stop()` keeps the graceful `stream.stop()` followed by `stream.close()` path.
5. If a native PortAudio call does not return within the configured bound, Oculizer logs the exact native stage and allows the application thread to continue shutting down instead of waiting indefinitely.

Default bounds are deliberately shorter than the headless service's existing five-second worker join:

- interrupt (`stream.abort()`): 0.5 seconds;
- final native shutdown: 2.0 seconds.

The native helper threads are daemon threads. If the host audio stack is already wedged, process teardown can therefore continue without creating another competing close of the same stream.

## Shutdown instrumentation

The service log identifies each native operation and its duration. Typical requested-shutdown messages are:

```text
PortAudio shutdown interrupt requested
PortAudio shutdown: stream.abort() started
PortAudio shutdown: stream.abort() finished in ...s
PortAudio shutdown: stream.stop() skipped after interrupt
PortAudio shutdown: stream.close() started
PortAudio shutdown: stream.close() finished in ...s
```

A graceful close that was not explicitly interrupted logs `stream.stop()` and `stream.close()` separately.

If a native operation exceeds its bound, the error names the stage, for example:

```text
PortAudio shutdown: stream.close() did not finish within 2.000s; leaving the native call on a daemon thread so application shutdown can continue
```

This makes Raspberry Pi shutdown logs useful even if the underlying PipeWire/ALSA/PortAudio condition is intermittent.

## systemd ordering

The installed system service already uses the service account's `XDG_RUNTIME_DIR` and D-Bus user-session address. The unit template now also starts after and wants `user@<uid>.service`.

Because systemd reverses `After=` ordering when units are stopped, this gives Oculizer an explicit opportunity to stop before the service user's manager—and therefore its user audio services—are torn down during system shutdown.

The installer substitutes `@SERVICE_UID@` with the selected service account UID, so a `pi` account with UID 1000 receives:

```text
After=network.target sound.target user@1000.service
Wants=network.target sound.target user@1000.service
```

## Validation policy

`TimeoutStopSec=30` remains unchanged during validation so the fix is measured against the original behavior rather than hidden by a shorter systemd timeout.

### Validation status

All three requested Raspberry Pi hardware regression cases were validated on 2026-08-18 with a live audio input.

#### Normal service stop

- `time sudo systemctl stop oculizer.service`: **2.820 seconds real time**;
- `stream.abort()` completed in **0.001 seconds**;
- `stream.close()` completed in **0.004 seconds**;
- the prediction thread stopped cleanly;
- the QLC+ Native client stopped cleanly;
- the headless runtime reported `Non-interactive Oculizer runtime stopped`;
- systemd reported `Deactivated successfully` and `Stopped oculizer.service`;
- no `Oculizer worker did not stop within five seconds`, systemd stop timeout, or `SIGKILL` was observed.

#### QLC+ already stopped

QLC+ was confirmed absent with `pgrep -af qlcplus-qml` before stopping Oculizer.

- `time sudo systemctl stop oculizer.service`: **4.096 seconds real time**;
- `stream.abort()` completed in **0.001 seconds**;
- the prediction thread stopped cleanly;
- the final `stream.close()` did **not** return within its **2.000-second** bound;
- Oculizer logged the exact blocked stage and left that native call on its daemon shutdown helper;
- the headless runtime still reported `Non-interactive Oculizer runtime stopped`;
- systemd reported `Deactivated successfully` and `Stopped oculizer.service`;
- no five-second worker warning, 30-second systemd timeout, or `SIGKILL` occurred.

This second case is important diagnostically: it reproduces the problematic native PortAudio behavior directly and identifies `stream.close()` as the blocking call in that run. It also confirms that the new bounded shutdown path prevents the blocked native close from delaying service shutdown to 30 seconds. The result does not indicate a dependency on QLC+; QLC+ was already absent before the stop began.

#### Full Raspberry Pi reboot

Oculizer and QLC+ were running normally, then the Raspberry Pi was rebooted and the previous boot journal was inspected with `journalctl -b -1 -u oculizer.service`.

- systemd sent Oculizer `SIGTERM` during the real shutdown;
- `stream.abort()` completed in **0.001 seconds**;
- `stream.close()` completed in **0.003 seconds**;
- the prediction processing thread stopped cleanly;
- the QLC+ Native connection was reset/disconnected while QLC+ was shutting down in parallel, then its client state reached `stopped`;
- the headless runtime reported `Non-interactive Oculizer runtime stopped`;
- systemd reported `Deactivated successfully` and `Stopped oculizer.service`;
- no five-second worker warning, 30-second stop timeout, or systemd `SIGKILL` occurred.

The `Connection reset by peer` message observed for the QLC+ Native socket during reboot is expected when QLC+ disappears in parallel and is not a shutdown failure. Oculizer continued through its normal teardown and systemd stopped the service cleanly.

The shutdown-delay fix is therefore considered hardware-validated for the three requested cases. A shorter `TimeoutStopSec` is no longer required to mask the original symptom; keeping the existing 30-second value remains conservative unless a separate operational reason justifies changing it.

## Regression procedure

### 1. Normal service stop — VALIDATED

```bash
sudo systemctl start oculizer.service
sudo systemctl stop oculizer.service
sudo journalctl -u oculizer.service -b -n 100 --no-pager
```

Expected result: Oculizer exits normally without reaching the 30-second systemd timeout. The PortAudio log should identify `abort()`/`close()` timings.

Observed result on 2026-08-18: PASS, with a 2.820-second command duration, `abort()` in 0.001 s and `close()` in 0.004 s.

### 2. QLC+ already stopped — VALIDATED

Stop QLC+ using the service owned by the QLC+ deployment, confirm that `pgrep -af qlcplus-qml` is empty, then stop Oculizer:

```bash
sudo systemctl stop oculizer.service
sudo journalctl -u oculizer.service -b -n 100 --no-pager
```

Expected result: Oculizer shutdown remains bounded and does not depend on QLC+ being alive.

Observed result on 2026-08-18: PASS for the service-lifecycle requirement. The command returned in 4.096 seconds even though `stream.close()` itself exceeded the 2.000-second native bound. The instrumentation therefore captured the exact native blocking stage while the bounded fallback prevented the old 30-second shutdown delay.

### 3. Full reboot/shutdown — VALIDATED

With Oculizer and the live audio stack running, perform a real reboot or shutdown. After the next boot inspect the persistent previous-boot log:

```bash
sudo journalctl -b -1 -u oculizer.service --no-pager
```

Expected result: no 30-second Oculizer stop timeout and no systemd `SIGKILL` caused by the service exceeding `TimeoutStopSec`.

Observed result on 2026-08-18: PASS. During the real reboot, `stream.abort()` completed in 0.001 s, `stream.close()` in 0.003 s, Oculizer completed its worker/client teardown, and systemd deactivated the service successfully without the former 30-second delay.

## Automated regression coverage

`tests/test_shutdown_lifecycle.py` covers:

- explicit live-stream interruption without caller-side close;
- a blocked `stream.stop()` returning control within a bounded interval;
- a blocked `stream.close()` returning control within a bounded interval;
- a blocked `stream.abort()` returning control without launching a competing close;
- preservation of `TimeoutStopSec=30` during validation;
- rendering of the `user@<uid>.service` ordering dependency.

The real reboot/shutdown test cannot be reproduced by a unit test because it depends on the Raspberry Pi's actual user audio session teardown order.
