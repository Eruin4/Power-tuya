import unittest
from unittest.mock import Mock

from light_control import operate_ceiling


ITEM = {'id': 'test-device', 'ip': '192.168.0.100',
        'local_key': '0123456789abcdef', 'version': 3.3}


class LightControlTests(unittest.TestCase):
    def test_only_requested_ceiling_channel_changes(self):
        device = Mock()
        device.status.side_effect = [
            {'dps': {'1': False, '2': True}},
            {'dps': {'1': True, '2': True}},
        ]
        device.set_multiple_values.return_value = {'dps': {'1': True, '2': True}}
        result = operate_ceiling(ITEM, 'ceiling-upper', 'on',
                                 Mock(return_value=device), sleep=lambda _: None)
        self.assertEqual(result['power'], 'on')
        self.assertEqual(result['channels'], {'1': 'on'})
        device.set_multiple_values.assert_called_once_with({1: True})

    def test_all_off_is_idempotent(self):
        device = Mock()
        device.status.return_value = {'dps': {'1': False, '2': False}}
        result = operate_ceiling(ITEM, 'ceiling-all', 'off',
                                 Mock(return_value=device), sleep=lambda _: None)
        self.assertFalse(result['changed'])
        device.set_multiple_values.assert_not_called()

    def test_all_status_reports_mixed_without_writing(self):
        device = Mock()
        device.status.return_value = {'dps': {'1': False, '2': True}}
        result = operate_ceiling(ITEM, 'ceiling-all', 'status',
                                 Mock(return_value=device), sleep=lambda _: None)
        self.assertEqual(result['power'], 'mixed')
        device.set_multiple_values.assert_not_called()

    def test_unverified_change_fails(self):
        device = Mock()
        device.status.return_value = {'dps': {'1': False, '2': False}}
        with self.assertRaises(RuntimeError):
            operate_ceiling(ITEM, 'ceiling-all', 'on',
                            Mock(return_value=device), attempts=1, sleep=lambda _: None)


if __name__ == '__main__':
    unittest.main()
