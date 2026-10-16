"""API command surface and private worker helpers for the configured lights.

Only named lights and on/off/status actions are accepted. Device secrets and
raw protocol responses never appear in the output.
"""

import argparse
import contextlib
import json
import logging
import signal
import time
from pathlib import Path

from ceiling_prompt import connect, set_switches, states
from tuya_control import DEFAULT_CONFIG, control, load_config
from iot_client import APIError, request


TARGETS = {
    'bedroom-light': ('bedroom-light', None),
    'ceiling-upper': ('ceiling-switch', (1,)),
    'ceiling-lower': ('ceiling-switch', (2,)),
    'ceiling-all': ('ceiling-switch', (1, 2)),
}


def operate_ceiling(item, target, action, factory, attempts=3, sleep=time.sleep):
    channels = TARGETS[target][1]
    desired = action == 'on'
    for attempt in range(attempts):
        device = None
        try:
            device = connect(item, factory)
            before = states(device)
            if action == 'status':
                after = before
                changed = False
            else:
                changes = {dp: desired for dp in channels if before[dp] != desired}
                if changes:
                    set_switches(device, changes)
                    sleep(1)
                after = states(device)
                if any(after[dp] != desired for dp in channels):
                    raise RuntimeError('Ceiling state did not match the requested state')
                changed = bool(changes)
            selected = [after[dp] for dp in channels]
            power = 'on' if all(selected) else 'off' if not any(selected) else 'mixed'
            return {'target': target, 'power': power, 'changed': changed,
                    'channels': {str(dp): 'on' if after[dp] else 'off' for dp in channels}}
        except (RuntimeError, OSError, ValueError, TypeError):
            if attempt == attempts - 1:
                raise RuntimeError('Ceiling light unavailable') from None
            sleep(1)
        finally:
            if device is not None:
                with contextlib.suppress(Exception):
                    device.close()


def operate(target, action, config, factory):
    name, channels = TARGETS[target]
    item = load_config(config, name)
    if channels is None:
        return {'target': target, **control(item, action, factory)}
    return operate_ceiling(item, target, action, factory)


def timed_out(_signum, _frame):
    raise TimeoutError('Light command timed out')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Control configured lights through the local IoT API')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='List supported light names without contacting devices')
    for action in ('status', 'on', 'off'):
        sub.add_parser(action).add_argument('target', choices=TARGETS)
    args = parser.parse_args(argv)
    if args.command == 'list':
        print(json.dumps({'targets': list(TARGETS)}))
        return 0
    logging.disable(logging.CRITICAL)
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(45)
    try:
        if args.config != DEFAULT_CONFIG:
            raise APIError('configuration-managed-by-api')
        result = request(args.command, args.target)
        print(json.dumps(result))
        return 0
    except APIError as error:
        print(json.dumps({'ok': False, 'target': args.target, 'error': error.code}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'target': args.target,
                          'error': 'light-unavailable'}))
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
