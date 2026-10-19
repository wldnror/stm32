"""Regression coverage for the panel's setup/held-button/cancel lifecycle."""
import ast
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wifi_backend as wifi


def functions(names, env):
    tree = ast.parse((ROOT / 'serve.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'serve.py', 'exec'), env)
    return env


class SetupLifecycleTests(unittest.TestCase):
    def test_transition_does_not_rearm_a_held_set_button(self):
        state = SimpleNamespace(execute_is_down=True, next_is_down=True,
            execute_long_handled=True, next_long_handled=True)
        functions({'enter_ui_transition'}, dict(st=state,
            button_state_lock=threading.Lock(), now_mono=lambda: 100))['enter_ui_transition']()
        self.assertTrue(state.execute_long_handled)
        self.assertTrue(state.next_long_handled)

    def test_one_held_press_cannot_queue_setup_again_after_cancel(self):
        state = SimpleNamespace(wifi_action_lock=threading.Lock(),
            wifi_action_requested=False, wifi_action_running=False, wifi_cancel_requested=False)
        env = functions({'request_wifi_setup', 'request_wifi_cancel'}, dict(st=state))
        self.assertTrue(env['request_wifi_setup']())
        for _ in range(20):
            self.assertFalse(env['request_wifi_setup']())
        state.wifi_action_running = True
        env['request_wifi_cancel']()
        self.assertFalse(state.wifi_action_requested)
        self.assertTrue(state.wifi_cancel_requested)
        self.assertFalse(env['request_wifi_setup']())
        self.assertTrue(state.wifi_cancel_requested)

    def test_ready_ap_replaces_preparing_screen_with_credentials(self):
        state = SimpleNamespace(stop_threads=False)
        stage = {'active': True}
        def clear():
            stage['active'] = False
        def waiting():
            self.assertFalse(stage['active'], 'credentials are hidden by stale progress')
            state.stop_threads = True
        portal = SimpleNamespace(start_ap=Mock(), run_portal=Mock(),
            pop_request=Mock(side_effect=waiting))
        env = dict(st=state, time=time, portal_module=lambda: portal,
            portal_set_state_safe=Mock(), wifi_cancelled=lambda: False,
            wifi_stage_set=Mock(), wifi_stage_clear=clear)
        self.assertFalse(functions({'portal_loop_until_connected_or_cancel'}, env)
                         ['portal_loop_until_connected_or_cancel']())
        portal.run_portal.assert_called_once_with(block=False)

    def test_cancel_during_preparation_never_starts_http_or_wait_loop(self):
        state = SimpleNamespace(stop_threads=False)
        portal = SimpleNamespace(start_ap=Mock(), run_portal=Mock())
        env = dict(st=state, portal_module=lambda: portal,
            portal_set_state_safe=Mock(), wifi_cancelled=lambda: True,
            wifi_stage_set=Mock())
        self.assertEqual(functions({'portal_loop_until_connected_or_cancel'}, env)
                         ['portal_loop_until_connected_or_cancel'](), 'cancel')
        portal.run_portal.assert_not_called()

    def test_ap_preparation_passes_cancellation_through_every_command(self):
        event = threading.Event()
        calls = []
        def command(*args, **kwargs):
            calls.append((args, kwargs))
            callback = kwargs['cancel']
            self.assertFalse(callback())
            event.set()
            self.assertTrue(callback())
            return SimpleNamespace(returncode=1)
        with patch.object(wifi, 'ap_active', return_value=False), \
             patch.object(wifi, 'active_profile', return_value=None), \
             patch.object(wifi, 'scan_ssids'), patch.object(wifi, 'nmcli', side_effect=command):
            with self.assertRaisesRegex(RuntimeError, '취소'):
                wifi.start_ap(cancel=event.is_set)
        self.assertEqual(len(calls), 1, 'cancelled preparation continued configuring the AP')

    def test_stop_also_cancels_an_inflight_networkmanager_activation(self):
        with patch.object(wifi, 'ap_active', return_value=False), patch.object(wifi, 'nmcli') as cmd:
            wifi.stop_ap()
        self.assertIn('down', cmd.call_args.args)


if __name__ == '__main__':
    unittest.main()
