# Local IoT API

Base URL: `http://127.0.0.1:8091`. This binds only to this server's loopback
interface. The hardware monitor remains on port 8088.

The API owns normal Tuya LAN access. `bin/iot-control`, `bin/light-control`,
`bin/tuya-control ... status/on/off`, and the ceiling dialog call this API.
The existing computer-monitoring schedule and desktop confirmation flow remain
in `monitor-power.sh` and `ceiling_prompt.py`.

## Targets

| Target | Device | Meaning |
| --- | --- | --- |
| `monitor-plug` | 192.168.0.96 | Monitor power |
| `bedroom-light` | 192.168.0.106 | Bedroom light |
| `ceiling-upper` | 192.168.0.104 | Ceiling switch 1 |
| `ceiling-lower` | 192.168.0.104 | Ceiling switch 2 |
| `ceiling-all` | 192.168.0.104 | Both ceiling switches |

Targets are explicitly registered in `iot_registry.py`. The API does not accept
arbitrary IP addresses, device IDs, credentials, or raw switch data points.

## Authentication and routes

Send `Authorization: Bearer <token>` on all device routes. The token is stored
in `~/.config/tuya-automation/api.token` with mode 600. Local command clients
read this file automatically. Responses and logs never contain the token or
Tuya local keys. Browser origins are rejected; there is no CORS access.

| Method | Path | Request / response |
| --- | --- | --- |
| GET | `/health` | API liveness; no token required; does not contact hardware |
| GET | `/v1/devices` | Registered targets and supported actions |
| GET | `/v1/devices/{target}/state` | Read current device power |
| PUT | `/v1/devices/{target}/state` | JSON `{"power":"on"}` or `{"power":"off"}` |

PUT requires `Content-Type: application/json`. Successful power responses:

```json
{"ok":true,"target":"bedroom-light","power":"off","changed":true}
```

Ceiling responses also contain `channels`, e.g. `{"1":"on","2":"off"}`.
`ceiling-all` reports `power: "mixed"` if only one channel is on.
Status reads report `changed: false`. Repeating a requested state is safe and
does not toggle the device. A change is successful only after the state is read
back and confirmed.

Example from Python, running on this server with the project on its import path:

```python
from iot_client import request
print(request('status', 'bedroom-light'))
print(request('off', 'bedroom-light'))
```

Equivalent HTTP request (substitute the private token in your HTTP client):

```http
PUT /v1/devices/bedroom-light/state HTTP/1.1
Host: 127.0.0.1:8091
Authorization: Bearer <token>
Content-Type: application/json
Content-Length: 15

{"power":"off"}
```

## Command clients

```bash
bin/iot-control list
bin/iot-control status ceiling-all
bin/iot-control on ceiling-upper
bin/iot-control off bedroom-light

# Existing automation commands retain their syntax:
bin/light-control off bedroom-light
bin/tuya-control monitor-plug status
```

`IOT_API_URL` overrides the clients' URL for a different local port. The service
port is selected by `iot_api.py --port`. Normal commands use the server's device
configuration; they cannot select their own `--config` file. Maintenance commands
(`setup`, `scan`, `import-wizard`, `diagnose`) still use the local Tuya tools.

## Concurrency, failures, and credentials

Only one operation can run on each physical device at a time. All three ceiling
targets share one lock. Independent devices can run concurrently. Each operation
runs in a separate worker with a 40-second deadline; timed-out workers are killed
and their lock is released. Clients wait up to 45 seconds per HTTP request.

Errors return JSON `{"ok":false,"error":"..."}`:

| HTTP | Meaning |
| --- | --- |
| 400 / 415 | Invalid request / JSON content type required |
| 401 | Missing or incorrect API token |
| 403 | Browser Origin header is not allowed |
| 404 / 405 | Unknown route or target / unsupported method |
| 409 | Device busy; retry after the active operation finishes |
| 422 | Missing or invalid private device configuration |
| 502 | Device operation failed or state could not be verified |
| 503 | Worker could not run or returned an invalid response |
| 504 | Device operation exceeded the deadline |

