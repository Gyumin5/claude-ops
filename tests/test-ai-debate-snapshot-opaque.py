#!/usr/bin/env python3
"""토론 호출 하나가 코덱스 토큰을 적게 쓰게 하는 장치들을 고정한다.

왜 있나 — 실측 둘이다.

(가) 그림 덩어리. 홈 회차 코덱스 원문 148건 30.9MB 중 base64 덩어리가 19.8MB 로
64.2% 이고 그게 11건에서만 나온다. 전부 라운드1 이고, 제일 큰 건은 브리프가 검토
대상으로 지목한 조판본 여덟 쪽을 그림으로 렌더링해 3.42MB 를 찍은 것이다. 같은
조판본의 글자는 68,580바이트다. 파일당 상한 8MB 에 안 걸려 기존 관문을 다 통과했다.

(나) 나눠 읽기. 옆 기계 24시간 실측에서 codex-ask 66회 중 호출 하나가 모델 요청을
44번 던져 6,900만 토큰을 썼다. 요청마다 그때까지의 대화 전체가 다시 실리므로 값이
요청 수의 제곱으로 큰다 — 호출당 총토큰 중앙값이 요청 1회 35,012 · 8회 625,960 ·
20회 4,070,416 · 44회 39,370,829 이다. 그 호출이 읽은 내용은 11만 토큰 규모인데
250줄씩 44번에 나눠 읽어 44번 재전송됐다. 요청 28회를 넘은 호출 넷이 그날의 48.7% 다.

그래서 고정하는 것들. 글자로 못 읽는 확장자는 사본에 안 넣는다, 지목된 조판본은
원본 대신 글자만 뽑아 넣는다(원본이 있으면 렌더링 동기가 남는다), 빠진 것은 프롬프트에
고지한다, 검토 대상 전문을 한 파일로 이어 붙여 한 번에 읽을 수 있게 둔다, 그리고
프롬프트가 한 번에 열라고·쪼개 읽지 말라고·대상 밖을 전문으로 읽지 말라고·2라운드에는
재독 범위를 좁히라고 말한다.
"""
import importlib.machinery
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


loader = importlib.machinery.SourceFileLoader("ai_debate_opaque", str(REPO / "bin" / "ai-debate"))
spec = importlib.util.spec_from_loader("ai_debate_opaque", loader)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

work = Path(tempfile.mkdtemp(prefix="aidebate-opaque-"))
src = work / "proj"
(src / "docs").mkdir(parents=True)
(src / "main.py").write_text("print('본문')\n", encoding="utf-8")
(src / "README.md").write_text("설명\n", encoding="utf-8")
# 사고를 낸 모양 그대로 — 소스 트리 안에 조판본·그림·모델가중치가 섞여 있다.
(src / "docs" / "root.pdf").write_bytes(b"%PDF-1.7\n" + b"\xde\xad\xbe\xef" * 4096)
(src / "docs" / "figure.PNG").write_bytes(b"\x89PNG\r\n" + b"\x00" * 2048)
(src / "weights.onnx").write_bytes(b"\x08\x07" + b"\x11" * 8192)


