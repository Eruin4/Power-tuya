"""Authenticated local API client, also used by the existing command wrappers."""
import argparse
import json
import os
from pathlib import Path
import stat
import time
import urllib.error
import urllib.request

from iot_registry import TARGETS

TOKEN_PATH = Path.home() / '.config/tuya-automation/api.token'
DEFAULT_URL = 'http://127.0.0.1:8091'


class APIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def read_token(path=TOKEN_PATH):
    with Path(path).open() as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('API token must be private and owned by the current user')
        token = stream.read().strip()
    if len(token) < 32 or not token.isascii() or not token.isalnum():
        raise ValueError('Invalid API token')
    return token


def request(action, target=None, *, base_url=None, token_path=TOKEN_PATH):
    if action not in ('list', 'status', 'on', 'off'):
        raise APIError('invalid-action')
    if action != 'list' and target not in TARGETS:
        raise APIError('unknown-device')
    try:
        token = read_token(token_path)
    except (OSError, ValueError):
        raise APIError('api-token-unavailable') from None
    url = (base_url or os.environ.get('IOT_API_URL', DEFAULT_URL)).rstrip('/')
    path = '/v1/devices' if action == 'list' else f'/v1/devices/{target}/state'
    data = json.dumps({'power': action}).encode() if action in ('on', 'off') else None
    req = urllib.request.Request(url + path, data=data,
        method='PUT' if data else 'GET',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(3):
        try:
            with opener.open(req, timeout=45) as response:
                result = json.load(response)
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise APIError('invalid-api-response')
            return result
        except urllib.error.HTTPError as error:
            # Surface only API error codes, never arbitrary response bodies.
            codes = {400: 'invalid-request', 401: 'unauthorized', 404: 'unknown-device',
                     409: 'device-busy', 422: 'configuration-invalid',
                     502: 'device-unavailable', 503: 'api-unavailable', 504: 'device-timeout'}
            if error.code == 409 and attempt < 2:
                error.close()
                time.sleep(.5)
                continue
            code = codes.get(error.code, 'api-error')
            error.close()
            raise APIError(code) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise APIError('api-unavailable') from None


def main():
    parser = argparse.ArgumentParser(description='Control devices through the local IoT API')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    for action in ('status', 'on', 'off'):
        sub.add_parser(action).add_argument('target', choices=TARGETS)
    args = parser.parse_args()
    try:
        print(json.dumps(request(args.action, getattr(args, 'target', None))))
        return 0
    except APIError as error:
        print(json.dumps({'ok': False, 'error': error.code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
