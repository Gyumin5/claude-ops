#!/usr/bin/env python3
"""bin/claude-usage-gauge 검사.

보는 것은 넷이다. 글줄을 막대로 바로 읽나, 남은 시간을 한국 시간으로 재나, 못 알아본 줄을
삼키지 않나, 그리고 못 박힌 열쇠가 그 막대를 거쳐 내보내나.
"""

import importlib.util
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAUGE = ROOT / "bin" / "claude-usage-gauge"
INSTALL = ROOT / "bin" / "claude-usage-serve-install"
KST = timezone(timedelta(hours=9))

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  — " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


def load(path):
    spec = importlib.util.spec_from_loader(path.stem, SourceFileLoader(path.stem, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE = "\n".join([
    "계정별 사용량 (2026-11-06(금) 12:14 KST)",
    "acct-a  5시간 45% (13:30 초기화)  7일 48% (11-07(토) 04:00 초기화)  방금 값",
    "acct-c  7일 한도를 다 썼다 — 11-09(월) 18:00 풀림",
    "방금 쟀다",
])

print("=== 글줄을 막대로 ===")
g = load(GAUGE)
now = datetime(2026, 11, 6, 13, 3, tzinfo=KST)
out = g.render(SAMPLE, now=now, color=False)
lines = out.splitlines()

check("계정마다 이름 한 줄과 막대 두 줄이다",
      len(lines) == 7 and lines[3] == "", repr(lines))
check("제목 줄과 잰 시각 줄은 숨긴다",
      "계정별 사용량" not in out and "방금 쟀다" not in out)
check("계정 이름이 혼자 윗줄에 선다", lines[0] == "acct-a", repr(lines[0]))
check("다섯 시간 값이 그 아래다", lines[1].strip().startswith("5h") and " 45% " in lines[1], lines[1])
check("7일 값이 그 아래다", lines[2].strip().startswith("1w") and " 48% " in lines[2], lines[2])
# 라벨은 라틴 글자 두 칸이다 — 한글 라벨은 텔레그램 고정폭에서 폭이 라틴의 정확히
# 두 배가 아니라 막대가 줄마다 어긋나 보인다(운영자 지적 8475).
check("라벨에 한글을 안 쓴다",
      all(ord(c) < 128 for c in g.LABEL5 + g.LABEL7), (g.LABEL5, g.LABEL7))
check("막대는 쓴 만큼 채운다",
      lines[1].count(g.FILLED) == 5 and lines[1].count(g.EMPTY) == 5,
      "{}/{}".format(lines[1].count(g.FILLED), lines[1].count(g.EMPTY)))
check("남은 시간을 한국 시간으로 잰다", "27분 뒤" in lines[1], lines[1])
check("하루가 넘으면 날과 시간으로 적는다", "14시간 57분 뒤" in lines[2], lines[2])
# 폰 화면 고정폭 블록에 들어가는 칸이 마흔 안팎이다. 넘으면 막대가 접혀 두 토막 난다.
check("한 줄이 마흔 칸을 안 넘는다",
      max(g.width(x) for x in lines) <= 40, max(g.width(x) for x in lines))

check("한도를 다 쓴 계정은 다섯 시간 줄에 한도 소진",
      "한도 소진" in lines[5] and lines[5].count(g.FILLED) == 10, lines[5])
check("한도를 다 쓴 계정의 7일 줄은 풀릴 때까지",
      "3일 4시간 뒤" in lines[6] and lines[6].count(g.FILLED) == 10, lines[6])

print("=== 줄 맞춤과 색 ===")
check("남은 시간 칸은 글자 수가 아니라 칸 수로 맞춘다",
      g.width("27분") == 4 and g.rpad("27분", 11) == " " * 7 + "27분",
      repr(g.rpad("27분", 11)))
check("두 막대 줄의 오른쪽 끝이 같다",
      g.width(lines[1]) == g.width(lines[2]),
      "{} vs {}".format(g.width(lines[1]), g.width(lines[2])))
check("반 칸은 올린다", g.bar(45, color=False).count(g.FILLED) == 5
      and g.bar(55, color=False).count(g.FILLED) == 6)
check("예순 미만은 초록", g.level_color(59) == "46;160;67", g.level_color(59))
check("여든다섯 미만은 노랑", g.level_color(84) == "210;153;34", g.level_color(84))
check("그 위는 빨강", g.level_color(85) == "203;36;49", g.level_color(85))
check("색을 끄면 제어문자가 없다", "\033" not in out)
colored = g.render(SAMPLE, now=now, color=True)
check("색을 켜면 24비트 색을 쓴다", "\033[38;2;46;160;67m" in colored)

print("=== 시각 읽기 ===")
check("시각만 적힌 줄은 오늘로 읽는다",
      g.parse_reset("13:30", now) == datetime(2026, 11, 6, 13, 30, tzinfo=KST))
check("이미 지난 시각은 다음 날로 읽는다",
      g.parse_reset("12:00", now) == datetime(2026, 11, 7, 12, 0, tzinfo=KST))
check("요일이 붙은 날짜도 읽는다",
      g.parse_reset("11-07(토) 04:00", now) == datetime(2026, 11, 7, 4, 0, tzinfo=KST))
check("해를 넘긴 날짜는 앞날로 읽는다",
      g.parse_reset("01-02 18:00", now) == datetime(2027, 1, 2, 18, 0, tzinfo=KST))
check("못 읽는 시각은 지어내지 않는다", g.parse_reset("알 수 없음", now) is None)
check("시각을 못 읽으면 그렇다고 적는다", g.remaining(None, now) == "시각 모름")
check("일 분이 안 남으면 곧이라고 적는다",
      g.remaining(now + timedelta(seconds=20), now) == "곧")

print("=== 7일 칸만 온 계정 ===")
# 7일가 거의 다 찬 계정은 다섯 시간 창이 안 열려서 그 칸이 아예 안 온다. 없는 칸을
# 백으로 채우면 쓰지도 않은 양을 쓴 것처럼 보인다(11-06 ogm3614, 7일 99퍼센트).
ONLY7 = "ogm3614  7일 99% (11-07(토) 04:00 초기화)  방금 값"
o7 = g.render(ONLY7, now=now, color=False).splitlines()
check("이름 한 줄과 7일 한 줄만 낸다", len(o7) == 2 and o7[0] == "ogm3614", repr(o7))
check("7일 줄에 수치와 남은 시간이 있다",
      o7[1].strip().startswith("1w") and " 99% " in o7[1] and "14시간 57분 뒤" in o7[1], o7[1])
check("다섯 시간 줄을 지어내지 않는다", "5h" not in "\n".join(o7), repr(o7))
o7f = g.render(ONLY7, now=now, color=False, bars=False).splitlines()
check("막대 없는 쪽도 한 줄만 낸다",
      o7f == ["ogm3614", "7일 99% · 14시간 57분 뒤"], repr(o7f))

print("=== 못 알아본 줄 ===")
other = g.render("무슨 까닭으로 못 쟀다\n\nsw9  이상한 줄", now=now, color=False)
check("못 알아본 줄은 그대로 내보낸다",
      "무슨 까닭으로 못 쟀다" in other and "sw9  이상한 줄" in other, repr(other))
check("빈 글줄이면 아무것도 안 그린다", g.render("   ", now=now, color=False) == "")

p = subprocess.run([sys.executable, str(GAUGE), "--no-color"], input=SAMPLE,
                   capture_output=True, text=True, timeout=30)
check("명령으로 불러도 같은 모양이다", p.returncode == 0 and "한도 소진" in p.stdout,
      p.stdout.strip()[:80])
p = subprocess.run([sys.executable, str(GAUGE)], input="", capture_output=True,
                   text=True, timeout=30)
check("들어온 것이 없으면 실패로 끝낸다", p.returncode == 1)

# 컨트롤봇은 이 글줄을 텔레그램으로 그대로 옮긴다 — 거기엔 색 제어문자를 그릴 자리가 없다.
p = subprocess.run([sys.executable, str(GAUGE)], input=SAMPLE,
                   capture_output=True, text=True, timeout=30)
check("화면이 아니면 색을 안 넣는다", p.returncode == 0 and "\033" not in p.stdout,
      repr(p.stdout[:60]))
check("색을 빼도 막대는 그대로 그린다", "▰" in p.stdout and "▱" in p.stdout,
      p.stdout.strip()[:80])
p = subprocess.run([sys.executable, str(GAUGE), "--color"], input=SAMPLE,
                   capture_output=True, text=True, timeout=30)
check("색을 켜라고 하면 화면이 아니어도 넣는다", "\033[38;2;" in p.stdout,
      repr(p.stdout[:60]))

print("=== 막대 없는 모양 (텔레그램) ===")
# 텔레그램 고정폭 글꼴은 한글 폭이 라틴의 정확히 두 배가 아니라, 칸을 맞출수록
# 줄마다 어긋나 보인다. 그래서 막대를 뺄 때는 칸 맞춤도 같이 뺀다(운영자 지시).
flat = g.render(SAMPLE, now=now, color=False, bars=False).splitlines()
check("막대 기호가 하나도 없다",
      all(g.FILLED not in x and g.EMPTY not in x for x in flat), repr(flat))
check("계정 이름이 혼자 윗줄에 선다", flat[0] == "acct-a", repr(flat[0]))
check("두 줄이 수치와 남은 시간을 그대로 담는다",
      flat[1] == "5시간 45% · 27분 뒤" and flat[2] == "7일 48% · 14시간 57분 뒤",
      repr(flat[1:3]))
check("줄머리를 빈칸으로 맞추지 않는다",
      not any(x.startswith(" ") for x in flat if x), repr(flat))
check("한도를 다 쓴 계정은 한 줄만 낸다",
      flat[4] == "acct-c" and flat[5] == "7일 한도 소진 · 3일 4시간 뒤 풀림" and len(flat) == 6,
      repr(flat[4:]))
p = subprocess.run([sys.executable, str(GAUGE), "--no-bar"], input=SAMPLE,
                   capture_output=True, text=True, timeout=30)
check("명령으로 불러도 막대가 안 나온다",
      p.returncode == 0 and g.FILLED not in p.stdout, repr(p.stdout[:80]))

print("=== 한 서식으로 모은 뒤 (운영자 8754) ===")
# 텔레그램으로 가는 글은 위아래가 같은 모양이어야 한다. 수치가 있는 계정과 없는 계정,
# 지금 쓰는 계정과 묵은 값이 전부 "이름 한 줄 + 아래 몇 줄" 로 선다.
MIXED = "\n".join([
    "계정별 사용량 (2026-11-06(금) 12:14 KST)",
    "acct-a      5시간 45% (13:30 초기화)  7일 48% (11-07(토) 04:00 초기화)  방금 값",
    "acct-b      7일 50% (11-07(토) 04:00 초기화)  3시간 12분 전 값",
    "gmoh     5시간 30% (13:30 초기화)  7일 15% (11-07(토) 04:00 초기화)  방금 값 (지금 이 계정)",
    "ogm3614  수치 없음 — 7일 한도가 05:00 에 풀렸고 그 뒤로 안 쟀다 (지금 이 계정)",
    "acct-d      로그인 안 함 — 그 홈에 한 번 로그인해야 수치가 생긴다",
])
mixed = g.render(MIXED, now=now, color=False, bars=False).splitlines()
check("막대 없는 쪽 라벨에 영어 줄임말이 없다",
      "5h" not in "\n".join(mixed) and "1w" not in "\n".join(mixed), repr(mixed))
check("지금 쓰는 계정 표식은 이름 줄로 올라간다",
      "gmoh (지금 이 계정)" in mixed, repr(mixed))
check("묵은 값이면 이름 줄에 그 나이를 적는다",
      "acct-b (3시간 12분 전 값)" in mixed, repr(mixed))
check("방금 잰 값에는 나이를 안 적는다", "acct-a" in mixed, repr(mixed))
i = mixed.index("ogm3614 (지금 이 계정)")
check("수치 없는 계정도 이름 줄과 까닭 줄로 선다",
      mixed[i + 1] == "수치 없음 — 7일 한도가 05:00 에 풀렸고 그 뒤로 안 쟀다", repr(mixed[i:i + 2]))
check("표식을 까닭 줄에 두 번 적지 않는다",
      mixed[i + 1].count("지금 이 계정") == 0, repr(mixed[i + 1]))
j = mixed.index("acct-d")
check("로그인 안 한 계정도 같은 모양이다",
      mixed[j + 1].startswith("로그인 안 함"), repr(mixed[j:j + 2]))
bar_mixed = g.render(MIXED, now=now, color=False, bars=True).splitlines()
check("막대 쪽은 수치 없는 줄을 안 접는다",
      any(x.startswith("ogm3614  수치 없음") for x in bar_mixed), repr(bar_mixed))

print("=== 못 박힌 열쇠가 거치는 길 ===")
src = INSTALL.read_text(encoding="utf-8")
shim = src.split('cat > "$HOME/.local/bin/claude-usage"', 1)[1].split("\nEOF\n", 1)[0]
check("열쇠가 내보내는 글이 막대를 거친다", "claude-usage-gauge" in shim, shim.strip()[-80:])
check("막대가 실패하면 받은 글줄을 그대로 낸다",
      re.search(r"claude-usage-gauge[^\n]*\|\|\s*printf", shim) is not None)
check("raw 를 붙이면 글줄 그대로 받는다", "raw" in shim and "SSH_ORIGINAL_COMMAND" in shim)
check("plain 을 붙이면 막대 없는 글을 받는다",
      '" plain "' in shim and "--no-bar" in shim, shim.strip()[-200:])
check("쪽이 내는 글줄 자체는 안 바꾼다",
      "claude-usage-gauge" not in (ROOT / "bin" / "claude-usage-web").read_text(encoding="utf-8"))

print()
print("실패 {}건".format(len(fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
