import ast
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import boot_display as display
import boot_status as status
from button_input import ButtonInputs


class BootFrameTests(unittest.TestCase):
    def test_packing_matches_installed_luma_rotation_and_page_order(self):
        frame = bytearray(128 * 64)
        for y in range(64):
            for x in range(128):
                frame[y * 128 + x] = int((x * 7 + y * 3) % 17 < 5)
        logical = Image.frombytes("L", (128, 64), bytes(frame))
        physical = logical.rotate(-90, expand=True)
        expected = []
        for page in range(16):
            expected.append(bytearray(sum((physical.getpixel((x, page * 8 + bit)) & 1) << bit
                                          for bit in range(8)) for x in range(64)))
        self.assertEqual(display.pack_pages(frame), expected)

    def test_controller_packets_address_only_expected_pages(self):
        with patch.object(display.os, "open", return_value=23), patch.object(display.fcntl, "ioctl") as ioctl, patch.object(display.os, "write") as write, patch.object(display.os, "close") as close:
            oled = display.SH1107()
            oled.display(display.render(30, "APP LOADING", 5, 1))
            oled.close()
        ioctl.assert_called_once_with(23, 0x0703, 0x3c)
        data = [call.args[1] for call in write.call_args_list if call.args[1][0] == 0x40]
        self.assertEqual(len(data), 16)
        self.assertTrue(all(len(packet) == 65 for packet in data))
        self.assertEqual(write.call_args.args[1], bytes((0, 0xaf)))
        close.assert_called_once_with(23)

    def test_animation_changes_while_progress_stays_at_real_stage(self):
        first = display.render(30, "APP LOADING", 3, 0)
        later = display.render(30, "APP LOADING", 3, 1)
        self.assertNotEqual(first, later)
        self.assertEqual(first[44 * 128:52 * 128], later[44 * 128:52 * 128])
        self.assertEqual(len(first), 128 * 64)
        self.assertTrue(set(first) <= {0, 1})

    def test_early_modules_do_not_import_the_heavy_application(self):
        forbidden = {"PIL", "luma", "flask", "RPi", "app_config", "wifi_portal"}
        for filename in ("boot_display.py", "boot_status.py", "boot_screen.py"):
            tree = ast.parse((ROOT / filename).read_text())
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(item.name.split(".")[0] for item in node.names)
                if isinstance(node, ast.ImportFrom):
                    imports.add(node.module.split(".")[0])
            self.assertFalse(imports & forbidden, filename)

    def test_configuration_does_not_eagerly_import_the_web_server(self):
        result = subprocess.run([sys.executable, "-c", "import app_config,sys; assert 'flask' not in sys.modules"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class BootStatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Path(self.directory.name)
        self.path_patch = patch.object(status, "RUNTIME", self.runtime)
        self.path_patch.start()
        self.notify_patch = patch.dict(os.environ, {"NOTIFY_SOCKET": ""})
        self.notify_patch.start()

    def tearDown(self):
        if status._display_lock is not None:
            os.close(status._display_lock)
            status._display_lock = None
        self.path_patch.stop()
        self.notify_patch.stop()
        self.directory.cleanup()

    def test_stage_is_atomic_and_has_the_live_main_process(self):
        status.publish(70, "MENU LOADING")
        state = status.read_state()
        self.assertEqual(state["progress"], 70)
        self.assertEqual(state["pid"], os.getpid())
        self.assertFalse(state["handoff"])
        self.assertEqual(list(self.runtime.glob(".state-*")), [])

    def test_bad_or_stale_state_does_not_crash_the_boot_screen(self):
        file = self.runtime / "state.json"
        file.write_text("{")
        self.assertEqual(status.read_state(), {})
        file.write_text(json.dumps({"pid": 2147483647, "handoff": True}))
        self.assertEqual(status.read_state(), {})

    def test_legacy_installation_without_runtime_can_still_start(self):
        with patch.object(status, "RUNTIME", self.runtime / "not-installed"):
            status.publish(30, "APP LOADING")
            status.claim_display()
        self.assertIsNone(status._display_lock)

    def test_first_frame_timestamp_survives_early_journal_startup(self):
        with patch.object(status.time, "monotonic", return_value=12.5):
            status.record_first_frame()
        file = self.runtime / "first-frame.json"
        self.assertEqual(json.loads(file.read_text())["first_frame_at"], 12.5)
        self.assertEqual(file.stat().st_mode & 0o777, 0o644)

    def test_handoff_releases_the_splash_before_main_takes_the_bus(self):
        script = '''
import os,time,fcntl
from pathlib import Path
import boot_status as status
fd=os.open(status.RUNTIME/'display.lock',os.O_CREAT|os.O_RDWR,0o664)
fcntl.flock(fd,fcntl.LOCK_EX)
(status.RUNTIME/'locked').touch()
while not status.read_state().get('handoff'):
 time.sleep(.01)
os.close(fd)
'''
        env = dict(os.environ, PYTHONPATH=str(ROOT), STM32_BOOT_RUNTIME=str(self.runtime))
        child = subprocess.Popen([sys.executable, "-c", script], env=env)
        try:
            deadline = time.monotonic() + 3
            while not (self.runtime / "locked").exists():
                if time.monotonic() > deadline:
                    self.fail("splash did not acquire lock")
                time.sleep(.01)
            status.claim_display()
            child.wait(timeout=2)
            self.assertIsNotNone(status._display_lock)
            self.assertTrue(status.read_state()["handoff"])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_busy_bus_times_out_instead_of_two_writers(self):
        descriptor = os.open(self.runtime / "display.lock", os.O_CREAT | os.O_RDWR, 0o664)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            with self.assertRaises(RuntimeError):
                status.claim_display(timeout=.05)
        finally:
            os.close(descriptor)

    def test_ready_notification_is_sent_after_real_menu_completion(self):
        address = str(self.runtime / "notify")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(address)
            server.settimeout(1)
            with patch.dict(os.environ, {"NOTIFY_SOCKET": address}), patch("builtins.print"):
                status.menu_ready()
            messages = [server.recv(1024), server.recv(1024)]
        self.assertTrue(any(b"READY=1" in message for message in messages))
        self.assertEqual(status.read_state()["progress"], 100)


class EarlyGPIOTests(unittest.TestCase):
    def load(self, gpio):
        tree = ast.parse((ROOT / "serve.py").read_text())
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "initialize_gpio")
        env = dict(GPIO=gpio, ButtonInputs=ButtonInputs, BUTTON_PIN_NEXT=27, BUTTON_PIN_EXECUTE=17,
                   LED_SUCCESS=24, LED_ERROR=25, LED_ERROR1=23,
                   button_next_edge=Mock(), button_execute_edge=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), "serve.py", "exec"), env)
        return env["initialize_gpio"]

    def test_startup_does_not_require_sysfs_edge_permissions(self):
        gpio = Mock(IN=1, OUT=2, PUD_UP=3, HIGH=1)
        gpio.add_event_detect.side_effect = RuntimeError("udev still changing ownership")
        self.assertIsInstance(self.load(gpio)(), ButtonInputs)
        gpio.add_event_detect.assert_not_called()
        self.assertEqual(gpio.setup.call_count, 5)

    def test_bounce_and_held_press_produce_one_press_and_one_release(self):
        gpio = Mock(HIGH=1, input=Mock(return_value=1))
        callback = Mock()
        inputs = ButtonInputs(gpio, {17: callback})
        for now, value in [(1, 0), (1.01, 1), (1.02, 0), (1.07, 0),
                           (2, 0), (3, 1), (3.01, 0), (3.02, 1), (3.07, 1)]:
            gpio.input.return_value = value
            inputs.poll(now)
        self.assertEqual([call.args for call in callback.call_args_list], [(17, 0), (17, 1)])

    def test_button_held_at_startup_is_detected_and_buttons_are_independent(self):
        levels = {17: 0, 27: 1}
        gpio = Mock(HIGH=1, input=Mock(side_effect=lambda pin: levels[pin]))
        execute, next_button = Mock(), Mock()
        inputs = ButtonInputs(gpio, {17: execute, 27: next_button})
        inputs.poll(1)
        inputs.poll(1.05)
        execute.assert_called_once_with(17, 0)
        next_button.assert_not_called()
        levels[27] = 0
        inputs.poll(2)
        inputs.poll(2.05)
        next_button.assert_called_once_with(27, 0)
        self.assertEqual(execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
