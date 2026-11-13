#!/usr/bin/env python3
"""한 회차는 자기만 쓰는 폴더를 갖는다.

회차 폴더 이름은 초 단위 시각이다. 같은 초에 둘을 제출하면 두 회차가 한 폴더를 같이
쓰게 되고, 뒤에 시작한 쪽이 task.txt·프롬프트·라운드 산출물을 덮어써서 앞 토론의
원문이 통째로 사라진다. 2026-11-09 둘째 기계에서 실제로 났다 — 17:59:13 에 낸 두 건이
run-20261109T085913Z 하나를 같이 썼고, 남은 task.txt 는 한쪽 것인데 나머지 파일은
다른 쪽 것이었다. 사라진 쪽은 복구할 방법이 없다.

고정하는 것 넷.
⑴ 폴더가 없으면 그 이름 그대로 만든다 — 평소 회차의 이름이 안 바뀐다.
⑵ 같은 이름이 이미 있으면 덮어쓰지 않고 꼬리번호를 붙인 새 폴더를 만든다.
⑶ 돌려주는 이름이 실제로 만든 폴더와 같다 — 이름이 곧 회차 이름이라, 갈라 놓고
   옛 이름을 돌려주면 기록·주입·캐시가 남의 폴더를 가리킨다.
⑷ 먼저 만든 폴더의 내용은 그대로 남는다.
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
    print("{}  {}".format("PASS" if cond else "FAIL", name)
          + ("\n      {}".format(detail) if not cond and detail else ""))


mod = load("ai_debate_run_dir", REPO / "bin" / "ai-debate")

if not hasattr(mod, "make_run_dir"):
    check("회차 폴더를 만드는 자리가 있다", False, "make_run_dir 가 없다")
    print("\n{}/{} passed".format(sum(results), len(results)))
    sys.exit(1)
check("회차 폴더를 만드는 자리가 있다", True)

RID = "20261109T085913Z"

with tempfile.TemporaryDirectory() as td:
    base = Path(td)

    d1, n1 = mod.make_run_dir(base, RID)
    check("처음이면 그 이름 그대로 만든다",
          n1 == RID and d1 == base / ("run-" + RID) and d1.is_dir(), str(d1))

    # 앞 회차가 이미 쓰고 있는 파일. 겹친 뒤에도 그대로 있어야 한다.
    (d1 / "task.txt").write_text("앞 회차 물음", encoding="utf-8")

    d2, n2 = mod.make_run_dir(base, RID)
    check("같은 초에 또 내면 다른 폴더를 만든다", d2 != d1 and d2.is_dir(), str(d2))
    check("돌려주는 이름이 실제 폴더와 같다",
          d2.name == "run-" + n2, "{} vs {}".format(d2.name, n2))
    check("꼬리번호가 원래 이름 뒤에 붙는다",
          n2.startswith(RID + "-"), n2)
    check("앞 회차 파일은 그대로 남는다",
          (d1 / "task.txt").read_text(encoding="utf-8") == "앞 회차 물음",
          str(d1 / "task.txt"))
    check("새 폴더는 비어 있다", not any(d2.iterdir()), str(list(d2.iterdir())))

    d3, n3 = mod.make_run_dir(base, RID)
    check("셋째도 또 갈린다",
          len({d1, d2, d3}) == 3 and n3 not in (n1, n2),
          "{} {} {}".format(n1, n2, n3))

    # 자리가 다 차면 조용히 덮어쓰지 않고 시끄럽게 죽는다.
    try:
        mod.make_run_dir(base, RID, limit=3)
        raised = False
    except RuntimeError:
        raised = True
    check("더 못 갈리면 덮어쓰지 않고 죽는다", raised)

    # 부모 폴더가 없어도 만든다 — 처음 돌리는 기계가 그렇다.
    d4, _ = mod.make_run_dir(base / "없던칸" / "그아래", RID)
    check("부모 폴더가 없으면 같이 만든다", d4.is_dir(), str(d4))

print("\n{}/{} passed".format(sum(results), len(results)))
sys.exit(0 if all(results) else 1)
