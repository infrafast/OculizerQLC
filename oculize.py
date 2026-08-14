import os
import io
import time
import threading
import curses
import argparse
import signal
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from curses import wrapper
from oculizer import Oculizer
from oculizer.scenes import LogicalSceneRegistry
from oculizer.config_editor import ConfigurationStore
from oculizer.runtime_config import DEFAULT_CONFIG_PATH, configured_audio_input, configured_dynamic_controls, configured_fast_detection, configured_frequency_modulation, configured_master_modulation, configured_prediction, configured_scene_max_duration, configured_silence, configured_speech, load_runtime_config
from oculizer.automatic import AutomaticSceneRouter, PolicyConflictError
from oculizer.control_socket import ControlSocketServer, default_control_socket_path
from oculizer.modulation import FrequencyBandModulator, MasterModulator
from oculizer.runtime_control import RuntimeControl
from oculizer.rms_graph import RmsGraph, SCENE_COLOR_FAMILIES, scene_visual
import logging
from collections import deque, OrderedDict
import math
from pathlib import Path

COLOR_PAIRS = {
    'title': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'info': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'error': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'warning': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'log': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'controls': (curses.COLOR_WHITE, curses.COLOR_BLACK),
    'scene_green': (curses.COLOR_GREEN, curses.COLOR_BLACK),
    'scene_yellow': (curses.COLOR_YELLOW, curses.COLOR_BLACK),
    'scene_blue': (curses.COLOR_BLUE, curses.COLOR_BLACK),
    'scene_magenta': (curses.COLOR_MAGENTA, curses.COLOR_BLACK),
    'scene_cyan': (curses.COLOR_CYAN, curses.COLOR_BLACK),
    'scene_red': (curses.COLOR_RED, curses.COLOR_BLACK),
    # Toggle mode colors
    'toggle_active': (curses.COLOR_WHITE, curses.COLOR_GREEN),  # Active scene (when not overridden)
    'toggle_selected': (curses.COLOR_BLACK, curses.COLOR_YELLOW),  # Selected for navigation
    'toggle_hover': (curses.COLOR_WHITE, curses.COLOR_BLUE),  # Mouse hover
    'toggle_normal': (curses.COLOR_WHITE, curses.COLOR_BLACK),  # Default
    'toggle_predicted': (curses.COLOR_WHITE, curses.COLOR_BLACK),  # Predicted by AI (not active)
    'toggle_override': (curses.COLOR_BLACK, curses.COLOR_MAGENTA),  # Manually overridden scene (active)
}

def setup_logging():
    """Set up logging configuration for all modules"""
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    log_file = os.path.join(os.path.dirname(__file__), 'oculizer.log')
    
    # Clear any existing handlers to avoid duplicates
    root_logger = logging.getLogger()
    root_logger.handlers = []
    
    # Set up file handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # Configure the root logger
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[file_handler]
    )
    # Route warnings.warn() through logging. Direct writes to stderr scroll the
    # physical terminal behind curses' virtual screen and corrupt subsequent
    # differential rendering.
    logging.captureWarnings(True)


def _stop_after_keyboard_interrupt(controller):
    """Finish bounded cleanup without repeated Ctrl+C interrupting thread joins."""
    previous_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        controller.stop()
    finally:
        signal.signal(signal.SIGINT, previous_handler)

def setup_colors():
    curses.start_color()
    for i, (name, (fg, bg)) in enumerate(COLOR_PAIRS.items(), start=1):
        curses.init_pair(i, fg, bg)
        COLOR_PAIRS[name] = i


_SCENE_PAIR_CACHE = {}
_ANSI_SCENE_COLORS = {
    'black': curses.COLOR_WHITE, 'blue': curses.COLOR_BLUE,
    'brown': curses.COLOR_YELLOW, 'cyan': curses.COLOR_CYAN,
    'gray': curses.COLOR_WHITE, 'green': curses.COLOR_GREEN,
    'lime': curses.COLOR_GREEN, 'magenta': curses.COLOR_MAGENTA,
    'orange': curses.COLOR_YELLOW, 'pink': curses.COLOR_MAGENTA,
    'purple': curses.COLOR_MAGENTA, 'red': curses.COLOR_RED,
    'white': curses.COLOR_WHITE, 'yellow': curses.COLOR_YELLOW,
}


def scene_curses_style(scene_name, background=curses.COLOR_BLACK):
    """Return the shared dynamic scene visual and its best terminal style."""
    visual = scene_visual(scene_name)
    key = (visual.family, visual.shade, background)
    if key not in _SCENE_PAIR_CACHE:
        pair = 32 + list(SCENE_COLOR_FAMILIES).index(visual.family) * 4 + visual.shade
        foreground = (
            SCENE_COLOR_FAMILIES[visual.family][visual.shade]
            if getattr(curses, 'COLORS', 0) >= 256
            else _ANSI_SCENE_COLORS[visual.family]
        )
        if pair < getattr(curses, 'COLOR_PAIRS', 0):
            curses.init_pair(pair, foreground, background)
            _SCENE_PAIR_CACHE[key] = pair
        else:
            _SCENE_PAIR_CACHE[key] = COLOR_PAIRS['info']
    attribute = curses.A_BOLD if visual.shade >= 2 else curses.A_NORMAL
    return visual, curses.color_pair(_SCENE_PAIR_CACHE[key]) | attribute


