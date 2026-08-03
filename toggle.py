import os
import json
import threading
import curses
import time
import argparse
import platform
import sounddevice as sd
import logging
from collections import OrderedDict
import math

from oculizer.light import Oculizer, OUTPUT_CHOICES
from oculizer.runtime_config import configured_audio_input, load_runtime_config


def setup_logging():
    logging.basicConfig(
        filename=os.path.join(os.path.dirname(__file__), 'oculizer.log'),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        force=True,
    )
from oculizer.scenes import SceneManager

def parse_args():
    # Detect OS and set defaults
    is_macos = platform.system() == 'Darwin'
    
    # macOS defaults (match oculize.py defaults)
    if is_macos:
        default_profile = 'mobile'
    else:
        # Windows/Linux defaults
        default_profile = 'bbgv'
    
    parser = argparse.ArgumentParser(description='Interactive scene toggler for Oculizer')
    parser.add_argument('--config', default=None,
                      help='General Oculizer JSON configuration (default: config/oculizer.json)')
    parser.add_argument('-p', '--profile', type=str, default=None,
                      help=f'Enttec fixture profile (default for Enttec: {default_profile}; unused for QLC+)')
    parser.add_argument('-i', '--input', type=str, default=None,
                      help='Override the configured audio input with default, an alias, a name, or an index')
    parser.add_argument('--average-dual-channels', action='store_true',
                      help='Average first two input channels together for FFT (useful for Scarlett 18i20)')
    parser.add_argument('--output', choices=OUTPUT_CHOICES, default='enttec',
                      help='Lighting output backend (default: enttec)')
    parser.add_argument('--osc-config', default=None,
                      help='QLC+ OSC JSON configuration (default: config/qlc_osc.json)')
    parser.add_argument('--scene-map', default=None,
                      help='Logical QLC+ scene map (default: config/qlc_scene_map.json)')
    parser.add_argument('--osc-host', default=None,
                      help='Override the QLC+ OSC destination host')
    parser.add_argument('--osc-port', type=int, default=None,
                      help='Override the QLC+ OSC destination port')
    parser.add_argument('--osc-dry-run', action='store_true', default=None,
                      help='Log OSC messages without sending UDP packets')
    parser.add_argument('--list-devices', action='store_true',
                      help='List available audio input devices and exit')
    args = parser.parse_args()
    try:
        config = load_runtime_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
    if args.input is None:
        args.input = configured_audio_input(config)
    if args.profile is None and args.output == 'enttec':
        args.profile = default_profile
    return args


def list_input_devices():
    devices = sd.query_devices()
    print("Available audio input devices:")
    found = False
    for index, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            found = True
            print(f"{index}: {device['name']} ({device['max_input_channels']} input channels)")
    if not found:
        print("No audio input device detected.")

def sort_scenes_alphabetically(scenes):
    return OrderedDict(sorted(scenes.items()))

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    # Active scene: White text on green background
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_GREEN)
    # Selected scene: Black text on yellow background
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    # Hover scene: White text on blue background
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)
    # Normal scene: White text on default background
    curses.init_pair(4, curses.COLOR_WHITE, -1)
    # Info text: Cyan text on default background
    curses.init_pair(5, curses.COLOR_CYAN, -1)
    # Instructions: Magenta text on default background
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    # Search text: Yellow text on default background
    curses.init_pair(7, curses.COLOR_YELLOW, -1)

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
    max_name_length = max(len(scene[0]) for scene in scene_list) + 2  # Add 2 for padding
    
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

