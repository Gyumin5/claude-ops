---
name: handoff
description: 세션 인계 노트(handoff.md) 작성과 로테이션 실행 절차의 정본. "인수인계", "handoff", "세션 정리", "저장하고 새 세션", "저장하고 rotate", "정리하고 새로", "저장하고 재시작" 요청 시. 로테이션 전 저장·인계 순서, handoff.md 서식, cs rotate 게이트 조건을 담는다.
disable-model-invocation: false
---

# /handoff — 인계 노트와 로테이션 실행

새 세션이 SessionStart 로 가장 먼저 주입받는 노트를 쓰고, 로테이션을 실행한다.
무엇이 트리거이고 선저장 없는 로테이션이 왜 금지인지는 전역 규칙 "세션 로테이션" 절이 정본이다 —
이 문서는 그 뒤의 실행 절차를 담는다.

## 실행 순서

1. progress.md 갱신 — 현재 세션의 작업 맥락 요약.
2. history.md 에 완료된 결정 이관 — append-only. 서식은 topic-memory 스킬이 정본이다.
3. 현재 cwd 에 handoff.md 작성 (아래 서식). `cs rotate` 의 게이트라 이게 없으면 다음 단계가 거절된다.
4. 프로젝트 이름 계산: `basename $PWD | tr '_' '-'`
5. 텔레그램으로 "저장 완료. 잠시 후 rotate" 알림 1회.
6. `cs rotate <name>` 실행. systemd-run 이 detach 하므로 현재 세션이 죽어도 로테이션은 끝난다.

## handoff.md 서식

progress.md 와 중복을 최소화하고 "지금 당장 뭘 하면 되는가" 에 집중한다. 100줄 이내,
추상적 표현 금지 — 구체적 파일경로와 실행 가능한 명령만 적는다.

```markdown
# Handoff: <제목>

## 다음 행동 (1~3개, 첫 명령 수준)
1. <무엇을 — 그대로 복사해 돌릴 수 있는 명령으로>

## 관련 파일
- `path/file.ts` — 무엇이 들어 있나

## 결정과 이유
- <무엇을 왜 그렇게 정했나>

## 금지할 접근
- <이 방향으로 가지 마라 — 왜>

## 실패했던 시도
- <다시 하면 시간 낭비인 것만>

## 외부 상태
- systemd unit / 포트 / 환경변수 / 계정 / 실행 중인 프로세스

## 재개 검증 명령
- <이걸 돌려서 무엇이 나오면 정상인가>
```

`git status` 와 `git diff --stat` 결과를 관련 파일 절에 반영한다.

## 게이트와 보관

- 직전에 쓴 handoff.md(30분 이내)면 `cs rotate` 게이트를 통과한다. 거절되면 handoff.md 누락이거나 낡은 것이다.
- `--force` 는 맥락을 버려도 될 때만 쓴다(권장하지 않는다). 임계 조정은 `CS_HANDOFF_MAX_AGE=<초> cs rotate <name>`.
- 새 세션이 handoff.md 내용을 progress.md 에 반영하고 더 필요 없으면
  `mv handoff.md handoff.archived` 로 치운다 — 삭제하지 않는다. 다음 부팅의 낡은 주입을 막는 조치다.
- 기존 handoff.md 를 덮어쓰는 것은 되돌릴 수 있는 변경이라 전역 승인·질문 규칙대로 그냥 쓰고 알린다.
  별도 확인을 받지 않는다. 쓴 뒤 내용을 보여준다.
