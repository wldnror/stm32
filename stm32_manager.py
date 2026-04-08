import os
import re
import subprocess
import time
from typing import Optional, Tuple

import app_state as st
from app_config import (
    OPENOCD_IFACE,
    OPENOCD_TARGET,
    STM32_RECENT_UNLOCK_TTL_SEC,
    STM32_FALLBACK_PROBE_TIMEOUT_SEC,
    STM32_DETECT_CACHE_TTL_SEC,
    STM32_DETECT_TIMEOUT_SEC,
    STM32_CONNECT_TIMEOUT_SEC,
    FLASH_KB_THRESHOLD,
    GENERAL_ROOT,
    TFTP_ROOT,
    GENERAL_DIRNAME,
    TFTP_DIRNAME,
)
from app_utils import run_capture, strip_order_prefix, canon_name


# =========================
# 내부 연결 체크 캐시
# =========================
_last_conn_check_ts = 0.0
_last_conn_ok = False
_last_conn_fail_ts = 0.0
_last_conn_proc_busy_until = 0.0


def kill_openocd():
    subprocess.run(
        ["sudo", "pkill", "-f", "openocd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_openocd_capture(commands, timeout):
    cmd = ["sudo", "openocd", "-f", OPENOCD_IFACE, "-f", OPENOCD_TARGET]
    for c in commands:
        cmd.extend(["-c", c])
    return run_capture(cmd, timeout=timeout)


def run_openocd_ok(commands, timeout):
    rc, _, _ = run_openocd_capture(commands, timeout=timeout)
    return rc == 0


def mark_recent_unlock(ok: bool):
    with st._unlock_cache_lock:
        st._unlock_cache["ts"] = time.time()
        st._unlock_cache["ok"] = bool(ok)


def has_recent_unlock(ttl=STM32_RECENT_UNLOCK_TTL_SEC) -> bool:
    with st._unlock_cache_lock:
        if not st._unlock_cache["ok"]:
            return False
        return (time.time() - (st._unlock_cache["ts"] or 0.0)) < ttl


def make_openocd_program_cmd(bin_path: str) -> str:
    return (
        "sudo openocd "
        f"-f {OPENOCD_IFACE} "
        f"-f {OPENOCD_TARGET} "
        f'-c "program {bin_path} verify reset exit 0x08000000"'
    )


def parse_openocd_flash_kb(text: str) -> Optional[int]:
    m = re.search(r"flash size\s*=\s*(\d+)\s*KiB", text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None

    m2 = re.search(r"flash size\s*=\s*(\d+)\s*kB", text, re.IGNORECASE)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return None

    return None


def detect_flash_kb_by_probe(timeout=STM32_FALLBACK_PROBE_TIMEOUT_SEC) -> Optional[int]:
    rc, out, err = run_openocd_capture(
        ["init", "reset halt", "flash probe 0", "shutdown"],
        timeout=timeout,
    )
    txt = (out or "") + "\n" + (err or "")
    return parse_openocd_flash_kb(txt)


def detect_stm32_flash_kb_with_unlock(timeout=STM32_DETECT_TIMEOUT_SEC) -> Tuple[Optional[int], Optional[int]]:
    now = time.time()

    with st._detect_cache_lock:
        if (
            st._detect_cache["flash_kb"] is not None
            and (now - st._detect_cache["ts"] < STM32_DETECT_CACHE_TTL_SEC)
        ):
            return st._detect_cache["dev_id"], st._detect_cache["flash_kb"]

    rc, out, err = run_openocd_capture(
        [
            "init",
            "reset halt",
            "stm32f1x unlock 0",
            "reset halt",
            "mdw 0xE0042000 1",
            "mdh 0x1FFFF7E0 1",
            "shutdown",
        ],
        timeout=timeout,
    )
    text = (out or "") + "\n" + (err or "")

    m_id = re.search(r"0xE0042000:\s+(0x[0-9a-fA-F]+)", text, re.IGNORECASE)
    m_fs = re.search(r"0x1FFFF7E0:\s+(0x[0-9a-fA-F]+)", text, re.IGNORECASE)

    if rc == 0:
        mark_recent_unlock(True)

    if (rc != 0) or (not m_id) or (not m_fs):
        kb = detect_flash_kb_by_probe()
        if kb is not None:
            with st._detect_cache_lock:
                st._detect_cache["ts"] = time.time()
                st._detect_cache["flash_kb"] = kb
                st._detect_cache["dev_id"] = None
            return None, kb
        return None, None

    try:
        id_val = int(m_id.group(1), 16)
        fs_val = int(m_fs.group(1), 16)
        dev_id = id_val % 4096
        flash_kb = fs_val

        with st._detect_cache_lock:
            st._detect_cache["ts"] = time.time()
            st._detect_cache["flash_kb"] = flash_kb
            st._detect_cache["dev_id"] = dev_id

        return dev_id, flash_kb

    except Exception:
        kb = detect_flash_kb_by_probe()
        if kb is not None:
            with st._detect_cache_lock:
                st._detect_cache["ts"] = time.time()
                st._detect_cache["flash_kb"] = kb
                st._detect_cache["dev_id"] = None
            return None, kb
        return None, None


def is_ir_variant(selected_bin_path: str) -> bool:
    fn = os.path.basename(selected_bin_path).upper()
    return fn.startswith("IR_") or fn.startswith("IR")


def key_from_filename(path_or_name: str) -> str:
    base = os.path.basename(path_or_name or "")
    m = re.match(r"^\s*\d+\.\s*([^.]+)\.bin\s*$", base, re.IGNORECASE)
    if m:
        return (m.group(1) or "").strip()

    stem = os.path.splitext(base)[0]
    m2 = re.match(r"^\s*\d+\.\s*(.+)\s*$", stem)
    if m2:
        return (m2.group(1) or "").strip()

    return strip_order_prefix(stem).strip()


def resolve_target_bin_by_gas(selected_bin_path: str, flash_kb: Optional[int]):
    if flash_kb is None:
        return selected_bin_path, "원본"

    want_tftp = flash_kb > FLASH_KB_THRESHOLD
    base_root = TFTP_ROOT if want_tftp else GENERAL_ROOT
    chosen_kind = "TFTP 376V" if want_tftp else "일반 360V"

    sp = os.path.abspath(selected_bin_path)
    gas_key = key_from_filename(sp)
    is_ir = is_ir_variant(sp)
    fname = os.path.basename(sp)
    m = re.match(r"^\d+\.(.*)$", fname)
    fname_no_order = (m.group(1) if m else fname)
    stem_base = strip_order_prefix(os.path.splitext(fname)[0]).strip()
    parent = os.path.basename(os.path.dirname(sp))
    parent_stripped = strip_order_prefix(parent).strip()

    candidates = []

    if want_tftp:
        if stem_base:
            candidates.append(os.path.join(base_root, f"{stem_base}.bin"))

        if is_ir:
            candidates += [
                os.path.join(base_root, f"IR_{gas_key}.bin"),
                os.path.join(base_root, f"IR{gas_key}.bin"),
                os.path.join(base_root, f"{gas_key}_IR.bin"),
                os.path.join(base_root, f"{gas_key}.bin"),
            ]

        candidates += [
            os.path.join(base_root, f"{gas_key}.bin"),
            os.path.join(base_root, fname_no_order),
            os.path.join(base_root, fname),
        ]

        if canon_name(parent) not in (canon_name(GENERAL_DIRNAME), canon_name(TFTP_DIRNAME)):
            candidates += [
                os.path.join(base_root, parent, fname),
                os.path.join(base_root, parent, fname_no_order),
            ]
            if parent_stripped and parent_stripped != parent:
                candidates += [
                    os.path.join(base_root, parent_stripped, fname),
                    os.path.join(base_root, parent_stripped, fname_no_order),
                ]
    else:
        candidates += [
            os.path.join(base_root, parent, fname),
            os.path.join(base_root, parent, fname_no_order),
            os.path.join(base_root, parent_stripped, fname),
            os.path.join(base_root, parent_stripped, fname_no_order),
            os.path.join(base_root, fname),
            os.path.join(base_root, fname_no_order),
            sp,
        ]

    for c in candidates:
        if c and os.path.isfile(c):
            return c, chosen_kind

    return selected_bin_path, "원본"


def check_stm32_connection():
    global _last_conn_check_ts, _last_conn_ok, _last_conn_fail_ts, _last_conn_proc_busy_until

    if st.is_command_executing:
        return False

    now = time.monotonic()

    # 최근 실패 직후에는 너무 빠르게 다시 두드리지 않음
    if (not _last_conn_ok) and ((now - _last_conn_fail_ts) < 0.20):
        with st.stm32_state_lock:
            st.connection_success = False
            st.connection_failed_since_last_success = True
        return False

    # 너무 짧은 간격의 중복 체크는 캐시 반환
    if (now - _last_conn_check_ts) < 0.10:
        with st.stm32_state_lock:
            st.connection_success = _last_conn_ok
            st.connection_failed_since_last_success = (not _last_conn_ok)
        return _last_conn_ok

    # openocd 실행 직후 겹침 방지
    if now < _last_conn_proc_busy_until:
        with st.stm32_state_lock:
            st.connection_success = _last_conn_ok
            st.connection_failed_since_last_success = (not _last_conn_ok)
        return _last_conn_ok

    _last_conn_check_ts = now
    _last_conn_proc_busy_until = now + max(0.35, STM32_CONNECT_TIMEOUT_SEC + 0.08)

    try:
        rc, out, err = run_openocd_capture(
            ["init", "targets", "exit"],
            timeout=STM32_CONNECT_TIMEOUT_SEC,
        )

        text = ((out or "") + "\n" + (err or "")).lower()
        ok = (rc == 0) and ("error" not in text)

        _last_conn_ok = ok
        if not ok:
            _last_conn_fail_ts = time.monotonic()

        with st.stm32_state_lock:
            if ok:
                st.connection_failed_since_last_success = False
                st.connection_success = True
            else:
                st.connection_failed_since_last_success = True
                st.connection_success = False

        return ok

    except Exception:
        _last_conn_ok = False
        _last_conn_fail_ts = time.monotonic()
        with st.stm32_state_lock:
            st.connection_failed_since_last_success = True
            st.connection_success = False
        return False