def run_toggle_mode(stdscr, scene_manager, light_controller, profile):
    """
    Run toggle mode with existing scene_manager and light_controller.
    Returns True if should exit program, False if should return to caller.
    """
    # Note: light_controller should already be running

    # Sort scenes alphabetically
    scene_manager.scenes = sort_scenes_alphabetically(scene_manager.scenes)

    # Keep this interface keyboard-only. Mouse protocols are not decoded
    # consistently by curses in macOS integrated terminals and over SSH.
    curses.mousemask(0)
    stdscr.keypad(1)
    stdscr.nodelay(1)
    init_colors()

    # Initialize variables
    selected_index = 0
    current_scene_name = scene_manager.current_scene['name']
    search_string = ""
    last_search_time = time.time()
    hover_pos = (-1, -1)

    try:
        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            scene_list = list(scene_manager.scenes.items())
            total_scenes = len(scene_list)

            # Calculate grid layout
            num_rows, num_columns, column_width = calculate_grid_dimensions(scene_list, max_x, max_y - 5)

            # Display header
            header_text = f"Current scene: {current_scene_name} (Profile: {profile})"
            commands_text = "Commands: [^T] Return  [^Q] Quit  [^R] Reload"
            if search_string:
                header_text += f" [Search: {search_string}]"
            
            padding = max(1, max_x - len(header_text) - len(commands_text))
            stdscr.addstr(0, 0, header_text, curses.color_pair(5))
            stdscr.addstr(0, len(header_text) + padding, commands_text, curses.color_pair(6))
            stdscr.addstr(1, 0, "Available scenes:", curses.color_pair(5))

            # Display scenes in grid
            for i, (scene, _) in enumerate(scene_list):
                row, col = get_grid_position(i, num_columns)
                if row >= num_rows or row >= max_y - 5:  # Account for header and footer
                    break

                display_y = row + 3  # Start after header
                display_x = col * column_width

                scene_str = scene
                if len(scene_str) > column_width - 2:
                    scene_str = scene_str[:column_width - 5] + "..."

                # Determine scene display style
                if scene == current_scene_name:
                    color = curses.color_pair(1)
                elif i == selected_index:
                    color = curses.color_pair(2)
                elif (row, col) == hover_pos:
                    color = curses.color_pair(3)
                else:
                    color = curses.color_pair(4)

                # Pad scene name to column width
                scene_str = scene_str.ljust(column_width - 1)
                stdscr.addstr(display_y, display_x, scene_str, color)

            # Display instructions
            instructions = "Ctrl+T return, Ctrl+Q quit, Ctrl+R reload | Type to search, arrows, Enter to activate"
            stdscr.addstr(max_y-1, 0, instructions, curses.color_pair(6))

            stdscr.refresh()

            try:
                event = stdscr.getch()
                current_time = time.time()

                if search_string and current_time - last_search_time > 1.0:
                    search_string = ""

                if event == 17:  # Ctrl+Q
                    return True  # Exit program
                elif event == 20:  # Ctrl+T
                    return False  # Return to oculizer mode
                elif event == 18:  # Ctrl+R
                    try:
                        light_controller.reload_scene_configuration()
                        scene_manager.scenes = sort_scenes_alphabetically(scene_manager.scenes)
                        light_controller.change_scene(current_scene_name)
                        scene_list = list(scene_manager.scenes.items())
                        total_scenes = len(scene_list)
                        stdscr.addstr(max_y-1, 0, "Scenes reloaded successfully.", curses.color_pair(5))
                    except Exception as e:
                        stdscr.addstr(max_y-1, 0, f"Error reloading scenes: {str(e)}", curses.color_pair(1))
                    stdscr.refresh()
                    time.sleep(2)
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
                    if 0 <= selected_index < total_scenes:
                        new_scene = scene_list[selected_index][0]
                        if light_controller.change_scene(new_scene):
                            current_scene_name = new_scene
                        else:
                            message = f"Scene '{new_scene}' has no active output mapping"
                            stdscr.addstr(max_y - 2, 0, message[:max_x - 1], curses.color_pair(1))
                            stdscr.refresh()
                            time.sleep(1)
                        search_string = ""
                elif event == 27:  # ESC key
                    search_string = ""
                    # Note: In standalone toggle.py, there's no prediction mode to revert to
                    # This is only applicable when run from oculize.py
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
        curses.mousemask(0)  # Disable mouse events

def main(stdscr, profile, input_device, average_dual_channels, output, osc_config,
         scene_map, osc_host, osc_port, osc_dry_run):
    # Load profile fixtures for scene manager
    from pathlib import Path
    profile_fixtures = set()
    if output == 'enttec':
        try:
            profile_path = Path(__file__).resolve().parent / 'profiles' / f'{profile}.json'
            if profile_path.exists():
                with open(profile_path, 'r') as f:
                    profile_data = json.load(f)
                    if 'lights' in profile_data:
                        profile_fixtures = {light['name'] for light in profile_data['lights'] if 'name' in light}
        except Exception:
            pass  # Fall back to no profile awareness
    
    # Initialize scene manager with profile awareness
    scene_manager = SceneManager(
        'scenes',
        profile_name=profile if output == 'enttec' else None,
        available_fixtures=profile_fixtures if output == 'enttec' else None,
    )
    scene_manager.set_scene('party')  # Set an initial scene
    light_controller = Oculizer(
        profile,
        scene_manager,
        input_device,
        average_dual_channels=average_dual_channels,
        output=output,
        osc_config_path=osc_config,
        osc_scene_map_path=scene_map,
        osc_host=osc_host,
        osc_port=osc_port,
        osc_dry_run=osc_dry_run,
    )

    if output == 'qlc-osc':
        light_controller.restrict_scenes_to_backend()
    light_controller.start()
    
    try:
        # Run toggle mode (standalone always exits on Ctrl+Q)
        run_toggle_mode(
            stdscr,
            scene_manager,
            light_controller,
            profile if output == 'enttec' else 'QLC+ logical',
        )
    finally:
        light_controller.stop()
        light_controller.join()
        curses.endwin()

if __name__ == '__main__':
    args = parse_args()
    if args.list_devices:
        list_input_devices()
    else:
        setup_logging()
        curses.wrapper(lambda stdscr: main(
            stdscr,
            args.profile,
            args.input,
            args.average_dual_channels,
            args.output,
            args.osc_config,
            args.scene_map,
            args.osc_host,
            args.osc_port,
            args.osc_dry_run,
        ))
