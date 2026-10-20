import unittest
from unittest.mock import Mock, patch
import ceiling_prompt as app


def make_device(status_sequence):
    """Create a mock device whose .status() returns items from status_sequence."""
    device = Mock()
    device.status.side_effect = status_sequence
    device.set_multiple_values.return_value = {}
    return device


def dps(sw1, sw2):
    """Build a status response dict for switch DP 1 and 2."""
    return {'dps': {'1': sw1, '2': sw2}}


class CeilingTests(unittest.TestCase):
    def test_already_off_no_popup(self):
        device = make_device([dps(False, False)])
        prompt = Mock()
        app.run(device, lambda: True, prompt)
        prompt.assert_not_called()
        device.set_multiple_values.assert_not_called()

    def test_keep_on_sends_nothing(self):
        device = make_device([dps(True, True)])
        app.run(device, lambda: True, lambda: False)
        device.set_multiple_values.assert_not_called()

    @patch.object(app.time, 'sleep')
    def test_only_lit_channels_switched_and_verified(self, sleep):
        device = make_device([
            dps(True, True),   # initial check
            dps(False, True),  # re-check before sending
            dps(False, False), # verification
        ])
        result = app.run(device, lambda: True, lambda: True)
        self.assertEqual(result, 'Ceiling off confirmed')
        device.set_multiple_values.assert_called_once_with({2: False})

    @patch.object(app.time, 'sleep')
    def test_reconnect_before_popup_wait(self, sleep):
        device = make_device([dps(True, False), dps(True, False), dps(False, False)])
        def prompt():
            device.close.assert_called_once()
            return True
        self.assertEqual(app.run(device, lambda: True, prompt), 'Ceiling off confirmed')

    @patch.object(app.time, 'sleep')
    def test_failed_read_reconnects_without_repeating_prompt(self, sleep):
        device = make_device([dps(True, False), {'Error': 'connection lost'},
                              dps(True, False), dps(False, False)])
        prompt = Mock(return_value=True)
        self.assertEqual(app.run(device, lambda: True, prompt), 'Ceiling off confirmed')
        prompt.assert_called_once()
        self.assertEqual(device.close.call_count, 2)

    @patch.object(app.time, 'sleep')
    def test_lost_command_reply_reads_state_before_resending(self, sleep):
        device = make_device([dps(True, False), dps(True, False), dps(False, False)])
        device.set_multiple_values.return_value = {'Error': 'lost reply'}
        self.assertEqual(app.run(device, lambda: True, lambda: True), 'Ceiling off confirmed')
        device.set_multiple_values.assert_called_once()

    @patch.object(app.time, 'sleep')
    def test_persistent_failure_has_bounded_retries(self, sleep):
        device = make_device([dps(True, False)] + [{'Error': 'lost'}] * 3)
        prompt = Mock(return_value=True)
        with self.assertRaisesRegex(RuntimeError, 'Cannot read device state'):
            app.run(device, lambda: True, prompt)
        prompt.assert_called_once()
        self.assertEqual(device.status.call_count, 4)

    def test_pc_goes_offline_while_prompt_open(self):
        device = make_device([dps(True, False)])
        app.run(device, Mock(side_effect=[True, False]), lambda: True)
        device.set_multiple_values.assert_not_called()

    def test_pc_unknown_no_popup(self):
        device = make_device([])
        app.run(device, lambda: False, Mock())
        device.status.assert_not_called()

    def test_malformed_state_raises_error(self):
        device = make_device([{'dps': {}}])
        with self.assertRaises(RuntimeError):
            app.run(device, lambda: True, lambda: True)
        device.set_multiple_values.assert_not_called()

    @patch.object(app.time, 'sleep')
    def test_turn_on_upper_only_and_verify(self, sleep):
        device = make_device([
            dps(False, True),  # initial states
            dps(True, True),   # verification
        ])
        result = app.turn_on_upper(device)
        self.assertEqual(result, 'Upper ceiling light on confirmed')
        device.set_multiple_values.assert_called_once_with({1: True})

    def test_turn_on_upper_already_on(self):
        device = make_device([dps(True, False)])
        result = app.turn_on_upper(device)
        self.assertEqual(result, 'Upper ceiling light already on')
        device.set_multiple_values.assert_not_called()

    @patch.object(app.subprocess, 'Popen')
    def test_dialog_accept_timeout_cancel_and_error(self, popen):
        for code, expected in [(0, True), (5, True), (1, False), (255, False), (-15, False)]:
            popup = Mock(returncode=code)
            popup.poll.return_value = code
            popen.return_value = popup
            self.assertEqual(app.ask(lambda: True), expected)
            self.assertEqual(popen.call_args.args[0][0], '/usr/bin/python3')
            self.assertTrue(popen.call_args.args[0][1].endswith('/ceiling_dialog.py'))

    @patch.object(app.subprocess, 'Popen')
    def test_offline_cancels_open_dialog(self, popen):
        popup = Mock()
        popup.poll.return_value = None
        popen.return_value = popup
        self.assertFalse(app.ask(lambda: False))
        popup.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
