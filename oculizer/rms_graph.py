"""Small bounded RMS history model for the interactive terminal UI."""

from collections import deque
from dataclasses import dataclass
import hashlib
import math
import re
import time


@dataclass(frozen=True)
class SceneVisual:
    """Terminal-independent visual identity derived from a scene name."""

    family: str
    shade: int
    symbol: str
    has_named_color: bool


SCENE_COLOR_FAMILIES = {
    "black": (238, 240, 242, 244),
    "blue": (19, 27, 33, 75),
    "brown": (94, 130, 137, 180),
    "cyan": (30, 37, 44, 51),
    "gray": (242, 245, 248, 251),
    "green": (28, 40, 46, 82),
    "lime": (64, 76, 112, 118),
    "magenta": (90, 127, 164, 201),
    "orange": (166, 208, 214, 215),
    "pink": (198, 205, 212, 219),
    "purple": (55, 91, 129, 141),
    "red": (160, 196, 203, 210),
    "white": (250, 252, 254, 255),
    "yellow": (178, 220, 226, 229),
}

_COLOR_ALIASES = {
    "aqua": "cyan", "azure": "blue", "gold": "yellow", "golden": "yellow",
    "grey": "gray", "indigo": "purple", "lavender": "purple",
    "maroon": "red", "navy": "blue", "rose": "pink", "scarlet": "red",
    "teal": "cyan", "turquoise": "cyan", "violet": "purple",
}
_NEUTRAL_SYMBOLS = ("◆", "▲", "■", "✦", "✚", "✖", "◇", "▼", "★", "▪")


def _stable_number(value):
    return int.from_bytes(hashlib.sha256(str(value).encode("utf-8")).digest()[:8], "big")


def scene_visual(scene_name):
    """Derive a colored dot or a gray icon from an arbitrary scene name."""
    name = str(scene_name)
    tokens = re.findall(r"[a-z]+", name.lower())
    family = None
    for token in tokens:
        candidate = _COLOR_ALIASES.get(token, token)
        if candidate in SCENE_COLOR_FAMILIES:
            family = candidate
            break
    stable = _stable_number(name.lower())
    if family is not None:
        return SceneVisual(family, stable % 4, "●", True)
    return SceneVisual("gray", stable % 4, _NEUTRAL_SYMBOLS[stable % len(_NEUTRAL_SYMBOLS)], False)


def scene_color_index(scene_name, palette_size=6):
    """Return a legacy stable palette index (prefer scene_visual for new UI)."""
    return _stable_number(str(scene_name).lower()) % palette_size


def format_elapsed(elapsed_seconds):
    """Format elapsed time as minutes and seconds for the graph axis."""
    total_seconds = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}'{seconds:02d}\""


