import os

import app_state as st
from app_config import FIRMWARE_DIR, GENERAL_ROOT, TFTP_ROOT, OUT_SCRIPT_PATH
from app_utils import parse_order_and_name, is_fw_extract_mode

def build_scan_menu():
    with st.scan_lock:
        ips = list(st.scan_ips)

    commands_local, names_local, types_local, extras_local = [], [], [], []
    for ip in ips:
        commands_local.append(None)
        names_local.append(f"▶ {ip}")
        types_local.append("scan_item")
        extras_local.append(ip)

    commands_local.append(None)
    names_local.append("◀ 이전으로")
    types_local.append("back_from_scan")
    extras_local.append(None)

    return {
        "dir": "__scan__",
        "commands": commands_local,
        "names": names_local,
        "types": types_local,
        "extras": extras_local,
    }

def build_scan_detail_menu(ip: str):
    return {
        "dir": "__scan_detail__",
        "commands": [None],
        "names": [f"{ip}"],
        "types": ["scan_detail"],
        "extras": [ip],
    }

def build_menu_for_dir(dir_path, is_root=False):
    entries = []
    try:
        if is_root:
            gas_dirs = {}
            root_bins = {}

            for base_root in (TFTP_ROOT, GENERAL_ROOT):
                if not os.path.isdir(base_root):
                    continue
                for name in os.listdir(base_root):
                    full = os.path.join(base_root, name)
                    if os.path.isdir(full):
                        gas_dirs[name] = full
                        continue
                    if name.lower().endswith(".bin"):
                        root_bins[name] = full

            for dname in sorted(gas_dirs.keys()):
                order, display_name = parse_order_and_name(dname, is_dir=True)
                entries.append((order, 0, "▶ " + display_name, "dir", gas_dirs[dname]))

            for bname in sorted(root_bins.keys()):
                order, display_name = parse_order_and_name(bname, is_dir=False)
                entries.append((order, 1, display_name, "bin", root_bins[bname]))
        else:
            for fname in os.listdir(dir_path):
                full_path = os.path.join(dir_path, fname)
                if os.path.isdir(full_path):
                    order, display_name = parse_order_and_name(fname, is_dir=True)
                    entries.append((order, 0, "▶ " + display_name, "dir", full_path))
                elif fname.lower().endswith(".bin"):
                    order, display_name = parse_order_and_name(fname, is_dir=False)
                    entries.append((order, 1, display_name, "bin", full_path))
    except FileNotFoundError:
        entries = []

    entries.sort(key=lambda x: (x[0], x[1], x[2]))

    commands_local = []
    names_local = []
    types_local = []
    extras_local = []

    for order, type_pri, display_name, item_type, extra in entries:
        commands_local.append(None)
        names_local.append(display_name)
        types_local.append(item_type)
        extras_local.append(extra)

    if is_root:
        if st.cached_online and is_fw_extract_mode():
            commands_local.append(f"python3 {OUT_SCRIPT_PATH}")
            names_local.append("FW 추출(OUT)")
            types_local.append("script")
            extras_local.append(None)

        if st.cached_online:
            with st.git_state_lock:
                has_update = st.git_has_update_cached
            if has_update:
                commands_local.append("git_pull")
                names_local.append("시스템 업데이트")
                types_local.append("system")
                extras_local.append(None)

        commands_local.append("wifi_setup")
        names_local.append("Wi-Fi 설정")
        types_local.append("wifi")
        extras_local.append(None)

        commands_local.append("device_scan")
        names_local.append("감지기 연결(스캔)")
        types_local.append("device_scan")
        extras_local.append(None)
    else:
        commands_local.append(None)
        names_local.append("◀ 이전으로")
        types_local.append("back")
        extras_local.append(None)

    return {
        "dir": dir_path,
        "commands": commands_local,
        "names": names_local,
        "types": types_local,
        "extras": extras_local,
    }

def refresh_root_menu(reset_index=False):
    st.current_menu = build_menu_for_dir(FIRMWARE_DIR, is_root=True)
    st.commands = st.current_menu["commands"]
    st.command_names = st.current_menu["names"]
    st.command_types = st.current_menu["types"]
    st.menu_extras = st.current_menu["extras"]

    if reset_index or (st.current_command_index >= len(st.commands)):
        st.current_command_index = 0

def is_root_menu_view() -> bool:
    return bool(st.current_menu and st.current_menu.get("dir") == FIRMWARE_DIR)
