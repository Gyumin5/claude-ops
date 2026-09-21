---
name: handoff-verify
description: 작업 완료 후 fresh-context 서브에이전트로 빌드/테스트/린트 검증. "검증", "확인", "handoff verify" 등 슬래시 호출 시.
disable-model-invocation: true
---

# /handoff-verify

작업 완료 후 서브에이전트를 fresh context로 띄워 빌드/테스트/린트 검증. 기존 컨텍스트 편향 없이 실제 동작 확인.

## 절차

### 1. 프로젝트 타입 감지
| 감지 파일 | 검증 명령 |
|---|---|
| `package.json` | `npm run build && npm test` |
| `Cargo.toml` | `cargo check && cargo test` |
| `CMakeLists.txt` | `cmake --build build && ctest --test-dir build` |
| `setup.py` / `pyproject.toml` | `python -m pytest` |
| `*.tex` (main) | `latexmk -pdf main.tex` |
| `package.xml` (catkin) | `catkin_make` 또는 `colcon build` |

CLAUDE.md에 Build/Test 섹션 있으면 우선.

### 2. 서브에이전트 실행
Agent tool, subagent_type=general-purpose, 아래 프롬프트:
```
프로젝트 fresh context 검증.
1. 프로젝트 CLAUDE.md 읽기 (있으면)
2. 명령 순서대로 실행: 타입체크 → 린트 → 빌드 → 테스트
3. 각 단계 결과 보고: ✅ PASS 또는 ❌ FAIL + 에러 분류
4. 에러 분류:
   - Fixable (import/타입/린트): 자동 수정 시도, 최대 3회
   - Non-Fixable (설계/외부 의존성): 보고만
5. 최종 보고 형식:
## 검증 결과
- TypeCheck/Lint/Build/Test: ✅/❌
## 자동 수정
- 수정 내용
## Non-Fixable
- 이슈 목록
```

### 3. 결과 요약
사용자에게 보고. 전체 PASS면 "검증 완료", 아니면 수정 내용/이슈 정리.

## 규칙
- 자동 수정 최대 3회. 수정 후 반드시 재실행
- 검증 대상 불명확하면 사용자 확인
