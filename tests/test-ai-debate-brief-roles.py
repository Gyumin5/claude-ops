#!/usr/bin/env python3
"""필수 자료와 참고 자료를 나눠 적는 계약 검사.

계기는 2026-09-17 실측이다. 홈의 논문 검토 회차가 합본 상한 1,500,000바이트에 정면으로
부딪혀 검토 대상 절반이 합본 밖에 남았다(run-20260917T015536Z: 들어간 63개 · 빠진 73개,
history #149). 상한을 올려도 한 번에 안 읽히므로 답은 합본에 들어갈 것을 줄이는 쪽이다.

고정하는 것 넷.

  1. 브리프가 나눠 적지 않으면 지금까지와 똑같이 돈다. 이미 도는 회차를 깨지 않는 것이
     이 갈래의 조건이다 — 운영자가 논문 검토 회차를 범위 안 좁히고 그대로 돌리라고 정했다.
  2. 참고 자료로 적힌 경로는 합본에서 빠진다. 사본 트리에는 남아 필요할 때 열 수 있다.
  3. 무엇이 왜 들어가고 빠졌는지가 파일 단위로 남는다. 개수 고지만으로는 아무도 못 짚는다.
  4. 담는 차례는 필수 먼저, 필수 안에서는 브리프가 적은 차례다. 이름순으로만 담으면
     상한에 걸릴 때 제일 중요한 파일이 알파벳 때문에 밀려난다.
"""
import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


mod = load("ai_debate_roles", REPO / "bin" / "ai-debate")

# 고치기 전 판본에는 이 이름들이 없다 — 크래시 대신 실패로 떨어지게 기본값으로 받는다.
_ref_paths = getattr(mod, "brief_ref_paths", None)
_role_of = getattr(mod, "brief_file_role", None)
_req_paths = getattr(mod, "brief_required_paths", None)
MANIFEST = getattr(mod, "SNAP_MANIFEST_NAME", "BRIEF-MANIFEST.txt")


def refs_of(task):
    return _ref_paths(task) if _ref_paths else []


def role_of(origin, req, ref):
    return _role_of(origin, req, ref)[0] if _role_of else "?"


def fulltext(dest, req, ref):
    """합본을 만들고 결과 사전을 돌려준다. 옛 판본은 자리 셋짜리 튜플을 준다."""
    try:
        got = mod.write_brief_fulltext(dest, req, ref)
    except TypeError:
        got = mod.write_brief_fulltext(dest)
    return got if isinstance(got, dict) else None


# ── 1. 표지 읽기 ───────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    need = root / "필수"
    ref = root / "참고"
    need.mkdir()
    ref.mkdir()
    (need / "a.txt").write_text("필수 본문\n", encoding="utf-8")
    (ref / "b.txt").write_text("참고 본문\n", encoding="utf-8")

    plain = f"{need} 와 {ref} 를 검토해 달라"
    check("표지가 없으면 참고가 없다", refs_of(plain) == [], str(refs_of(plain)))

    split = (f"검토 대상: {need}\n"
             f"[참고 자료]\n{ref}\n")
    check("표지 아래 경로는 참고다", refs_of(split) == [str(ref)], str(refs_of(split)))
    check("표지 앞 경로는 필수로 남는다", str(need) not in refs_of(split), str(refs_of(split)))

    back = (f"[참고 자료]\n{ref}\n[필수 자료]\n{need}\n")
    check("필수 표지로 되돌아온다", refs_of(back) == [str(ref)], str(refs_of(back)))

    same_line = f"[참고 자료] {ref} 는 배경이다"
    check("표지와 같은 줄의 경로도 참고다", refs_of(same_line) == [str(ref)],
          str(refs_of(same_line)))

    mod.set_brief_paths(split)
    check("확정 목록에는 둘 다 든다", set(mod.BRIEF_PATHS) == {str(need), str(ref)},
          str(mod.BRIEF_PATHS))
    held = list(getattr(mod, "BRIEF_REF_PATHS", []))
    check("참고 목록이 따로 선다", held == [str(ref)], str(held))
    check("필수 목록은 참고를 뺀 나머지다",
          (_req_paths() if _req_paths else []) == [str(need)],
          str(_req_paths() if _req_paths else []))

    # ── 2. 역할 판정 ───────────────────────────────────────────────────
    check("참고 뿌리 아래 파일은 참고다",
          role_of(f"{ref}/b.txt", [str(need)], [str(ref)]) == "참고")
    check("필수 뿌리 아래 파일은 필수다",
          role_of(f"{need}/a.txt", [str(need)], [str(ref)]) == "필수")
    check("참고 안의 더 구체적인 필수가 이긴다",
          role_of(f"{ref}/안쪽/c.txt", [f"{ref}/안쪽"], [str(ref)]) == "필수")
    check("어느 뿌리에도 없으면 필수로 본다 — 모르는 것을 조용히 빼지 않는다",
          role_of(f"{root}/딴것.txt", [str(need)], [str(ref)]) == "필수")

