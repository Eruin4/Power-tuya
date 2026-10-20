"""Exercise the local runtime with real TinyTuya and simulated LAN sockets.

Run with the project's Python: python -m unittest -v test_local_runtime
No host credentials, real devices, or external network access are used.
"""
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.request

PROJECT = Path(__file__).resolve().parent


def install_network_mocks():
    """Installed by sitecustomize in every CLI/server/worker child process."""
    fixture = Path(os.environ['LOCAL_VERIFY_FIXTURE'])
    events = fixture / 'events.jsonl'

    def record(kind, **details):
        with events.open('a') as stream:
            stream.write(json.dumps({'kind': kind, **details}) + '\n')

    def denied(kind):
        record('blocked', operation=kind)
        raise OSError('External network access is blocked during local verification')

    def audit(event, args):
        if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr') and args[0] not in (
                '127.0.0.1', 'localhost', '::1'):
            denied('dns')
        if event in ('socket.connect', 'socket.sendto'):
            address = args[1] if event == 'socket.connect' else args[-1]
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
                denied('socket')

    sys.addaudithook(audit)
    # Redirect default credential paths only in these test child processes.
    # Do not change HOME, or read/copy any host credential file.
    Path.home = classmethod(lambda cls: fixture)
    import requests
    requests.sessions.Session.request = lambda *a, **kw: denied('requests')
    import tinytuya
    tinytuya.Cloud.__init__ = lambda *a, **kw: denied('cloud-client')
    core = importlib.import_module('tinytuya.core.XenonDevice')
    from tinytuya.core.crypto_helper import AESCipher
    from tinytuya.core.message_helper import TuyaMessage, pack_message, unpack_message

    class LANSocket:
        """Simulate only TCP/6668 peers, below TinyTuya's encryption boundary."""
        def __init__(self, *args, **kwargs):
            self.buffer = b''

        def connect(self, address):
            if address not in ((f'192.0.2.{n}', 6668) for n in (11, 12, 13)):
                denied('unexpected-lan-address')
            self.peer = address[0]
            record('lan-connect', peer=self.peer, port=6668)

        def setsockopt(self, *args):
            pass

        def settimeout(self, value):
            pass

        def close(self):
            pass

        def sendall(self, wire):
            message = unpack_message(wire, no_retcode=True)
            payload = message.payload
            if payload.startswith(b'3.3'):
                payload = payload[15:]
            # This is a synthetic test key, never a copied host local key.
            cipher = AESCipher(b'0' * 16)
            command = json.loads(cipher.decrypt(payload, use_base64=False))
            state_path = fixture / 'peer-state.json'
            state = json.loads(state_path.read_text())
            if message.cmd == 7:
                changes = command['dps']
                state[self.peer].update(changes)
                state_path.write_text(json.dumps(state))
                record('lan-write', peer=self.peer, channels=changes)
            else:
                record('lan-read', peer=self.peer)
            response = cipher.encrypt(json.dumps({'dps': state[self.peer]}).encode(),
                                      use_base64=False)
            # Responses have a four-byte return code before the encrypted data.
            self.buffer += pack_message(TuyaMessage(message.seqno, message.cmd, 0,
                                                    b'\0' * 4 + response, 0))

        def recv(self, size):
            result, self.buffer = self.buffer[:size], self.buffer[size:]
            return result

    core.socket = types.SimpleNamespace(**{
        name: getattr(socket, name) for name in dir(socket) if not name.startswith('__')
    })
    core.socket.socket = LANSocket
    real_urlopen = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        if url == 'http://127.0.0.1:8088/status.json':
            record('status-http', method='GET', port=8088)
            return io.BytesIO(b'{"main_pc":{"online":true}}')
        return real_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = urlopen
    real_popen = subprocess.Popen

    def popen(argv, *args, **kwargs):
        # Replace only the GUI child; keep the real prompt decision loop and
        # CLI/API/worker subprocesses, without opening a desktop window.
        if (isinstance(argv, (list, tuple)) and len(argv) == 2
                and Path(argv[1]) == PROJECT / 'ceiling_dialog.py'):
            code = int(os.environ.get('LOCAL_VERIFY_DIALOG_EXIT', '0'))
            record('dialog-decision', exit_code=code)
            argv = [sys.executable, '-c', f'raise SystemExit({code})']
        return real_popen(argv, *args, **kwargs)

    subprocess.Popen = popen
    time.sleep = lambda seconds: None
    record('guard-installed', component=Path(sys.argv[0]).name,
           tinytuya_version=tinytuya.__version__)


