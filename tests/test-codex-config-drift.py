#!/usr/bin/env python3
"""두 호스트의 코덱스 모델 설정이 갈린 것을 감시가 잡는지 고정한다.

ai-debate 의 최종 정리자는 codex 단독이고, 그 모델과 강도는 저장소 밖 파일 하나
(~/.codex/config.toml)가 단일 출처다. 코덱스 화면에서 바꾸면 한쪽만 바뀌는데, 갈린 채로
돌아도 토론 결과만 보고는 못 가린다. 2026-09-23 에 손으로 점검했을 때는 같았지만 그건
그때의 사실일 뿐이다.

고정하는 것 여섯.

  1. 두 값이 같으면 조용하다.
  2. 원격 접속 실패는 어긋남이 아니라 미관측이다 — 알리지도, 어긋남 시계를 켜지도 않는다.
  3. 어긋나도 바로 안 알린다. 지속시간과 원격 관측 횟수를 둘 다 채워야 한 번 알린다.
  4. 같은 어긋남으로 두 번 알리지 않고, 해소되면 해소를 알린다.
  5. 설정 파일이 없거나 두 키를 못 읽는 것은 어긋남과 다른 고장으로 따로 센다.
  6. 알림과 관측 줄에 설정 원문이 실리지 않는다. 읽는 것은 두 키뿐이다.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
results = []
TMP = Path(tempfile.mkdtemp(prefix="codex-config-test-"))
REMOTE_SH = TMP / "remote.sh"
LOCAL_CFG = TMP / "config.toml"
BASE = 1790000000

os.environ["HOME_INFRA_WATCH_CODEX_CONFIG"] = str(LOCAL_CFG)
os.environ["HOME_INFRA_WATCH_CODEX_CONFIG_REMOTE"] = f"bash {REMOTE_SH}"
os.environ["HOME_INFRA_WATCH_CODEX_CONFIG_PERSIST_S"] = str(2 * 3600)
os.environ["HOME_INFRA_WATCH_NOW_EPOCH"] = str(BASE)


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


w = load("home_infra_watch_cfg", REPO / "bin" / "home-infra-watch")


def set_local(model="gpt-6-sol", effort="high", text=None, missing=False):
    if missing:
        LOCAL_CFG.unlink(missing_ok=True)
        return
    LOCAL_CFG.write_text(
        text if text is not None else
        f'model = "{model}"\nmodel_reasoning_effort = "{effort}"\n'
        'approval_policy = "never"\n', encoding="utf-8")


def set_remote(model="gpt-6-sol", effort="high", mode="ok"):
    if mode == "ssh-fail":
        body = "exit 255\n"
    elif mode == "nofile":
        body = "echo NOFILE\n"
    elif mode == "partial":
        body = f'echo \'model = "{model}"\'\n'
    else:
        body = (f'echo \'model = "{model}"\'\n'
                f'echo \'model_reasoning_effort = "{effort}"\'\n')
    REMOTE_SH.write_text(body, encoding="utf-8")


def at(seconds_from_base):
    os.environ["HOME_INFRA_WATCH_NOW_EPOCH"] = str(BASE + seconds_from_base)


def tick(st):
    alerts = []
    obs = w.check_codex_config(st, alerts)
    return obs, alerts


# --- 1. 같으면 조용하다 ----------------------------------------------------------
st = {}
set_local()
set_remote()
obs, alerts = tick(st)
check("같으면 알리지 않는다", alerts == [], str(alerts)[:200])
check("관측 줄에 동일이라고 남는다", "동일" in obs, obs)
check("관측 줄에 설정 원문이 안 실린다", "approval_policy" not in obs, obs)

# --- 2. 원격 접속 실패는 미관측 --------------------------------------------------
st = {}
set_remote(mode="ssh-fail")
obs, alerts = tick(st)
check("원격 접속 실패는 알리지 않는다", alerts == [], str(alerts)[:200])
check("미관측이라고 남는다", "미관측" in obs, obs)
check("어긋남 시계를 켜지 않는다", "codex_config" not in st, str(st)[:200])

# --- 3~4. 어긋남은 지속시간과 관측 횟수를 둘 다 채워야 알린다 --------------------
st = {}
set_remote(model="gpt-6-astra")
obs, alerts = tick(st)
check("첫 관측에는 안 알린다", alerts == [], str(alerts)[:200])
check("그래도 갈렸다고 남긴다", "갈림" in obs, obs)

at(60)
obs, alerts = tick(st)
check("두 번째 관측이어도 두 시간 전엔 안 알린다", alerts == [], str(alerts)[:200])

st2 = dict(st)
st2["codex_config"] = dict(st2["codex_config"], seen=0)
at(2 * 3600 + 60)
obs, alerts = tick(st2)
check("두 시간이 지나도 관측 두 번을 못 채우면 안 알린다", alerts == [], str(alerts)[:200])

at(2 * 3600 + 120)
obs, alerts = tick(st)
check("두 시간 + 관측 두 번이면 한 번 알린다", len(alerts) == 1, str(alerts)[:200])
msg = alerts[0] if alerts else ""
check("알림에 두 호스트 값이 들어간다",
      "gpt-6-sol" in msg and "gpt-6-astra" in msg, msg[:200])
check("알림에 설정 원문이 안 실린다", "approval_policy" not in msg, msg[:200])

at(3 * 3600)
obs, alerts = tick(st)
check("같은 어긋남으로 두 번 알리지 않는다", alerts == [], str(alerts)[:200])

set_remote()
at(3 * 3600 + 60)
obs, alerts = tick(st)
check("해소되면 해소를 알린다", len(alerts) == 1 and "해소" in alerts[0], str(alerts)[:200])
check("해소 뒤 상태가 지워진다", "codex_config" not in st, str(st)[:200])

# --- 5. 못 읽는 것은 어긋남과 다른 고장 ------------------------------------------
st = {}
set_remote(mode="nofile")
obs, alerts = tick(st)
check("설정이 없어도 첫 회차엔 안 알린다", alerts == [], str(alerts)[:200])
obs, alerts = tick(st)
check("두 번 연속이면 못 읽는다고 알린다", len(alerts) == 1, str(alerts)[:200])
check("그 알림은 어긋남이 아니라 읽기 고장이다",
      "못 읽는다" in alerts[0] if alerts else False, str(alerts)[:200])
check("못 읽는 동안 어긋남 시계는 안 돈다", "codex_config" not in st, str(st)[:200])

st = {}
set_remote()
set_local(missing=True)
tick(st)
obs, alerts = tick(st)
# 알림이 이 기계를 가리키는지만 본다. 이름 자체는 기계마다 다르므로 글자로
# 박지 않는다 — 박으면 그 검사는 어느 기계에서 도느냐로 붙었다 떨어졌다 한다.
check("이쪽 설정이 없어도 잡는다",
      len(alerts) == 1 and w.LOCAL_LABEL in alerts[0], str(alerts)[:200])

# --- 6. 값의 글자를 좁게 잡는다 --------------------------------------------------
# 이 검사는 알림에 값을 싣기 때문에, 모델 이름·강도로 쓰일 수 있는 글자만 통과시킨다.
set_local(text='model = "gpt-6-sol"\nmodel_reasoning_effort = "sk-live AAAA BBBB"\n')
st = {}
set_remote()
tick(st)
obs, alerts = tick(st)
check("값이 예상 글자를 벗어나면 읽기실패로 본다",
      len(alerts) == 1 and "못 읽는다" in alerts[0], str(alerts)[:200])
check("그 값이 알림에 안 실린다", "sk-live" not in (alerts[0] if alerts else ""),
      str(alerts)[:200])

check("두 키만 뽑는다",
      w.parse_codex_config('model = "a"\nmodel_reasoning_effort = "b"\nx = "c"\n')
      == {"model": "a", "model_reasoning_effort": "b"})
check("한 키만 있으면 못 읽은 것으로 본다",
      w.parse_codex_config('model = "a"\n') == {})

# --- 7. 기본 원격 명령은 그 파일의 두 줄만 가져온다 ------------------------------
# 같은 폴더에 자격증명 파일(auth.json)이 있다. 통째로 가져오면 그게 이 프로세스로 들어온다.
src = (REPO / "bin" / "home-infra-watch").read_text(encoding="utf-8")
default_cmd = src.split('"HOME_INFRA_WATCH_CODEX_CONFIG_REMOTE",', 1)[1].split(")", 1)[0]
check("기본 원격 명령이 config.toml 만 연다",
      "auth.json" not in default_cmd and "config.toml" in default_cmd, default_cmd[:200])
check("기본 원격 명령이 두 키만 거른다",
      "grep" in default_cmd and "model_reasoning_effort" in default_cmd, default_cmd[:200])
check("파일을 통째로 옮기지 않는다",
      "cat " not in default_cmd and "scp" not in default_cmd, default_cmd[:200])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
