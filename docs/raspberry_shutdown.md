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

`TimeoutStopSec=30` remains unchanged while this fix is being validated. Do not reduce it merely to hide the symptom.

Run these regression cases on the Raspberry Pi after reinstalling the service pack:

### 1. Normal service stop

```bash
sudo systemctl start oculizer.service
sudo systemctl stop oculizer.service
sudo journalctl -u oculizer.service -b -n 100 --no-pager
```

Expected result: Oculizer exits normally without reaching the 30-second systemd timeout. The PortAudio log should identify `abort()`/`close()` timings.

### 2. QLC+ already stopped

Stop QLC+ using the service owned by the QLC+ deployment, then stop Oculizer:

```bash
sudo systemctl stop oculizer.service
sudo journalctl -u oculizer.service -b -n 100 --no-pager
```

Expected result: Oculizer shutdown remains prompt and does not depend on QLC+ being alive.

### 3. Full reboot/shutdown

With Oculizer and the live audio stack running, perform a real reboot or shutdown. After the next boot inspect the persistent previous-boot log:

```bash
sudo journalctl -b -1 -u oculizer.service --no-pager
```

Expected result: no 30-second Oculizer stop timeout and no systemd `SIGKILL` caused by the service exceeding `TimeoutStopSec`.

Only after the full Raspberry Pi reboot/shutdown case is validated should a shorter `TimeoutStopSec` be considered.

## Automated regression coverage

`tests/test_shutdown_lifecycle.py` covers:

- explicit live-stream interruption without caller-side close;
- a blocked `stream.close()` returning control within a bounded interval;
- a blocked `stream.abort()` returning control without launching a competing close;
- preservation of `TimeoutStopSec=30` during validation;
- rendering of the `user@<uid>.service` ordering dependency.

The real reboot/shutdown test cannot be reproduced by a unit test because it depends on the Raspberry Pi's actual user audio session teardown order.
