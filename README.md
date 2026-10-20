# Tuya 홈 자동화

## 현재 구성 (2026-10-06)

모니터 플러그·침대 조명·천장등은 로컬 IoT API와 TinyTuya LAN으로 조회·제어한다.
천장등 확인창은 `ceiling_dialog.py`의 GTK 창에서 남은 시간을
`30초 → 29초 → 28초…`로 표시한다. `끄기` 또는 무응답 30초는 소등,
`켜두기` 또는 창 닫기는 취소이며 PC 오프라인일 때는 소등하지 않는다.

실내 온도·습도는 별도 [대시보드](../server-status/README-KO.md)가 Smart IR
`192.168.0.105:6668/TCP`에서 직접 읽는다. 전원 API의 제어 대상에는 추가하지 않았다.
조명과 온습도 조회 모두 일반 실행 중에는 Tuya 클라우드 호출을 사용하지 않는다.
클라우드는 로컬 키 확보·갱신을 위한 수동 유지보수 때만 사용한다.

## Local IoT API (active)

All normal status and power commands now go through the authenticated API at
`http://127.0.0.1:8091`. The API runs as `iot-api.service`; the computer monitor,
ceiling confirmation dialog, and existing command wrappers are API clients.
See [API.md](API.md) for endpoints, authentication, installation, and operations.

```bash
bin/iot-control list
bin/iot-control status bedroom-light
bin/iot-control off bedroom-light
```

## KingoGPT용 로컬 조명 명령

`/home/eruin/project/power-tuya/bin/light-control`은 등록된 조명만 제어하는 로컬 IoT API 클라이언트다.
HTTP 서버는 이 서버의 `127.0.0.1:8091`에서만 접근할 수 있다.
KingoGPT가 로컬 명령 실행 권한을 갖는 경우 이 절대 경로를 호출하면 된다.

```bash
/home/eruin/project/power-tuya/bin/light-control list
/home/eruin/project/power-tuya/bin/light-control status ceiling-all
/home/eruin/project/power-tuya/bin/light-control on ceiling-upper
/home/eruin/project/power-tuya/bin/light-control off ceiling-all
/home/eruin/project/power-tuya/bin/light-control on bedroom-light
```

대상은 `bedroom-light`, `ceiling-upper`(스위치 1), `ceiling-lower`(스위치 2),
`ceiling-all`이다. 동작은 `status`, `on`, `off`다. 결과는 `ok`, `target`, `power`,
`changed`가 담긴 JSON이며 천장등은 채널별 상태도 포함한다. 이미 요청한 상태면
재전송하지 않고, 변경 후 상태를 다시 읽어 확인한다. 실패 시 종료 코드 1과
`api-unavailable`, `device-unavailable`, `device-timeout` 등의 오류 코드만 반환해
장치 키나 원본 응답이 출력되지 않는다.
비밀 설정은 기존 `~/.config/tuya-automation/devices.json`을 권한 600으로 사용한다.


아래는 구현 이력과 남은 작업 계획이다.
2026-09-06: 스마트플러그 LAN 검색·설정·상태 조회·전원 제어 도구를 구현했다.
실행 방법은 [SETUP.md](SETUP.md)를 참고한다.
2026-09-06: monitor-plug의 실제 off/on 명령 응답을 확인했다. 모니터 감시 스크립트에
Tuya 전원 제어, 복귀 대기, 실패 재시도 및 상태 조회 오류 처리를 구현했다.
PC 시작·종료에 연동되는 침대 조명과 천장등은 활성 상태다. 별도 아침 일정은
활성화하지 않은 systemd 예제로 제공한다.
밝기·색온도·점진 점등·일출 및 휴일 일정은 후속 구현 범위다.

## 목표

1. 메인 PC가 오프라인이 되면 서브모니터 연결 장치의 스마트플러그를 끈다.
2. 메인 PC가 다시 온라인이 되면 스마트플러그를 켜고, 잠시 기다린 뒤 모니터를 깨운다.
3. 지정한 아침 시간에 Tuya 조명을 자동으로 켠다.
4. 필요하면 취침 시간 자동 소등, 평일·주말 시간표, 일출 기준 점등을 추가한다.
5. 인터넷 장애가 있어도 가능한 한 내부 LAN에서 동작하게 한다.