A failed or timed-out write may have reached the device. Read the current state
before deciding what to do next; on/off operations are idempotent. The existing
monitor retries failed operations on its next poll. A successful manual command
does not disable future automation transitions.

Device configuration remains in `~/.config/tuya-automation/devices.json` (mode
600). Workers reread it for every request, so updated local keys take effect
without restarting the API. Device re-pairing can change a local key; refresh
the affected entry from the Tuya account when that happens. The API does not
automatically refresh keys or require the cloud for normal operation.

`api.token` is a locally generated bearer token for this HTTP server. It is
not a Tuya cloud access token and has no Tuya IoT Core expiry or refresh call.
The monitor's bedroom-light command and ceiling confirmation dialog use this
API, whose workers use the configured IP addresses and local keys over LAN.
The manual `python -m tinytuya wizard` provisioning step in SETUP.md contacts
the Tuya cloud to obtain local keys and optional DP mappings. Run it only when
provisioning or refreshing keys; automation does not invoke it.

To verify the lights-off paths without contacting real devices or the cloud,
run `.venv/bin/python -m unittest -v test_local_runtime` from the project root.
The regression uses private dummy credentials, blocks external networking in
the Python children, and simulates device sockets below TinyTuya's encryption
boundary. It also checks the watcher transitions and ceiling dialog decisions.
The GUI child is simulated by its exit code; countdown timing and button/close
behavior are covered separately by `test_ceiling_dialog`.

## Room temperature and humidity

The [hardware dashboard](../server-status/README-KO.md) reads the Smart IR
sensor directly over Tuya LAN 3.5 at `192.168.0.105:6668`. It is not a power
target registered in this API. DP 101 is temperature in tenths of a degree
Celsius; DP 102 is relative humidity in percent. The dashboard polls every
30 seconds and hides measurements after 120 seconds without a successful read.

Its sensor-only configuration is `~/.config/tuya-automation/room-sensor.json`
(user-owned, mode 600), mounted read-only at `/run/secrets/room_sensor`. The
dashboard does not mount the former cloud credential file or call Tuya Cloud.
The LAN guard permits the sensor's TCP 6668 connection from the host and the
dashboard Docker network (`172.19.0.0/16`); other LAN restrictions remain.

## Install and operate

The existing `.venv` and TinyTuya installation are used; no new dependencies.
Run from `/home/eruin/project/power-tuya`:

```bash
# Create a token once; existing tokens are preserved.
.venv/bin/python - <<'PY'
import os, secrets
from pathlib import Path
p = Path.home() / '.config/tuya-automation/api.token'
p.parent.mkdir(parents=True, exist_ok=True)
if not p.exists():
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_hex(32) + '\n')
PY
chmod +x bin/iot-control
install -m 644 systemd/iot-api.service ~/.config/systemd/user/
mkdir -p ~/.config/systemd/user/eruin-monitor-power.service.d
install -m 644 systemd/eruin-monitor-power.service.d/iot-api.conf \
  ~/.config/systemd/user/eruin-monitor-power.service.d/
systemctl --user daemon-reload
systemctl --user enable --now iot-api.service
bin/iot-control list
```

The monitor's `Wants`/`After` dependency starts the API first on future startup.
Existing running watcher processes pick up the client migration on their next
command; a watcher restart is not required for this migration. The morning-light
service example also starts after the API.

```bash
systemctl --user status iot-api.service
journalctl --user -u iot-api.service -n 30 --no-pager
systemctl --user restart iot-api.service
.venv/bin/python -B -m unittest discover -v
```

The API starts with the user's systemd session. Starting it before login requires
user lingering to be enabled on the host. `/health` checks service liveness;
query a device's state to check its actual availability.

Pre-migration copies of the three control modules and documentation are in
`backups/pre-iot-api/`. To revert, restore those modules, remove the monitor's
`iot-api.conf` dependency, and disable `iot-api.service`. Avoid reverting the
device configuration: it includes the repaired bedroom-light key.
