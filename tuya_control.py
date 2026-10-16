"""Small LAN-only Tuya plug controller. Never print keys or raw device errors."""
import argparse
import contextlib
import getpass
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time

from iot_client import APIError, request

DEFAULT_CONFIG = Path.home() / ".config/tuya-automation/devices.json"


def validate(item):
    if not isinstance(item, dict):
        raise ValueError("장치 설정은 JSON 객체여야 합니다.")
    for field in ("id", "ip", "local_key", "version"):
        if field not in item:
            raise ValueError("설정에 id, ip, local_key, version이 필요합니다.")
    if not isinstance(item["id"], str) or not item["id"] or item["id"].startswith("REPLACE"):
        raise ValueError("실제 Device ID를 입력하세요.")
    ipaddress.IPv4Address(item["ip"])
    if not isinstance(item["local_key"], str) or len(item["local_key"].encode()) != 16 or item["local_key"].startswith("REPLACE"):
        raise ValueError("Local Key는 실제 16바이트 값이어야 합니다.")
    if type(item["version"]) not in (int, float) or item["version"] not in (3.1, 3.2, 3.3, 3.4, 3.5):
        raise ValueError("프로토콜 버전은 3.1~3.5 중 하나여야 합니다.")
    dp = item.get("switch_dp", 1)
    if type(dp) is not int or not 1 <= dp <= 255:
        raise ValueError("switch_dp는 1~255 정수여야 합니다.")
    return item


def load_config(path, name):
    with path.open() as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("설정 파일은 현재 사용자 소유이며 권한 600이어야 합니다.")
        data = json.load(stream)
    if not isinstance(data, dict) or name not in data:
        raise ValueError("설정 파일에 요청한 장치 이름이 없습니다.")
    return validate(data[name])


def setup(path, name):
    if path.exists():
        raise ValueError("기존 설정이 있습니다. 파일을 직접 편집하세요.")
    item = validate({
        "id": input("Device ID: ").strip(),
        "ip": input("장치 IPv4 주소: ").strip(),
        "version": float(input("프로토콜 버전 (검색 결과): ")),
        "switch_dp": int(input("전원 DP [1]: ") or "1"),
        "local_key": getpass.getpass("Local Key (화면에 표시하지 않음): "),
    })
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({name: item}, stream, indent=2)
        stream.write("\n")
    print("설정 저장 완료 (권한 600).")


def import_wizard(source, path, source_name, target_name, ip, version, switch_dp):
    with source.open() as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Wizard 파일은 현재 사용자 소유이며 권한 600이어야 합니다.")
        devices = json.load(stream)
    matches = [item for item in devices
               if isinstance(item, dict) and item.get("name") == source_name]
    if len(matches) != 1:
        raise ValueError("Wizard 파일에서 장치 이름을 하나로 특정할 수 없습니다.")
    source_item = matches[0]
    item = validate({"id": source_item.get("id"), "ip": ip,
                     "local_key": source_item.get("key"), "version": version,
                     "switch_dp": switch_dp})
    if path.exists():
        with path.open() as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("설정 파일은 현재 사용자 소유이며 권한 600이어야 합니다.")
            data = json.load(stream)
        if not isinstance(data, dict) or target_name in data:
            raise ValueError("대상 장치 설정이 이미 있거나 설정 형식이 잘못되었습니다.")
    else:
        data = {}
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data[target_name] = item
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    os.chmod(path, 0o600)
    print(f"{target_name} 설정 가져오기 완료 (IP {ip}, 권한 600).")


def read_state(device, dp):
    response = device.status()
    if not isinstance(response, dict) or "Error" in response:
        raise RuntimeError("장치 응답 실패: IP, Local Key, 버전을 확인하세요.")
    value = response.get("dps", {}).get(str(dp))
    if type(value) is not bool:
        raise RuntimeError("전원 DP를 확인할 수 없습니다. switch_dp를 확인하세요.")
    return value


def control(item, action, factory, attempts=3, sleep=time.sleep):
    desired = action == "on"
    dp = item.get("switch_dp", 1)
    for attempt in range(attempts):
        device = None
        try:
            device = factory(item["id"], item["ip"], item["local_key"],
                             version=item["version"], connection_timeout=3,
                             connection_retry_limit=1, persist=False)
            before = read_state(device, dp)
            if action == "status" or before == desired:
                return {"power": "on" if before else "off", "changed": False}
            device.set_status(desired, switch=dp)
            sleep(1)
            after = read_state(device, dp)
            if after != desired:
                raise RuntimeError("명령 후 전원 상태가 일치하지 않습니다.")
            return {"power": "on" if after else "off", "changed": True}
        except (RuntimeError, OSError, ValueError, TypeError):
            if attempt == attempts - 1:
                raise RuntimeError("3회 시도 후 제어 실패. IP, Local Key, 버전, 전원 DP를 확인하세요.") from None
            sleep(1)
        finally:
            if device is not None:
                device.close()