## 권장 구성

- 서버: 유선 LAN 연결 유지
- Tuya 장치: 같은 공유기의 2.4GHz Wi-Fi 사용
- 제어 방식: Python `tinytuya`를 이용한 로컬 LAN 제어
- 일정 실행: systemd timer 사용
- 장치 설정 및 비밀 값: 소스 코드와 분리된 권한 제한 설정 파일에 저장
- 로그: systemd journal에 기록

서버가 유선이고 장치가 무선이어도 같은 LAN/VLAN에서 서로 통신할 수 있으면 문제없다.
게스트 Wi-Fi, AP isolation 또는 IoT VLAN 방화벽이 통신을 막으면 별도 허용 규칙이 필요하다.

## 장치 도착 후 준비

- [ ] `Smart Life` 또는 `Tuya Smart` 앱에 장치 등록
- [ ] 장치 펌웨어 업데이트
- [ ] 서버와 통신 가능한 2.4GHz Wi-Fi에 연결
- [ ] 공유기에서 장치별 DHCP 예약 설정
- [ ] 각 장치의 이름과 용도 기록
- [ ] Device ID, Local Key, IP 주소, Tuya 프로토콜 버전 확보
- [ ] 스마트플러그의 정격 전압·전류가 연결 장치에 충분한지 확인
- [ ] 스마트플러그의 정전 복구 상태를 `마지막 상태` 또는 목적에 맞는 값으로 설정

장치를 초기화하거나 앱에 다시 등록하면 Local Key가 바뀔 수 있다.

## 예정 파일 구조

```text
/home/eruin/project/power-tuya/
├── README.md
├── bin/
│   ├── tuya-control
│   └── morning-light
├── config/
│   └── devices.example.json
└── systemd/
    ├── morning-light.service
    └── morning-light.timer
```

실제 비밀 설정 파일은 예를 들어 아래 위치에 두고 파일 권한을 `600`으로 제한한다.

```text
/home/eruin/.config/tuya-automation/devices.json
```

Git 저장소나 로그에는 Local Key, Tuya 계정 정보, API Secret을 기록하지 않는다.

## 공통 제어 도구 구현

장치별 코드를 여러 스크립트에 복사하지 않고 하나의 명령으로 통합한다.

예상 사용 형태:

```bash
tuya-control monitor-plug on
tuya-control monitor-plug off
tuya-control bedroom-light on
tuya-control bedroom-light off
tuya-control bedroom-light brightness 40
tuya-control bedroom-light status
```

구현 시 포함할 기능:

- 장치 이름을 Device ID에 매핑
- 장치 IP 자동 탐색 또는 고정 IP 사용
- 장치별 프로토콜 버전 지정
- 명령 전후 상태 확인
- 통신 실패 시 짧은 간격으로 2~3회 재시도
- 실행 제한 시간 설정
- 비밀 값이 로그에 출력되지 않도록 처리
- 이미 원하는 상태라면 불필요한 재명령 생략

## 모니터 전원 연동

현재 파일:

```text
/home/eruin/project/power-tuya/monitor-power.sh
```

현재 메인보드 USB 루트 허브는 `nops`로 표시되어 실제 5V 전원 차단을 지원하지 않는다.
기존 `uhubctl` 호출을 Tuya 제어 명령으로 교체했다. 사용자 서비스가 프로젝트의 `monitor-power.sh`를 직접 실행한다.
모니터 플러그는 `TUYA_CONTROL`, 침대 조명과 낮 시간 천장등 점등은
`LIGHT_CONTROL`(기본 `/home/eruin/project/power-tuya/bin/light-control`)을 호출한다.
천장등 자동 소등 확인창은 사용자 확인이 필요한 별도 동작으로 유지한다.

절전 순서:

1. 메인 PC 오프라인 판정
2. 화면 절전 및 오디오 음소거
3. `monitor-plug off`
4. 결과를 journal에 기록

복귀 순서:

1. 메인 PC 온라인 판정
2. `monitor-plug on`
3. 연결 장치 부팅을 위해 약 5~10초 대기
4. 화면 깨우기 및 오디오 복원
5. 결과를 journal에 기록

