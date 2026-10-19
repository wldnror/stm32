import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def functions(names, env):
    tree = ast.parse((ROOT / "serve.py").read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "serve.py", "exec"), env)
    return env


class DisplaySchedulingTests(unittest.TestCase):
    def run_frame(self, dirty, during_draw=False, wifi=False):
        state = SimpleNamespace(stop_threads=False, wifi_action_lock=threading.Lock(),
            wifi_action_running=wifi, next_is_down=False, execute_is_down=False,
            scan_menu_dirty=False, need_update=dirty, last_oled_update_time=100.0,
            is_executing=False)
        def draw():
            if during_draw:
                state.need_update = True
        render = Mock(side_effect=draw)
        def finish(_):
            state.stop_threads = True
        env = dict(st=state, time=SimpleNamespace(time=lambda: 100.01, sleep=finish),
            button_state_lock=threading.Lock(), oled_refresh_lock=threading.Lock(),
            wifi_stage_tick=Mock(), update_oled_display=render,
            ap_client_tick=Mock(side_effect=AssertionError("Wi-Fi query on renderer")))
        functions({"realtime_update_display"}, env)["realtime_update_display"]()
        return state, render

    def test_dirty_menu_does_not_wait_for_periodic_refresh(self):
        for wifi in (False, True):
            with self.subTest(wifi=wifi):
                _, draw = self.run_frame(True, wifi=wifi)
                draw.assert_called_once()

    def test_request_during_draw_is_not_lost(self):
        state, _ = self.run_frame(True, during_draw=True)
        self.assertTrue(state.need_update)

    def test_idle_frame_keeps_the_periodic_limit(self):
        _, draw = self.run_frame(False)
        draw.assert_not_called()


class MenuPublicationTests(unittest.TestCase):
    def menu(self, names, directory=None):
        import menu_manager as menu
        return dict(dir=directory or menu.FIRMWARE_DIR, commands=[None] * len(names),
                    names=names, types=["bin"] * len(names), extras=names)

    def state(self):
        old = self.menu(["A", "B"])
        return SimpleNamespace(current_menu=old, commands=old["commands"],
            command_names=old["names"], command_types=old["types"], menu_extras=old["extras"],
            current_command_index=0, root_menu_pending_lock=threading.Lock(),
            root_menu_pending=None, need_update=False)

    def test_button_selection_during_background_build_is_preserved(self):
        import menu_manager as menu
        state = self.state()
        def build(*args, **kwargs):
            state.current_command_index = 1
            return self.menu(["A", "B", "C"])
        with patch.object(menu, "st", state), patch.object(menu, "build_menu_for_dir", side_effect=build):
            menu.queue_root_menu_refresh()
            self.assertEqual(state.command_names, ["A", "B"])
            menu.apply_pending_root_menu()
            self.assertEqual(state.command_names[state.current_command_index], "B")
            self.assertTrue(state.need_update)

    def test_background_result_cannot_pull_the_user_out_of_a_folder(self):
        import menu_manager as menu
        state = self.state()
        folder = self.menu(["child"], "/firmware/folder")
        with patch.object(menu, "st", state), patch.object(menu, "build_menu_for_dir", return_value=self.menu(["A", "B", "C"])):
            menu.queue_root_menu_refresh()
            menu.install_root_menu(folder)
            menu.apply_pending_root_menu()
            self.assertIs(state.current_menu, folder)
            self.assertIsNone(state.root_menu_pending)

    def test_removing_current_item_leaves_a_valid_selection(self):
        import menu_manager as menu
        state = self.state()
        state.current_command_index = 1
        with patch.object(menu, "st", state):
            menu.install_root_menu(self.menu(["A"]))
        self.assertEqual(state.current_command_index, 0)


