"""Private worker process. Only the API server invokes device operations here."""
import argparse
import json
import logging
from pathlib import Path

from iot_registry import TARGETS
from light_control import operate_ceiling
from tuya_control import DEFAULT_CONFIG, control, load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('target', choices=TARGETS)
    parser.add_argument('action', choices=('status', 'on', 'off'))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    group = TARGETS[args.target][0]
    try:
        item = load_config(args.config, group)
    except (OSError, ValueError, TypeError):
        print(json.dumps({'ok': False, 'error': 'configuration-invalid'}))
        return 1
    try:
        import tinytuya
        if group == 'ceiling-switch':
            result = operate_ceiling(item, args.target, args.action, tinytuya.OutletDevice)
        else:
            result = control(item, args.action, tinytuya.OutletDevice)
        print(json.dumps({**result, 'ok': True, 'target': args.target}))
        return 0
    except Exception:
        print(json.dumps({'ok': False, 'error': 'device-unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
