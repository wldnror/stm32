import time
from datetime import datetime

from PIL import Image, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1107
from luma.core.render import canvas

import app_state as st
from app_config import (
    FONT_PATH,
    BATTERY_ICON_PATH,
    VISUAL_X_OFFSET,
    AP_SSID,
    AP_PASS,
    AP_IP,
    PORTAL_PORT,
    APP_VERSION,
)

serial = i2c(port=1, address=0x3C)
device = sh1107(serial, rotate=1)

font_big = ImageFont.truetype(FONT_PATH, 12)
font_st = ImageFont.truetype(FONT_PATH, 11)
font_time = ImageFont.truetype(FONT_PATH, 12)

low_battery_icon = Image.open(BATTERY_ICON_PATH)
medium_battery_icon = Image.open(BATTERY_ICON_PATH)
high_battery_icon = Image.open(BATTERY_ICON_PATH)
full_battery_icon = Image.open(BATTERY_ICON_PATH)


def get_font(size: int):
    f = st.font_cache.get(size)
    if f is None:
        f = ImageFont.truetype(FONT_PATH, size)
        st.font_cache[size] = f
    return f


def select_battery_icon(percentage):
    if percentage < 20:
        return low_battery_icon
    if percentage < 60:
        return medium_battery_icon
    if percentage < 100:
        return high_battery_icon
    return full_battery_icon


def text_size(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])
    except Exception:
        try:
            return draw.textsize(text, font=font)
        except Exception:
            return (len(text) * 6, 10)


def ellipsis_to_width(draw, text, font, max_w):
    s = text or ""
    if max_w <= 0:
        return ""
    w, _ = text_size(draw, s, font)
    if w <= max_w:
        return s
    ell = "…"
    w_ell, _ = text_size(draw, ell, font)
    if w_ell > max_w:
        return ""
    lo, hi = 0, len(s)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = s[:mid] + ell
        w_c, _ = text_size(draw, cand, font)
        if w_c <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best else ell


def draw_center_text_autofit(draw, text, center_x, center_y, max_width, start_size, min_size=10):
    size = start_size
    while size >= min_size:
        f = get_font(size)
        w, _ = text_size(draw, text, f)
        if w <= max_width:
            try:
                draw.text((center_x, center_y), text, font=f, fill=255, anchor="mm")
            except TypeError:
                draw.text((center_x, center_y), text, font=f, fill=255)
            return
        size -= 1
    f = get_font(min_size)
    try:
        draw.text((center_x, center_y), text, font=f, fill=255, anchor="mm")
    except TypeError:
        draw.text((center_x, center_y), text, font=f, fill=255)


def draw_wifi_bars(draw, x, y, level):
    bar_w = 3
    gap = 2
    base_h = 3
    max_h = base_h + 3 * 3
    for i in range(4):
        h = base_h + i * 3
        xx = x + i * (bar_w + gap)
        yy = y + (max_h - h)
        if level >= (i + 1):
            draw.rectangle([xx, yy, xx + bar_w, y + max_h], fill=255)
        else:
            draw.rectangle([xx, y + max_h - 1, xx + bar_w, y + max_h], fill=255)


def set_ui_progress(percent, message, pos=(0, 0), font_size=15):
    with st.ui_override_lock:
        st.ui_override["active"] = True
        st.ui_override["kind"] = "progress"
        st.ui_override["percent"] = int(max(0, min(100, percent)))
        st.ui_override["message"] = message
        st.ui_override["pos"] = pos
        st.ui_override["font_size"] = font_size
        st.ui_override["line2"] = ""


def set_ui_text(line1, line2="", pos=(0, 0), font_size=15):
    with st.ui_override_lock:
        st.ui_override["active"] = True
        st.ui_override["kind"] = "text"
        st.ui_override["message"] = line1
        st.ui_override["line2"] = line2
        st.ui_override["pos"] = pos
        st.ui_override["font_size"] = font_size
        st.ui_override["percent"] = 0


def clear_ui_override():
    with st.ui_override_lock:
        st.ui_override["active"] = False
        st.ui_override["kind"] = "none"
        st.ui_override["message"] = ""
        st.ui_override["line2"] = ""
        st.ui_override["percent"] = 0


def wifi_stage_set(percent, line1, line2=""):
    with st.wifi_stage_lock:
        st.wifi_stage["active"] = True
        st.wifi_stage["target_percent"] = int(max(0, min(100, percent)))
        if st.wifi_stage["display_percent"] > st.wifi_stage["target_percent"]:
            st.wifi_stage["display_percent"] = st.wifi_stage["target_percent"]
        st.wifi_stage["line1"] = line1 or ""
        st.wifi_stage["line2"] = line2 or ""


