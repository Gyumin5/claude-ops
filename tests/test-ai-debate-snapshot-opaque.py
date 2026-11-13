#!/usr/bin/env python3
"""토론 호출 하나가 코덱스 토큰을 적게 쓰게 하는 장치들을 고정한다.

왜 있나 — 실측 둘이다.

(가) 그림 덩어리. 홈 회차 코덱스 원문 148건 30.9MB 중 base64 덩어리가 19.8MB 로
64.2% 이고 그게 11건에서만 나온다. 전부 라운드1 이고, 제일 큰 건은 브리프가 검토
대상으로 지목한 조판본 여덟 쪽을 그림으로 렌더링해 3.42MB 를 찍은 것이다. 같은
조판본의 글자는 68,580바이트다. 파일당 상한 8MB 에 안 걸려 기존 관문을 다 통과했다.

(나) 나눠 읽기. 둘째 기계 24시간 실측에서 codex-ask 66회 중 호출 하나가 모델 요청을
44번 던져 6,900만 토큰을 썼다. 요청마다 그때까지의 대화 전체가 다시 실리므로 값이
요청 수의 제곱으로 큰다 — 호출당 총토큰 중앙값이 요청 1회 35,012 · 8회 625,960 ·
20회 4,070,416 · 44회 39,370,829 이다. 그 호출이 읽은 내용은 11만 토큰 규모인데
250줄씩 44번에 나눠 읽어 44번 재전송됐다. 요청 28회를 넘은 호출 넷이 그날의 48.7% 다.

그래서 고정하는 것들. 글자로 못 읽는 확장자는 사본에 안 넣는다, 지목된 조판본은
원본 대신 글자만 뽑아 넣는다(원본이 있으면 렌더링 동기가 남는다), 빠진 것은 프롬프트에
고지한다, 그리고 프롬프트가 한 번에 열라고·쪼개 읽지 말라고·대상 밖을 전문으로 읽지
말라고·2라운드에는 재독 범위를 좁히라고 말한다.

전문을 한 파일로 이어 붙여 주던 장치는 2026-09-26 통제 비교로 접었다(history #167).
같은 브리프를 켠 판 12회·끈 판 12회 돌려 호출당 모델 요청 수를 쟀더니 중앙값이 양쪽
9.0 으로 같았다 — 나눠 읽기를 줄이는 효과가 없었다. 그래서 여기서는 그 장치가 되살아
나지 않았는지만 확인한다.
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


def stage(task, dest_name):
    """브리프를 확정하고 사본을 떠서 (치환된 프롬프트, 사본 루트, 성공 여부) 를 돌려준다."""
    mod.set_brief_paths(task)
    dest = work / "snap" / dest_name
    out, made = mod.stage_readonly(task, dest)
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

print("\n접은 합본이 되살아나지 않았는지")
# 2026-09-26 통제 비교(켠 판 12회·끈 판 12회, 호출당 모델 요청 중앙 9.0 대 9.0)에서
# 효과가 없다고 판명돼 걷어냈다. 되살리려면 그때보다 나은 근거가 있어야 하므로,
# 실수로 되돌아오는 것만 여기서 막는다.
root_names = sorted(p.name for p in dest.iterdir())
check("사본 뿌리에 합본 파일이 없다",
      not any(n.startswith("BRIEF-FULLTEXT") for n in root_names), root_names)
check("사본 뿌리에 남는 것은 명세 하나뿐이다",
      mod.SNAP_MANIFEST_NAME in root_names, root_names)
check("프롬프트에 전문을 통째로 싣지 않는다",
      "[전문]" not in out and "[전문 합본]" not in out and "[전문 끝]" not in out,
      out[-300:])
check("합본 관련 이름이 코드에서 사라졌다",
      not hasattr(mod, "SNAP_FULLTEXT_NAME") and not hasattr(mod, "SNAP_INLINE_MAX_BYTES"))
check("사본을 뜨는 함수에 합본 스위치가 없다",
      "want_fulltext" not in mod.stage_readonly.__code__.co_varnames,
      mod.stage_readonly.__code__.co_varnames)

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
