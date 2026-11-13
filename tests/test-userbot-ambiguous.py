#!/usr/bin/env python3
"""겹치는 짧은 세션 이름 거절 계약 검사.

사고(2026-10-18): 둘째 기계의 세 세션이 첫째 기계의 질문에 답하면서 출처 마커의
기계 이름을 떼고 `--session dotfiles` 로 보냈고, 전부 둘째 기계 자신의 dotfiles 로 갔다.
짧은 이름은 각 호스트에서 '이 기계의 그 세션' 으로 조용히 풀려서 보내는 쪽도 받는
쪽도 오배송을 모른다.

고정하는 것:
  1. 겹치는 이름으로 등록된 짧은 이름은 발송 전에 거절한다(rc≠0).
  2. 거절 메시지는 쓸 수 있는 완전 주소 형태를 알려준다.
  3. 목록에 없는 이름은 그대로 통과한다(짧은 이름 전부를 막는 게 아니다).
  4. 목록 파일이 없으면 거절도 없다 — 새 코드가 먼저 배포돼도 동작이 그대로다.
  5. 목록 파일이 깨졌으면 거절하지 않는다(알림 경로를 깨뜨리지 않는다).
  6. 완전 주소는 거절 대상이 아니다.
  7. 대상 표에 완전 주소가 실제로 등록돼 있어 거절 뒤의 안내가 막다른 길이 아니다.
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if not cond and detail else ""))


def load():
    path = REPO / "bin" / "claude-userbot-send"
    loader = importlib.machinery.SourceFileLoader("userbot_send", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# 지어낸 기계 이름. 이 검사는 이 기계의 등록 상태를 안 읽는다 — 읽으면 등록이
# 바뀌는 날 판정이 같이 깨지고, 그때 깨진 쪽이 코드인지 등록인지가 안 갈린다.
TARGETS = {"alpha/dotfiles": "@bot_alpha_dotfiles",
           "beta/dotfiles": "@bot_beta_dotfiles",
           "alpha/other": "@bot_alpha_other"}


def reject(mod, target, listing, targets=None):
    """거절 여부와 메시지. listing=None 이면 목록 파일 자체가 없는 상태."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ambiguous.json")
        if listing is not None:
            with open(p, "w") as f:
                f.write(listing if isinstance(listing, str) else json.dumps(listing))
        t = os.path.join(d, "targets.json")
        with open(t, "w") as f:
            json.dump(TARGETS if targets is None else targets, f)
        mod.TARGETS_PATH = t
        mod.AMBIGUOUS_PATH = p
        try:
            mod.reject_if_ambiguous(target)
        except SystemExit as e:
            return True, str(e.code)
        return False, ""


mod = load()

hit, msg = reject(mod, "dotfiles", ["dotfiles"])
check("1. 겹치는 짧은 이름은 거절한다", hit)
check("2. 거절 메시지가 대상 표의 완전 주소를 그대로 알려준다",
      hit and "alpha/dotfiles" in msg and "beta/dotfiles" in msg, msg)
check("2-1. 상관없는 세션은 안내에 안 섞인다",
      hit and "alpha/other" not in msg, msg)

hit2, msg2 = reject(mod, "dotfiles", ["dotfiles"], targets={})
check("2-2. 대상 표가 비어도 거절은 하고 찾는 법을 알려준다",
      hit2 and "<기계>/dotfiles" in msg2, msg2)

hit, _ = reject(mod, "other", ["dotfiles"])
check("3. 목록에 없는 짧은 이름은 통과한다", not hit)

hit, _ = reject(mod, "dotfiles", None)
check("4. 목록 파일이 없으면 거절하지 않는다", not hit)

hit, _ = reject(mod, "dotfiles", "{깨진 json")
check("5. 목록 파일이 깨졌으면 거절하지 않는다", not hit)

hit, _ = reject(mod, "alpha/dotfiles", ["dotfiles"])
check("6. 완전 주소는 거절 대상이 아니다", not hit)

hit, msg = reject(mod, "dotfiles", ["dotfiles"])
suggested = [w.strip(",") for w in msg.split() if "/dotfiles" in w and "<" not in w]
check("7. 안내한 완전 주소가 대상 표에 실제로 있다 — 막다른 길이 아니다",
      bool(suggested) and all(s in TARGETS for s in suggested), str(suggested))

print(f"\n{sum(results)}/{len(results)} PASS")
raise SystemExit(0 if all(results) else 1)
