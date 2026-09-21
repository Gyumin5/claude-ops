---
name: route
description: 요청을 분류해 실행 경로(모델·인라인/서브)를 자동 선정. 무거운 작업(설계·다단계 디버깅·리서치·긴 코드변경·대량 반복)을 착수하기 전에 자동 적용. "이거 무거운데", "어떻게 처리", "opus 써야 하나", "위임", "route" 등 판단이 필요할 때. 즉답 가능한 가벼운 요청엔 스킵.
disable-model-invocation: false
effort: high
---

# /route — quota 절약 우선 라우팅 (2026-07-13 ai-debate run-20260713T064600Z 개정)

목적: 무거운 작업이라고 자동으로 서브에이전트에 위임하는 게 아니라, 최소 quota
소비 경로를 고른다. 서브에이전트는 메인+서브 합산으로 같은 공유 quota를 쓰므로
"컨텍스트 격리"만으로는 위임 정당화가 안 된다. 기본값은 인라인. 서브는 (a) 사용자
명시 요청, (b) 범위축소·요약으로도 메인 컨텍스트가 150k+로 폭발할 위험이 명확한
경우만. 과설계(dispatcher/DB/systemd worker)는 범위 밖.

## 0. 오버라이드 우선 (다른 모든 규칙보다 먼저)

사용자가 실행 방식을 명시하면 루브릭 무시하고 그대로 따른다.
- "이건 그냥 네가 해" / "빠르게" / "인라인으로" → inline 고정.
- "opus로 해" / "제대로 파봐" / "서브에이전트 써" → opus-sub 고정.
- "haiku로" / "기계적으로 쭉" → haiku-sub 고정.
오버라이드가 있으면 아래 분류를 건너뛰고 3장(위임)만 수행.

## 1. 분류 루브릭

기본은 인라인. 아래 (a)/(b) 조건 중 하나가 명확할 때만 서브를 검토한다 — "파일이
몇 개다", "도구 호출이 몇 번이다" 류의 개수 기준으로 자동 위임하지 않는다.

inline (메인 세션에서 직접 처리) — 기본값, 대부분의 작업:
- 즉답 가능한 질문, 분석/요약, 코드 수정, 좁은 탐색.
- 조사가 좀 걸리더라도 rg/head/tail/offset로 범위를 줄이고 필요한 부분만 읽으면
  메인 컨텍스트가 크게 안 커질 것으로 판단되면 계속 인라인.

sonnet-sub / opus-sub (agents/sonnet-worker, agents/opus-worker):
- (a) 사용자가 명시적으로 위임을 요청한 경우.
- (b) 범위축소·부분읽기·요약으로도 메인 컨텍스트가 150k+ 로 폭발할 위험이 명확하고,
  서브 1개가 요약만 반환하는 쪽이 총 quota 상 더 싸다고 판단될 때만.
- 모델 선택은 작업 성격 기준(설계·깊은 인과분석이면 opus-worker, 단순 정리·훑기면
  sonnet-worker) — "무거워 보이니 opus로 승격"하는 자동 상향은 하지 않는다.

haiku-sub (agents/haiku-batch):
- 대량 반복(수십~수백 항목 동일처리), 포맷변환, 기계적 치환 — 판단 거의 불필요한 일.
  이 경우는 규모가 커도 haiku가 적합하면 haiku 유지(opus로 승격 안 함).

R&D 세션(peer/session-a 등)도 예외 없음 — 병렬조사가 필요해 보여도 (a)/(b) 조건
없이 서브를 늘리지 않는다.

## 2. 동시 서브에이전트 상한

기본 상한 1개. 이 상한은 세션 종류와 무관하게 적용된다(R&D 세션 포함).
정말 독립적인 병렬조사가 필요하면 사용자에게 먼저 확인한다.

## 3. 위임 절차

경로 선언 (필수, 위임/처리 직전 한 줄):
```
[route] inline — <근거>
[route] sonnet-sub — <근거>
[route] opus-sub — <근거>
[route] haiku-sub — <근거>
```

### 3.1 착수 전 memory 회수 (모든 경로 공통)
- 관련 토픽이 있으면 memory/topics/<topic-id>.md 를 먼저 Read.
- 토픽 id 를 모르면 memory/MEMORY.md 인덱스에서 aliases/키워드로 찾는다.
- 서브에이전트에는 이 토픽 파일의 요약·결정·열린질문을 프롬프트에 발췌해 넣는다(파일 경로만 주지 말고 핵심을 붙여줌 — 서브는 부모 히스토리를 상속하지 않는다).

### 3.2 서브에이전트 호출 (sonnet-sub / opus-sub / haiku-sub)

사전 확인 (필수 — 존재/지원 전제 금지):
- 위임 전에 대상 subagent_type(sonnet-worker / opus-worker / haiku-batch)이 실제로 등록돼 있는지 확인한다.
  (Agent 도구가 제공하는 사용가능 subagent 목록, 또는 ~/.claude/agents/ 존재 여부.)
- 없으면 임의 이름으로 Agent 를 호출하지 말 것. 아래 fallback 사다리를 따른다.
- background 지원을 전제하지 말 것. run_in_background 가 안 되면 동기 실행(run_in_background=false).

