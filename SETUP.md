# 스마트플러그 실행 안내

구현 범위: LAN 검색, 안전한 설정 입력, 상태 조회, 전원 켜기/끄기.
모니터 감시 원본은 `monitor-power.sh`이며 기존 사용자 서비스에서 사용한다.
조명 일정 예제는 대상 조명 및 시간이 정해진 후 활성화한다.

상태 조회와 전원 제어는 이제 `iot-api.service`를 거친다. 먼저 [API.md](API.md)의
API 설치 절차를 완료한다. 검색·설정·진단 명령은 유지보수용으로 직접 LAN에 접근한다.

## 설치 및 실행

프로젝트 경로: `/home/eruin/project/power-tuya`

```bash
cd /home/eruin/project/power-tuya
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
bin/tuya-control scan
bin/tuya-control setup
bin/tuya-control monitor-plug status
bin/tuya-control monitor-plug on
bin/tuya-control monitor-plug off
```

`setup`은 Device ID, IP, 검색된 프로토콜 버전, 전원 DP(기본 1), Local Key를
입력받는다. Local Key는 숨겨 입력하며 명령행 인수로 전달하지 않는다.
설정은 `~/.config/tuya-automation/devices.json`에 권한 600으로 저장한다.
기존 설정은 덮어쓰지 않는다. 예제의 값은 실제 장치 값으로 교체해야 한다.
전원 차단 명령은 연결 부하를 확인한 뒤 실행한다.

## Local Key 확보

LAN 검색만으로 Local Key는 얻을 수 없다. Smart Life/Tuya Smart 앱 등록 후,
Tuya 개발자 플랫폼에서 앱 계정을 연결한 프로젝트의 인증 정보를 이용하여
TinyTuya wizard로 가져올 수 있다. 이미 확보한 키가 있으면 이 과정은 생략한다.

다음 명령은 로컬 터미널에서 직접 실행한다. Wizard 출력과 생성 파일에는 비밀 값이
포함되므로 채팅, Git, 로그로 공유하지 않는다.

```bash
mkdir -p ~/.config/tuya-automation/wizard
chmod 700 ~/.config/tuya-automation/wizard
cd ~/.config/tuya-automation/wizard
umask 077
/home/eruin/project/power-tuya/.venv/bin/python -m tinytuya wizard
```

공식 절차: https://github.com/jasonacox/tinytuya#tuya-device-preparation

## 동작 및 장애 처리

- 조회된 전원 DP가 boolean일 때만 전원 명령을 전송한다.
- 이미 원하는 상태이면 명령을 생략하고, 변경 후 다시 조회해 검증한다.
- 최대 3회 시도하며 API의 장치 작업 프로세스에는 40초 제한을 적용한다.
- 출력은 전원 상태와 변경 여부만 포함하며 장치 원본 응답과 비밀 값은 출력하지 않는다.
- 검색은 UDP 알림을 이용하며 전원 제어 및 장치별 상태 조회를 하지 않는다.
- 검색 실패 시 같은 LAN/VLAN인지, AP isolation과 UDP 6666/6667/7000 및 TCP 6668을 확인한다.
- 이 서버의 `lan_guard`는 지정된 목적지 외 LAN 연결을 차단한다. 자동 검색 요청이나
  UDP 수신이 차단되면 온라인 장치도 검색 결과에 나오지 않을 수 있다. MAC을 ARP로
  대조하고 `sudo nft list table inet lan_guard`에서 해당 IP의 TCP 6668 허용을 확인한다.
  Docker에서 직접 조회할 때는 `forward_guard`의 출발지 대역과 목적지도 확인한다.
- IP는 공유기에서 DHCP 예약을 권장한다. 재등록 시 Local Key가 바뀔 수 있다.
- 별도 장치 설정 경로는 API 서비스의 `iot_api.py --config /경로/devices.json`으로 지정한다.
  일반 상태/전원 클라이언트는 서버의 설정을 사용한다.

테스트: `.venv/bin/python -m unittest -v`

실내 온습도 센서는 전원 API와 별도 설정을 사용한다. 현재 Smart IR은
`192.168.0.105`, LAN 3.5, 온도 DP 101·습도 DP 102이며 설정은
`~/.config/tuya-automation/room-sensor.json`(mode 600)이다.
대시보드의 조회·장애 처리 절차는 [대시보드 운영 문서](../server-status/README-KO.md)를
참고한다. 일반 조회는 클라우드나 IoT Core 토큰 갱신을 사용하지 않는다.

## 모니터 연동

사용자 서비스의 `ExecStart`를 `/home/eruin/project/power-tuya/monitor-power.sh`로 설정한다.
스크립트 변경 후 다음을 실행한다.

```bash
systemctl --user restart eruin-monitor-power.service
journalctl --user -u eruin-monitor-power.service -n 30 --no-pager
```

Tuya 장애는 감시를 종료하지 않는다. API 조회 오류일 때는 현재 전원을 유지한다.
복구 후에도 실패한 전원 명령은 다음 poll에서 다시 시도한다.
연동을 끄려면 `systemctl --user edit eruin-monitor-power.service`에 추가한다.

```ini
[Service]
Environment=TUYA_ENABLED=0
```

이후 서비스를 재시작한다. 완전 롤백은 백업 스크립트를 복원하고 서비스를 재시작한다.
기존 USB sudoers 규칙은 다른 사용처 확인 없이 삭제하지 않는다.

## 아침 조명 일정 예제 (기본 비활성)

조명을 devices.json에 등록하고 전원 DP 및 LAN on/off를 검증한 후 적용한다.
`systemd/morning-light.timer.example`의 시각과 시간대를 실제 일정으로 바꾼다.
예제는 평일 07:00 Asia/Seoul이며 사용자의 확정 일정이 아니다.
주말은 별도 `OnCalendar=Sat,Sun *-*-* HH:MM:SS Asia/Seoul` 행으로 추가할 수 있다.

```bash
install -m 600 config/morning-light.env.example ~/.config/tuya-automation/morning-light.env
# 위 파일의 TUYA_LIGHT_NAME을 실제 등록된 조명 이름으로 수정
mkdir -p ~/.config/systemd/user
install -m 644 systemd/morning-light.service ~/.config/systemd/user/
install -m 644 systemd/morning-light.timer.example ~/.config/systemd/user/morning-light.timer
# 설치한 timer의 OnCalendar 시각·시간대 수정 후 검증
systemd-analyze --user verify ~/.config/systemd/user/morning-light.service ~/.config/systemd/user/morning-light.timer
systemctl --user daemon-reload
systemctl --user enable --now morning-light.timer
systemctl --user list-timers morning-light.timer
```

한 일정당 한 번 실행하고 반복 재점등하지 않는다. `Persistent=false`이므로
부팅/로그인 시 놓친 아침 점등을 뒤늦게 실행하지 않는다. 사용자 systemd가 실행 중이어야 한다.
밝기와 색온도 제어는 아직 구현하지 않았다. 소등 일정과 일출 계산도 후속 범위다.
중지: `systemctl --user disable --now morning-light.timer`.
