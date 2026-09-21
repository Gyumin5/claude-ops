---
name: project-review
description: 현재 프로젝트 CLAUDE.md 점검하고 빠진 항목 보완 제안. "/project-review" 슬래시 호출 시.
disable-model-invocation: true
---

# /project-review

현재 프로젝트 CLAUDE.md 점검 → 보완.

## 절차

1. CLAUDE.md 읽기 (루트 + 서브디렉토리 모두)
2. 실태 대조하여 누락 항목 찾기:
   - 빌드 시스템 있는데 Build 섹션 없음
   - 테스트 프레임워크 있는데 Test 섹션 없음
   - 린터/포매터 설정 있는데 Style 섹션 없음
   - launch / config 등 건드리면 안 되는 파일이 보호 안 됨
   - 주요 디렉토리 설명 빠짐
3. 보완 제안: 항목별 "추가할까요?" 확인. 승인된 것만 추가
4. 정리: 더 이상 해당 안 되는 규칙 / 중복 규칙 → 병합 제안
