"""Local authenticated IoT API with one operation per physical device."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from iot_client import TOKEN_PATH, read_token
from iot_registry import TARGETS, device_list
from tuya_control import DEFAULT_CONFIG

LOG = logging.getLogger('iot-api')
WORKER = Path(__file__).with_name('iot_backend.py')


def run_device(target, action, config=DEFAULT_CONFIG):
    try:
        proc = subprocess.run([sys.executable, str(WORKER), '--config', str(config),
                               target, action], capture_output=True, text=True, timeout=40)
        result = json.loads(proc.stdout)
        if not isinstance(result, dict):
            raise ValueError()
        if result.get('ok') is True and proc.returncode == 0:
            # Keep the public response independent of raw Tuya responses.
            if result.get('power') not in ('on', 'off', 'mixed'):
                raise ValueError()
            return 200, {k: result[k] for k in ('ok', 'target', 'power', 'changed', 'channels')
                         if k in result}
        code = result.get('error')
        if code == 'configuration-invalid':
            return 422, {'ok': False, 'error': code}
        return 502, {'ok': False, 'error': 'device-unavailable'}
    except subprocess.TimeoutExpired:
        return 504, {'ok': False, 'error': 'device-timeout'}
    except (OSError, ValueError, TypeError):
        return 503, {'ok': False, 'error': 'backend-unavailable'}


class Application:
    def __init__(self, token, backend=run_device):
        self.token = token
        self.backend = backend
        self.locks = {group: threading.Lock() for group, _ in TARGETS.values()}

    def dispatch(self, method, path, headers, body=b''):
        def fail(status, code):
            return status, {'ok': False, 'error': code}
        route = urlsplit(path)
        if route.query or route.fragment or route.scheme or route.netloc:
            return fail(400, 'invalid-request')
        if headers.get('Origin'):
            return fail(403, 'browser-origin-not-allowed')
        if method == 'GET' and path == '/health':
            return 200, {'ok': True, 'service': 'iot-api'}
        auth = headers.get('Authorization', '')
        if not hmac.compare_digest(auth.encode(), ('Bearer ' + self.token).encode()):
            return fail(401, 'unauthorized')
        if path == '/v1/devices':
            if method != 'GET':
                return fail(405, 'method-not-allowed')
            return 200, {'ok': True, 'devices': device_list()}
        parts = path.split('/')
        if len(parts) != 5 or parts[1:3] != ['v1', 'devices'] or parts[4] != 'state':
            return fail(404, 'not-found')
        target = parts[3]
        if target not in TARGETS:
            return fail(404, 'unknown-device')
        if method == 'GET':
            if body:
                return fail(400, 'invalid-request')
            action = 'status'
        elif method == 'PUT':
            if headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                return fail(415, 'json-required')
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) != {'power'}:
                    raise ValueError()
                action = payload['power']
                if action not in ('on', 'off'):
                    raise ValueError()
            except (ValueError, TypeError):
                return fail(400, 'invalid-request')
        else:
            return fail(405, 'method-not-allowed')
        lock = self.locks[TARGETS[target][0]]
        if not lock.acquire(blocking=False):
            return fail(409, 'device-busy')
        try:
            status, result = self.backend(target, action)
            LOG.info('target=%s action=%s status=%s power=%s error=%s', target, action,
                     status, result.get('power', '-'), result.get('error', '-'))
            return status, result
        finally:
            lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'IoTAPI/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def handle_request(self):
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) > 1:
                self.reply(400, {'ok': False, 'error': 'invalid-request'})
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 1024:
                self.reply(413, {'ok': False, 'error': 'request-too-large'})
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self.reply(400, {'ok': False, 'error': 'invalid-request'})
                return
            status, result = self.server.app.dispatch(self.command, self.path, self.headers, body)
            self.reply(status, result)
        except (OSError, ValueError):
            self.close_connection = True

    do_GET = do_PUT = do_POST = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_request

    def reply(self, status, result):
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        if status == 409:
            self.send_header('Retry-After', '1')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self.close_connection = True

    def log_message(self, format, *args):
        # Device actions are logged above; request headers and tokens are never logged.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--token-file', type=Path, default=TOKEN_PATH)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    token = read_token(args.token_file)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.app = Application(token, lambda target, action: run_device(target, action, args.config))
    LOG.info('Listening on http://127.0.0.1:%s', args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
