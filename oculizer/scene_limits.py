"""Small user-facing interpreter for optional automatic scene limits."""

import math


def describe_scene_limits(scene_rate_limit=None, scene_throttle=None):
    """Return concise startup guidance without changing the configured policy."""
    if scene_rate_limit is None and scene_throttle is None:
        return []

    parts = []
    if scene_rate_limit is not None:
        maximum, window = scene_rate_limit
        parts.append(f"max {maximum} changes/{window:g}s")
    if scene_throttle is not None:
        burst, recovery = scene_throttle
        parts.append(f"burst {burst}, +1 credit/{recovery:g}s")
    lines = ["Scene limits: " + " | ".join(parts)]

    if scene_rate_limit is None:
        burst, recovery = scene_throttle
        if recovery > 3:
            lines.append("Analysis: organic bursts allowed, but recovery may feel slow.")
            lines.append("Recommendation: reduce recovery time if the show becomes unresponsive.")
        elif burst <= 2:
            lines.append("Analysis: small organic bursts followed by progressive recovery.")
        else:
            lines.append("Analysis: generous bursts followed by progressive recovery.")
        return lines

    maximum, window = scene_rate_limit
    rate_interval = window / maximum
    if scene_throttle is None:
        if rate_interval < 0.5:
            lines.append("Analysis: light burst protection; this limit may rarely engage.")
        elif rate_interval <= 1.5:
            lines.append("Analysis: moderate rolling-window protection without fixed spacing.")
        else:
            lines.append("Analysis: strong rolling-window protection; short bursts remain possible.")
        return lines

    burst, recovery = scene_throttle
    throttle_envelope = burst + math.floor(window / recovery)
    if maximum >= throttle_envelope:
        lines.append(
            f"Analysis: throttle allows at most ~{throttle_envelope} changes/{window:g}s; "
            "rate limit is effectively redundant."
        )
        lines.append("Recommendation: omit --scene-rate-limit unless you lower its maximum.")
    elif maximum <= burst:
        lines.append("Analysis: rolling rate caps the initial burst; throttle then smooths recovery.")
    elif recovery > 3:
        lines.append("Analysis: bursts remain organic, but throttle recovery may feel slow.")
        lines.append("Recommendation: reduce recovery time if transitions arrive too late.")
    else:
        lines.append("Analysis: complementary limits; absolute safety cap plus organic bursts.")
    return lines
