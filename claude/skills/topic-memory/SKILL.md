---
name: topic-memory
description: 토픽 단위 장기 기억 관리와 기록 서식의 정본. 토픽 파일 회수(rehydrate)·갱신, 장기·고위험 작업 Goal 작성, 태스크 완료·중단·폐기·인계 종결 처리, history.md 작성·정정·월별 보관을 담당. "이거 예전에 하던 거", "맥락 이어서", "기억해둬", "토픽 정리", "active.md 갱신", "히스토리에 적어", "결정 기록", "태스크 종결", "Goal 작성", TOPIC-UPDATE 반영 시. progress/history/topic/active.md 사이 역할분리 규칙의 단일 진입점.
disable-model-invocation: false
effort: high
---

# /topic-memory — 토픽 기억 회수·갱신 (단일 writer)

목적: 컨텍스트 압축이 무손실이 아니라 세부를 잃는 문제를, markdown 외부 기억 + 재주입 규율로 완화.
DB·blackboard 없이 markdown 만 쓴다. memory 파일을 write 하는 주체는 메인 세션 하나뿐.

## 파일 역할분리 (엄격 — 중복 금지)
- progress.md : 재개용 실행 상태(현재 어디까지·다음 행동·막힌 것).
- history.md : append-only 결정 로그(ADR). 되돌아볼 가치 있는 결정만.
- history/active.md : SessionStart 주입용 compact view. 활성 토픽·최근 progress·유효 결정 요약. append 아님, 재생성.
- memory/MEMORY.md : 토픽/영구사실 인덱스(제목 + 한 줄).
- memory/topics/<topic-id>.md : 토픽 상세(스키마는 memory/topics/_SCHEMA.md).

규칙: 같은 정보를 두 파일에 중복 쓰지 않는다. 결정은 history, 진행상태는 progress, 토픽 누적 지식은 topic 파일.

