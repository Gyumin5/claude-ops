---
name: handoff
description: 세션 인수인계 문서(handoff.md) 생성. "인수인계", "handoff", "세션 정리" 등 슬래시 호출 시.
disable-model-invocation: true
---

# /handoff

현재 세션 작업을 다음 세션/에이전트가 이어갈 수 있게 정리.

## 절차
1. 대화에서 추출: 목표 / 완료 / 진행중 / 미해결 / 수정 파일
2. 프로젝트 루트에 `handoff.md` 생성 (아래 템플릿)
3. `git status`, `git diff --stat` 결과 포함

## 템플릿
```markdown
# Handoff: [제목]

## 목표
(전체 목표)

## 완료
- [x] ...

## 진행 중
- [ ] ... (현재 상태: ...)

## 미해결
- ⚠️ ... (결정 필요)
- 🐛 ... (버그)

## 수정된 파일
- `path/file.ts` - 변경 요약

## 다음 세션 시작 시
1. handoff.md 읽기
2. (구체적 다음 단계)
3. 완료 후 handoff.md 삭제
```

## 규칙
- 100줄 이내, 추상적 표현 금지 (구체적 파일명·명령어만)
- 기존 handoff.md 있으면 덮어쓸지 사용자 확인
- 생성 후 내용 보여주기
