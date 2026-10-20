"""Exercise the actual watcher loop with fake hardware and status input."""
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('monitor-power.sh')

class MonitorTests(unittest.TestCase):
    def run_loop(self, statuses, fail=False, hour="20", fail_light=False):
        source = SCRIPT.read_text().replace(
            '"$TUYA_CEILING_PYTHON" "$TUYA_CEILING_SCRIPT" &', 'echo ceiling-popup')
        head, loop = source.split('\nwhile true; do\n', 1)
        overrides = '''
i=0
read_online() { case "$i" in
'''
        for i, value in enumerate(statuses):
            overrides += f'{i}) '+ ('return 1' if value is None else f'echo {value}') + ';;\n'
        overrides += 'esac; }\ndate() { if [ \"$1\" = +%H ]; then echo '+hour+'; else command date \"$@\"; fi; }\n'
        overrides += '''
set_display_power() { echo "display:$1"; }
set_active_fallback() { :; }
pause_media_players() { echo pause; }
mute_audio_sinks() { echo mute; }
restore_audio_sinks() { echo restore; }
gsettings() { :; }
timeout() {
  if [ "$2" = "$TUYA_CONTROL" ]; then echo "tuya:$3:$4"; fi
  if [ "$2" = "$LIGHT_CONTROL" ]; then echo "light:$3:$4"; ''' + ('return 1;' if fail_light else '') + ''' fi
  ''' + ('return 1' if fail else 'return 0') + '''
}
sleep() {
  if [ "$1" = 7 ]; then echo boot-wait; return; fi
  i=$((i+1))
  if [ "$i" -ge '''+str(len(statuses))+''' ]; then exit 0; fi
}
'''
        return subprocess.run(['bash'], input=head+overrides+'\nwhile true; do\n'+loop,
                              text=True, capture_output=True, timeout=5, check=True).stdout

    def test_offline_then_online_order_and_no_repeated_switch(self):
        out = self.run_loop([0, 0, 1, 1])
        self.assertEqual(out.count('tuya:monitor-plug:off'), 1)
        self.assertEqual(out.count('tuya:monitor-plug:on'), 1)
        self.assertEqual(out.count('light:off:bedroom-light'), 1)
        self.assertNotIn('light:on:bedroom-light', out)
        self.assertLess(out.index('display:3'), out.index('mute'))
        self.assertLess(out.index('mute'), out.index('tuya:monitor-plug:off'))
        self.assertLess(out.index('tuya:monitor-plug:on'), out.index('boot-wait'))
        self.assertLess(out.index('boot-wait'), out.index('display:0'))
        self.assertLess(out.index('display:0'), out.index('restore'))

    def test_failed_switch_retries_and_loop_continues(self):
        out = self.run_loop([1, 1], fail=True)
        self.assertEqual(out.count('tuya:monitor-plug:on'), 2)
        self.assertEqual(out.count('light:off:bedroom-light'), 2)
        self.assertEqual(out.count('restore'), 2)
        self.assertNotIn('boot-wait', out)
        self.assertEqual(out.count('ceiling-popup'), 1)

    def test_unknown_status_never_switches(self):
        out = self.run_loop([None])
        self.assertNotIn('tuya:', out)
        self.assertNotIn('light:', out)
        self.assertNotIn('display:', out)

    def test_online_at_start_restores_plug(self):
        out = self.run_loop([1])
        self.assertIn('tuya:monitor-plug:on', out)
        self.assertIn('light:off:bedroom-light', out)

    def test_light_is_checked_again_after_next_offline_period(self):
        out = self.run_loop([1, 0, 1])
        self.assertEqual(out.count('light:off:bedroom-light'), 2)

    def test_ceiling_popup_after_startup_once_per_online_period(self):
        out = self.run_loop([0, 1, 1, 0, 1, 1])
        self.assertEqual(out.count('ceiling-popup'), 2)
        self.assertLess(out.index('restore'), out.index('ceiling-popup'))
        self.assertLess(out.index('light:off:bedroom-light'), out.index('ceiling-popup'))

    def test_ceiling_popup_runs_when_bedroom_light_fails(self):
        out = self.run_loop([1, 1], fail_light=True)
        self.assertEqual(out.count('light:off:bedroom-light'), 2)
        self.assertEqual(out.count('ceiling-popup'), 1)

    def test_ceiling_popup_runs_when_monitor_plug_fails(self):
        out = self.run_loop([1, 1], fail=True)
        self.assertEqual(out.count('ceiling-popup'), 1)

    def test_evening_shutdown_turns_light_on_once(self):
        out = self.run_loop([1, 0, 0], hour="21")
        self.assertEqual(out.count('light:on:bedroom-light'), 1)

    def test_daytime_shutdown_does_not_turn_light_on(self):
        out = self.run_loop([1, 0], hour="20")
        self.assertNotIn('light:on:bedroom-light', out)
        self.assertIn('light:on:ceiling-upper', out)

    def test_offline_start_at_night_does_not_turn_light_on(self):
        self.assertNotIn('light:on:bedroom-light', self.run_loop([0, 0], hour="23"))

    def test_midnight_shutdown_turns_light_on(self):
        self.assertIn('light:on:bedroom-light', self.run_loop([1, 0], hour="00"))

    def test_early_morning_shutdown_turns_light_on(self):
        self.assertIn('light:on:bedroom-light', self.run_loop([1, 0], hour="03"))

    def test_six_am_shutdown_turns_upper_ceiling_light_on(self):
        out = self.run_loop([1, 0], hour="06")
        self.assertNotIn('light:on:bedroom-light', out)
        self.assertIn('light:on:ceiling-upper', out)

    def test_five_am_shutdown_turns_bedroom_light_on(self):
        self.assertIn('light:on:bedroom-light', self.run_loop([1, 0], hour="05"))

    def test_evening_failure_retries(self):
        out = self.run_loop([1, 0, 0], fail=True, hour="23")
        self.assertEqual(out.count('light:on:bedroom-light'), 2)

    def test_unknown_status_does_not_trigger_shutdown(self):
        out = self.run_loop([1, None, 0, 0], hour="21")
        self.assertEqual(out.count('light:on:bedroom-light'), 1)

    def test_parse_nested_status_and_reject_invalid(self):
        head = SCRIPT.read_text().split('\nwhile true; do\n', 1)[0]
        for payload, expected in [(' {"main_pc":{"online":true}}', '1'),
                                  ('{"main_pc":{"online":false}}', '0'),
                                  ('{"main_pc":{"online":"false"}}', None),
                                  ('invalid', None)]:
            result = subprocess.run(['bash'], input=head+'\ncurl() { printf "%s" '\
                                    +repr(payload)+'; }\nread_online\n', text=True, capture_output=True)
            if expected is None:
                self.assertNotEqual(result.returncode, 0)
            else:
                self.assertEqual(result.stdout.strip(), expected)

if __name__ == '__main__':
    unittest.main()