def wifi_stage_clear():
    with st.wifi_stage_lock:
        st.wifi_stage["active"] = False
        st.wifi_stage["target_percent"] = 0
        st.wifi_stage["display_percent"] = 0
        st.wifi_stage["line1"] = ""
        st.wifi_stage["line2"] = ""
        st.wifi_stage["spinner"] = 0


def wifi_stage_tick():
    with st.wifi_stage_lock:
        if not st.wifi_stage["active"]:
            st.wifi_stage["spinner"] = (st.wifi_stage["spinner"] + 1) % 4
            return
        t = st.wifi_stage["target_percent"]
        d = st.wifi_stage["display_percent"]
        if d < t:
            step = 1
            if t - d > 25:
                step = 3
            elif t - d > 12:
                step = 2
            st.wifi_stage["display_percent"] = min(t, d + step)
        st.wifi_stage["spinner"] = (st.wifi_stage["spinner"] + 1) % 4


def draw_box_label(draw, x, y, w, h, label, active):
    f = get_font(10)
    if active:
        draw.rectangle([x, y, x + w, y + h], fill=255)
        draw.text((x + 2, y + 2), label, font=f, fill=0)
    else:
        draw.rectangle([x, y, x + w, y + h], outline=255, fill=0)
        draw.text((x + 2, y + 2), label, font=f, fill=255)


