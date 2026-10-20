"""API boundary, physical-device contention, and worker failure contracts."""
import json
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from iot_api import Application, run_device

TOKEN = 'a' * 64
HEADERS = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}


class APITests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock(return_value=(200, {'ok': True, 'power': 'off'}))
        self.app = Application(TOKEN, self.backend)

    def test_unauthorized_write_never_reaches_device(self):
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            status, _ = self.app.dispatch('PUT', '/v1/devices/bedroom-light/state',
                                          headers, b'{"power":"on"}')
            self.assertEqual(status, 401)
        self.backend.assert_not_called()

    def test_health_and_inventory_do_not_contact_hardware(self):
        self.assertEqual(self.app.dispatch('GET', '/health', {})[0], 200)
        status, data = self.app.dispatch('GET', '/v1/devices', HEADERS)
        self.assertEqual(status, 200)
        self.assertEqual(len(data['devices']), 5)
        self.assertNotIn('local_key', json.dumps(data))
        self.backend.assert_not_called()

    def test_valid_command_and_status_have_distinct_actions(self):
        route = '/v1/devices/bedroom-light/state'
        self.app.dispatch('PUT', route, HEADERS, b'{"power":"off"}')
        self.backend.assert_called_once_with('bedroom-light', 'off')
        self.app.dispatch('GET', route, HEADERS)
        self.backend.assert_called_with('bedroom-light', 'status')

    def test_invalid_input_never_reaches_hardware(self):
        for body in (b'null', b'[]', b'{"power":true}', b'{"power":"toggle"}',
                     b'{"power":"on","ip":"192.168.0.1"}', b'broken', b'\xff'):
            self.assertEqual(self.app.dispatch('PUT', '/v1/devices/bedroom-light/state',
                                               HEADERS, body)[0], 400)
        self.assertEqual(self.app.dispatch('GET', '/v1/devices/unknown/state', HEADERS)[0], 404)
        self.assertEqual(self.app.dispatch('POST', '/v1/devices/bedroom-light/state', HEADERS)[0], 405)
        self.assertEqual(self.app.dispatch('GET', '/v1/devices?key=secret', HEADERS)[0], 400)
        self.assertEqual(self.app.dispatch('GET', '/v1/devices',
            HEADERS | {'Origin': 'http://example.com'})[0], 403)
        self.backend.assert_not_called()

    def test_ceiling_aliases_share_lock_but_other_devices_do_not(self):
        entered, release = threading.Event(), threading.Event()
        def backend(target, action):
            if target == 'ceiling-upper':
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timed out')
            return 200, {'ok': True}
        app = Application(TOKEN, backend)
        thread = threading.Thread(target=app.dispatch,
            args=('GET', '/v1/devices/ceiling-upper/state', HEADERS))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(app.dispatch('GET', '/v1/devices/ceiling-all/state', HEADERS)[0], 409)
            self.assertEqual(app.dispatch('GET', '/v1/devices/bedroom-light/state', HEADERS)[0], 200)
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(app.dispatch('GET', '/v1/devices/ceiling-all/state', HEADERS)[0], 200)

    def test_failed_backend_releases_device_lock(self):
        self.backend.side_effect = RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            self.app.dispatch('GET', '/v1/devices/bedroom-light/state', HEADERS)
        self.assertFalse(self.app.locks['bedroom-light'].locked())

    def test_worker_timeout_is_bounded_and_sanitized(self):
        with patch('iot_api.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 40)):
            self.assertEqual(run_device('bedroom-light', 'off'),
                             (504, {'ok': False, 'error': 'device-timeout'}))

    def test_worker_output_is_filtered_and_errors_are_sanitized(self):
        result = Mock(returncode=0, stdout=json.dumps({'ok': True, 'target': 'bedroom-light',
                      'power': 'off', 'changed': False, 'local_key': 'must-not-leak'}))
        with patch('iot_api.subprocess.run', return_value=result):
            status, data = run_device('bedroom-light', 'off')
            self.assertEqual(status, 200)
            self.assertNotIn('local_key', data)
        result.stdout = 'raw-secret-traceback'
        with patch('iot_api.subprocess.run', return_value=result):
            self.assertEqual(run_device('bedroom-light', 'off'),
                             (503, {'ok': False, 'error': 'backend-unavailable'}))


if __name__ == '__main__':
    unittest.main()