def diagnose(item, factory, connector=socket.create_connection):
    try:
        connection = connector((item["ip"], 6668), timeout=3)
        connection.close()
    except TimeoutError:
        return {"tcp_6668": "timeout", "hint": "공유기 AP/client isolation 또는 잘못된 IP를 확인하세요."}
    except OSError as error:
        labels = {111: "refused", 113: "no-route", 101: "network-unreachable"}
        return {"tcp_6668": labels.get(error.errno, "unreachable"),
                "hint": "장치 IP, 같은 LAN/VLAN, AP/client isolation을 확인하세요."}

    attempts = []
    ordered_versions = list(dict.fromkeys((item["version"], 3.5, 3.4, 3.3, 3.2, 3.1)))
    for version in ordered_versions:
        device = None
        try:
            device = factory(item["id"], item["ip"], item["local_key"],
                             version=version, connection_timeout=3,
                             connection_retry_limit=1, persist=False)
            response = device.status()
            dps = response.get("dps") if isinstance(response, dict) else None
            if isinstance(dps, dict):
                boolean_dps = sorted(int(key) for key, value in dps.items()
                                     if str(key).isdigit() and type(value) is bool)
                return {"tcp_6668": "open", "version": version,
                        "boolean_dps": boolean_dps}
            attempts.append({"version": version, "result": "rejected"})
        except (OSError, ValueError, TypeError):
            attempts.append({"version": version, "result": "no-response"})
        finally:
            if device is not None:
                with contextlib.suppress(Exception):
                    device.close()
    return {"tcp_6668": "open", "protocol": "not-detected", "attempts": attempts,
            "hint": "Local Key/Device ID를 확인하고 Smart Life 앱을 완전히 닫은 뒤 다시 시도하세요."}


def save_detected(path, name, version, boolean_dps):
    item = load_config(path, name)
    item["version"] = version
    if item.get("switch_dp") not in boolean_dps:
        if len(boolean_dps) == 1:
            item["switch_dp"] = boolean_dps[0]
        else:
            raise ValueError("전원 DP를 자동 결정할 수 없습니다.")
    with path.open() as stream:
        data = json.load(stream)
    data[name] = item
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def deadline(_signum, _frame):
    raise TimeoutError("전체 실행 제한 시간을 초과했습니다.")


def main():
    parser = argparse.ArgumentParser(description="Tuya 스마트플러그 LAN 제어")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="LAN 장치 검색 (전원 변경 없음)")
    config = sub.add_parser("setup", help="Local Key를 숨겨 입력하고 권한 600으로 저장")
    config.add_argument("name", nargs="?", default="monitor-plug")
    imported = sub.add_parser("import-wizard", help="TinyTuya Wizard 결과에서 키를 안전하게 가져오기")
    imported.add_argument("source_name")
    imported.add_argument("target_name")
    imported.add_argument("ip")
    imported.add_argument("--source", type=Path,
                          default=Path.home() / ".config/tuya-automation/wizard/devices.json")
    imported.add_argument("--version", type=float, default=3.3)
    imported.add_argument("--switch-dp", type=int, default=1)
    plug = sub.add_parser("plug")
    plug.add_argument("name")
    plug.add_argument("action", choices=("status", "on", "off"))
    diagnostic = sub.add_parser("diagnose", help="전원 변경 없이 TCP와 프로토콜 버전 진단")
    diagnostic.add_argument("name")
    # README's short form: tuya-control monitor-plug status
    args_in = sys.argv[1:]
    if len(args_in) == 2 and args_in[1] in ("status", "on", "off"):
        args_in.insert(0, "plug")
    args = parser.parse_args(args_in)
    try:
        if args.command == "setup":
            setup(args.config, args.name)
            return 0
        if args.command == "import-wizard":
            import_wizard(args.source, args.config, args.source_name, args.target_name,
                          args.ip, args.version, args.switch_dp)
            return 0
        if args.command == "plug":
            if args.config != DEFAULT_CONFIG:
                raise APIError('configuration-managed-by-api')
            result = request(args.action, args.name)
            print(json.dumps({k: result[k] for k in ('power', 'changed') if k in result}))
            return 0
        logging.getLogger("tinytuya").disabled = True
        import tinytuya
        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(45)
        if args.command == "scan":
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
                found = tinytuya.deviceScan(False, 20, poll=False)
            result = [{"id": d.get("gwId", d.get("id")), "ip": d.get("ip", ip),
                       "version": d.get("version")} for ip, d in found.items()]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result else 1
        item = load_config(args.config, args.name)
        if args.command == "diagnose":
            result = diagnose(item, tinytuya.OutletDevice)
            if "version" in result:
                save_detected(args.config, args.name, result["version"], result["boolean_dps"])
                result["saved"] = True
            print(json.dumps(result, ensure_ascii=False))
            return 0 if "version" in result else 1
    except APIError as error:
        print(json.dumps({'ok': False, 'error': error.code}))
    except FileNotFoundError:
        print("설정 파일이 없습니다. tuya-control setup을 실행하세요.", file=sys.stderr)
    except ImportError:
        print("TinyTuya가 없습니다. requirements.txt를 설치하세요.", file=sys.stderr)
    except Exception:
        # Exception text may contain credentials or device payloads.
        print("실패: 설정 파일/권한, IP, Local Key, 프로토콜 버전 및 LAN 연결을 확인하세요.", file=sys.stderr)
    finally:
        signal.alarm(0)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