@unittest.skipUnless(importlib.util.find_spec('tinytuya'), 'TinyTuya is required for the LAN regression')
class LocalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The sandbox omits the host virtualenv. Supply a launcher only when
        # absent, and remove it afterwards. An installed project is left alone.
        cls.launcher_dir = PROJECT / '.venv'
        cls.created_launcher = not cls.launcher_dir.exists()
        if cls.created_launcher:
            (cls.launcher_dir / 'bin').mkdir(parents=True)
            launcher = cls.launcher_dir / 'bin/python'
            fd = os.open(launcher, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
            with os.fdopen(fd, 'w') as stream:
                stream.write('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')

    @classmethod
    def tearDownClass(cls):
        if cls.created_launcher:
            shutil.rmtree(cls.launcher_dir)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='local-verify-')
        self.fixture = Path(self.temp.name)
        self.server = None
        self.addCleanup(self.cleanup)
        config_dir = self.fixture / '.config/tuya-automation'
        config_dir.mkdir(parents=True)
        devices = {}
        for name, suffix in (('monitor-plug', 11), ('bedroom-light', 12), ('ceiling-switch', 13)):
            devices[name] = {'id': 'fixture-' + name, 'ip': f'192.0.2.{suffix}',
                             'local_key': '0' * 16, 'version': 3.3, 'switch_dp': 1}
        for filename, contents in (('devices.json', json.dumps(devices)), ('api.token', 'a' * 64)):
            fd = os.open(config_dir / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(contents)
        self.reset_peers()
        (self.fixture / 'sitecustomize.py').write_text(
            'import os\ntry:\n'
            ' from test_local_runtime import install_network_mocks\n'
            ' install_network_mocks()\nexcept Exception:\n os._exit(97)\n')
        # The GUI child is intercepted above; desktop side effects in the
        # watcher are replaced by shell functions. Real command wrappers run.
        self.env = {**os.environ, 'LOCAL_VERIFY_FIXTURE': str(self.fixture),
                    'LOCAL_VERIFY_PYTHON': sys.executable,
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONPATH': os.pathsep.join((str(self.fixture), str(PROJECT),
                                                  os.environ.get('PYTHONPATH', ''))),
                    'PATH': str(self.fixture) + os.pathsep + os.environ['PATH']}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.env['IOT_API_URL'] = f'http://127.0.0.1:{port}'
        self.server = subprocess.Popen([sys.executable, str(PROJECT / 'iot_api.py'),
                                        '--port', str(port)], env=self.env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for attempt in range(100):
            if self.server.poll() is not None:
                self.fail('Mocked API startup failed')
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        self.env['IOT_API_URL'] + '/health', timeout=.2) as response:
                    self.assertTrue(json.load(response)['ok'])
                break
            except OSError:
                time.sleep(.02)
        else:
            self.fail('Mocked API did not become ready')

    def cleanup(self):
        if self.server is not None and self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=5)
        self.temp.cleanup()

    def reset_peers(self):
        (self.fixture / 'peer-state.json').write_text(json.dumps({
            '192.0.2.11': {'1': False}, '192.0.2.12': {'1': True},
            '192.0.2.13': {'1': True, '2': True}}))

    def events(self):
        return [json.loads(line) for line in (self.fixture / 'events.jsonl').read_text().splitlines()]

    def command(self, argv, **extra):
        result = subprocess.run(argv, env={**self.env, **extra}, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'Local command failed (output withheld)')
        return result.stdout.strip()

    def assert_local(self, label):
        events = self.events()
        self.assertEqual([item for item in events if item['kind'] == 'blocked'], [])
        self.assertTrue(any(item['kind'] == 'guard-installed' and
                            item['component'] == 'iot_backend.py' for item in events))
        if label.startswith(('Ceiling', 'Watcher')):
            self.assertTrue(any(item['kind'] == 'dialog-decision' for item in events))
        writes = sum(item['kind'] == 'lan-write' for item in events)
        print(f'{label}: completed; simulated LAN writes={writes}; cloud/external attempts=0',
              flush=True)

    def test_command_wrappers_and_idempotence(self):
        light = str(PROJECT / 'bin/light-control')
        first = json.loads(self.command(['sh', light, 'off', 'bedroom-light']))
        second = json.loads(self.command(['sh', light, 'off', 'bedroom-light']))
        self.assertEqual(first['power'], 'off')
        self.assertTrue(first['changed'])
        self.assertFalse(second['changed'])
        self.command(['sh', light, 'on', 'bedroom-light'])
        self.command(['sh', str(PROJECT / 'bin/tuya-control'), 'monitor-plug', 'on'])
        self.command(['sh', str(PROJECT / 'bin/iot-control'), 'status', 'ceiling-all'])
        self.assert_local('Actual CLI -> HTTP API -> worker -> TinyTuya sockets')

    def test_ceiling_dialog_accept_timeout_and_cancel(self):
        for code, expected in (('1', True), ('0', False), ('5', False)):
            self.reset_peers()
            result = self.command([sys.executable, str(PROJECT / 'ceiling_prompt.py')],
                                  LOCAL_VERIFY_DIALOG_EXIT=code)
            state = json.loads((self.fixture / 'peer-state.json').read_text())
            self.assertEqual(state['192.0.2.13'], {'1': expected, '2': expected})
            self.assertEqual(result, 'Ceiling off cancelled' if expected else 'Ceiling off confirmed')
        self.assert_local('Ceiling dialog cancel/accept/30-second-timeout exit')

    def test_watcher_online_offline_schedule(self):
        source = (PROJECT / 'monitor-power.sh').read_text()
        head, loop = source.split('\nwhile true; do\n', 1)
        # Run the actual watcher loop and controls; synchronize its background
        # popup for deterministic tests, and replace desktop/status/time I/O.
        head = head.replace('"$TUYA_CEILING_PYTHON" "$TUYA_CEILING_SCRIPT" &',
                            '"$TUYA_CEILING_PYTHON" "$TUYA_CEILING_SCRIPT"')
        for hour, expected_target in (('05', 'bedroom-light'), ('06', 'ceiling-upper'),
                                      ('20', 'ceiling-upper'), ('21', 'bedroom-light')):
            self.reset_peers()
            before = len(self.events())
            overrides = r'''
i=0
python3() { "$LOCAL_VERIFY_PYTHON" "$@"; }
curl() {
  [ "$*" = '-fsS --max-time 5 http://127.0.0.1:8088/status.json' ] || exit 98
  case "$i" in 0|3|4) echo '{"main_pc":{"online":true}}';;
    *) echo '{"main_pc":{"online":false}}';; esac
}
date() { if [ "$1" = +%H ]; then echo "$LOCAL_VERIFY_HOUR"; else echo test-time; fi; }
set_display_power() { :; }
set_active_fallback() { :; }
pause_media_players() { :; }
mute_audio_sinks() { :; }
restore_audio_sinks() { :; }
gsettings() { :; }
# Invoke shell wrappers explicitly so a content-only sandbox copy works too.
timeout() { local seconds=$1; shift; command timeout "$seconds" sh "$@"; }
sleep() {
  [ "$1" = "$TUYA_WAKE_DELAY" ] && return
  i=$((i+1))
  [ "$i" -ge 5 ] && exit 0
  return 0
}
'''
            result = subprocess.run(['bash'], input=head + overrides + '\nwhile true; do\n' + loop,
                                    env={**self.env, 'LOCAL_VERIFY_HOUR': hour,
                                         'TUYA_ENABLED': '1', 'POLL_SECONDS': '5',
                                         'TUYA_WAKE_DELAY': '7', 'TUYA_LIGHT_NAME': 'bedroom-light',
                                         'TUYA_CONTROL': str(PROJECT / 'bin/tuya-control'),
                                         'LIGHT_CONTROL': str(PROJECT / 'bin/light-control'),
                                         'TUYA_CEILING_PYTHON': sys.executable,
                                         'TUYA_CEILING_SCRIPT': str(PROJECT / 'ceiling_prompt.py')},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, 'Watcher failed (output withheld)')
            self.assertNotIn('failed', result.stdout + result.stderr)
            self.assertEqual(result.stdout.count('bedroom-light off confirmed for main pc online'), 2)
            writes = [item for item in self.events()[before:] if item['kind'] == 'lan-write']
            light_on = [item for item in writes if item['peer'] in ('192.0.2.12', '192.0.2.13')
                        and any(item['channels'].values())]
            peer = '192.0.2.12' if expected_target == 'bedroom-light' else '192.0.2.13'
            self.assertEqual(light_on, [{'kind': 'lan-write', 'peer': peer, 'channels': {'1': True}}])
            self.assert_local(f'Watcher online -> offline -> online at {hour}:00')

    def test_guard_blocks_cloud_before_network(self):
        probe = '''
import socket, requests, tinytuya
for call in (lambda: socket.getaddrinfo('openapi.tuyacn.com', 443),
             lambda: socket.gethostbyname('openapi.tuyacn.com'),
             lambda: requests.get('https://openapi.tuyacn.com/v1.0/token?grant_type=1'),
             lambda: tinytuya.Cloud(apiKey='dummy', apiSecret='dummy'),
             lambda: socket.socket().connect(('192.0.2.99', 443))):
    try:
        call()
    except OSError:
        continue
    raise SystemExit(1)
'''
        self.command([sys.executable, '-c', probe])
        blocked = [item['operation'] for item in self.events() if item['kind'] == 'blocked']
        self.assertEqual(blocked, ['dns', 'dns', 'requests', 'cloud-client', 'socket'])
        print('Guard negative controls: DNS, HTTPS, Cloud construction, external TCP blocked before network',
              flush=True)


if __name__ == '__main__':
    unittest.main()
