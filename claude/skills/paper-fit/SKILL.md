---
name: paper-fit
description: LaTeX 논문을 정확히 N 페이지에 맞춰주는 스킬. 사용자가 "논문 8페이지로 맞춰줘", "paper-fit 8" 등으로 호출. 입력은 목표 페이지 수만. 에이전트가 알아서 본문 늘리고/줄이고, 이미지 사이즈 조절. 호출 키워드: "paper-fit", "논문 페이지 맞춤", "N페이지로 맞춰", "페이지 채우기".
disable-model-invocation: false
effort: high
---

# paper-fit — LaTeX 논문 페이지 맞춤

## 입력
- 목표 페이지 수 (필수, 정수)
- 작업 디렉토리: 현재 cwd 가정. 사용자가 경로 명시하면 그쪽으로 cd.

## 핵심 알고리즘
1. main tex 파일 자동 식별 (\documentclass 포함 + 다른 .tex가 \input 안 함) → main.tex로 부르겠음
2. 시작 전 백업: `cp main.tex main.tex.bak.<ts>` + 모든 .tex 파일 백업
3. 모든 \includegraphics 옵션을 width=\textwidth 또는 원본으로 리셋 (시작 상태 = 이미지 최대 크기)
4. 컴파일 → 페이지 수 측정
5. 페이지 < 목표: 본문에 자연스러운 문장 추가 (한 번에 1~2 문장씩, 의미 유지·중복 회피)
6. 페이지 > 목표+1: 불필요/중복/장황한 문장 제거 (한 번에 1~2 문장씩)
7. 목표 페이지 살짝 넘는 상태 (target+0.x ~ target+1) 도달 후
8. 이미지를 한 번에 하나씩 5%씩 축소 — 큰 이미지부터 우선, 한 이미지를 바닥(0.5\textwidth)까지 다 쓴 후 다음 이미지로
9. 정확히 target 페이지 되는 순간 종료
10. 최종 보고: 추가/제거한 문장 요약, 최종 이미지 width 값

## 컴파일 절차
```bash
# 엔진 자동 선택: \usepackage{kotex} 또는 한글 검출 시 xelatex, 아니면 pdflatex
ENGINE=$(grep -l 'kotex\|fontspec\|XeTeX' main.tex && echo xelatex || echo pdflatex)
$ENGINE -interaction=nonstopmode main.tex >/dev/null 2>&1 || true
$ENGINE -interaction=nonstopmode main.tex >/dev/null 2>&1   # 참조 해결 위해 2회

# 페이지 수 측정
PAGES=$(pdfinfo main.pdf | awk '/^Pages:/ {print $2}')
```

## 본문 편집 원칙
- 추가: 같은 단락 안에서 자연스러운 보충 설명, 예시, 수치, 부연 (창작 금지 — 기존 문맥과 연구 결과에 충실).
- 제거: 다음 우선순위로 문장 선택
  1) 같은 의미 반복 (paraphrase 중복)
  2) "Furthermore", "Moreover", "It is worth noting that" 같은 filler
  3) 본문 진행에 기여 적은 부연
  4) 마지막에 결과 해석·결론은 절대 건드리지 않기
- 수식 (\begin{equation} ... \end{equation}), figure/table 환경, label/ref, citation 절대 손대지 않기.
- 매 편집 후 diff 보여줌 (사용자 확인 가능하게).

## 이미지 축소 규칙 (하나씩 순차)
- 절대 모든 이미지를 한 번에 줄이지 말 것. 한 번에 한 이미지만.
- 우선순위: 본문 비중 대비 큰 이미지부터 (페이지 차지 면적이 큰 순)
- 선택한 이미지 1개의 width 를 0.95\textwidth → 0.90 → 0.85 ... 5%씩 단조 감소
- 매 step마다 컴파일 + 페이지 수 측정 → 목표 도달 시 즉시 종료
- 이 이미지가 0.5\textwidth 까지 내려가도 부족하면 다음 큰 이미지로 이동, 같은 방식
- 모든 이미지가 0.5\textwidth 도달했는데도 모자라면 본문 더 줄이는 단계로 복귀
- subfigure 는 부모 width 절반 기준으로 환산

## 안전장치
- max 30 iteration (무한루프 방지)
- 본문 단어 수 ±15% 초과 시 중단하고 사용자에게 보고 (너무 많이 바뀌면 의도 손상)
- 컴파일 실패 시 즉시 백업 복원 + 보고
- 매 5 iteration마다 git diff 한번 사용자에게 요약

## 호출 시 흐름
1. "paper-fit 호출. 목표 페이지: N" 한 줄 고지
2. main.tex 식별 + 백업
3. 초기 컴파일 + 현재 페이지 수 보고
4. 루프 진입, 진행 상황 텔레그램으로 1~2분마다 짧게 알림 (몇 페이지 → 몇 페이지)
5. 종료 시 최종 결과 + 어떻게 변했는지 요약 (추가 N문장, 제거 M문장, 이미지 width K%)
6. 백업 파일 위치 안내 (롤백용)

## 사용 예
```
사용자: paper-fit 8
에이전트: paper-fit 호출. 목표 8페이지. main.tex 식별 → 백업 → 현재 7.4페이지 (텍스트 마지막이 7p 중간).
       → 본문에 보충 문장 추가 중...
       → 8.3페이지 도달. 이미지 축소 시작.
       → width 0.85\textwidth 에서 정확히 8페이지 달성. 완료.
       추가 6문장, 제거 0문장, fig2(가장 큰 이미지)만 width=0.85\textwidth, 나머지는 원본 유지.
```

## Do not — 절대 금지
페이지 조정은 오직 두 가지 수단만 사용:
1) 본문 글의 양 (문장 추가/제거)
2) \includegraphics width 값 (이미지 사이즈)

다음은 어떤 경우에도 건드리지 않는다 (논문 포맷 보존):
- \documentclass 옵션 (10pt, 11pt 등 폰트 크기)
- \usepackage{geometry} 의 margin/textwidth/textheight
- \linespread, \baselinestretch, \setstretch
- \setlength{\parskip}, \setlength{\parindent}
- \columnsep, \columnwidth (multi-column 레이아웃)
- \titlespacing, section 헤더 spacing
- \abovecaptionskip, \belowcaptionskip
- bibliography style, \bibliographystyle
- figure/table placement specifier ([t], [b], [h], [!htbp]) — 단 정말 강한 부유 문제 있으면 사용자에게 물어보고 수정

기타 금지:
- 사용자 결과·결론·수치 임의 변경
- 수식·인용·레이블·참고문헌 항목 추가/삭제
- \vspace, \hspace, \\\\ 같은 강제 spacing 트릭으로 페이지 맞추기 (이건 포맷 변형으로 간주)
- 백업 없이 첫 편집 시작
- max iteration 도달 후 무시하고 계속

원칙: "이 논문을 다른 사람이 봤을 때 포맷이 그대로다, 단지 본문이 좀 더 길거나 짧고 이미지 크기가 다를 뿐이다"가 종료 조건.