class RmsGraph:
    """Sample RMS at a fixed rate and render a scrolling text graph."""

    def __init__(self, duration_seconds=30.0, sample_rate_hz=10.0, clock=None):
        self.duration_seconds = float(duration_seconds)
        self.sample_interval = 1.0 / float(sample_rate_hz)
        self.clock = clock or time.monotonic
        self.started_at = self.clock()
        self.last_sample_at = None
        self.last_scene = None
        self.initial_sample_at = None
        self.samples = deque(maxlen=max(2, int(duration_seconds * sample_rate_hz) + 1))

    def sample(self, rms, scene_name, now=None):
        """Record one bounded sample when the sampling interval has elapsed."""
        now = self.clock() if now is None else now
        if self.last_sample_at is not None and now - self.last_sample_at < self.sample_interval:
            return False
        value = 0.0 if rms is None else max(0.0, float(rms))
        scene = str(scene_name)
        if self.last_scene is None:
            self.initial_sample_at = now
        scene_changed = self.last_scene is not None and scene != self.last_scene
        self.samples.append((now, value, scene, scene_changed))
        self.last_sample_at = now
        self.last_scene = scene
        return True

    def render_frame(self, width, height, now=None):
        """Return plot lines plus colored Braille cells and scene names."""
        if width < 20 or height < 5:
            return [], []
        now = self.clock() if now is None else now
        label_width = 7
        plot_width = width - label_width - 1
        plot_height = height - 2
        virtual_width = plot_width * 2
        virtual_height = plot_height * 4
        scale_max = 1.0
        canvas = [[" " for _ in range(plot_width)] for _ in range(plot_height)]
        braille_cells = {}

        # Anchor complete Braille cells to startup time. Two fixed time buckets
        # live inside each cell, but scrolling waits for a full-cell boundary;
        # this prevents regrouping subpixels into different glyphs over time.
        cell_duration = self.duration_seconds / plot_width
        bucket_duration = cell_duration / 2.0
        current_cell = math.floor((now - self.started_at) / cell_duration)
        first_cell = current_cell - plot_width + 1
        first_bucket = first_cell * 2
        last_bucket = first_bucket + virtual_width - 1
        columns = {}
        for timestamp, value, scene, _scene_changed in self.samples:
            bucket = math.floor((timestamp - self.started_at) / bucket_duration)
            if bucket < first_bucket or bucket > last_bucket:
                continue
            x = bucket - first_bucket
            column = columns.setdefault(x, [0.0, 0, scene])
            column[0] += value
            column[1] += 1
            column[2] = scene

        virtual_points = []
        for x, (total, count, scene) in sorted(columns.items()):
            average = total / count
            y = min(virtual_height - 1, round(average / scale_max * (virtual_height - 1)))
            virtual_points.append((x, y, scene))

        def draw_virtual_point(x, y, scene):
            cell_x = x // 2
            cell_row = plot_height - 1 - y // 4
            local_x = x % 2
            local_y_from_top = 3 - (y % 4)
            bit_by_position = (
                (0, 1, 2, 6),
                (3, 4, 5, 7),
            )
            bit = bit_by_position[local_x][local_y_from_top]
            key = (cell_row, label_width + 1 + cell_x)
            mask, _previous_scene = braille_cells.get(key, (0, scene))
            braille_cells[key] = (mask | (1 << bit), scene)

        def draw_virtual_line(start, end):
            x0, y0, start_scene = start
            x1, y1, end_scene = end
            delta_x = abs(x1 - x0)
            step_x = 1 if x0 < x1 else -1
            delta_y = -abs(y1 - y0)
            step_y = 1 if y0 < y1 else -1
            error = delta_x + delta_y
            total_steps = max(delta_x, abs(delta_y), 1)
            step = 0
            while True:
                scene = end_scene if step / total_steps >= 0.5 else start_scene
                draw_virtual_point(x0, y0, scene)
                if x0 == x1 and y0 == y1:
                    break
                doubled_error = 2 * error
                if doubled_error >= delta_y:
                    error += delta_y
                    x0 += step_x
                if doubled_error <= delta_x:
                    error += delta_x
                    y0 += step_y
                step += 1

        previous = None
        for point in virtual_points:
            if previous is None:
                draw_virtual_point(*point)
            else:
                draw_virtual_line(previous, point)
            previous = point

        lines = []
        for row, row_characters in enumerate(canvas):
            if row == 0:
                label = f"{scale_max:5.3f} "
            elif row == plot_height - 1:
                label = "0.000 "
            else:
                label = "      "
            lines.append(label + "|" + "".join(row_characters))

        right = format_elapsed(now - self.started_at)
        axis = " " * label_width + "+" + "-" * plot_width
        label_characters = [" "] * width
        fixed_start = max(0, width - len(right))
        label_characters[fixed_start:width] = right[-width:]

        # Place newest transition labels first. Older labels that would overlap
        # are omitted, while the fixed elapsed counter always owns the right
        # edge and remains separated by two spaces.
        transition_labels = []
        for timestamp, _value, _scene, scene_changed in self.samples:
            if not scene_changed:
                continue
            bucket = math.floor((timestamp - self.started_at) / bucket_duration)
            if bucket < first_bucket or bucket > last_bucket:
                continue
            x = bucket - first_bucket
            marker_column = label_width + 1 + x // 2
            transition_labels.append((timestamp, marker_column))

        occupied_ranges = []
        latest_allowed_end = fixed_start - 2
        for timestamp, marker_column in reversed(transition_labels):
            text = format_elapsed(timestamp - self.started_at)
            end = min(latest_allowed_end, marker_column + (len(text) + 1) // 2)
            start = max(label_width, end - len(text))
            end = start + len(text)
            if end > latest_allowed_end:
                continue
            if any(start < occupied_end + 2 and end + 2 > occupied_start
                   for occupied_start, occupied_end in occupied_ranges):
                continue
            label_characters[start:end] = text
            occupied_ranges.append((start, end))

        labels = "".join(label_characters)
        lines.append(axis[:width])
        lines.append(labels[:width])
        rendered_cells = {
            (row, column): (chr(0x2800 + mask), scene)
            for (row, column), (mask, scene) in braille_cells.items()
        }

        # Overlay the scene identity at startup and at the first sample of each
        # subsequent scene. Only actual transitions receive an axis timestamp.
        for timestamp, value, scene, scene_changed in self.samples:
            if not scene_changed and timestamp != self.initial_sample_at:
                continue
            bucket = math.floor((timestamp - self.started_at) / bucket_duration)
            if bucket < first_bucket or bucket > last_bucket:
                continue
            x = bucket - first_bucket
            total, count, _latest_scene = columns[x]
            average = total / count
            y = min(virtual_height - 1, round(average / scale_max * (virtual_height - 1)))
            cell_row = plot_height - 1 - y // 4
            cell_column = label_width + 1 + x // 2
            rendered_cells[(cell_row, cell_column)] = (scene_visual(scene).symbol, scene)

        colored_cells = [
            (row, column, character, scene)
            for (row, column), (character, scene) in rendered_cells.items()
        ]
        return lines, colored_cells

    def render_lines(self, width, height, now=None):
        """Return text-only lines for callers that do not render colors."""
        lines, points = self.render_frame(width, height, now=now)
        mutable_lines = [list(line) for line in lines]
        for row, column, character, _scene in points:
            if row < len(mutable_lines) and column < len(mutable_lines[row]):
                mutable_lines[row][column] = character
        lines = ["".join(line) for line in mutable_lines]
        return lines