스마트플러그 통신 실패만으로 모니터 감시 서비스 전체가 종료되지 않게 한다.

전환 완료 후 기존 USB 제어 sudoers 규칙이 더 이상 사용되지 않는지 확인하고 제거 여부를 결정한다.

## 아침 조명 자동화

기본안은 systemd timer로 정해진 시각에 실행하는 것이다.

초기 예시 정책:

- 평일: 지정한 아침 시간에 켜기
- 주말: 별도의 시간 사용 또는 자동 점등 생략
- 초기 밝기: 낮게 시작
- 가능하면 수 분 동안 단계적으로 밝기 증가
- 일정 시간이 지난 뒤에도 사람이 없을 경우 자동 소등 기능은 추후 추가

구현 전에 결정할 항목:

- [ ] 대상 조명 이름
- [ ] 평일 점등 시각
- [ ] 주말 점등 시각 또는 사용 안 함
- [ ] 시작 밝기와 최종 밝기
- [ ] 색온도 또는 조명 색상
- [ ] 자동 소등 시각
- [ ] 공휴일 적용 방식
- [ ] 고정 시각 또는 실제 일출 시각 기준

일출 기준을 선택하면 위치와 시간대를 명시하고, 일출 계산 실패 시 사용할 기본 시각도 정한다.
서버 시간대가 UTC이므로 사용자 지역 시간 기준 일정에는 명시적인 시간대 처리가 필요하다.

수동으로 조명을 끈 경우 자동화가 즉시 다시 켜지 않도록 한 번 실행 후 반복 명령을 보내지 않는다.
필요하면 `오늘 자동화 건너뛰기` 상태 파일이나 수동 재정의 기능을 추가한다.

## 테스트 절차

### 스마트플러그

- [ ] 앱에서 수동 켜기·끄기 확인
- [ ] 서버에서 상태 조회 확인
- [ ] 서버 명령으로 끄고 실제 전원이 차단되는지 확인
- [ ] 30초 후 다시 켜서 연결 장치가 정상 부팅되는지 확인
- [ ] 인터넷을 차단해도 LAN 제어가 되는지 확인
- [ ] IP 변경 또는 장치 응답 지연 시 재시도 확인
- [ ] 메인 PC 오프라인·온라인 전환과 함께 동작하는지 확인

### 조명

- [ ] 켜기·끄기 확인
- [ ] 지원하는 경우 밝기와 색온도 조절 확인
- [ ] 테스트용 timer를 2~3분 뒤로 설정해 실행 확인
- [ ] 평일·주말 일정 확인
- [ ] 재부팅 후 timer 유지 확인
- [ ] 수동 조작과 자동화가 충돌하지 않는지 확인

## 장애 및 복구 원칙

- Tuya 제어 실패가 모니터 감시 루프를 중단시키지 않게 한다.
- 스마트플러그가 꺼진 상태에서 서버가 재부팅돼도 다음 온라인 판정 시 다시 켤 수 있어야 한다.
- 설정 파일이 없거나 손상되면 장치 명령을 보내지 않고 명확한 오류만 기록한다.
- 로컬 제어가 불가능한 모델이면 Home Assistant의 공식 Tuya 연동 또는 Tuya Cloud API를 대안으로 검토한다.
- 자동화 적용 전 기존 스크립트와 systemd unit을 백업한다.
- 문제 발생 시 Tuya 연동을 비활성화하고 기존 모니터 절전 기능만 유지할 수 있게 구성한다.

## 작업 완료 기준

- [ ] 스마트플러그와 조명을 서버에서 각각 상태 조회 및 제어 가능
- [ ] Local Key 등 비밀 값의 파일 권한 확인
- [ ] 모니터 절전·복귀 연동 실기기 테스트 통과
- [ ] 아침 조명 timer 실기기 테스트 통과
- [ ] 서비스 재시작 및 서버 재부팅 후 정상 동작
- [ ] 로그에 비밀 값이 노출되지 않음
- [ ] 운영 방법과 수동 명령을 이 문서에 최종 반영

## 구현 및 검증 상태 (2026-09-06)

