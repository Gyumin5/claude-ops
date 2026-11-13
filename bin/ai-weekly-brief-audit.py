#!/usr/bin/env python3
"""주간 브리핑 2단계 — 설치된 확장의 근거를 모은다.

사용: ai-weekly-brief-audit.py --out <설치근거.json> [--budget BYTES]

판단하지 않는다. 기원·설치 판본·마지막 변경 시각을 모아 적기만 한다.
오래 안 바뀐 것을 폐기나 취약으로 단정하지 않는다 — 그 판정은 평가 단계가 하고,
근거가 없으면 확인 필요로 남긴다.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc
                                      ).strftime("%Y-%m-%d")
    except OSError:
        return ""


def mcp_servers():
    """어느 파일이 실제로 읽히는지와 함께 적는다. 경로가 갈리면 둘 다 남긴다."""
    out = []
    for path in (os.path.join(HOME, ".claude", "mcp.json"),
                 os.path.join(HOME, ".claude.json")):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as exc:
            out.append({"kind": "mcp", "name": "(읽기 실패)", "config": path,
                        "note": str(exc)[:100]})
            continue
        for name, cfg in (d.get("mcpServers") or {}).items():
            cmd = cfg.get("command", "")
            args = " ".join(cfg.get("args", []) or [])
            origin = ""
            for tok in (args.split() + [cmd]):
                if tok.startswith("@") or "/" in tok or tok.endswith(".js"):
                    origin = tok
                    break
            item = {"kind": "mcp", "name": name, "config": path,
                    "origin": origin or cmd, "command": (cmd + " " + args)[:160],
                    "config_changed": mtime(path)}
            item.update(git_state(cmd + " " + args))
            out.append(item)
    return out


def git_state(command):
    """명령줄이 가리키는 로컬 클론이 상류보다 얼마나 뒤인지 센다.

    설정 파일 변경일로는 알 수 없는 것이 이것이다 — 클론은 복사본이라 상류를
    따라가지 않고 멈춰 있는다. 그래서 매주 "비교해야 한다" 만 적히고 아무도
    비교하지 않는다. 숫자를 여기서 내면 그 줄이 판단이 된다.
    """
    for tok in command.replace("&&", " ").split():
        cand = os.path.expanduser(tok.strip("'\";"))
        if not cand.startswith("/") or not os.path.isdir(cand):
            continue
        if not os.path.isdir(os.path.join(cand, ".git")):
            continue
        def git(*a, timeout=60):
            r = subprocess.run(["git", "-C", cand] + list(a),
                               capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip() if r.returncode == 0 else ""
        try:
            head = git("rev-parse", "--short", "HEAD")
            git("fetch", "-q", "origin", timeout=120)
            behind = ""
            for ref in ("origin/HEAD", "origin/main", "origin/master"):
                behind = git("rev-list", "--count", "HEAD..%s" % ref)
                if behind:
                    break
            dirty = "있음" if git("status", "--porcelain") else "없음"
        except Exception as exc:
            return {"클론": cand, "상류_대조": "실패(%s)" % type(exc).__name__}
        return {"클론": cand, "설치_지점": head or "미상",
                "상류보다_뒤": (behind + "커밋") if behind else "미상",
                "로컬_수정": dirty}
    return {}


REPO_SKILLS = os.path.join(HOME, "dotfiles", "claude", "skills")


def skills():
    """스킬의 마지막 변경은 저장소 기록으로 본다 — 심링크 시각은 설치 시각이다.

    기원을 세 갈래로 가른다. 저장소를 가리키는 심링크, 저장소에서 복사된 사본,
    저장소에 없는 것. 사본은 저장소와 내용이 갈렸는지까지 본다 — 갈렸다는 것은
    배포가 안 돌았거나 설치본을 손으로 고쳤다는 뜻이라 실제로 확인할 거리다.
    """
    base = os.path.join(HOME, ".claude", "skills")
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if name.startswith("."):
            continue  # .trash 처럼 탐색 대상이 아닌 보관 자리
        p = os.path.join(base, name)
        if name == "synced":
            out.extend(synced_skills(p))
            continue  # 스킬 하나가 아니라 계정 동기화 묶음을 담는 자리다
        real = os.path.realpath(p)
        repo = os.path.join(REPO_SKILLS, name)
        changed = ""
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%cs", "--", real],
                               cwd=os.path.dirname(real), capture_output=True,
                               text=True, timeout=15)
            changed = (r.stdout or "").strip()
        except Exception:
            changed = ""
        item = {"kind": "skill", "name": name, "path": real,
                "changed": changed or mtime(real)}
        if "/dotfiles/" in real:
            item["origin"] = "자체 작성(저장소 심링크)"
        elif os.path.isdir(repo):
            item["origin"] = "자체 작성(저장소 사본)"
            item["drift"] = diff_against_repo(real, repo)
        else:
            item["origin"] = "저장소에 없음"
        out.append(item)
    return out


def synced_skills(base):
    """계정에서 내려받은 스킬 묶음. 묶음마다 받은 시각이 다른 사본이 쌓인다.

    하나로 묶어 적는다 — 묶음 안의 스킬을 낱개로 펼치면 수십 줄이 되어 크기 상한을
    먹고 정작 자체 작성 스킬이 뒤에서부터 잘린다. 오래된 묶음이 몇 개 남았는지가
    실제로 볼 거리다.
    """
    try:
        snaps = sorted(d for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d)))
    except OSError:
        return []
    if not snaps:
        return []
    dates = sorted(mtime(os.path.join(base, d)) for d in snaps)
    names = set()
    for d in snaps:
        try:
            names.update(x for x in os.listdir(os.path.join(base, d))
                         if os.path.isdir(os.path.join(base, d, x)))
        except OSError:
            pass
    return [{"kind": "skill", "name": "synced(계정 동기화 묶음)",
             "origin": "계정 동기화 — 저장소 관리 대상이 아니다",
             "묶음": len(snaps), "가장_오래된_묶음": dates[0],
             "changed": dates[-1],
             "스킬": ",".join(sorted(names))}]


def diff_against_repo(installed, repo):
    """사본과 저장소 원본을 비교한다. 같으면 빈 문자열."""
    try:
        r = subprocess.run(["diff", "-rq", installed, repo],
                           capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return "비교 실패: " + str(exc)[:80]
    if r.returncode == 0:
        return ""
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    return "저장소와 다름 %d건" % len(lines) if lines else "비교 결과 불명"


def plugins():
    path = os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as exc:
        return [{"kind": "plugin", "name": "(읽기 실패)", "note": str(exc)[:100]}]
    for name, entries in (d.get("plugins") or {}).items():
        for e in entries or []:
            out.append({"kind": "plugin", "name": name,
                        "origin": name.split("@")[-1],
                        "version": e.get("version", ""),
                        "installed": (e.get("installedAt") or "")[:10],
                        "changed": (e.get("lastUpdated") or "")[:10],
                        "commit": (e.get("gitCommitSha") or "")[:12],
                        "scope": e.get("scope", "")})
    return out


RULES_MAIN = os.path.join(HOME, "dotfiles", "claude", "CLAUDE.md")
RULES_WHY = os.path.join(HOME, "dotfiles", "claude", "RATIONALE.md")


def rules():
    """규칙 본문과 근거 파일을 대조한 근거를 모은다. 판단은 평가 단계가 한다.

    본문의 [R##] 표식과 근거 파일의 ### R## 제목을 맞춰 보고, 한쪽에만 있는 번호와
    본문 안에서 두 번 쓰인 번호를 적는다. 이것만으로 폐기나 중복이라고 단정하지
    않는다 — 폐기한 번호는 근거만 남겨 두는 것이 규약이다.
    """
    def read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    main_txt, why_txt = read(RULES_MAIN), read(RULES_WHY)
    if not main_txt:
        return [{"kind": "rules", "name": "전역 규칙", "note": "본문을 읽지 못했다"}]
    used = re.findall(r"\[R(\d{2})\]", main_txt)
    documented = re.findall(r"^###\s+R(\d{2})", why_txt, re.M)
    retired = re.findall(r"^###\s+R(\d{2})\s*\(폐기", why_txt, re.M)
    seen, dup = set(), []
    for n in used:
        if n in seen:
            dup.append(n)
        seen.add(n)
    missing = sorted(set(used) - set(documented))
    orphan = sorted(set(documented) - set(used) - set(retired))
    item = {"kind": "rules", "name": "전역 규칙",
            "lines": main_txt.count("\n") + 1,
            "why_lines": why_txt.count("\n") + 1,
            "numbered": len(seen), "documented": len(set(documented)),
            "changed": mtime(RULES_MAIN)}
    # 비어 있어도 "없음" 으로 적는다. 키를 빼면 평가 단계가 "집계만 있고 대조가
    # 없다" 고 읽어 판정을 보류한다 — 실제로는 이상이 없다는 뜻인데도 그렇다.
    item["근거_없는_번호"] = ",".join("R" + n for n in missing) or "없음"
    item["본문에_없는_근거"] = ",".join("R" + n for n in orphan) or "없음"
    item["본문에서_두_번_쓴_번호"] = (
        ",".join("R" + n for n in sorted(set(dup))) or "없음")
    # 본문 표식 수와 근거 절 수가 다른 것은 정상이다 — 폐기한 번호는 근거만 남긴다.
    item["폐기된_번호"] = ",".join("R" + n for n in sorted(set(retired))) or "없음"
    return [item]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=int, default=10000)
    a = ap.parse_args()

    items, problems = [], []
    # 규칙을 맨 앞에 둔다 — 크기 상한은 뒤에서부터 덜어내므로 뒤에 두면 먼저 잘린다.
    for fn in (rules, mcp_servers, skills, plugins):
        try:
            items.extend(fn())
        except Exception as exc:
            problems.append("%s: %s" % (fn.__name__, str(exc)[:120]))

    payload = {"host": os.uname().nodename, "items": items, "problems": problems,
               "collected": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    # 상한을 넘으면 설명이 긴 항목부터 줄이고, 줄였다는 사실을 남긴다.
    def size():
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    dropped = 0
    while size() > a.budget and len(payload["items"]) > 1:
        payload["items"].pop()
        dropped += 1
    if dropped:
        payload["truncated"] = "%d개 항목이 크기 상한으로 빠졌다" % dropped

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("설치물 %d건 (문제 %d, 생략 %d)" % (len(payload["items"]), len(problems), dropped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