def initialize_screen(stdscr):
    """Synchronize the physical terminal with the GUI background once."""
    background = curses.color_pair(COLOR_PAIRS['info'])
    stdscr.bkgd(' ', background)
    stdscr.clear()
    stdscr.refresh()


def show_loading_screen(stdscr, details):
    """Show immediate startup feedback while heavy components are constructed."""
    height, width = stdscr.getmaxyx()
    lines = [
        "Loading Oculizer...",
        *details,
        "Loading profiles, scenes, models and audio pipeline.",
        "This can take several seconds.",
    ]
    start_row = max(0, (height - len(lines)) // 2)
    for offset, line in enumerate(lines):
        if start_row + offset >= height:
            break
        column = max(0, (width - len(line)) // 2)
        attribute = curses.A_BOLD if offset == 0 else curses.A_NORMAL
        stdscr.addstr(
            start_row + offset,
            column,
            line[:max(0, width - column - 1)],
            curses.color_pair(COLOR_PAIRS['info']) | attribute,
        )
    stdscr.refresh()


def _log_captured_startup_output(output):
    """Preserve legacy startup prints without writing outside curses."""
    for line in output.getvalue().splitlines():
        if line.strip():
            logging.info("Startup: %s", line.strip())

# Toggle mode helper functions (from toggle.py)
def sort_scenes_alphabetically(scenes):
    return OrderedDict(sorted(scenes.items()))

def find_scene_by_prefix(scenes, prefix):
    if not prefix:
        return -1
    prefix = prefix.lower()
    for i, (scene, _) in enumerate(scenes):
        if scene.lower().startswith(prefix):
            return i
    return -1

def calculate_grid_dimensions(scene_list, max_x, max_y):
    # Find the longest scene name to determine column width
    max_name_length = max(len(scene[0]) for scene in scene_list) + 4  # Marker plus padding
    
    # Calculate number of columns that can fit
    num_columns = max(1, min(len(scene_list), max_x // max_name_length))
    
    # Calculate number of rows needed
    num_rows = math.ceil(len(scene_list) / num_columns)
    
    # Adjust column width to be uniform
    column_width = max_x // num_columns
    
    return num_rows, num_columns, column_width

def get_grid_position(index, num_columns):
    row = index // num_columns
    col = index % num_columns
    return row, col

def get_index_from_position(row, col, num_columns, total_scenes):
    index = row * num_columns + col
    return min(index, total_scenes - 1)

class AudioOculizerController:
    def __init__(self, stdscr, input_device='default',
                 predictor_version='v6', average_dual_channels=False, scene_cache_size=10,
                 test_mode=False, config_path=None, qlc_host=None,
                 qlc_port=None, dry_run=None, silence_config=None,
                 speech_config=None, master_config=None, frequency_config=None,
                 prediction_window_seconds=4.0, prediction_interval_seconds=1.0,
                 audio_file=None, graph_enabled=True,
                 dynamic_control="off", dynamic_controls=None, scene_max_duration=40.0,
                 control_socket_path=None, fast_detection_config=None,
                 qlc_encryption_key=None):
        dynamic_controls = dynamic_controls or {}
        off_cache_size = scene_cache_size
        dynamic_policy = ({"cache": scene_cache_size, "rate": None, "throttle": None}
                          if dynamic_control == "off"
                          else dynamic_controls[dynamic_control])
        scene_cache_size = dynamic_policy["cache"]
        self.stdscr = stdscr
        curses.curs_set(0)
        self.stdscr.nodelay(1)
        self.test_mode = test_mode
        self.graph_enabled = bool(graph_enabled)
        self.rms_graph = RmsGraph()
        
        self.scene_manager = LogicalSceneRegistry(config_path)
        
        # The one input source feeds both FFT/reactivity and scene prediction.
        self.oculizer = Oculizer(
            scene_manager=self.scene_manager,
            input_device=input_device,
            scene_prediction_enabled=True,
            predictor_version=predictor_version,
            average_dual_channels=average_dual_channels,
            scene_cache_size=scene_cache_size,
            test_mode=test_mode,
            config_path=config_path,
            qlc_host=qlc_host,
            qlc_port=qlc_port,
            dry_run=dry_run,
            prediction_window_seconds=prediction_window_seconds,
            prediction_interval_seconds=prediction_interval_seconds,
            audio_file=audio_file,
            fast_detection_config=fast_detection_config,
            qlc_encryption_key=qlc_encryption_key,
        )
        self.oculizer.restrict_scenes_to_backend()
        self.scene_router = AutomaticSceneRouter(
            self.oculizer,
            silence_config=silence_config,
            speech_config=speech_config,
            scene_rate_limit=dynamic_policy["rate"],
            scene_throttle=dynamic_policy["throttle"],
            scene_max_duration=scene_max_duration,
        )
        self.master_modulator = MasterModulator(self.oculizer, config=master_config)
        self.frequency_modulator = FrequencyBandModulator(self.oculizer, config=frequency_config)
        self.log_messages = deque(maxlen=50)
        resolved_config_path = str(
            Path(config_path).resolve() if config_path else DEFAULT_CONFIG_PATH.resolve()
        )
        self.runtime_control = RuntimeControl(
            self.oculizer, self.scene_router, self.master_modulator,
            self.frequency_modulator, dynamic_controls=dynamic_controls,
            active_dynamic_control=dynamic_control, off_cache_size=off_cache_size,
            health_check=lambda: self.oculizer.is_alive(),
            config_store=ConfigurationStore(resolved_config_path),
            log_provider=lambda limit: list(self.log_messages)[-limit:],
            launch_info={
                "mode": "interactive",
                "restart_capability": "manual",
                "config_path": resolved_config_path,
                "restart_command": shlex.join([str(Path(sys.executable).resolve()), *sys.argv]),
                "working_directory": str(Path.cwd()),
            },
        )
        self.control_server = ControlSocketServer(control_socket_path, self.runtime_control) if control_socket_path else None
        
        self.predictor_version = predictor_version
        self.average_dual_channels = average_dual_channels
        self.error_message = ""
        self.info_message = ""
        
        # Toggle mode state
        self.in_toggle_mode = False
        self.toggle_override_active = False
        
        # Set up logging for curses display
        self.log_handler = self.LogHandler(self.log_messages)
        logging.getLogger().addHandler(self.log_handler)
    
    class LogHandler(logging.Handler):
        def __init__(self, log_messages):
            super().__init__()
            self.log_messages = log_messages

        def emit(self, record):
            log_entry = self.format(record)
            self.log_messages.append(log_entry)

    def start(self):
        try:
            self.master_modulator.startup()
            self.frequency_modulator.startup()
            self.oculizer.start()
            if self.control_server is not None:
                self.control_server.start()
            self.run()
        except Exception as e:
            self.error_message = f"Error starting controller: {str(e)}"
            logging.error(f"Error starting controller: {str(e)}")

    def run(self):
        update_thread = threading.Thread(target=self.update_loop)
        update_thread.daemon = True
        update_thread.start()

        display_interval = 0.25
        last_display_at = 0.0
        last_terminal_size = None
        while True:
            user_interaction = self.handle_user_input()
            current_scene_name = self.scene_manager.current_scene['name']
            if self.graph_enabled:
                self.rms_graph.sample(
                    self.oculizer.current_audio_rms,
                    current_scene_name,
                )

            now = time.monotonic()
            terminal_size = self.stdscr.getmaxyx()
            terminal_resized = terminal_size != last_terminal_size
            if user_interaction or terminal_resized or now - last_display_at >= display_interval:
                self.update_display()
                last_display_at = now
                last_terminal_size = terminal_size
            time.sleep(0.05)

    def update_loop(self):
        """Update lighting based on real-time audio predictions."""
        while True:
            try:
                # The integrated selector owns routing while it is open.
                if self.in_toggle_mode:
                    time.sleep(0.1)
                    continue
                
                if self.runtime_control.tick():
                    current_scene = self.scene_manager.current_scene['name']
                    self.info_message = f"Changed to scene: {current_scene}"
                        
            except Exception as e:
                self.error_message = f"Error in update loop: {str(e)}"
                logging.error(f"Error in update loop: {str(e)}")
            
            time.sleep(0.02)

    def run_toggle_mode(self):
        """
        Run interactive toggle mode with live prediction visualization.
        
        Features:
        - Shows predicted scenes in CYAN (not active)
        - Shows active scene in GREEN (when following predictions)
        - Press Ctrl+O to override with manual selection (MAGENTA when active)
        - Press Ctrl+O again to resume following predictions
        """
        # Set flag to indicate we're in toggle mode
        self.in_toggle_mode = True
        
        # Sort scenes alphabetically
        original_scenes = self.scene_manager.scenes.copy()
        self.scene_manager.scenes = sort_scenes_alphabetically(self.scene_manager.scenes)
        
        # Keep terminal interaction keyboard-only for portability.
        curses.mousemask(0)
        self.stdscr.keypad(1)
        
        # Initialize variables
        selected_index = 0
        current_scene_name = self.scene_manager.current_scene['name']
        search_string = ""
        last_search_time = time.time()
        hover_pos = (-1, -1)
        
        # Override state
        override_active = False
        override_scene = None
        
        try:
            while True:
                self.stdscr.clear()
                max_y, max_x = self.stdscr.getmaxyx()
                scene_list = list(self.scene_manager.scenes.items())
                total_scenes = len(scene_list)
                
                # Sync override state with class-level flag
                self.toggle_override_active = override_active
                
                # Get current prediction
                predicted_scene = self.oculizer.current_predicted_scene
                
                # Update current scene name based on override state
                if not override_active and self.runtime_control.step():
                    current_scene_name = self.scene_manager.current_scene['name']
                
                # Calculate grid layout
                num_rows, num_columns, column_width = calculate_grid_dimensions(scene_list, max_x, max_y - 6)
                
                # Display header with override status
                if override_active:
                    mode_status = f"OVERRIDE ACTIVE (Manual: {override_scene})"
                    status_color = COLOR_PAIRS['warning']
                else:
                    mode_status = f"FOLLOWING PREDICTIONS (Auto: {current_scene_name})"
                    status_color = COLOR_PAIRS['info']
                
                header_text = f"TOGGLE MODE - {mode_status}"
                commands_text = "[Ctrl+T] Exit  [Ctrl+Q] Quit  [Ctrl+R] Reload"
                if search_string:
                    header_text += f" [Search: {search_string}]"
                
                try:
                    self.stdscr.addstr(0, 0, header_text[:max_x-1], curses.color_pair(status_color) | curses.A_BOLD)
                    self.stdscr.addstr(1, 0, commands_text[:max_x-1], curses.color_pair(COLOR_PAIRS['controls']))
                    
                    # Show prediction info
                    if predicted_scene:
                        pred_info = f"Current Prediction: {predicted_scene}"
                        self.stdscr.addstr(2, 0, pred_info[:max_x-1], curses.color_pair(COLOR_PAIRS['toggle_predicted']))
                    
                    self.stdscr.addstr(3, 0, "Available scenes:", curses.color_pair(COLOR_PAIRS['info']))
                except curses.error:
                    pass
                
                # Display scenes in grid
                for i, (scene, _) in enumerate(scene_list):
                    row, col = get_grid_position(i, num_columns)
                    if row >= num_rows or row >= max_y - 6:
                        break
                    
                    display_y = row + 5  # Start after header (now 5 to account for extra line)
                    display_x = col * column_width
                    
                    scene_str = scene
                    if len(scene_str) > column_width - 4:
                        scene_str = scene_str[:max(0, column_width - 7)] + "..."
                    
                    # Determine scene display style with new override logic
                    if override_active and scene == override_scene:
                        # Manually overridden scene (active)
                        color = curses.color_pair(COLOR_PAIRS['toggle_override'])
                    elif not override_active and scene == current_scene_name:
                        # Active scene when following predictions
                        color = curses.color_pair(COLOR_PAIRS['toggle_active'])
                    elif predicted_scene and scene == predicted_scene and override_active:
                        # Predicted scene when override is active (show but not active)
                        color = curses.color_pair(COLOR_PAIRS['toggle_predicted'])
                    elif i == selected_index:
                        # Selected for navigation
                        color = curses.color_pair(COLOR_PAIRS['toggle_selected'])
                    elif (row, col) == hover_pos:
                        # Mouse hover
                        color = curses.color_pair(COLOR_PAIRS['toggle_hover'])
                    else:
                        # Normal scene
                        color = curses.color_pair(COLOR_PAIRS['toggle_normal'])
                    
                    # Pad scene name to column width
                    scene_str = scene_str.ljust(max(0, column_width - 3))
                    try:
                        self.stdscr.addstr(display_y, display_x, self._scene_symbol(scene), self._scene_color(scene))
                        self.stdscr.addstr(display_y, display_x + 2, scene_str[:max_x-display_x-3], color)
                    except curses.error:
                        pass
                
                # Display instructions with color legend
                legend = "🟢=Active 🟣=Override ⚫=Available"
                if override_active:
                    legend += " 🔵=Predicted"
                instructions = f"{legend} | Enter: Select Scene (Override) | ESC: Resume Predictions | Type: Search"
                try:
                    self.stdscr.addstr(max_y-1, 0, instructions[:max_x-1], curses.color_pair(COLOR_PAIRS['controls']))
                except curses.error:
                    pass
                
                self.stdscr.refresh()
                
                try:
                    event = self.stdscr.getch()
                    current_time = time.time()
                    
                    if search_string and current_time - last_search_time > 1.0:
                        search_string = ""
                    
                    if event == 17:  # Ctrl+Q
                        curses.mousemask(0)
                        self.stop()
                        exit()
                    elif event == 20:  # Ctrl+T
                        # Return to oculizer mode
                        break
                    elif event == 18:  # Ctrl+R
                        try:
                            self.oculizer.reload_scene_configuration()
                            self.scene_manager.scenes = sort_scenes_alphabetically(self.scene_manager.scenes)
                            if override_active:
                                self.runtime_control.set_scene(override_scene)
                            else:
                                self.scene_router.last_target = None
                                self.scene_router.step()
                            scene_list = list(self.scene_manager.scenes.items())
                            total_scenes = len(scene_list)
                            self.info_message = "Scenes reloaded"
                            logging.info("Scenes reloaded")
                        except Exception as e:
                            self.error_message = f"Error reloading: {str(e)}"
                            logging.error(f"Error reloading scenes: {str(e)}")
                        time.sleep(1)
                    elif event in [curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT]:
                        row, col = get_grid_position(selected_index, num_columns)
                        
                        if event == curses.KEY_UP and row > 0:
                            row -= 1
                        elif event == curses.KEY_DOWN and row < num_rows - 1:
                            row += 1
                        elif event == curses.KEY_LEFT and col > 0:
                            col -= 1
                        elif event == curses.KEY_RIGHT and col < num_columns - 1:
                            col += 1
                        
                        new_index = get_index_from_position(row, col, num_columns, total_scenes)
                        if 0 <= new_index < total_scenes:
                            selected_index = new_index
                            search_string = ""
                    elif event in [curses.KEY_ENTER, 10, 13]:  # Enter key
                        # Check if Shift+Enter (KEY_ENTER with shift modifier doesn't work reliably)
                        # We'll use a different approach - check for specific key codes
                        # In curses, we can't easily detect Shift+Enter, so we'll use a workaround
                        # Let's use regular Enter for selection and implement Shift+Enter detection
                        if 0 <= selected_index < total_scenes:
                            new_scene = scene_list[selected_index][0]
                            if self.runtime_control.set_scene(new_scene):
                                current_scene_name = self.scene_manager.current_scene['name']
                                search_string = ""
                                override_active = True
                                override_scene = new_scene
                                self.info_message = f"Override: {new_scene} (ESC to resume predictions)"
                    elif event == 27:  # ESC key
                        # ESC: Resume following predictions if override is active
                        if override_active:
                            override_active = False
                            override_scene = None
                            self.runtime_control.set_auto()
                            current_scene_name = self.scene_manager.current_scene['name']
                            self.info_message = "Override disabled - following predictions"
                            logging.info("Toggle mode: Override disabled via ESC, resuming predictions")
                        search_string = ""
                    elif event in [curses.KEY_BACKSPACE, 127, 8]:  # Backspace
                        search_string = search_string[:-1]
                        last_search_time = current_time
                        new_index = find_scene_by_prefix(scene_list, search_string)
                        if new_index != -1:
                            selected_index = new_index
                    elif 32 <= event <= 126:  # Printable characters
                        search_string += chr(event)
                        last_search_time = current_time
                        new_index = find_scene_by_prefix(scene_list, search_string)
                        if new_index != -1:
                            selected_index = new_index
                
                except curses.error:
                    pass
                
                time.sleep(0.01)
        
        finally:
            curses.mousemask(0)
            # Restore original scene order if needed
            # self.scene_manager.scenes = original_scenes
            
            # Reset toggle mode flags
            self.in_toggle_mode = False
            self.toggle_override_active = False
            if self.scene_router.manual_override is not None:
                self.runtime_control.set_auto()

    def handle_user_input(self):
        try:
            key = self.stdscr.getch()
            if key == -1:
                return False
            if key == ord('q'):
                self.stop()
                exit()
            elif key == 20:  # Ctrl+T
                # Enter toggle mode
                logging.info("Entering toggle mode")
                self.info_message = "Entering toggle mode..."
                self.run_toggle_mode()
                # Returned from toggle mode
                logging.info("Returned to oculizer mode")
                self.info_message = "Returned to oculizer mode"
            elif key == ord('r'):
                self.oculizer.reload_scene_configuration()
                self.info_message = "Configuration reloaded"
                logging.info("Logical scene and QLC+ configuration reloaded")
            elif key == ord('l'):
                self.run_dynamic_control_mode()
            return True
        except Exception as e:
            self.error_message = f"Error handling user input: {str(e)}"
            logging.error(f"Error handling user input: {str(e)}")
            return False

    def run_dynamic_control_mode(self):
        """Select one configured dynamic-control profile without restarting."""
        status = self.runtime_control.status()
        initial_revision = status["policy_revision"]
        profiles = ["off", *self.runtime_control.dynamic_controls]
        selected = profiles.index(status["dynamic_control"])
        while True:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()
            name = profiles[selected]
            profile = ({"cache": self.runtime_control.off_cache_size,
                        "rate": None, "throttle": None}
                       if name == "off" else self.runtime_control.dynamic_controls[name])
            title = "DYNAMIC CONTROL"
            self.stdscr.addstr(1, max(0, (width - len(title)) // 2), title[:width - 1],
                               curses.color_pair(COLOR_PAIRS['title']) | curses.A_BOLD)
            rate = "Off" if profile["rate"] is None else f"{profile['rate'][0]}/{profile['rate'][1]:g}s"
            throttle = ("Off" if profile["throttle"] is None
                        else f"{profile['throttle'][0]}/{profile['throttle'][1]:g}s")
            lines = (f"Profile: < {name} >", f"Cache: {profile['cache']}",
                     f"Rate limit: {rate}", f"Throttle: {throttle}")
            for index, line in enumerate(lines):
                attribute = curses.color_pair(
                    COLOR_PAIRS['toggle_selected'] if index == 0 else COLOR_PAIRS['info']
                )
                self.stdscr.addstr(4 + index, 2, line[:max(0, width - 4)], attribute)
            help_text = "Up/Down or Left/Right: select | Enter: apply | Esc: cancel"
            note = "Profiles are defined under control.dynamic_controls in config/oculizer.json"
            if height >= 3:
                self.stdscr.addstr(height - 2, 0, note[:width - 1], curses.color_pair(COLOR_PAIRS['info']))
                self.stdscr.addstr(height - 1, 0, help_text[:width - 1], curses.color_pair(COLOR_PAIRS['controls']))
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key == -1:
                time.sleep(0.02)
                continue
            if key == 27:
                self.info_message = "Scene control changes cancelled"
                return
            if key in (curses.KEY_UP, curses.KEY_LEFT, ord('k'), ord('-')):
                selected = (selected - 1) % len(profiles)
            elif key in (curses.KEY_DOWN, curses.KEY_RIGHT, ord('j'), ord('+'), ord('=')):
                selected = (selected + 1) % len(profiles)
            elif key in (curses.KEY_ENTER, 10, 13):
                try:
                    self.runtime_control.apply_dynamic_control(
                        profiles[selected], expected_revision=initial_revision
                    )
                    self.info_message = f"Dynamic control: {profiles[selected]}"
                    return
                except PolicyConflictError as exc:
                    self.error_message = str(exc)
                    self.info_message = "External dynamic-control change detected; reopen 'l'"
                    return
                except (ValueError, RuntimeError) as exc:
                    self.error_message = str(exc)

    def _scene_color(self, scene_name):
        return scene_curses_style(scene_name)[1]

    def _scene_symbol(self, scene_name):
        return scene_visual(scene_name).symbol

    def _safe_addstr(self, row, column, text, attribute=0):
        """Write inside the current terminal bounds without reaching its last cell."""
        height, width = self.stdscr.getmaxyx()
        if row < 0 or row >= height or column < 0 or column >= width - 1:
            return False
        available = width - column - 1
        if available <= 0:
            return False
        try:
            self.stdscr.addstr(row, column, str(text)[:available], attribute)
            return True
        except curses.error:
            # A resize can happen between getmaxyx() and addstr(). The next
            # display cycle will redraw using the new dimensions.
            return False

    def _render_graph_area(self, top, bottom, width, scene_name):
        """Render only inside the unused area between status and logs."""
        area_height = bottom - top + 1
        if area_height <= 0 or width < 2:
            return
        if not self.graph_enabled:
            message = "No graph, activate it by removing  --no-graph option at startup "
            row = top + area_height // 2
            col = max(0, (width - len(message)) // 2)
            self._safe_addstr(row, col, message, curses.color_pair(COLOR_PAIRS['info']))
            return

        header = "RMS history (30s)"
        self._safe_addstr(top, 0, header, curses.color_pair(COLOR_PAIRS['info']) | curses.A_BOLD)

        lines, points = self.rms_graph.render_frame(width - 1, area_height - 1)
        for offset, line in enumerate(lines, start=1):
            if offset >= area_height:
                break
            self._safe_addstr(top + offset, 0, line, curses.color_pair(COLOR_PAIRS['info']))
        for row, column, character, point_scene in points:
            if row + 1 < area_height and column < width - 1:
                self._safe_addstr(
                    top + row + 1,
                    column,
                    character,
                    self._scene_color(point_scene) | curses.A_BOLD,
                )

    def update_display(self):
        try:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()

            if height < 8 or width < 20:
                self._safe_addstr(0, 0, "Terminal too small; resize to at least 20x8",
                                  curses.color_pair(COLOR_PAIRS['warning']) | curses.A_BOLD)
                self.stdscr.noutrefresh()
                curses.doupdate()
                return
            
            # Display title
            title = "https://github.com/infrafast/OculizerQLC"
            rendered_title = title[:width - 1]
            self._safe_addstr(0, max(0, (width - len(rendered_title)) // 2), rendered_title,
                              curses.color_pair(COLOR_PAIRS['title']) | curses.A_BOLD)

            # Display audio device info with channel details (top left)
            if self.oculizer.audio_file is not None:
                audio_info = f"Audio file: {self.oculizer.audio_file.name} (looped)"
            else:
                import sounddevice as sd
                device_info = sd.query_devices(self.oculizer.device_idx)
                if self.average_dual_channels:
                    channel_info = " ch1-2 averaged"
                else:
                    channel_info = " ch1"
                audio_info = f"Audio: {device_info['name']}{channel_info}"
            
            # Compact primary status into one line to preserve graph height.
            primary_parts = [
                audio_info,
                "Lighting: disabled" if self.test_mode else "Lighting: QLC+ Native",
                f"Predictor: {self.predictor_version}",
            ]
            if self.average_dual_channels:
                primary_parts.append("FFT: ch1-2 averaged")
            primary_info = " | ".join(primary_parts)
            self._safe_addstr(1, 0, primary_info, curses.color_pair(COLOR_PAIRS['info']))

            # Compact scene and prediction status into a second line.
            current_scene_name = self.scene_manager.current_scene['name']
            scene_parts = [f"Mode: {self.runtime_control.mode}", f"Current scene: {current_scene_name}"]
            scene_status_color = COLOR_PAIRS['info']
            if self.oculizer.current_predicted_scene is not None:
                predicted_scene = self.oculizer.current_predicted_scene
                fallback_scene = None
                if hasattr(self.scene_manager, 'fallback_mappings') and predicted_scene in self.scene_manager.fallback_mappings:
                    fallback_scene = self.scene_manager.fallback_mappings[predicted_scene]
                if fallback_scene and fallback_scene == current_scene_name:
                    scene_parts.append(f"Predicted: {predicted_scene} → {fallback_scene} (fallback)")
                    scene_status_color = COLOR_PAIRS['warning']
                else:
                    scene_parts.append(f"Predicted: {predicted_scene}")
            else:
                scene_parts.append("Predicted: -")
            if self.oculizer.latest_prediction is not None:
                scene_parts.append(f"Latest prediction: {self.oculizer.latest_prediction}")
            else:
                scene_parts.append("Latest prediction: -")
            scene_info = " | ".join(scene_parts)
            self._safe_addstr(2, 0, scene_info, curses.color_pair(scene_status_color))

            # Keep secondary diagnostics on one optional line.
            detail_parts = []
            if self.oculizer.current_cluster is not None:
                detail_parts.append(f"Cluster: {self.oculizer.current_cluster}")
            policy = self.scene_router.get_transition_policy_status()
            detail_parts.append(
                f"Dynamic: {self.runtime_control.active_dynamic_control} (cache {policy['scene_cache_size']})"
            )
            detail_info = " | ".join(detail_parts)
            self._safe_addstr(3, 0, detail_info, curses.color_pair(COLOR_PAIRS['info']))

            # Display log messages (bottom)
            visible_logs = list(self.log_messages)
            graph_top = 4
            log_capacity = min(self.log_messages.maxlen, max(0, height - 8))
            visible_logs = visible_logs[-log_capacity:] if log_capacity else []
            log_start = height - log_capacity - 4
            self._render_graph_area(graph_top, log_start - 2, width, current_scene_name)
            self._safe_addstr(log_start, 0, "Log Messages:", curses.color_pair(COLOR_PAIRS['log']) | curses.A_BOLD)
            for i, message in enumerate(visible_logs):
                self._safe_addstr(log_start + i + 1, 0, message, curses.color_pair(COLOR_PAIRS['log']))

            # Display info message (bottom - with blank line above)
            if self.info_message:
                self._safe_addstr(height-3, 0, self.info_message, curses.color_pair(COLOR_PAIRS['info']) | curses.A_BOLD)

            # Display error message (bottom)
            if self.error_message:
                self._safe_addstr(height-2, 0, self.error_message, curses.color_pair(COLOR_PAIRS['error']))

            # Display controls (bottom)
            controls = "Press 'q' to quit, Ctrl+T for toggle mode, 'r' to reload scenes, 'l' for dynamic control"
            self._safe_addstr(height-1, 0, controls, curses.color_pair(COLOR_PAIRS['controls']))

            self.stdscr.noutrefresh()
            curses.doupdate()
        except curses.error:
            # Terminal resizes are asynchronous on several curses builds.
            # Avoid writing to stderr while curses owns the screen; the next
            # scheduled refresh will repaint it.
            return
        except Exception as e:
            logging.error("Error updating display: %s", e)

    def stop(self):
        try:
            if self.control_server is not None:
                self.control_server.stop()
            self.master_modulator.shutdown()
            self.frequency_modulator.shutdown()
            self.oculizer.stop()
            # Use timeout to avoid hanging indefinitely on Windows
            self.oculizer.join(timeout=3.0)
            if self.oculizer.is_alive():
                logging.warning("Oculizer thread did not stop within timeout")
            logging.info("Audio Oculizer Controller stopped")
        except Exception as e:
            self.error_message = f"Error stopping controller: {str(e)}"
            logging.error(f"Error stopping controller: {str(e)}")

def parse_args():
    parser = argparse.ArgumentParser(
        description='Real-time audio-based Oculizer controller',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Audio input:
  - --input-device selects the one source shared by FFT/reactivity and prediction
  - --average-dual-channels optionally averages channels 1 and 2 from that source

Device Selection:
  - Devices are detected by default input, alias, name, or input index
  - This is more reliable than device indices which can change between sessions
  - You can still use a device index if needed (e.g., --input-device 84)

Scene Cache Size:
  - Controls smoothing of scene predictions (default: 10 on all platforms)
  - 1: Instant response, may flicker between scenes
  - 3-5: Minimal smoothing (~0.3-0.5s)
  - 10: Balanced smoothing (~1s)
  - 25: Heavy smoothing (~2.5s)
        """
    )
    parser.add_argument('--config', default=None,
                      help='General Oculizer JSON configuration (default: config/oculizer.json)')
    parser.add_argument('-i', '--input-device', type=str, default=None,
                      help='Override the shared audio input with default, an alias, a name, or an index')
    parser.add_argument('--audio-file', type=str, default=None,
                      help='Loop a local PCM WAV file in real time instead of opening an audio device')
    from oculizer.scene_predictors import list_available_versions
    parser.add_argument('--predictor-version', '--predictor', type=str, default='v6',
                        choices=list_available_versions(),
                      help='Scene predictor version to use (default: v6)')
    parser.add_argument('--average-dual-channels', action='store_true',
                      help='Average first two input channels together for FFT (useful for Scarlett 18i20)')
    parser.add_argument('--scene-cache-size', type=int, default=10,
                      help='Number of recent predictions to cache for smoothing (default: 10). 1=instant, 25=heavy smoothing')
    parser.add_argument('--dynamic-control', default='off', metavar='PROFILE',
                      help="Apply a named dynamic-control profile (default: off)")
    parser.add_argument('--scene-max-duration', type=float, default=None, metavar='SECONDS',
                      help='Override the configured base automatic music-scene duration')
    parser.add_argument('--control-socket', default=default_control_socket_path(), help='Unix runtime control socket path')
    parser.add_argument('--no-control-socket', action='store_true', help='Disable the local runtime control socket')
    parser.add_argument('--test', action='store_true',
                      help='Test mode: enable scene predictions without lighting output; use a virtual cable for live audio if needed')
    parser.add_argument('--qlc-host', default=None,
                      help='Override the QLC+ Native host')
    parser.add_argument('--qlc-port', type=int, default=None,
                      help='Override the QLC+ Native port')
    parser.add_argument('--qlc-encryptionkey', default=None, metavar='KEY',
                      help='Override the QLC+ native encryption key (default: lighting.native in --config)')
    parser.add_argument('--dry-run', action='store_true', default=None,
                      help='Validate native QLC+ intentions without opening a network connection')
    parser.add_argument('--no-graph', action='store_true',
                      help='Disable the interactive RMS history graph')
    parser.add_argument('--list-devices', action='store_true',
                      help='List available audio devices and exit')
    args = parser.parse_args()
    try:
        config = load_runtime_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    args.config = str(
        Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH.resolve()
    )
    if args.input_device is None:
        args.input_device = configured_audio_input(config)
    args.silence_config = configured_silence(config)
    args.speech_config = configured_speech(config)
    args.prediction_config = configured_prediction(config)
    args.master_config = configured_master_modulation(config)
    args.frequency_config = configured_frequency_modulation(config)
    args.fast_detection_config = configured_fast_detection(config)
    args.dynamic_controls = configured_dynamic_controls(config)
    if args.scene_max_duration is None:
        args.scene_max_duration = configured_scene_max_duration(config)
    if args.dynamic_control != 'off' and args.dynamic_control not in args.dynamic_controls:
        parser.error("--dynamic-control must be 'off' or a profile from control.dynamic_controls")
    if args.audio_file:
        audio_file = Path(args.audio_file).expanduser().resolve()
        if not audio_file.is_file():
            parser.error(f'--audio-file does not exist: {audio_file}')
        args.audio_file = str(audio_file)
    if not 1 <= args.scene_cache_size <= 100:
        parser.error('--scene-cache-size must be between 1 and 100')
    if not 0.5 <= args.scene_max_duration <= 3600:
        parser.error('--scene-max-duration must be between 0.5 and 3600 seconds')
    return args

def main(stdscr, input_device, predictor_version,
         average_dual_channels, scene_cache_size, test_mode,
         config_path, qlc_host, qlc_port, dry_run,
         silence_config, speech_config, master_config, frequency_config,
         prediction_window_seconds, prediction_interval_seconds, audio_file,
         graph_enabled, dynamic_control, dynamic_controls,
         scene_max_duration, control_socket_path, fast_detection_config,
         qlc_encryption_key):
    setup_colors()
    initialize_screen(stdscr)
    lighting_detail = "Lighting: QLC+ Native"
    audio_detail = (
        f"Audio: WAV file {os.path.basename(audio_file)}"
        if audio_file else f"Audio input: {input_device}"
    )
    loading_details = [
        lighting_detail,
        audio_detail,
        f"Predictor: {predictor_version}",
        f"Dynamic control: {dynamic_control}",
    ]
    show_loading_screen(stdscr, loading_details)

    startup_output = io.StringIO()
    try:
        with redirect_stdout(startup_output), redirect_stderr(startup_output):
            controller = AudioOculizerController(
                stdscr,
                input_device=input_device,
                predictor_version=predictor_version,
                average_dual_channels=average_dual_channels,
                scene_cache_size=scene_cache_size,
                test_mode=test_mode,
                config_path=config_path,
                qlc_host=qlc_host,
                qlc_port=qlc_port,
                dry_run=dry_run,
                silence_config=silence_config,
                speech_config=speech_config,
                master_config=master_config,
                frequency_config=frequency_config,
                prediction_window_seconds=prediction_window_seconds,
                prediction_interval_seconds=prediction_interval_seconds,
                audio_file=audio_file,
                graph_enabled=graph_enabled,
                dynamic_control=dynamic_control,
                dynamic_controls=dynamic_controls,
                scene_max_duration=scene_max_duration,
                control_socket_path=control_socket_path,
                fast_detection_config=fast_detection_config,
                qlc_encryption_key=qlc_encryption_key,
            )
    finally:
        _log_captured_startup_output(startup_output)
    # Replace the loading screen with a clean differential-rendering baseline.
    initialize_screen(stdscr)
    
    try:
        controller.start()
    except KeyboardInterrupt:
        _stop_after_keyboard_interrupt(controller)
    except Exception as e:
        stdscr.addstr(0, 0, f"Unhandled error: {str(e)}", curses.color_pair(COLOR_PAIRS['error']))
        stdscr.refresh()
        time.sleep(5)

if __name__ == "__main__":
    # Parse args first to handle --list-devices without curses
    args = parse_args()
    setup_logging()  # Set up logging before creating any objects
    
    # List devices if requested (don't use curses for this)
    if args.list_devices:
        import sounddevice as sd
        print("\nAvailable audio devices:")
        devices = sd.query_devices()
        print(devices)
        print("\n=== Input Devices ===")
        for i, device in enumerate(devices):
            if isinstance(device, dict) and device.get('max_input_channels', 0) > 0:
                print(f"{i}: {device['name']} ({device['max_input_channels']} channels)")
    else:
        if args.audio_file:
            logging.info("Starting with looped WAV input: %s", args.audio_file)
        else:
            logging.info("Using audio input '%s' for FFT/reactivity and prediction", args.input_device)
        if args.test:
            logging.info("TEST MODE enabled: lighting output disabled")
        
        try:
            wrapper(lambda stdscr: main(
                stdscr,
                args.input_device,
                args.predictor_version,
                args.average_dual_channels,
                args.scene_cache_size,
                args.test,
                args.config,
                args.qlc_host,
                args.qlc_port,
                args.dry_run,
                args.silence_config,
                args.speech_config,
                args.master_config,
                args.frequency_config,
                args.prediction_config.window_seconds,
                args.prediction_config.interval_seconds,
                args.audio_file,
                not args.no_graph,
                args.dynamic_control,
                args.dynamic_controls,
                args.scene_max_duration,
                None if args.no_control_socket else args.control_socket,
                args.fast_detection_config,
                args.qlc_encryptionkey,
            ))
        except Exception:
            raise
