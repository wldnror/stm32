import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import ast
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import Mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from process_utils import run_command
from network_utils import split_nmcli
import wifi_backend as wifi
import wifi_portal as portal


def result(out="", code=0, err=""):
    return subprocess.CompletedProcess([], code, out, err)


def load_functions(filename, names, namespace):
    """Exercise real worker code without initializing GPIO or starting the app."""
    tree = ast.parse((ROOT / filename).read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)
    return namespace


class ProcessTests(unittest.TestCase):
    def test_exit_status_and_output(self):
        r = run_command([sys.executable, "-c", "import sys;print('ok');sys.exit(7)"])
        self.assertEqual((r.returncode, r.stdout.strip()), (7, "ok"))

    def test_timeout_kills_grandchild(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = str(Path(directory) / "orphan")
            child = "import time,pathlib;time.sleep(.7);pathlib.Path(%r).touch()" % marker
            parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',%r]);time.sleep(5)" % child
            start = time.monotonic()
            r = run_command([sys.executable, "-c", parent], timeout=.2)
            self.assertEqual(r.returncode, 124)
            self.assertLess(time.monotonic() - start, 2)
            time.sleep(.8)
            self.assertFalse(Path(marker).exists(), "timed-out child is still running")

    def test_cancel_is_bounded(self):
        event = threading.Event()
        timer = threading.Timer(.15, event.set)
        timer.start()
        try:
            r = run_command([sys.executable, "-c", "import time;time.sleep(5)"], timeout=4, cancel=event.is_set)
            self.assertEqual(r.returncode, 125)
        finally:
            timer.cancel()


class WifiTests(unittest.TestCase):
    def setUp(self):
        wifi._scan_cache.update(ssids=[], at=0)

    def test_escaped_nmcli_fields(self):
        self.assertEqual(split_nmcli(r"Office\:2G\\Main:72"), ["Office:2G\\Main", "72"])

    def test_nm_130_type_and_uuid(self):
        with patch.object(wifi, "nmcli", return_value=result("uuid-one:802-11-wireless:wlan0\n")):
            self.assertEqual(wifi.active_profile(), "uuid-one")

    def test_saved_network_uses_uuid_not_filename(self):
        with patch.object(wifi, "nmcli", return_value=result("u1:Office\\:2G:802-11-wireless\nu2:gdseng-setup-ap:802-11-wireless\n")):
            self.assertEqual(wifi.list_saved_nm(), [{"nm_id": "u1", "ssid": "Office:2G", "display": "Office:2G"}])

    def test_scan_sorts_signal_and_preserves_ssid_spaces(self):
        with patch.object(wifi, "ap_active", return_value=False), patch.object(wifi, "nmcli", return_value=result(" Office\\:2G :40\nB:70\nB:20\n")) as call:
            self.assertEqual(wifi.scan_ssids(), ["B", " Office:2G "])
            self.assertEqual(wifi.scan_ssids(), ["B", " Office:2G "])
            self.assertEqual(call.call_count, 1)

    def test_no_radio_rescan_while_serving_ap(self):
        with patch.object(wifi, "ap_active", return_value=True), patch.object(wifi, "nmcli", return_value=result("B:70")) as call:
            wifi.scan_ssids(force=True)
            self.assertEqual(call.call_args.args[-2:], ("--rescan", "no"))

    def test_local_network_is_a_success_without_internet(self):
        with patch.object(wifi, "stop_ap"), patch.object(wifi, "nmcli", return_value=result()) as command, patch.object(wifi, "wifi_link", return_value={"connected": True}):
            self.assertTrue(wifi.connect_network(" office ", " secret123 "))
            args = next(call.args for call in command.call_args_list if "connect" in call.args)
            self.assertIn(" office ", args)
            self.assertIn(" secret123 ", args)

    def test_connection_without_ip_fails(self):
        with patch.object(wifi, "stop_ap"), patch.object(wifi, "nmcli", return_value=result()), patch.object(wifi, "wifi_link", return_value={"connected": False}):
            with self.assertRaises(RuntimeError):
                wifi.connect_network("office", "password123")

    def test_credentials_byte_limits(self):
        wifi.validate_credentials(" a ", " password ")
        wifi.validate_credentials("open", "")
        wifi.validate_credentials("office", "a" * 64)
        for ssid, password in (("가" * 11, "password123"), ("ok", "short"), ("ok", "z" * 64)):
            with self.assertRaises(ValueError):
                wifi.validate_credentials(ssid, password)

    def test_restore_uses_previous_uuid(self):
        with patch.object(wifi, "_previous_profile", "old-uuid"), patch.object(wifi, "stop_ap"), patch.object(wifi, "nmcli", return_value=result()) as call, patch.object(wifi, "wifi_link", return_value={"connected": True}):
            self.assertTrue(wifi.restore_connection())
            self.assertIn("old-uuid", call.call_args.args)
            self.assertIn("uuid", call.call_args.args)

    def test_cancel_does_not_expose_password_in_exception(self):
        with patch.object(wifi, "run_command", return_value=result(code=125)):
            with self.assertRaisesRegex(RuntimeError, "취소"):
                wifi.nmcli("password", "private-password")


class PortalTests(unittest.TestCase):
    def setUp(self):
        portal._set_state(running=True, requested=None, connecting=False, done=False, last_error="", last_ok="")
        self.client = portal.app.test_client()

    def test_hidden_network_and_password_whitespace(self):
        r = self.client.post("/connect", data={"ssid_manual": " hidden ", "psk": " password "})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(portal.pop_request(), {"ssid": " hidden ", "psk": " password ", "hidden": True})
        self.assertIsNone(portal.pop_request())

    def test_duplicate_submission_rejected(self):
        self.client.post("/connect", data={"ssid": "office", "psk": "password123"})
        self.assertEqual(self.client.post("/connect", data={"ssid": "office"}).status_code, 409)

    def test_controls_disabled_outside_setup(self):
        portal._set_state(running=False)
        self.assertEqual(self.client.post("/reset").status_code, 403)

    def test_lan_cannot_change_wifi(self):
        self.assertEqual(self.client.post("/reset", environ_base={"REMOTE_ADDR": "192.168.219.47"}).status_code, 403)

    def test_cross_origin_post_rejected(self):
        self.assertEqual(self.client.post("/reset", headers={"Origin": "https://other.example"}).status_code, 403)

    def test_api_does_not_ping_or_leak_password(self):
        portal._set_state(requested={"ssid": "office", "psk": "secret"}, done=True)
        with patch.object(portal, "has_internet", side_effect=AssertionError("API must be cached")):
            r = self.client.get("/api/state")
        self.assertTrue(r.json["connected"])
        self.assertNotIn("secret", r.get_data(as_text=True))

    def test_page_without_scan_still_offers_manual_input(self):
        with patch.object(portal, "scan_ssids", return_value=[]), patch.object(portal, "list_saved_nm", return_value=[]):
            r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="ssid_manual"', r.get_data(as_text=True))


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import ImageFont
        font_paths = [os.environ.get("TEST_FONT", ""), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/System/Library/Fonts/Supplemental/Arial.ttf"]
        font_path = None
        for candidate in font_paths:
            if not candidate:
                continue
            try:
                # Pillow also resolves font basenames in installed font folders.
                font_path = ImageFont.truetype(candidate, 12).path
                break
            except OSError:
                continue
        if not font_path:
            raise unittest.SkipTest("Set TEST_FONT to an installed TrueType font")
        class Device:
            width, height = 128, 64
            bounding_box = (0, 0, 127, 63)
            frames = []
            def display(self, image):
                self.frames.append(image.copy())
        cls.device = Device()
        modules = {}
        for name in ("luma", "luma.core", "luma.core.interface", "luma.core.interface.serial", "luma.oled", "luma.oled.device", "luma.core.render"):
            modules[name] = types.ModuleType(name)
        modules["luma.core.interface.serial"].i2c = lambda **kw: None
        modules["luma.oled.device"].sh1107 = lambda *a, **kw: cls.device
        modules["luma.core.render"].canvas = None
        original_font = ImageFont.truetype
        cls.patches = [patch.dict(sys.modules, modules),
                       patch("PIL.ImageFont.truetype", side_effect=lambda name, size: original_font(font_path, size)),
                       patch("app_config.BATTERY_ICON_PATH", str(ROOT / "img" / "bat.png"))]
        for item in cls.patches:
            item.start()
        cls.display = importlib.import_module("display_manager")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "patches"):
            for item in reversed(cls.patches):
                item.stop()

    def test_longest_ipv4_fits_and_is_not_truncated(self):
        from PIL import Image, ImageDraw
        image = Image.new("1", (128, 64))
        draw = ImageDraw.Draw(image)
        with patch.object(draw, "text", wraps=draw.text) as text:
            box = self.display.draw_system_header_footer(draw, "255.255.255.255", "15시 30분", True)
        self.assertLessEqual(box[2], 127)
        self.assertLessEqual(box[3], 64)
        self.assertEqual(text.call_args.args[1], "255.255.255.255")
        self.assertGreaterEqual(box[1], 51)

    def test_unbounded_title_is_clipped_at_minimum_font(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("1", (128, 64)))
        with patch.object(draw, "text", wraps=draw.text) as text:
            self.display.draw_center_text_autofit(draw, "x" * 200, 64, 32, 124, 17, 11)
        self.assertIn("…", text.call_args.args[1])

    def test_unchanged_frame_is_not_sent_again(self):
        self.display._last_frame = None
        self.device.frames.clear()
        for _ in range(2):
            with self.display.oled_canvas() as draw:
                draw.rectangle((1, 1, 5, 5), fill=255)
        self.assertEqual(len(self.device.frames), 1)

    def test_unchanged_state_skips_font_layout_work(self):
        import app_state as state
        state.commands = ["wifi_setup"]
        state.current_menu = {"dir": "root", "names": ["Wi-Fi"], "types": ["wifi"]}
        state.command_names, state.command_types = ["Wi-Fi"], ["wifi"]
        state.current_command_index = 0
        self.display._last_render_key = None
        self.display.update_oled_display()
        with patch.object(self.display, "draw_system_header_footer", wraps=self.display.draw_system_header_footer) as layout:
            self.display.update_oled_display()
            layout.assert_not_called()


class UpdateTests(unittest.TestCase):
    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_updater_fast_forward_dirty_and_failed_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, source, device = root / "remote.git", root / "source", root / "device"
            self.git("init", "--bare", str(remote))
            self.git("clone", str(remote), str(source))
            self.git("config", "user.email", "test@example.invalid", cwd=source)
            self.git("config", "user.name", "Test", cwd=source)
            (source / "tracked").write_text("one")
            self.git("add", ".", cwd=source)
            self.git("commit", "-m", "initial", cwd=source)
            self.git("push", "-u", "origin", "HEAD", cwd=source)
            self.git("clone", str(remote), str(device))
            shutil.copyfile(ROOT / "git-pull.sh", device / "git-pull.sh")
            (source / "tracked").write_text("two")
            self.git("commit", "-am", "second", cwd=source)
            self.git("push", cwd=source)
            run = lambda: subprocess.run(["bash", str(device / "git-pull.sh")], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            self.assertEqual((device / "tracked").read_text(), "two")
            (device / "tracked").write_text("local work")
            self.assertNotEqual(run().returncode, 0)
            self.assertEqual((device / "tracked").read_text(), "local work")
            self.git("restore", "tracked", cwd=device)
            self.git("remote", "set-url", "origin", str(root / "missing"), cwd=device)
            self.assertNotEqual(run().returncode, 0, "failed fetch must not be reported as success")


class WorkerTests(unittest.TestCase):
    def test_cleanup_on_cancel_timeout_exception_and_success(self):
        for outcome in ("cancel", False, RuntimeError("AP failed"), True):
            with self.subTest(outcome=outcome):
                state = types.SimpleNamespace(stop_threads=False, wifi_action_lock=threading.Lock(),
                    wifi_action_requested=True, wifi_action_running=False, wifi_cancel_requested=False,
                    status_message="", need_update=False)
                def finish(*args, **kwargs):
                    state.stop_threads = True
                    return True
                backend = types.SimpleNamespace(ap_active=Mock(return_value=False), stop_portal=Mock(), restore_connection=Mock(side_effect=finish))
                if outcome is True:
                    backend.stop_portal.side_effect = finish
                loop = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                env = dict(st=state, wifi_portal=backend, time=types.SimpleNamespace(sleep=lambda n: None),
                    portal_loop_until_connected_or_cancel=loop, logging=Mock(),
                    portal_set_state_safe=Mock(), wifi_stage_clear=Mock(), wifi_stage_set=Mock(),
                    text_stage=Mock(), clear_ui_override=Mock(), is_root_menu_view=lambda: False,
                    refresh_root_menu=Mock())
                load_functions("serve.py", {"wifi_worker_thread"}, env)["wifi_worker_thread"]()
                backend.stop_portal.assert_called_once()
                self.assertEqual(backend.restore_connection.call_count, 0 if outcome is True else 1)
                self.assertFalse(state.wifi_action_running)


class ScanTests(unittest.TestCase):
    def test_bounded_parallel_scan_includes_host_one_and_skips_self(self):
        state = types.SimpleNamespace(stop_threads=False, scan_lock=threading.Lock(), scan_active=True,
            scan_generation=1, scan_base_prefix="192.168.9.", scan_done=False,
            scan_ips=[], scan_infos={}, scan_selected_idx=0, scan_menu_dirty=False, need_update=False)
        probes, simultaneous, peak = [], [0], [0]
        guard = threading.Lock()
        def probe(ip, timeout):
            with guard:
                probes.append(ip)
                simultaneous[0] += 1
                peak[0] = max(peak[0], simultaneous[0])
            time.sleep(.002)
            with guard:
                simultaneous[0] -= 1
            return ip.endswith(".1")
        def loop_sleep(seconds):
            if state.scan_done:
                state.stop_threads = True
        env = dict(st=state, time=types.SimpleNamespace(sleep=loop_sleep, monotonic=time.monotonic),
            ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
            get_ip_address=lambda: "192.168.9.133", quick_modbus_probe=probe,
            read_device_info_fast=lambda ip: "gas:0")
        load_functions("modbus_manager.py", {"modbus_scan_loop"}, env)["modbus_scan_loop"]()
        self.assertEqual(len(probes), 253)
        self.assertNotIn("192.168.9.133", probes)
        self.assertEqual(state.scan_ips, ["192.168.9.1"])
        self.assertLessEqual(peak[0], 8)
        self.assertGreater(peak[0], 1)


class STM32Tests(unittest.TestCase):
    def test_program_argv_quotes_path_without_a_shell(self):
        env = dict(OPENOCD_IFACE="iface", OPENOCD_TARGET="target")
        fn = load_functions("stm32_manager.py", {"make_openocd_program_cmd"}, env)["make_openocd_program_cmd"]
        argv = fn('/tmp/firmware $value [cmd] "one".bin')
        self.assertIsInstance(argv, list)
        self.assertIn('\\$value', argv[-1])
        self.assertIn('\\[cmd\\]', argv[-1])
        self.assertIn('\\"one\\"', argv[-1])


class TFTPTests(unittest.TestCase):
    def test_failed_copy_preserves_last_good_firmware(self):
        env = dict(os=os, uuid=uuid, shutil=shutil, run_quiet=lambda *a, **kw: False)
        publish = load_functions("modbus_manager.py", {"publish_tftp_firmware"}, env)["publish_tftp_firmware"]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "firmware.bin"
            destination.write_bytes(b"last good")
            with self.assertRaises(FileNotFoundError):
                publish(str(Path(directory) / "missing.bin"), str(destination))
            self.assertEqual(destination.read_bytes(), b"last good")
            source = Path(directory) / "new.bin"
            source.write_bytes(b"new firmware")
            publish(str(source), str(destination))
            self.assertEqual(destination.read_bytes(), b"new firmware")
            self.assertEqual(list(Path(directory).glob("*.tmp-*")), [])


class MenuTests(unittest.TestCase):
    def test_blocked_public_ping_does_not_hide_updates_or_local_extraction(self):
        import menu_manager as menu
        import app_state as state
        with tempfile.TemporaryDirectory() as directory, patch.object(menu, "GENERAL_ROOT", directory), patch.object(menu, "TFTP_ROOT", directory), patch.object(menu, "is_fw_extract_mode", return_value=True), patch.object(state, "cached_online", False), patch.object(state, "cached_wifi_connected", True), patch.object(state, "git_has_update_cached", True):
            built = menu.build_menu_for_dir(directory, is_root=True)
        self.assertIn("system", built["types"])
        self.assertIn("script", built["types"])


if __name__ == "__main__":
    unittest.main()
