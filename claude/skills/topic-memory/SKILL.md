---
name: topic-memory
description: 토픽 단위 장기 기억 관리. 작업 맥락을 잊지 않도록 토픽 파일 회수(rehydrate)와 갱신을 담당. "이거 예전에 하던 거", "맥락 이어서", "기억해둬", "토픽 정리", "active.md 갱신", TOPIC-UPDATE 반영 시. progress/history/topic/active.md 사이 역할분리 규칙의 단일 진입점.
disable-model-invocation: false
effort: high
---

# /topic-memory — 토픽 기억 회수·갱신 (단일 writer)

목적: 컨텍스트 압축이 무손실이 아니라 세부를 잃는 문제를, markdown 외부 기억 + 재주입 규율로 완화.
DB·blackboard 없이 markdown 만 쓴다. memory 파일을 write 하는 주체는 메인 세션 하나뿐.

## 파일 역할분리 (엄격 — 중복 금지)
- progress.md : 재개용 실행 상태(현재 어디까지·다음 행동·막힌 것). hard limit 120줄.
- history.md : append-only 결정 로그(ADR). 되돌아볼 가치 있는 결정만.
- history/active.md : SessionStart 주입용 compact view(30~80줄). 활성 토픽·최근 progress·유효 결정 요약. append 아님, 재생성.
- memory/MEMORY.md : 토픽/영구사실 인덱스(제목 + 한 줄).
- memory/topics/<topic-id>.md : 토픽 상세(스키마는 memory/topics/_SCHEMA.md).

규칙: 같은 정보를 두 파일에 중복 쓰지 않는다. 결정은 history, 진행상태는 progress, 토픽 누적 지식은 topic 파일.

## 1. 토픽 선택 (작업 시작 시)
1. 요청 키워드로 memory/MEMORY.md 인덱스를 훑어 관련 topic_id 를 찾는다.
2. aliases 로도 매칭(topic 파일 frontmatter 의 aliases).
3. 매칭되면 memory/topics/<topic-id>.md 를 Read(전체는 짧으니 통째 OK, 단 매우 크면 앞부분+decisions만).
4. 매칭 없고 지속될 작업이면 새 토픽 생성 후보로 둔다(2장에서 생성은 신중히).

## 2. Rehydrate (맥락 회수)
- topic 파일에서 summary / current_state / decisions / open_questions / next_actions 를 읽어 현재 작업 머리에 반영.
- 서브에이전트로 위임할 땐 이 발췌를 프롬프트에 직접 넣는다(경로만 주지 않는다).
- 여러 토픽이 얽히면 각 파일의 summary 만 먼저 훑고 필요한 것만 깊게 읽는다.

## 3. 갱신 규칙 (언제 무엇을 쓰나)
과잉기록 방지 — 매 턴 쓰지 않는다. 아래 트리거에서만.

progress.md 갱신 (실행상태 변화 시):
- 방향/범위 변경, 결정 번복, 새 제약, 작업 단위 완료, 주제 전환.
- 주의: git-commit-post 훅과 claude-progress-updater 타이머도 progress.md 를 건드린다.
  손으로 편집할 때는 AUTO_TAIL_SNAPSHOT 블록(`<!-- AUTO_TAIL_SNAPSHOT begin/end -->`)은 건드리지 말 것(훅 소유 구역).

history.md append (결정 확정 시):
- "결정 + 근거" 짝으로만. 3~6줄, 태그 1~3개. 포맷은 CLAUDE.md history.md 규약 따름.
- 커밋에 [DECISION] 마커가 있으면 git-commit-post 훅이 자동 append 하므로 중복 append 금지.

memory/topics/<topic-id>.md 갱신 (토픽 지식 누적 시):
- TOPIC-UPDATE 블록을 받거나, 스스로 토픽에 대해 새로 알게 되었을 때.
- frontmatter 의 updated_at(KST), status, confidence 갱신.
- summary 는 짧게 유지, 상세는 current_state/decisions 로.

MEMORY.md 인덱스:
- 새 토픽 생성 시 한 줄 추가. 토픽 폐기 시 줄 제거 또는 status 표기.

## 4. TOPIC-UPDATE 블록 반영 (서브 → 메인)
서브에이전트가 반환한 `<<<TOPIC-UPDATE ...>>>` 블록을 메인이 파싱해:
1. topic_id 로 memory/topics/<id>.md 를 열고(없으면 _SCHEMA 기반 새로 생성),
2. summary_delta → summary 에 병합, new_decisions → decisions 에 append,
   open_questions/artifacts/next_actions 갱신, confidence·updated_at 갱신.
3. 결정 가치가 크면 history.md 에도 ADR 한 줄(중복 아니게).
`<<<TOPIC-UPDATE none>>>` 이면 아무것도 쓰지 않는다.

## 5. active.md 재생성 절차
active.md 는 손으로 매번 쓰지 말고 스크립트로 재생성한다:
- 실행: `~/.claude/hooks/update_active_memory.sh <cwd>` (또는 배포된 절대경로).
- 이 스크립트가 (a) 활성 토픽 파일들의 summary, (b) progress.md 의 상단 상태, (c) history.md 최근 결정 몇 개를 합쳐 30~80줄 compact view 로 history/active.md 를 재생성한다.
- LLM 호출 없음. 언제 도나: Stop 훅 조건부(마지막 기록 후 30분 경과 / [DECISION]·TOPIC-UPDATE 감지 / 파일 변경) + 수동. (PreCompact 훅은 active.md 재생성을 하지 않는다 — 압축 직전 최신 보장은 실제 파일 갱신 이후 경로인 Stop/수동으로 일원화. 코드리뷰 Critical#2 반영.)
- SessionStart 훅이 active.md 를 주입하므로, Stop 훅/수동으로 갱신된 최신 active.md 를 다음 세션이 이어받는다. (압축 시점의 즉시 최신화는 보장하지 않음.)

## 6. 새 토픽 생성 기준 (남발 금지)
- 여러 세션에 걸쳐 재개될 가능성이 있는 작업/주제만 토픽화.
- 일회성 Q&A·단발 수정은 토픽 만들지 않음(progress 로 충분하면 progress 만).
- topic_id 는 kebab-case, 짧고 유일하게. _SCHEMA.md 형식 복사해 시작.
