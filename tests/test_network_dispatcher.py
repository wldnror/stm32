"""Run the actual dispatcher shell flow with fake network command adapters."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AnnouncementTests(unittest.TestCase):
    def run_hook(self, iface='wlan0', action='up', profile='company', addresses=None):
        if addresses is None:
            addresses = '2: wlan0    inet 192.168.219.133/24 brd 192.168.219.255 scope global dynamic wlan0\n'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            calls = directory / 'calls.jsonl'
            ip = directory / 'ip'
            ip.write_text('#!' + sys.executable + '\nimport sys\nsys.stdout.write(' + repr(addresses) + ')\n')
            busybox = directory / 'busybox'
            busybox.write_text('#!' + sys.executable + '\nimport json,os,sys\nwith open(os.environ["CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
            for executable in (ip, busybox):
                executable.chmod(0o755)
            script = directory / 'hook'
            text = (ROOT / 'deploy/stm32-announce-ip').read_text()
            script.write_text(text.replace('/sbin/ip', shlex.quote(str(ip)))
                                  .replace('/bin/busybox', shlex.quote(str(busybox))))
            env = dict(os.environ, CALLS=str(calls), CONNECTION_ID=profile,
                       DHCP4_IP_ADDRESS='203.0.113.99')
            completed = subprocess.run(['/bin/sh', str(script), iface, action], env=env,
                                       capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def test_up_and_lease_change_announce_current_address_with_finite_limits(self):
        for action in ('up', 'dhcp4-change'):
            with self.subTest(action=action):
                self.assertEqual(self.run_hook(action=action),
                    [['arping','-q','-U','-I','wlan0','-c','2','-w','2','192.168.219.133']])

    def test_other_interfaces_events_and_setup_ap_never_announce(self):
        for kwargs in ({'iface':'eth0'}, {'action':'down'}, {'action':'pre-up'},
                       {'profile':'gdseng-setup-ap'}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.run_hook(**kwargs), [])

    def test_no_global_address_does_not_fall_back_to_stale_environment(self):
        self.assertEqual(self.run_hook(addresses=''), [])

    def test_announces_one_owned_address_without_using_secondary_or_stale_ip(self):
        addresses = ('2: wlan0 inet 192.168.219.133/24 scope global wlan0\n'
                     '2: wlan0 inet 192.168.219.134/24 scope global secondary wlan0\n')
        self.assertEqual(len(self.run_hook(addresses=addresses)), 1)


if __name__ == '__main__':
    unittest.main()
