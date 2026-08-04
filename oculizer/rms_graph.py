"""Small bounded RMS history model for the interactive terminal UI."""

from collections import deque
import math
import time


def scene_color_index(scene_name, palette_size=6):
    """Return a stable palette index shared by graph and scene selectors."""
    return sum(ord(char) for char in str(scene_name)) % palette_size


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
        self.samples = deque(maxlen=max(2, int(duration_seconds * sample_rate_hz) + 1))

    def sample(self, rms, scene_name, now=None):
        """Record one bounded sample when the sampling interval has elapsed."""
        now = self.clock() if now is None else now
        if self.last_sample_at is not None and now - self.last_sample_at < self.sample_interval:
            return False
        value = 0.0 if rms is None else max(0.0, float(rms))
        scene = str(scene_name)
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
        labels = " " * max(0, width - len(right)) + right
        lines.append(axis[:width])
        lines.append(labels[:width])
        rendered_cells = {
            (row, column): (chr(0x2800 + mask), scene)
            for (row, column), (mask, scene) in braille_cells.items()
        }

        # Overlay one visually distinct marker at the first sample of each new
        # scene. The initial scene is deliberately not marked as a transition.
        for timestamp, value, scene, scene_changed in self.samples:
            if not scene_changed:
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
            rendered_cells[(cell_row, cell_column)] = ("●", scene)

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