줄 상한·활성 작업 상한·active.md 의 분량과 주입 경계는 전역 규칙이 정본이다(R44·R45, 그리고
전역 "progress.md / history.md" 절). 이 스킬은 그 값을 다시 적지 않는다 — 사본을 두면 한쪽이
낡고, 낡은 쪽을 세션이 사실로 읽는다.

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
- 포맷과 규칙은 아래 "history.md 기록 서식" 절이 정본이다.
- 커밋에 `[DECISION]` 마커가 있으면 git-commit-post 훅이 `~/.claude/state/history-stubs/<세션>.md`
  에 (미배번) 스텁을 쌓는다. 훅은 history.md 에 직접 쓰지 않는다 — 번호를 매기고 본문을 적어
  history.md 로 합치는 것은 그 세션이 한다(R08). 그래서 `[DECISION]` 커밋 뒤 history.md 에
  항목이 없는 것은 훅 고장이 아니라 안 합친 상태다. 확인은
  `grep -c '^- hash:' ~/.claude/state/history-stubs/<세션>.md` 한 줄이다.

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
- 이 스크립트가 (a) 활성 토픽 파일들의 summary, (b) progress.md 의 상단 상태, (c) history.md 최근 결정 몇 개를 합쳐 compact view 로 history/active.md 를 재생성한다. 담을 양과 주입 경계는 전역 규약을 따른다(현재 유효한 결정 10~20개 + 항상 적용되는 제약).
- LLM 호출 없음. 언제 도나: Stop 훅 조건부(마지막 기록 후 30분 경과 / [DECISION]·TOPIC-UPDATE 감지 / 파일 변경) + 수동. (PreCompact 훅은 active.md 재생성을 하지 않는다 — 압축 직전 최신 보장은 실제 파일 갱신 이후 경로인 Stop/수동으로 일원화. 코드리뷰 Critical#2 반영.)
- SessionStart 훅이 active.md 를 주입하므로, Stop 훅/수동으로 갱신된 최신 active.md 를 다음 세션이 이어받는다. (압축 시점의 즉시 최신화는 보장하지 않음.)

## 6. 새 토픽 생성 기준 (남발 금지)
- 여러 세션에 걸쳐 재개될 가능성이 있는 작업/주제만 토픽화.
- 일회성 Q&A·단발 수정은 토픽 만들지 않음(progress 로 충분하면 progress 만).
- topic_id 는 kebab-case, 짧고 유일하게. _SCHEMA.md 형식 복사해 시작.

## 7. 장기·고위험 작업의 Goal 서식

progress.md 의 `## Goal` 에 아래 여섯 답을 한 줄씩 적는다. 대상은 고위험·장기·재개 가능성
있는 작업이고, 작은 작업은 생략한다.

- 결과(Outcome): 무엇이 만들어지면 끝인가
- 입력(Inputs): 무엇이 있어야 시작 가능
- 의존(Depends on): 어떤 외부 파일·API·합의에 묶이는가
- 검토자(Verifier): self / critic / verifier(ai-debate) / test 중 하나 — 검토 주체를 기록하는
  값이고 실제 테스트 출력을 대신하지 않는다
- 운영 상태 확인: healthcheck / 해당 없음 — 세션 생존·배포 연결·알림 전달 주장에만 쓰고
  코드 변경의 완료 판정에는 안 쓴다
- 완료기준(Done when): 어떤 신호로 끝났다고 본다

## 8. 태스크 종결 처리

- active 끝 → 결정은 history 로 이관하고 progress 에서 삭제
- paused → progress 유지, status 갱신
- abandoned → 폐기 사유를 history 에 기록하고 progress 에서 삭제
- handoff → progress 갱신만

Blockers 항목의 종결은 삭제가 아니라 이동이다 — `### 완료(최근)` 으로 옮기고 거기서 오래된
것부터 덜어낸다(R51). 결정가치가 있으면 history 로 가고 그 구역엔 한 줄만 남긴다.

## 9. history.md 기록 서식

append-only 결정 로그다. 경량 ADR(Y-statement 영감).

"결정" 이 아니어도 다시 조사하면 아까운 조사결과(재조사 비용이 크고 압축으로 사라지면 실제로
곤란했던 것만)는 가벼운 `## [날짜] #NN 발견: <요약>` 형식으로 기록할 수 있다. 태그 1개,
`- 내용: <2~4줄>` 만으로 충분하고 결정/근거 짝을 강제하지 않는다. 남발 금지 — 아무 조사나
다 넣으면 history.md 자체가 못 쓰게 된다. 프로젝트 고유 기술절차는 여기 말고 해당 프로젝트
CLAUDE.md 로.  [R46]

포맷
```
## [YYYY-MM-DD] #NN <한 줄 요약>
tags: 주제1, 주제2
- 결정: (필수)
- 근거: (필수, 한 줄)
- 되돌릴 조건: (있으면)
- 실험/실패: (다시 시도 시 시간 낭비인 경우만)
- Prediction: (사고 가드 관련 변경에서만 필수) <사고 클래스>가 <기간> 동안 telemetry/history 상 재발하지 않으면 성공, 재발하면 재검토.
```

규칙
- 결정 + 근거는 짝. "결정" 만 있는 항목 금지
- 실패는 모두 남기지 말 것. "다시 시도하면 시간 낭비하는 실패" 만 negative decision 으로 기록
- 항목당 3~6줄, 태그 1~3개
- 정정은 supersedes 체인(`supersedes: YYYY-MM-DD/#NN`)
- 크기 관리(루트 상한과 월별 보관 시점)는 전역 규칙 "progress.md / history.md" 절이 정본이다

Prediction 적용 범위: prompt-length / large-read / secrets-scan / rate-limit / progress-updater
drift / cross-session leak / stuck-loop / bash-latency 등 명시적 사고 가드 변경. 일반 commit·
기능 추가에는 강제하지 않는다.  [R47]
