"""Ask on the server desktop before switching off the ceiling light.

The desktop dialog uses the local IoT API for all device operations.
Low-level helpers are retained for the API's isolated Tuya worker.
"""
import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import urllib.request

from iot_client import request

CONFIG = Path.home() / '.config/tuya-automation/devices.json'
DEVICE_NAME = 'ceiling-switch'
SWITCH_DPS = (1, 2)
UPPER_DP = 1


class APIDevice:
    """Keep dialog decisions local while device access belongs to the API."""
    def status(self):
        result = request('status', 'ceiling-all')
        return {'dps': {dp: power == 'on' for dp, power in result['channels'].items()}}

    def set_multiple_values(self, changes):
        if not changes or any(dp not in SWITCH_DPS or type(value) is not bool
                              for dp, value in changes.items()):
            raise RuntimeError('Invalid ceiling change')
        if len(set(changes.values())) != 1:
            raise RuntimeError('Mixed ceiling commands are not supported')
        target = ('ceiling-all' if len(changes) == 2 else
                  'ceiling-upper' if 1 in changes else 'ceiling-lower')
        request('on' if next(iter(changes.values())) else 'off', target)
        return {'dps': {str(dp): value for dp, value in changes.items()}}

    def close(self):
        pass


def online():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8088/status.json', timeout=3) as response:
            return json.load(response)['main_pc']['online'] is True
    except Exception:
        return False


def load_config(path=CONFIG):
    with path.open() as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
            raise RuntimeError('Config must be user-owned and mode 600')
        data = json.load(stream)
    if not isinstance(data, dict) or DEVICE_NAME not in data:
        raise RuntimeError('Config missing ceiling-switch entry')
    return data[DEVICE_NAME]


def connect(item, factory):
    device = factory(item['id'], item['ip'], item['local_key'],
                     version=item['version'],
                     connection_timeout=5,
                     connection_retry_limit=2,
                     persist=True)
    return device


def states(device):
    response = device.status()
    dps = response.get('dps') if isinstance(response, dict) else None
    if not isinstance(dps, dict):
        raise RuntimeError('Cannot read device state')
    result = {}
    for dp in SWITCH_DPS:
        val = dps.get(str(dp))
        if type(val) is not bool:
            raise RuntimeError(f'DP {dp} state is not boolean')
        result[dp] = val
    return result


def read_state(device, dp):
    return states(device)[dp]


def set_switches(device, values):
    """Set one or more switch DPs in a single LAN command."""
    response = device.set_multiple_values(values)
    if not isinstance(response, dict) or 'Error' in response or 'Err' in response:
        raise RuntimeError('Ceiling switch command failed')


def ask(is_online=online):
    popup = subprocess.Popen([
        '/usr/bin/python3', str(Path(__file__).with_name('ceiling_dialog.py')),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        started = time.monotonic()
        while popup.poll() is None:
            if not is_online() or time.monotonic() - started > 40:
                return False
            time.sleep(.5)
        # Dialog code 5 is its own timeout; display failures never authorize off.
        return popup.returncode in (0, 5)
    finally:
        if popup.poll() is None:
            popup.terminate()
            try:
                popup.wait(timeout=3)
            except subprocess.TimeoutExpired:
                popup.kill()
                popup.wait()


def run(device, is_online=online, prompt=ask):
    if not is_online():
        return 'PC offline or unknown; cancelled'
    if not any(states(device).values()):
        return 'Ceiling already off; no popup'
    # The device may expire an idle LAN session while the dialog is open.
    device.close()
    if not prompt():
        return 'Ceiling off cancelled'
    if not is_online():
        return 'PC offline or unknown; cancelled'
    for attempt in range(3):
        if not is_online():
            return 'PC offline or unknown; cancelled'
        try:
            current = states(device)
            changes = {dp: False for dp, value in current.items() if value}
            if not changes:
                return 'Ceiling already off' if attempt == 0 else 'Ceiling off confirmed'
            set_switches(device, changes)
            for _ in range(3):
                if not is_online():
                    return 'PC offline or unknown; cancelled'
                time.sleep(1)
                if not any(states(device).values()):
                    return 'Ceiling off confirmed'
            raise RuntimeError('Ceiling off verification failed')
        except (RuntimeError, OSError):
            # Reconnect and re-read before retrying; a lost reply may still
            # mean the command was applied. Keep the user's original decision.
            device.close()
            if attempt == 2:
                raise
            time.sleep(1)


def turn_on_upper(device):
    """Turn on ceiling switch DP 1 (upper) and verify the resulting state."""
    current = states(device)
    if current[UPPER_DP]:
        return 'Upper ceiling light already on'
    set_switches(device, {UPPER_DP: True})
    for _ in range(3):
        time.sleep(1)
        if states(device)[UPPER_DP]:
            return 'Upper ceiling light on confirmed'
    raise RuntimeError('Upper ceiling light on verification failed')


def stop(signum, frame):
    raise SystemExit(0)


def timed_out(signum, frame):
    raise TimeoutError()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Read ceiling state without popup or commands')
    parser.add_argument('--on-upper', action='store_true', help='Turn on ceiling switch 1')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    signal.signal(signal.SIGALRM, timed_out)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    device = None
    try:
        device = APIDevice()
        if args.check:
            print(json.dumps(states(device)))
        elif args.on_upper:
            print(turn_on_upper(device), flush=True)
        else:
            print(run(device), flush=True)
        return 0
    except Exception as error:
        # Avoid logging credentials or provider response bodies.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('Ceiling automation failed: ' + detail, flush=True)
        return 1
    finally:
        if device is not None:
            with contextlib.suppress(Exception):
                device.close()


if __name__ == '__main__':
    raise SystemExit(main())