Fallback 사다리 (대상 에이전트 부재 / Agent 호출 실패 / background 미지원 시):
1. opus-worker 없음 → general-purpose(또는 존재하는 범용 Agent)에 model=opus 로 위임.
   sonnet-worker 없음 → general-purpose model=sonnet, 없으면 인라인 처리(격리 이득만 포기).
2. 범용 Agent 도 못 쓰면 → 인라인으로 직접 수행하되, escalate-on-evidence(2장) 임계를
   넘어서면 사용자에게 "서브에이전트 미가용, 인라인 진행 중" 한 줄 보고.
3. haiku-batch 없음 → general-purpose model=haiku, 그것도 없으면 인라인 배치 처리.
4. Agent 호출이 에러로 실패하면 재시도 1회, 그래도 실패면 인라인 fallback + 실패 사유를
   사용자에게 보고(무응답 진행 금지). 텔레그램 채널이면 reply 로 보고.

단일 writer 원칙:
- memory/progress.md/history/active.md 를 write 하는 주체는 메인 세션 하나뿐이다.
- 서브에이전트(및 fallback 범용 에이전트)는 이 파일들을 절대 write 하지 않는다.
  결과는 TOPIC-UPDATE 블록(3.3)으로만 반환하고, 메인이 반영한다.
- fallback 으로 인라인 처리하는 경우에도 progress.md 갱신은 메인이 규약대로 한다.

호출 방법:
- sonnet-sub → Agent(subagent_type 로 sonnet-worker 지정), opus-sub → opus-worker, haiku-sub → haiku-batch (각각 존재 확인 후).
- run_in_background 기본값(백그라운드) 사용(지원 확인). 완료 알림 오면 결과를 사용자에게 중계.
- 서브 프롬프트 템플릿:

```
[작업] <한 줄 목표>

[맥락 — 토픽 memory 발췌]
- topic_id: <id>
- 현재 상태(current_state): <핵심 2~4줄>
- 유효 결정(decisions): <관련된 것만>
- 열린 질문(open_questions): <있으면>
- 관련 파일/산출물(artifacts): <경로>

[해야 할 일]
<구체 지시. 완료 판정 기준 포함>

[제약]
- memory/progress/history 파일을 직접 write 하지 마라. 아래 TOPIC-UPDATE 블록만 반환.
- 산출물(생성/수정 파일)은 절대경로로 명시해 반환하라(핸들 유실 대비 — 부모 압축/재시작 시 서브 핸들 소멸).
- 큰 파일 통째 read 금지(offset/limit, head/tail). sudo·블로킹 명령 금지.
```

### 3.3 TOPIC-UPDATE 반환 형식 (서브 → 메인)
서브에이전트는 작업 끝에 반드시 아래 블록을 마지막 메시지에 포함한다. 메인만 이걸 memory 에 반영한다(단일 writer).

```
<<<TOPIC-UPDATE topic_id=<id>>>>
summary_delta: <요약에 더할 한두 줄>
new_decisions:
  - 결정: <...>
    근거: <...>
open_questions:
  - <...>
artifacts:
  - <절대경로> — <역할>
next_actions:
  - <다음 행동>
confidence: high|medium|low
<<<END TOPIC-UPDATE>>>
```
업데이트가 없으면 `<<<TOPIC-UPDATE none>>>` 한 줄만 반환.

### 3.4 서브의 외부 크로스체크 (ai-debate) — 능동 허용
서브워커(sonnet-worker/opus-worker/haiku-batch)는 작업 중 진짜 결정 지점(설계 갈림길, 불확실한 트레이드오프, 최신 외부 정보 필요, 되돌리기 어려운 위험한 선택)에서 ai-collaborate 단일 진입(ai-debate CLI)을 스스로 호출해 세컨드 오피니언을 받을 수 있다.
- 실행: Bash 로 `ai-debate "<짧은 task>"` 또는 `cat <file> | ai-debate "검토: ..."`. 호출 직전 한 줄 고지, 수 분 소요(timeout 인지).
- 단 파일 수정 자체를 codex/gemini 에 위임하는 것은 금지 — ai-debate 는 조언·검토용이고 실제 편집은 워커(claude)가 한다.
- 일상적·자명한 작업엔 호출 금지(과호출 방지). haiku-sub 는 원칙적으로 거의 부르지 않는다.

## 4. 핸들 유실 대비 (중요)
서브에이전트는 부모 세션의 자식이라 부모가 압축/재시작되면 SendMessage 핸들이 소멸한다.
- 서브에게 "결과는 반드시 파일 경로로 남기고, 진행상황을 짧게 파일에 적으라"고 지시.
- 위임 직후 메인은 progress.md 의 Resume Hints 에 "opus-sub 위임 중: <목표>, 산출물 예정 경로 <path>" 한 줄을 남긴다(핸들 잃어도 재개 가능).

## 5. 결과 중계
- 서브 완료 후 메인이 요약해 사용자에게 전달. 서브의 긴 원문 로그를 그대로 붙이지 않는다.
- 텔레그램 채널 요청이면 reply 도구로 전달(전송 규칙 준수).
- TOPIC-UPDATE 가 있으면 /topic-memory 절차로 반영.
