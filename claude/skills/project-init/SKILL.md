---
name: project-init
description: 프로젝트 분석 후 맞춤형 CLAUDE.md (+ 선택적 AGENTS.md) 자동 생성. "/project-init" 슬래시 호출 시.
disable-model-invocation: true
---

# /project-init

현재 프로젝트 디렉토리 분석 → 프로젝트 유형에 맞는 CLAUDE.md 생성.

## 절차

### 1. 자동 탐지
- 빌드 시스템: catkin, cmake, npm, cargo, pip 등
- 언어: C++, Python, Rust, TypeScript 등
- 프레임워크: ROS, Flask, React 등
- 테스트: gtest, pytest, jest 등
- 린터/포매터: .clang-format, black, ruff, eslint 등
- 기존 CLAUDE.md / AGENTS.md / .cursorrules
- 주요 디렉토리 구조

### 2. 사용자 확인
탐지 결과 요약 + 추가 질문: 스택 맞는지 / 추가 규칙 / 특수 워크플로우 (원격 빌드 등)

### 3. CLAUDE.md 생성 (프로젝트 루트)
포함:
- Stack: 언어, 프레임워크, 버전
- Build: 정확한 명령 (단일/전체)
- Test: 정확한 명령 (단일/전체)
- Style: 기존 포매터/린터 참조
- Do not: 금지 패턴 (자주 실수할 수 있는 것)
- Done means: 완료 기준 (빌드 성공, 테스트 통과 등)

### 4. 선택: AGENTS.md
멀티 AI 도구 호환용 공용 규칙. CLAUDE.md와 중복 피하고 범용만 포함.

### 5. 서브디렉토리 CLAUDE.md (해당 시)
LaTeX 논문, 실험/데이터 디렉토리가 있으면 그 안에도 별도 생성.

## 규칙
- 200줄 이내, 추상적 문구("깔끔하게", "잘 테스트해") 금지. 구체적 명령만
- 기존 파일 있으면 덮어쓰지 말고 병합 제안
- 생성 후 내용 보여주기
