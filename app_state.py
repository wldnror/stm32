import threading

display_lock = threading.Lock()
stm32_state_lock = threading.Lock()

wifi_action_lock = threading.Lock()
wifi_action_requested = False
wifi_action_running = False

ui_override_lock = threading.Lock()
ui_override = {
    "active": False,
    "kind": "none",
    "percent": 0,
    "message": "",
    "pos": (0, 0),
    "font_size": 15,
    "line2": "",
}

wifi_stage_lock = threading.Lock()
wifi_stage = {
    "active": False,
    "target_percent": 0,
    "display_percent": 0,
    "line1": "",
    "line2": "",
    "spinner": 0,
}

ap_state_lock = threading.Lock()
ap_state = {
    "last_clients": 0,
    "flash_until": 0.0,
    "poll_next": 0.0,
    "spinner": 0,
}

auto_flash_done_connection = False
need_update = False
is_command_executing = False
is_executing = False

execute_press_time = None
execute_is_down = False
execute_long_handled = False
execute_short_event = False

next_press_time = None
next_is_down = False
next_long_handled = False
next_pressed_event = False

menu_stack = []
current_menu = None
commands = []
command_names = []
command_types = []
menu_extras = []
current_command_index = 0

status_message = ""
message_position = (0, 0)
message_font_size = 17

ina = None
battery_percentage = -1

ina_lock = threading.Lock()
ina_last = {"v": None, "c": None, "p": None, "ts": 0.0}
ina_poll_started = False

connection_success = False
connection_failed_since_last_success = False
last_stm32_check_time = 0.0

stop_threads = False
wifi_cancel_requested = False

cached_ip = "0.0.0.0"
cached_wifi_level = 0
cached_online = False
last_menu_online = None
last_good_wifi_profile = None

git_state_lock = threading.Lock()
git_has_update_cached = False
git_last_check = 0.0
git_check_interval = 5.0

scan_lock = threading.Lock()
scan_active = False
scan_done = False
scan_ips = []
scan_infos = {}
scan_selected_idx = 0
scan_selected_ip = None
scan_last_tick = 0.0
scan_base_prefix = None
scan_cursor = 2
scan_seen = {}
scan_menu_dirty = False
scan_menu_dirty_ts = 0.0
scan_menu_rebuild_last = 0.0
scan_prefix_candidate = None
scan_prefix_candidate_cnt = 0

scan_detail_lock = threading.Lock()
scan_detail_active = False
scan_detail_ip = None
scan_detail = {
    "gas": None,
    "flags": {"PWR": False, "A1": False, "A2": False, "FUT": False},
    "ts": 0.0,
    "err": "",
}

_detect_cache_lock = threading.Lock()
_detect_cache = {"ts": 0.0, "flash_kb": None, "dev_id": None}

_unlock_cache_lock = threading.Lock()
_unlock_cache = {"ts": 0.0, "ok": False}

last_time_button_next_pressed = 0.0
last_time_button_execute_pressed = 0.0

last_oled_update_time = 0.0
font_cache = {}