- `monitor-power.sh`: 화면 절전 → 미디어 일시정지·음소거 → 플러그 끄기.
- 온라인: 플러그 켜기 확인 → 7초 대기 → 화면 깨우기·오디오 복원.
- 전원 제어 실패 시 다음 poll에서 재시도하며 감시 루프를 계속한다.
- 상태 API 오류/잘못된 JSON은 전원 상태를 유지한다. `main_pc.online` boolean만 인정한다.
- 시작 시 플러그 상태를 재확인한다. 성공한 전환은 다음 상태 전환까지 반복하지 않는다.
- `TUYA_ENABLED=0`으로 플러그 연동을 비활성화하고 화면·오디오 절전만 사용할 수 있다.
- `TUYA_WAKE_DELAY` 기본 7초, `POLL_SECONDS` 기본 5초.
- 기존 제어 테스트 8개 및 감시 동작 테스트 5개 통과.
- 물리적인 PC 오프라인/온라인 전환, 인터넷 단절, 서버 재부팅 검증은 아직 남아 있다.
- 조명 실기기 검증 및 일정 활성화는 대상 장치·시간대·시각 결정 후 진행한다.

## 저녁 PC 종료 시 조명 켜기

메인 PC가 온라인에서 오프라인으로 전환될 때 한국 시간 21:00 이상 또는 다음 날 06:00 미만이면 bedroom-light(192.168.0.106)에 on 명령을 보낸다. 06:00 이상 21:00 미만이면 천장스위치의 `switch_1`(위쪽)에 on 명령을 보낸다. 꺼진 조명은 켜고 이미 켜진 조명은 유지한다. 성공 후 반복 점등하지 않으며 실패하면 다음 상태 확인 때 재시도한다. PC가 다시 온라인이면 재시도를 취소하고 기존 소등 동작을 수행한다. 감시 시작 시 이미 오프라인이거나 상태 조회에 실패한 경우에는 점등하지 않는다. 종료는 기존 상태 보고 누락 감지 기준을 사용하므로 네트워크 단절도 오프라인으로 판단할 수 있다. 시간대는 TUYA_LIGHT_TIMEZONE으로 변경할 수 있으며 기본값은 Asia/Seoul이다.

## PC 시작 시 천장등 확인 팝업

PC 온라인 시 기존 모니터 전원 복귀, 침대전구 소등, 화면 깨우기 및 오디오
복원이 끝나면 `ceiling_prompt.py`를 백그라운드에서 한 번 실행한다.
별도 30초 대기는 없다. 천장스위치의 두 채널 중 켜진 채널이 있을 때만
서버 데스크톱에 30초 제한 확인 팝업을 띄운다. 안내 문구는 자동 소등까지
남은 시간을 `30초 → 29초 → 28초…`로 갱신한다. `끄기` 또는 30초 무응답이면
현재 켜진 채널을 끄고 결과를 확인한다. `켜두기` 또는 창 닫기는 취소한다.
팝업 표시 실패, PC 오프라인 또는 상태 조회 실패 시 소등하지 않는다.
PC가 계속 온라인인 동안에는 팝업을 반복하지 않으며 다음 온라인 주기에
다시 확인한다. 서비스 시작 시 이미 PC가 온라인이면 기존 조명과 동일하게 실행한다.

천장등의 조회·소등은 `ceiling_prompt.py` → 로컬 IoT API → LAN 작업자 경로다.
설정의 `ceiling-switch` 항목에 유효한 Device ID, Local Key, IP 및 버전이
필요하다. 통신 오류 시 해당 온라인 주기의 자동 소등은 실패로 기록한다.
모니터 감시는 팝업을 기다리지 않고 계속된다. 기존 저녁 침대전구 점등은 유지된다.
읽기 전용 확인: `.venv/bin/python ceiling_prompt.py --check`.
팝업은 `/usr/bin/python3`와 시스템 GTK 3 바인딩(`python3-gi`)을 사용한다.

2026-10-06 검증: 자동화 테스트 59개 통과. 로컬 회귀 시험은 실제 장치 대신
모의 LAN 소켓을 사용하며 클라우드·외부 통신 시도 0건을 확인했다.
카운트다운 시험은 실제 경과 시간 기준 30초 제한과 버튼·닫기 동작을 확인한다.

```bash
.venv/bin/python -B -m unittest discover -v
.venv/bin/python -B -m unittest -v test_local_runtime test_ceiling_dialog
```
