#!/usr/bin/env python3
"""브리프가 가리킨 자료가 빠졌을 때 회차를 착수 전에 멈추는가.

2026-11-11 소식지 회차가 통째로 버려진 자리다. 브리프가 필수 자료 경로를 적었는데
그 경로가 사본 부정목록에 걸려, 참여자 프롬프트에는 "경로 제외" 한 줄만 들어갔다.
세 모델은 그 글만 읽고 답했고, 정리자가 "아무도 원문을 못 읽었다" 를 스스로 적고 나서야
제출한 쪽이 알았다. 그때까지 경고는 표준오류 한 줄뿐이었고 회차는 그대로 돌았다.

보는 것 넷.
1. 역할 판정 — 표지가 없으면 전부 필수다. 모르는 것을 참고로 내리면 검토 대상이
   조용히 사라진다.
2. 필수 자료가 하나라도 못 열리면 중단 사유문이 그 경로를 이름으로 댄다.
3. 참고 자료만 못 열리면 중단하지 않는다 — 그쪽은 경고로 끝낸다.
4. 그 점검이 캐시 반환보다 앞에 있다. 뒤에 있으면 자료가 빠진 브리프가 캐시를 타고
   조용히 답을 받는다.
"""
import importlib.machinery
import importlib.util
import inspect
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail: object = ""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (("\n      " + str(detail)) if detail and not cond else ""))


loader = importlib.machinery.SourceFileLoader(
    "ai_debate_missing", str(REPO / "bin" / "ai-debate"))
spec = importlib.util.spec_from_loader("ai_debate_missing", loader)
assert spec is not None
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp:
    live = Path(tmp) / "live.md"
    live.write_text("x\n", encoding="utf-8")
    gone = Path(tmp) / "gone.md"          # 만들지 않는다
    ref = Path(tmp) / "ref-gone.md"       # 참고 자료 쪽, 역시 없다

    # 1. 표지가 없으면 전부 필수다.
    plain = f"검토 대상\n{live}\n{gone}\n"
    roles = mod.brief_path_roles(plain)
    check("표지가 없으면 전부 필수다",
          roles.get(str(live)) == "필수" and roles.get(str(gone)) == "필수", roles)

    # 표지를 쓰면 그 줄부터 역할이 바뀐다.
    tagged = f"[필수 자료]\n{live}\n[참고 자료]\n{ref}\n"
    roles2 = mod.brief_path_roles(tagged)
    check("필수 표지 아래는 필수다", roles2.get(str(live)) == "필수", roles2)
    check("참고 표지 아래는 참고다", roles2.get(str(ref)) == "참고", roles2)

    # 2. 필수가 빠지면 잡아낸다.
    usable, bad = mod.classify_brief_paths(plain)
    check("열리는 것은 열린다고 센다", usable == [str(live)], usable)
    bad_req = [(p, why) for p, why in bad
               if mod.brief_path_roles(plain).get(p, "필수") == "필수"]
    check("못 여는 필수 자료를 집어낸다",
          [p for p, _ in bad_req] == [str(gone)], bad_req)
    msg = mod.brief_required_abort_message(bad_req)
    check("중단 사유문이 그 경로를 이름으로 댄다", str(gone) in msg, msg)
    check("중단 사유문이 회차를 시작하지 않는다고 적는다",
          "시작하지 않는다" in msg, msg)

    # 3. 참고 자료만 빠지면 중단 대상이 아니다.
    usable2, bad2 = mod.classify_brief_paths(tagged)
    roles3 = mod.brief_path_roles(tagged)
    bad_req2 = [(p, why) for p, why in bad2 if roles3.get(p, "필수") == "필수"]
    check("참고 자료만 빠지면 중단하지 않는다", not bad_req2, bad_req2)
    check("그래도 못 연 것은 목록에 남는다",
          [p for p, _ in bad2] == [str(ref)], bad2)

# 4. 점검이 캐시 반환보다 앞이다.
src = inspect.getsource(mod.main)
i_gate = src.find("brief_required_abort_message")
i_cache = src.find("24h 캐시 검사")
check("필수 자료 점검이 캐시 검사보다 앞에 있다",
      i_gate != -1 and i_cache != -1 and i_gate < i_cache, (i_gate, i_cache))

ok = sum(results)
print(f"\n{ok}/{len(results)} passed")
sys.exit(0 if ok == len(results) else 1)
