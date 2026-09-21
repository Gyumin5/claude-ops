# claude-ops

Claude Code 세션 여러 개를 한 리눅스 기계에서 상시 돌리고, 텔레그램으로 조작하고,
멈추거나 한도에 걸렸을 때 알아서 되살아나게 하는 운영 도구 모음이다.

세션 하나가 systemd user 서비스 하나다. 각 세션은 자기 텔레그램 봇에 붙어 있어서,
바깥에서 그 봇에 말을 걸면 그 세션이 답한다. 그 위에 감시 타이머와 훅이 얹혀 있어
세션이 멈추면 다시 띄우고, 한도에 걸리면 풀리는 시점에 다시 깨운다.

세우는 절차는 SETUP.md 에 있다. 봇 만들기부터 첫 대화까지 위에서 아래로 한 번이다.

## 무엇이 들어 있나

- `systemd/user/` — 세션 유닛과 감시 타이머. 세션 유닛의 본보기는 `claude-dotfiles.service`
  이고, 새 세션 유닛은 `claude-service-template` 이 폴더 경로만 받아 만들어 준다.
- `bin/` — 운영 명령. 세션 통제(`claude-sessions`, `claude-soft-restart`), 감시
  (`claude-session-healthcheck`, `claude-stall-guard`, `claude-watchdog`,
  `claude-api-5xx-watcher`), 한도 관측과 재개(`claude-quota-observe`,
  `rate-limit-recovery`), 세션끼리 메시지를 넣는 통로(`claude-userbot-send`),
  외부 모델 세 개를 붙여 토론시키는 `ai-debate`.
- `claude/hooks/` — Claude Code 훅. 긴 프롬프트 차단, 한도 차단과 큐 적재,
  장시간 작업을 세션 밖으로 내보내는 가드, 산출물 경로 가드 따위.
- `claude/CLAUDE.md`, `claude/rules/RATIONALE.md` — 모든 세션이 공유하는 작업 규칙과
  그 규칙이 생긴 근거. 규칙 본문의 `[R##]` 이 근거 파일의 같은 번호를 가리킨다.
- `userbot/` — 세션끼리 메시지를 넣는 통로의 설정 본보기. 쓰지 않아도 된다.
- `lib/` — 여러 감시기가 같은 판단을 하도록 규칙을 한 곳에 모은 것.
- `tests/` — 위의 것들이 갈라지지 않는지 보는 검사.
- `install.sh` — 위를 제자리에 심는다.

## 기본 테스트 명령

저장소 기본 테스트 명령의 정본은 이 절이다. 다른 곳에 같은 명령을 적지 않는다.

저장소 루트에서 다음 한 줄을 돌린다. 대상은 `tests/` 밑의 파이썬 파일 전부다.

```
bash -c 'r=0; for f in tests/*.py; do python3 "$f" || r=1; done; exit "$r"'
```

각 파일은 자기 안에서 통과·실패를 세고 마지막에 종료코드를 낸다. 위 반복문은
하나라도 실패하면 1로 끝난다. 러너·Makefile·설정 파일은 두지 않는다 — 파일을 늘리는
대신 셸 한 줄로 끝나는 일이다.

기계마다 달라지는 자리는 검사 안에서 건너뛰기를 찍고 그 사유를 출력한다.

## 전제

- 리눅스. `systemctl --user` 가 도는 환경이어야 한다(Ubuntu 20.04 이상에서 쓰고 있다).
- 파이썬 3.8 이상. 표준 라이브러리만 쓴다.
- Claude Code CLI. 텔레그램 연결에는 bun 이, 토론 도구에는 외부 모델 CLI 가 더 필요하다.

## 안 들어 있는 것

토큰과 방 번호는 이 저장소가 다루지 않는다. 각자 자기 기계에 직접 넣는다 — 자리는
SETUP.md 에 적어 뒀다. 특정 회사·조직에 묶인 자동화와 개인 일정에 묶인 보고 도구는
빼고 옮겼다.

규칙 문서에 나오는 `session-a`·`proj-b` 같은 이름은 실제로 돌던 세션을 가리키던 자리다.
사고 경위가 문장의 근거라 지우지 않고 일반 이름으로 옮겼다.

## 라이선스

MIT. LICENSE 파일에 있다.