class BackgroundNetworkTests(unittest.TestCase):
    def test_link_update_does_not_wait_for_public_ping_or_publish_menu(self):
        state = SimpleNamespace(stop_threads=False, cached_wifi_connected=False)
        def finish(_):
            state.stop_threads = True
        queue = Mock()
        env = dict(st=state, wifi_link=lambda: dict(connected=True, ip="192.168.1.2", level=3),
            time=SimpleNamespace(sleep=finish), logging=Mock(), is_root_menu_view=lambda: True,
            queue_root_menu_refresh=queue,
            internet_available=Mock(side_effect=AssertionError("unnecessary public ping")),
            refresh_root_menu=Mock(side_effect=AssertionError("background menu publication")))
        functions({"net_poll_thread"}, env)["net_poll_thread"]()
        queue.assert_called_once()
        self.assertEqual(state.cached_ip, "192.168.1.2")
        self.assertTrue(state.need_update)

    def test_update_check_yields_cpu_to_foreground_input(self):
        import os
        capture = Mock(return_value=(0, "new-hash\trefs/heads/master\n", ""))
        env = dict(os=os, GIT_REPO_DIR=str(ROOT), git_has_origin=lambda: True,
            git_branch_name=lambda: "master", git_head_hash=lambda: "old-hash", run_capture=capture)
        check = functions({"git_has_remote_updates_light"}, env)["git_has_remote_updates_light"]
        self.assertTrue(check())
        self.assertEqual(capture.call_args.args[0][:4], ["nice", "-n", "10", "git"])


class OLEDPageTests(unittest.TestCase):
    def test_a_small_change_only_transmits_its_controller_page(self):
        from PIL import Image
        from oled_frames import display_rotated_frame
        device = SimpleNamespace(command=Mock(), data=Mock())
        image = Image.new("1", (128, 64))
        display_rotated_frame(device, image)
        self.assertEqual(device.data.call_count, 16)
        device.command.reset_mock()
        device.data.reset_mock()
        display_rotated_frame(device, image)
        device.data.assert_not_called()
        image.putpixel((33, 20), 1)
        display_rotated_frame(device, image)
        device.command.assert_called_once_with(0x12, 0x0b, 0xb4)
        device.data.assert_called_once_with([2])

    def test_a_failed_transfer_is_retried_without_advancing_the_cache(self):
        from PIL import Image
        from oled_frames import display_rotated_frame
        device = SimpleNamespace(command=Mock(), data=Mock())
        image = Image.new("1", (128, 64))
        display_rotated_frame(device, image)
        old = device._cached_pages
        image.putpixel((33, 20), 1)
        device.data.side_effect = OSError("transfer interrupted")
        with self.assertRaises(OSError):
            display_rotated_frame(device, image)
        self.assertIs(device._cached_pages, old)
        device.data.side_effect = None
        device.data.reset_mock()
        display_rotated_frame(device, image)
        device.data.assert_called_once()

    def test_partial_writes_reconstruct_the_entire_rotated_image(self):
        import random
        from PIL import Image
        from oled_frames import display_rotated_frame
        class Controller:
            def __init__(self):
                self.ram = [bytearray(64) for _ in range(16)]
            def command(self, high, low, page):
                self.column = ((high & 0x0f) << 4) | low
                self.page = page & 0x0f
            def data(self, values):
                assert self.column + len(values) <= 64
                self.ram[self.page][self.column:self.column + len(values)] = bytes(values)
        controller = Controller()
        image = Image.new("1", (128, 64))
        rng = random.Random(19)
        for _ in range(20):
            for _ in range(12):
                image.putpixel((rng.randrange(128), rng.randrange(64)), rng.randrange(2))
            display_rotated_frame(controller, image)
            physical = image.rotate(-90, expand=True)
            expected = [bytearray(sum((physical.getpixel((x, page * 8 + bit)) & 1) << bit
                        for bit in range(8)) for x in range(64)) for page in range(16)]
            self.assertEqual(controller.ram, expected)


if __name__ == "__main__":
    unittest.main()