# ── 3. 합본과 명세 ─────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp).resolve() / "snap"
    need_o, ref_o = "/home/user/필수", "/home/user/참고"
    for origin, body in ((f"{need_o}/a.txt", "필수 본문"), (f"{ref_o}/b.txt", "참고 본문")):
        p = dest / origin.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body + "\n", encoding="utf-8")

    got = fulltext(dest, [need_o], [ref_o])
    merged = got["path"].read_text(encoding="utf-8") if got else ""
    check("필수 본문은 합본에 든다", "필수 본문" in merged, merged[:200])
    check("참고 본문은 합본에 없다", "참고 본문" not in merged, merged[:400])
    check("뺀 참고 수를 센다", got and got["refs"] == 1, str(got))
    check("머리줄이 참고를 뺐다고 밝힌다", "참고 자료 1개는 합본에 안 넣었다" in merged,
          merged[:300])

    man = dest / MANIFEST
    man_text = man.read_text(encoding="utf-8") if man.exists() else ""
    check("명세 파일이 생긴다", man.exists(), sorted(p.name for p in dest.iterdir()))
    check("명세가 필수 파일을 포함으로 적는다",
          f"필수\t{need_o}/a.txt" in man_text and "포함" in man_text, man_text)
    check("명세가 참고 파일을 사유와 함께 적는다",
          f"참고\t{ref_o}/b.txt" in man_text and "제외: 참고 자료" in man_text, man_text)
    check("명세에 내용 해시가 든다",
          man_text.count("\t") >= 8 and len(man_text.splitlines()) == 4, man_text)
    # 머리줄은 명세를 가리킨다(그게 목적이다). 담지 말아야 하는 것은 명세 본문이다 —
    # 담으면 같은 내용을 두 번 싣고 합본 상한만 먹는다.
    check("합본이 명세 본문을 담지는 않는다",
          f"===== {dest / MANIFEST}" not in merged and "제외: 참고 자료" not in merged,
          merged[:300])

    # 나눠 적지 않은 브리프는 예전 그대로다 — 참고가 없으니 전부 들어간다.
    same = fulltext(dest, [need_o, ref_o], [])
    merged2 = same["path"].read_text(encoding="utf-8") if same else ""
    check("안 나눠 적으면 전부 합본에 든다",
          "필수 본문" in merged2 and "참고 본문" in merged2, merged2[:300])
    head2 = merged2.splitlines()[0] if merged2 else ""
    check("그때는 참고 고지가 안 붙는다", bool(head2) and "참고 자료" not in head2, head2)

# ── 4. 담는 차례 ───────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp).resolve() / "snap"
    first, second = "/home/user/ㄴ뒤", "/home/user/ㄱ앞"
    for origin, body in ((f"{first}/x.txt", "먼저 적은 것"), (f"{second}/y.txt", "나중 적은 것")):
        p = dest / origin.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body + "\n", encoding="utf-8")

    keep = mod.SNAP_FULLTEXT_MAX_BYTES
    mod.SNAP_FULLTEXT_MAX_BYTES = 30   # 한 파일만 들어가는 크기
    try:
        got = fulltext(dest, [first, second], [])
    finally:
        mod.SNAP_FULLTEXT_MAX_BYTES = keep
    merged = got["path"].read_text(encoding="utf-8") if got else ""
    check("상한에 걸리면 브리프가 먼저 적은 쪽이 들어간다",
          "먼저 적은 것" in merged and "나중 적은 것" not in merged, merged[:300])
    man_text = (dest / MANIFEST).read_text(encoding="utf-8") if (dest / MANIFEST).exists() else ""
    check("밀려난 파일의 사유가 상한이라고 적힌다", "제외: 합본 상한 초과" in man_text,
          man_text)

# ── 5. 실제 사본 경로로 한 번 ──────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    need = root / "코드"
    ref = root / "배경"
    need.mkdir()
    ref.mkdir()
    (need / "main.py").write_text("print('검토 대상')\n", encoding="utf-8")
    (ref / "옛보고서.md").write_text("배경 설명\n", encoding="utf-8")

    task = f"검토 대상: {need}\n[참고 자료]\n{ref}\n"
    mod.set_brief_paths(task)
    dest = root / "snap"
    staged, made = mod.stage_readonly(task, dest)
    check("참고 파일도 사본 트리에는 있다",
          (dest / str(ref).lstrip("/") / "옛보고서.md").is_file(),
          str(sorted(p.name for p in dest.rglob("*"))))
    check("프롬프트 전문에 참고 본문이 안 실린다",
          "검토 대상" in staged and "배경 설명" not in staged, staged[-400:])
    check("참고를 뺐다는 것을 프롬프트가 알린다", "참고 자료 1개는 합본에 없다" in staged,
          staged[-400:])
    check("명세 경로를 프롬프트가 알려준다", MANIFEST in staged, staged[-400:])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