def draw_scan_detail_screen():
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, fill="black")

        W, H = device.width, device.height
        TOP_H = 16
        BOT_H = 15
        MID_Y0 = TOP_H
        MID_Y1 = H - BOT_H
        MID_CY = (MID_Y0 + MID_Y1) // 2

        f = st.scan_detail.get("flags", {}) or {}

        boxes = [
            ("PWR", 26, bool(f.get("PWR"))),
            ("AL1", 24, bool(f.get("A1"))),
            ("AL2", 24, bool(f.get("A2"))),
            ("FUT", 26, bool(f.get("FUT"))),
        ]
        gap = 2
        total_w = sum(b[1] for b in boxes) + gap * (len(boxes) - 1)
        start_x = max(0, (W - total_w) // 2)
        x = start_x
        for label, bw, active in boxes:
            draw_box_label(draw, x, 1, bw, 14, label, active)
            x += bw + gap

        gas_txt = st.scan_detail.get("gas", None)
        if gas_txt is None:
            gas_txt = "--"
        else:
            try:
                gas_txt = str(int(gas_txt)) if abs(float(gas_txt) - int(float(gas_txt))) < 1e-6 else f"{float(gas_txt):.1f}"
            except Exception:
                gas_txt = "--"

        draw_center_text_autofit(draw, gas_txt, (W // 2 + VISUAL_X_OFFSET), MID_CY, max_width=W - 6, start_size=30, min_size=18)

        err = (st.scan_detail.get("err") or "").strip()
        fbot = get_font(11)
        max_w = W - 4
        y0 = H - BOT_H + 1

        if err:
            msg = ellipsis_to_width(draw, "ERR " + err, fbot, max_w)
            draw.text((2, y0), msg, font=fbot, fill=255)
        else:
            msg = ellipsis_to_width(draw, "NEXT=뒤로  HOLD=업뎃", fbot, max_w)
            draw.text((2, y0), msg, font=fbot, fill=255)


def draw_scan_screen():
    with st.scan_lock:
        ips = list(st.scan_ips)
        idx = st.scan_selected_idx
        infos = dict(st.scan_infos)
        done = st.scan_done

    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, fill="black")

        W, H = device.width, device.height
        now_dt = datetime.now()
        current_time = now_dt.strftime("%H:%M")
        draw.text((2, 1), current_time, font=get_font(12), fill=255)

        cx = W // 2 + VISUAL_X_OFFSET

        if ips:
            if idx < 0:
                idx = 0
            if idx >= len(ips):
                idx = len(ips) - 1

            sel_ip = ips[idx]
            st.scan_selected_ip = sel_ip

            title_y = 30
            info_y = H - 12

            draw_center_text_autofit(draw, sel_ip, cx, title_y, W - 4, 18, min_size=12)

            info = infos.get(sel_ip, "")
            if not info:
                info = "가스: 읽는중..."
            else:
                info = "가스: " + info

            draw.text((2, info_y), ellipsis_to_width(draw, info, get_font(10), W - 4), font=get_font(10), fill=255)
        else:
            title_y = 30
            info_y = H - 12
            if done:
                draw_center_text_autofit(draw, "장치 없음", cx, title_y, W - 4, 18, min_size=12)
                draw.text((2, info_y), ellipsis_to_width(draw, "◀ 이전으로", get_font(10), W - 4), font=get_font(10), fill=255)
            else:
                draw_center_text_autofit(draw, "장치 검색중...", cx, title_y, W - 4, 18, min_size=12)
                draw.text((2, info_y), ellipsis_to_width(draw, "잠시만...", get_font(10), W - 4), font=get_font(10), fill=255)


def draw_override(draw):
    with st.ui_override_lock:
        active = st.ui_override["active"]
        kind = st.ui_override["kind"]
        percent = st.ui_override["percent"]
        msg = st.ui_override["message"]
        pos = st.ui_override["pos"]
        fs = st.ui_override["font_size"]
        line2 = st.ui_override["line2"]

    if not active:
        return False

    draw.rectangle(device.bounding_box, fill="black")
    if kind == "progress":
        draw.text(pos, msg, font=get_font(fs), fill=255)
        x1, y1, x2, y2 = 10, 50, 110, 60
        draw.rectangle([(x1, y1), (x2, y2)], fill=0)
        fill_w = int((x2 - x1) * (percent / 100.0))
        fill_w = int(max(0, min((x2 - x1), fill_w)))
        if fill_w > 0:
            draw.rectangle([(x1, y1), (x1 + fill_w, y2)], fill=255)
        return True

    if kind == "text":
        draw.text(pos, msg, font=get_font(fs), fill=255)
        if line2:
            draw.text((pos[0], pos[1] + 18), line2, font=get_font(fs), fill=255)
        return True

    return False


def update_oled_display():
    if not st.display_lock.acquire(timeout=0.2):
        return
    try:
        if not st.commands:
            return

        with st.wifi_action_lock:
            wifi_running = st.wifi_action_running

        now_dt = datetime.now()
        current_time = now_dt.strftime("%H시 %M분")
        voltage_percentage = st.battery_percentage
        ip_address = st.cached_ip
        wifi_level = st.cached_wifi_level

        with canvas(device) as draw:
            if draw_override(draw):
                return

            if st.current_menu and st.current_menu.get("dir") == "__scan_detail__":
                draw.rectangle(device.bounding_box, fill="black")
                W, H = device.width, device.height
                TOP_H = 16
                BOT_H = 15
                MID_Y0 = TOP_H
                MID_Y1 = H - BOT_H
                MID_CY = (MID_Y0 + MID_Y1) // 2

                f = st.scan_detail.get("flags", {}) or {}
                boxes = [
                    ("PWR", 26, bool(f.get("PWR"))),
                    ("AL1", 24, bool(f.get("A1"))),
                    ("AL2", 24, bool(f.get("A2"))),
                    ("FUT", 26, bool(f.get("FUT"))),
                ]
                gap = 2
                total_w = sum(b[1] for b in boxes) + gap * (len(boxes) - 1)
                start_x = max(0, (W - total_w) // 2)
                x = start_x
                for label, bw, active in boxes:
                    draw_box_label(draw, x, 1, bw, 14, label, active)
                    x += bw + gap

                gas = st.scan_detail.get("gas", None)
                gas_txt = "--"
                try:
                    if gas is not None:
                        gas_txt = str(int(gas)) if abs(float(gas) - int(float(gas))) < 1e-6 else f"{float(gas):.1f}"
                except Exception:
                    gas_txt = "--"

                draw_center_text_autofit(draw, gas_txt, (W // 2 + VISUAL_X_OFFSET), MID_CY, max_width=W - 6, start_size=30, min_size=18)

                err = (st.scan_detail.get("err") or "").strip()
                fbot = get_font(11)
                max_w = W - 4
                y0 = H - BOT_H + 1

                if err:
                    msg = ellipsis_to_width(draw, "ERR " + err, fbot, max_w)
                    draw.text((2, y0), msg, font=fbot, fill=255)
                else:
                    msg = ellipsis_to_width(draw, "NEXT=뒤로  HOLD=업뎃", fbot, max_w)
                    draw.text((2, y0), msg, font=fbot, fill=255)
                return

            if st.current_menu and st.current_menu.get("dir") == "__scan__":
                draw.rectangle(device.bounding_box, fill="black")

                W, H = device.width, device.height
                draw.text((2, 1), datetime.now().strftime("%H:%M"), font=get_font(12), fill=255)
                cx = W // 2 + VISUAL_X_OFFSET

                with st.scan_lock:
                    ips = list(st.scan_ips)
                    idx = st.scan_selected_idx
                    infos = dict(st.scan_infos)
                    done = st.scan_done

                if ips:
                    if idx < 0:
                        idx = 0
                    if idx >= len(ips):
                        idx = len(ips) - 1

                    sel_ip = ips[idx]
                    st.scan_selected_ip = sel_ip
                    draw_center_text_autofit(draw, sel_ip, cx, 30, W - 4, 18, min_size=12)

                    info = infos.get(sel_ip, "")
                    if not info:
                        info = "가스: 읽는중..."
                    else:
                        info = "가스: " + info

                    draw.text((2, H - 12), ellipsis_to_width(draw, info, get_font(10), W - 4), font=get_font(10), fill=255)
                else:
                    if done:
                        draw_center_text_autofit(draw, "장치 없음", cx, 30, W - 4, 18, min_size=12)
                        draw.text((2, H - 12), ellipsis_to_width(draw, "◀ 이전으로", get_font(10), W - 4), font=get_font(10), fill=255)
                    else:
                        draw_center_text_autofit(draw, "장치 검색중...", cx, 30, W - 4, 18, min_size=12)
                        draw.text((2, H - 12), ellipsis_to_width(draw, "잠시만...", get_font(10), W - 4), font=get_font(10), fill=255)
                return

            title = st.command_names[st.current_command_index]
            item_type = st.command_types[st.current_command_index]

            if item_type in ("system", "wifi"):
                ip_display = "연결 없음" if ip_address == "0.0.0.0" else ip_address
                draw.text((0, 51), ellipsis_to_width(draw, ip_display, font_big, device.width - 2), font=font_big, fill=255)
                draw.text((80, -3), "GDSENG", font=font_big, fill=255)
                draw.text((83, 50), APP_VERSION, font=font_big, fill=255)
                draw.text((0, -3), current_time, font=font_time, fill=255)
                if not st.cached_online:
                    draw.text((0, 38), "WiFi(옵션)", font=font_big, fill=255)
            else:
                battery_icon = select_battery_icon(voltage_percentage if voltage_percentage >= 0 else 0)
                draw.bitmap((90, -11), battery_icon, fill=255)
                perc_text = f"{voltage_percentage:.0f}%" if (voltage_percentage is not None and voltage_percentage >= 0) else "--%"
                draw.text((99, 1), perc_text, font=font_st, fill=255)
                draw.text((2, 1), current_time, font=font_time, fill=255)
                draw_wifi_bars(draw, 70, 3, wifi_level)

            if st.status_message:
                draw.rectangle(device.bounding_box, fill="black")
                draw.text(st.message_position, st.status_message, font=get_font(st.message_font_size), fill=255)
                return

            if wifi_running:
                draw.rectangle(device.bounding_box, fill="black")
                x = 2
                with st.wifi_stage_lock:
                    st_active = st.wifi_stage["active"]
                    st_p = st.wifi_stage["display_percent"]
                    st1 = st.wifi_stage["line1"]
                    st2 = st.wifi_stage["line2"]
                    sp = st.wifi_stage["spinner"]
                with st.ap_state_lock:
                    flash_until = st.ap_state["flash_until"]
                    ap_sp = st.ap_state["spinner"]
                dots = "." * sp
                dots2 = "." * ap_sp
                now = time.time()

                if st_active:
                    draw.text((x, 0), (st1 or "")[:16], font=get_font(13), fill=255)
                    line2 = (st2 or "")
                    if line2:
                        draw.text((x, 16), ellipsis_to_width(draw, (line2 + dots), get_font(11), device.width - 4), font=get_font(11), fill=255)
                    else:
                        draw.text((x, 16), ellipsis_to_width(draw, ("처리중" + dots), get_font(11), device.width - 4), font=get_font(11), fill=255)
                    x1, y1, x2, y2 = 8, 48, 120, 60
                    draw.rectangle([(x1, y1), (x2, y2)], outline=255, fill=0)
                    fill_w = int((x2 - x1) * (st_p / 100.0))
                    if fill_w > 0:
                        draw.rectangle([(x1, y1), (x1 + fill_w, y2)], fill=255)
                    draw.text((x, 32), "NEXT 길게: 취소", font=get_font(11), fill=255)
                else:
                    if now < flash_until:
                        draw.text((x, 0), ("연결됨!" + dots2)[:16], font=get_font(14), fill=255)
                    else:
                        draw.text((x, 0), "WiFi 설정 모드", font=get_font(14), fill=255)
                    draw.text((x, 18), f"AP: {AP_SSID}"[:18], font=get_font(12), fill=255)
                    draw.text((x, 34), f"PW: {AP_PASS}"[:18], font=get_font(12), fill=255)
                    draw.text((x, 50), f"IP: {AP_IP}:{PORTAL_PORT}"[:18], font=get_font(12), fill=255)
                return

            center_x = device.width // 2 + VISUAL_X_OFFSET
            if item_type in ("system", "wifi"):
                center_y = 33
                start_size = 17
            else:
                center_y = 42
                start_size = 21
            max_w = device.width - 4
            draw_center_text_autofit(draw, title, center_x, center_y, max_w, start_size, min_size=11)
    except Exception:
        return
    finally:
        st.display_lock.release()