def make_pdf(path, line):
    """글자가 실제로 뽑히는 최소 조판본 하나. 외부 라이브러리 없이 손으로 만든다."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n"
            "%%EOF\n").encode()
    path.write_bytes(bytes(out))


paper = src / "docs" / "paper.pdf"
make_pdf(paper, "LiDAR registration experiment results")

only_bin = work / "assets"
only_bin.mkdir()
(only_bin / "a.png").write_bytes(b"\x89PNG\r\n" + b"\x00" * 512)


def stage(task, dest_name, want_fulltext=None):
    """브리프를 확정하고 사본을 떠서 (치환된 프롬프트, 사본 루트) 를 돌려준다.

    want_fulltext 를 준 호출은 고치기 전 판본에서 TypeError 로 죽지 않게 감싼다 —
    그 판본에는 그 인자가 없다. 크래시 대신 실패로 떨어져야 대조가 된다.
    """
    mod.set_brief_paths(task)
    dest = work / "snap" / dest_name
    if want_fulltext is None:
        out, made = mod.stage_readonly(task, dest)
    else:
        try:
            out, made = mod.stage_readonly(task, dest, want_fulltext=want_fulltext)
        except TypeError:
            return "<want_fulltext 인자 없음>", dest, False
    return out, dest, made


def copied(dest, rel):
    return (dest / str(src).lstrip("/") / rel).exists()


print("디렉토리 브리프")
task = f"{src} 를 검토해 달라."
out, dest, made = stage(task, "dir")
check("글자로 읽는 파일은 그대로 사본에 들어간다",
      made and copied(dest, "main.py") and copied(dest, "README.md"))
check("조판본은 사본에 안 들어간다",
      not copied(dest, "docs/root.pdf") and not copied(dest, "docs/paper.pdf"))
check("그림은 확장자가 대문자여도 안 들어간다", not copied(dest, "docs/figure.PNG"))
check("모델 가중치도 안 들어간다", not copied(dest, "weights.onnx"))
check("빠뜨린 파일을 프롬프트에 고지한다", "[글자 아닌 파일 제외]" in out, out[-300:])
check("고지에 빠진 개수가 들어간다", "4개" in out, out[-300:])
check("고지가 base64 로 찍는 것을 금지한다", "base64" in out and "찍지 마라" in out)
# 사본 경로는 원본 트리를 미러링해서 원본 경로 문자열을 통째로 품는다. 그래서
# "원본 경로가 없다" 를 그냥 찾으면 영원히 실패한다 — 원본 경로가 나온 횟수와 그것이
# 사본 접두를 달고 나온 횟수가 같은지를 본다(맨몸으로 나온 것이 하나도 없다는 뜻).
_pdf = f"{src}/docs/root.pdf"
check("고지에 적힌 것은 맨몸 원본 경로가 아니라 사본 경로다",
      str(dest) in out and out.count(_pdf) == out.count(f"{dest}{_pdf}"),
      f"{out.count(_pdf)} vs {out.count(f'{dest}{_pdf}')}")
check("상한 고지와 섞이지 않는다", "[사본 불완전]" not in out, out[-300:])

print("\n브리프가 조판본을 지목했을 때")
# 사고를 낸 회차(run-20260915T182901Z)가 이 모양이다 — 검토 대상이 논문 자체라
# 브리프가 조판본을 직접 지목했다. 원본을 그대로 주면 쪽을 그림으로 찍어 쏟는 동기가
# 남으므로, 지목됐어도 원본은 안 넣고 글자만 뽑아 넣는다.
one = f"{paper} 를 검토해 달라."
out2, dest2, made2 = stage(one, "pdf")
pdf_txt = dest2 / (str(paper).lstrip("/") + ".txt")
check("지목돼도 조판본 원본은 사본에 안 들어간다",
      not (dest2 / str(paper).lstrip("/")).exists())
if shutil.which("pdftotext"):
    check("대신 글자만 뽑은 파일이 들어간다", pdf_txt.exists() and pdf_txt.stat().st_size > 0,
          pdf_txt)
    body = pdf_txt.read_text(errors="ignore") if pdf_txt.exists() else ""
    check("본문 글자가 실제로 들어 있다", "LiDAR registration" in body, body[:120])
    check("프롬프트가 뽑은 파일을 가리킨다", str(pdf_txt) in out2, out2[-300:])
    check("조판본 대체를 따로 고지한다", "[조판본은 글자만]" in out2, out2[-300:])
else:
    # 글자 뽑는 도구가 없는 기계에서는 이름만 알리는 쪽으로 떨어진다. 조용히
    # 원본을 넣는 일만 없으면 된다.
    check("뽑는 도구가 없으면 이름만 알린다", "[글자 아닌 파일 제외]" in out2, out2[-300:])
# 글자를 못 뽑는 종류는 이름만 알린다.
out3, dest3, made3 = stage(f"{src / 'weights.onnx'} 를 봐 달라.", "onnx")
check("글자를 못 뽑는 파일은 이름만 알린다",
      not (dest3 / str(src / 'weights.onnx').lstrip("/")).exists()
      and "[글자 아닌 파일 제외]" in out3)

print("\n글자 파일이 하나도 없는 디렉토리")
out4, dest4, made4 = stage(f"{only_bin} 를 봐 달라.", "bin")
check("사본 없음을 상한 실패로 적지 않는다",
      "글자로 읽을 파일이 없다" in out4 and "상한" not in out4.split("[글자 아닌")[0], out4[-300:])
check("그래도 무엇이 있었는지는 알려준다", "[글자 아닌 파일 제외]" in out4)

print("\n글자 파일만 있으면 고지가 안 붙는다")
clean = work / "clean"
clean.mkdir()
(clean / "a.py").write_text("x = 1\n", encoding="utf-8")
out5, dest5, made5 = stage(f"{clean} 를 봐 달라.", "clean")
check("제외할 게 없으면 고지도 없다", made5 and "[글자 아닌 파일 제외]" not in out5)

print("\n전문 합본")
# 고치기 전 판본에도 이 파일을 그대로 돌려 검사가 실제로 무언가를 재는지 본다.
# 그때는 이 이름들이 없으므로 기본값으로 받아 크래시 대신 실패로 떨어지게 한다.
FULLTEXT_NAME = getattr(mod, "SNAP_FULLTEXT_NAME", "BRIEF-FULLTEXT.txt")
FULLTEXT_MAX = getattr(mod, "SNAP_FULLTEXT_MAX_BYTES", 1_500_000)
# 값은 읽는 양이 아니라 나눠 읽는 횟수에서 난다 — 도구를 한 번 더 부를 때마다 그때까지의
# 대화가 다시 실린다. 옆 기계 실측(2026-09-16, 24시간): 11만 토큰어치를 250줄씩 44번에
# 나눠 읽은 호출 하나가 6,900만 토큰을 썼고, 요청 28회를 넘은 호출 넷이 그날의 48.7% 다.
full = dest / FULLTEXT_NAME
check("사본 뿌리에 합본이 생긴다", full.exists(), sorted(p.name for p in dest.iterdir()))
merged = full.read_text(encoding="utf-8") if full.exists() else ""
check("합본에 글자 파일 본문이 다 들어 있다",
      "print('본문')" in merged and "설명" in merged)
check("합본에 원본 파일 경로가 구분선으로 적힌다",
      merged.count("=====") >= 4, merged[:200])
check("합본은 자기 자신을 안 담는다", merged.count(FULLTEXT_NAME) == 0)

print("\n전문을 프롬프트에 싣는다")
# 합본을 경로로만 가리키면 그걸 여는 일이 샌드박스에 걸린다. 옆 기계
# run-20260916T080634Z 에서 코덱스 호출 다섯이 전부 첫 읽기에 실패했고
# (bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted) 둘은 근거 없이
# 답했다. 나눠 읽던 시절엔 실행이 여러 번이라 한 번 실패해도 다른 실행이 근거를
# 채웠는데, 한 번에 읽게 만들면서 그 한 번이 전부가 됐다. 들어갈 크기면 싣는다.
INLINE_MAX = getattr(mod, "SNAP_INLINE_MAX_BYTES", 90_000)
check("작은 브리프는 전문이 프롬프트 본문에 실린다",
      "[전문]" in out and "print('본문')" in out and "설명" in out, out[-400:])
check("실은 회차에는 경로만 가리키는 문단이 없다", "[전문 합본]" not in out, out[-400:])
check("읽기 계약을 덮는다고 명시한다",
      "[파일 읽기 계약]" in out and "대체된다" in out and "셸을 열지 마라" in out, out[-400:])
check("합본 파일 경로도 같이 알려준다 — 특정 자리를 다시 볼 때 쓴다", str(full) in out)
check("전문 끝에 출력 형식을 다시 못박는다",
      "[전문 끝]" in out and "JSON 스키마" in out, out[-200:])
check("실을 수 있는 크기를 제미나이 인자 한계에서 뽑는다",
      INLINE_MAX < mod.GEMINI_ARG_LIMIT, (INLINE_MAX, mod.GEMINI_ARG_LIMIT))

# 못 싣는 크기면 종전대로 경로만 가리키되, 첫 실행이 샌드박스로 죽었을 때 무엇을
# 할지를 같이 적는다. 실측에서 다섯 중 셋이 재시도로 열었다.
wide = work / "wide"
wide.mkdir()
(wide / "long.txt").write_text("가" * (INLINE_MAX // 2), encoding="utf-8")
out7, dest7, _ = stage(f"{wide} 를 봐 달라.", "wide")
check("한계를 넘는 전문은 안 싣고 경로를 가리킨다",
      "[전문 합본]" in out7 and "[전문 끝]" not in out7, out7[-300:])
check("그 경우 샌드박스 실패 때 재시도하라고 적는다",
      "샌드박스 오류" in out7 and "한 번 더 실행" in out7, out7[-300:])
check("프롬프트가 나눠 읽기를 막는다", "250줄씩" in out7, out7[-300:])

# 정리자에게는 합본을 만들지도 알리지도 않는다. [근거 취급] 으로 "너는 파일을 직접
# 열 필요가 없다" 고 말해 놓고 같은 프롬프트 끝에서 합본을 읽으라고 시키고 있었다 —
# 그래서 정리자가 읽기를 시도했다가 샌드박스로 실패했다(같은 회차).
out8, dest8, arb_ok = stage(task, "arb", want_fulltext=False)
check("정리자 프롬프트에는 전문도 합본 안내도 없다",
      arb_ok and "[전문]" not in out8 and "[전문 합본]" not in out8, out8[-300:])
check("정리자 사본에는 합본 파일 자체를 안 만든다",
      arb_ok and dest8.is_dir() and not (dest8 / FULLTEXT_NAME).exists(),
      sorted(p.name for p in dest8.iterdir()) if dest8.is_dir() else "사본 없음")
# 상한을 넘기면 넘긴 것만 빠지고 나머지는 합본에 남는다.
big = work / "big"
big.mkdir()
(big / "small.py").write_text("작은 파일\n", encoding="utf-8")
(big / "huge.txt").write_text("가" * (FULLTEXT_MAX // 2), encoding="utf-8")
out6, dest6, _ = stage(f"{big} 를 봐 달라.", "big")
m6p = dest6 / FULLTEXT_NAME
merged6 = m6p.read_text(encoding="utf-8") if m6p.exists() else ""
check("상한을 넘긴 파일은 합본에서 빠진다",
      "작은 파일" in merged6 and len(merged6) < FULLTEXT_MAX)
check("빠진 것이 있으면 몇 개인지 알린다", "안 들어간 파일 1개" in out6, out6[-300:])

print("\n읽는 방법과 검토 대상")
rp = mod.role_prompt("debater", task, "", "", round_idx=1)
check("한 번에 열라고 적는다", "[읽는 방법]" in rp and "한 번의 셸 실행" in rp)
check("쪼개 읽기를 막는 이유까지 적는다", "다시 실려서" in rp)
check("검토 대상 밖을 전문으로 읽지 말라고 적는다",
      "[검토 대상]" in rp and "맥락이지 대상이 아니다" in rp)
check("경로 없는 브리프에는 둘 다 안 붙는다",
      "[읽는 방법]" not in mod.role_prompt("debater", "정책만 정하자", "", "", round_idx=1))
check("중재자에는 안 붙는다",
      "[읽는 방법]" not in mod.role_prompt("arbiter", task, "", "x", round_idx=1))

print("\n2라운드 재독 범위")
p1 = mod.role_prompt("debater", task, "", "", round_idx=1)
p2 = mod.role_prompt("debater", task, "", "[이전 라운드 출력]\n[]", round_idx=2)
pa = mod.role_prompt("arbiter", task, "", "[이전 라운드 출력]\n[]", round_idx=2)
check("1라운드에는 재독 범위 고지가 없다", "[재독 범위]" not in p1)
check("2라운드에는 재독 범위 고지가 붙는다", "[재독 범위]" in p2)
check("재독 범위 고지가 전체 재훑기를 막는다", "처음부터 다시 훑지 마라" in p2)
check("중재자에는 안 붙는다 — 애초에 파일을 안 연다", "[재독 범위]" not in pa)
check("경로 없는 브리프에는 안 붙는다",
      "[재독 범위]" not in mod.role_prompt("debater", "정책만 정하자", "", "x", round_idx=2))

shutil.rmtree(work, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
