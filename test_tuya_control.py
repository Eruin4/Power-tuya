import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tuya_control import control, diagnose, import_wizard, load_config

ITEM = {"id": "test-device", "ip": "192.168.0.100", "local_key": "0123456789abcdef", "version": 3.3}


class ControllerTests(unittest.TestCase):
    def test_no_write_when_already_on(self):
        device = Mock()
        device.status.return_value = {"dps": {"1": True}}
        self.assertEqual(control(ITEM, "on", Mock(return_value=device))["changed"], False)
        device.set_status.assert_not_called()
        device.close.assert_called_once()

    def test_write_verified(self):
        device = Mock()
        device.status.side_effect = [{"dps": {"1": False}}, {"dps": {"1": True}}]
        self.assertEqual(control(ITEM, "on", Mock(return_value=device), sleep=lambda _: None)["power"], "on")
        device.set_status.assert_called_once_with(True, switch=1)

    def test_wrong_dp_never_writes_and_retries_are_bounded(self):
        device = Mock()
        device.status.return_value = {"dps": {"1": 42}}
        factory = Mock(return_value=device)
        with self.assertRaises(RuntimeError):
            control(ITEM, "off", factory, sleep=lambda _: None)
        self.assertEqual(factory.call_count, 3)
        device.set_status.assert_not_called()

    def test_unverified_write_fails(self):
        device = Mock()
        device.status.return_value = {"dps": {"1": False}}
        with self.assertRaises(RuntimeError):
            control(ITEM, "on", Mock(return_value=device), sleep=lambda _: None)

    def test_private_config_required(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "devices.json"
            path.write_text(json.dumps({"monitor-plug": ITEM}))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_config(path, "monitor-plug")
            path.chmod(0o600)
            self.assertEqual(load_config(path, "monitor-plug"), ITEM)

    def test_import_wizard_keeps_key_out_of_output_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "wizard.json"
            target = Path(folder) / "devices.json"
            source.write_text(json.dumps([{"name": "모니터 96", "id": ITEM["id"],
                                           "key": ITEM["local_key"]}]))
            source.chmod(0o600)
            import_wizard(source, target, "모니터 96", "monitor-plug",
                          ITEM["ip"], ITEM["version"], 1)
            self.assertEqual(load_config(target, "monitor-plug"), ITEM | {"switch_dp": 1})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_diagnose_reports_unreachable_without_device_protocol(self):
        connector = Mock(side_effect=OSError(113, "unreachable"))
        factory = Mock()
        self.assertEqual(diagnose(ITEM, factory, connector)["tcp_6668"], "no-route")
        factory.assert_not_called()

    def test_diagnose_finds_version_and_boolean_dp(self):
        connection = Mock()
        bad = Mock()
        bad.status.return_value = {"Error": "bad version"}
        good = Mock()
        good.status.return_value = {"dps": {"1": True, "9": 23}}
        factory = Mock(side_effect=[bad, good])
        result = diagnose(ITEM, factory, Mock(return_value=connection))
        self.assertEqual(result, {"tcp_6668": "open", "version": 3.5,
                                  "boolean_dps": [1]})
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
