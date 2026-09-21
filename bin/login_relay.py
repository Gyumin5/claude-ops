"""login_relay — `claude auth login` 을 격리 HOME 에서 구동하는 릴레이.

흐름: 격리 HOME 에서 `claude auth login --claudeai` 를 pty 로 띄움
  → device URL 을 스크랩해 운영자(텔레그램)에게 push
  → 운영자가 브라우저서 승인 후 보낸 코드를 자식 stdin 에 주입
  → 로그인 성공 시 격리 HOME 의 .credentials.json 경로를 on_done 으로 넘김.

격리 HOME 인 이유(gate2, 2026-06-19 검증): 그 안엔 telegram plugin·봇토큰이 없어
자식 로그인 프로세스가 getUpdates 를 폴링할 경로 자체가 없음 → 라이브 컨트롤봇과
409 충돌 불가. creds 도 격리 HOME 으로 써져 실 ~/.claude 를 직접 건드리지 않음
(적용=on_done 가 스냅샷/live 로 복사).

보안: 코드·creds 내용은 절대 로그/출력하지 않음. 단일 진행만(single-flight).
모든 종료 경로에서 자식 프로세스를 kill(고아 방지). 코드/URL 타임아웃.
"""
import os
import pty
import re
import select
import shutil
import signal
import tempfile
import threading
import time

# device URL: 관측된 실 URL = https://claude.com/cai/oauth/authorize?code=true&...
# claude.ai / claude.com / anthropic 도메인의 oauth/authorize/device 링크를 잡는다.
# \S 대신 "공백·제어문자 아님"으로 잡는다 — ESC(0x1b) 도 \S 라서 예전 패턴은 터미널
# 하이퍼링크 이스케이프를 넘어 URL 두 벌과 꼬리표까지 한 덩어리로 삼켰다(2026-07-29 신고).
URL_RE = re.compile(r"https://[^\s\x00-\x20\x7f]*(?:claude\.(?:ai|com)|anthropic)[^\s\x00-\x20\x7f]*", re.I)

# claude CLI 는 URL 을 OSC 8 하이퍼링크로 출력한다:
#   ESC]8;;<URL>ESC\ <보이는 텍스트> ESC]8;;ESC\
# pty 로 받으면 이 이스케이프가 그대로 들어오고, 텔레그램으로 보내면서 ESC 만 빠져
# "URL URL ]8;;" 처럼 보인다. 그래서 URL 을 찾기 전에 이스케이프를 걷어낸다.
ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC (하이퍼링크·타이틀 등)
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"          # CSI (색상·커서)
    r"|\x1b[@-Z\\-_]"                      # 단일문자 ESC 시퀀스
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"   # 기타 제어문자(줄바꿈·탭은 보존)
)


def strip_ansi(s):
    """터미널 제어 시퀀스 제거. 줄바꿈·탭은 남긴다."""
    return ANSI_RE.sub("", s)

DEFAULT_LOGIN_CMD = ["claude", "auth", "login", "--claudeai"]


def _reap(pid, grace=0.5):
    """자식을 reap. 살아있으면 SIGTERM→SIGKILL. 블로킹 안 함. exit_code 반환."""
    def _waitnb():
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            return True, -1
        if done == 0:
            return False, None
        if os.WIFEXITED(status):
            return True, os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return True, -os.WTERMSIG(status)
        return True, -1

    reaped, code = _waitnb()
    if reaped:
        return code
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        for _ in range(int(grace / 0.05) + 1):
            reaped, code = _waitnb()
            if reaped:
                return code
            time.sleep(0.05)
    return -1


class LoginRelay:
    """단일 진행 로그인 릴레이. 컨트롤봇이 싱글톤으로 보유."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = None  # 진행 중일 때 state dict

    def is_active(self):
        with self._lock:
            return self._active is not None

    def status(self):
        with self._lock:
            if self._active is None:
                return None
            return {"target": self._active["target"], "state": self._active["state"]}

    def start(self, target, send, on_done, login_cmd=None,
              url_timeout=25.0, code_timeout=300.0):
        """백그라운드 스레드로 로그인 시작. (ok, msg) 반환(즉시).

        send(text): 운영자에게 메시지 push (텔레그램).
        on_done(target, success, cred_path, detail): 종료 콜백. success 시
            cred_path=격리 HOME 의 .credentials.json (없으면 None). 이 콜백은
            격리 HOME 정리 전에 호출되므로 그 안에서 즉시 복사해야 함.
        """
        with self._lock:
            if self._active is not None:
                return False, "이미 진행 중인 로그인이 있어요. 끝나거나 취소 후 다시."
            ev = threading.Event()
            st = {"target": target, "code": None, "code_event": ev,
                  "state": "STARTING"}
            self._active = st
        t = threading.Thread(
            target=self._run,
            args=(st, target, send, on_done, login_cmd or DEFAULT_LOGIN_CMD,
                  url_timeout, code_timeout),
            daemon=True,
        )
        t.start()
        return True, "로그인 시작 — device URL 기다리는 중…"

    def submit_code(self, code):
        """운영자가 보낸 코드 주입. 코드를 기다리는 중일 때만 수락."""
        with self._lock:
            st = self._active
        if st is None or st["state"] != "WAITING_CODE":
            return False
        st["code"] = (code or "").strip()
        st["code_event"].set()
        return True

    def cancel(self):
        with self._lock:
            st = self._active
        if st is None:
            return False
        st["state"] = "CANCELLED"
        st["code_event"].set()
        return True

    def _finish(self):
        with self._lock:
            self._active = None

    def _run(self, st, target, send, on_done, login_cmd, url_timeout, code_timeout):
        probe_home = tempfile.mkdtemp(prefix="login-relay-home.")
        os.makedirs(os.path.join(probe_home, ".claude"), exist_ok=True)
        cred_file = os.path.join(probe_home, ".claude", ".credentials.json")
        url = None
        success = False
        detail = ""
        pid = fd = None
        try:
            pid, fd = pty.fork()
            if pid == 0:  # 자식: 격리 HOME 으로 로그인 명령 exec
                os.environ["HOME"] = probe_home
                # 브라우저 창을 띄우지 않는다 (사용자 지시 2026-08-04). claude CLI 는
                # device URL 을 낼 때 브라우저를 자동으로 여는데, 이 릴레이는
                # systemd --user 서비스라 DISPLAY=:1 을 그대로 물려받아 사용자
                # 데스크탑에 창이 뜬다. URL 은 어차피 텔레그램으로 보내니 창은 방해다.
                # --no-browser 류 플래그가 없어서(claude auth login --help 확인)
                # 환경에서 막는다: GUI 좌표를 지우고 BROWSER 도 무해한 것으로 둔다.
                for _v in ("DISPLAY", "WAYLAND_DISPLAY",
                           "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE"):
                    os.environ.pop(_v, None)
                os.environ["BROWSER"] = "/bin/true"
                try:
                    os.execvp(login_cmd[0], login_cmd)
                except Exception:
                    os._exit(127)
            # 부모
            buf = ""
            deadline = time.monotonic() + url_timeout
            while time.monotonic() < deadline:
                r, _, _ = select.select([fd], [], [], 0.2)
                if fd in r:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk.decode(errors="replace")
                    m = URL_RE.search(strip_ansi(buf))
                    if m:
                        # 줄바꿈으로 잘린 URL 이 이어 붙는 걸 기다리지 않는다 — 이 URL 은
                        # 한 줄로 나온다(실측). 꼬리 구두점만 정리한다.
                        url = m.group(0).rstrip(").,'\"]}>")
                        break
            if not url:
                detail = "device URL 못 찾음 — 로그인 플로우 불일치/실행 실패."
                raise RuntimeError(detail)

            st["state"] = "WAITING_CODE"
            # 안내문과 URL 을 분리해 보낸다. 한 메시지에 섞으면 텔레그램에서 URL 만
            # 집어내기 어려워 복사가 번거롭다(사용자 요청 2026-07-30). URL 을 뒤에 두어
            # 마지막 메시지가 URL 단독이 되게 한다.
            send("로그인 URL 을 다음 메시지로 보낸다. 창은 열지 않으니 직접 열어서"
                 " 승인한 뒤, 받은 코드를 그대로 여기에 보내줘"
                 " (5분 내, 취소하려면 /login_cancel).")
            send(url)

            got = st["code_event"].wait(timeout=code_timeout)
            if st.get("state") == "CANCELLED":
                detail = "사용자 취소."
                raise RuntimeError(detail)
            if not got or not st.get("code"):
                detail = "코드 입력 시간초과(5분)."
                raise RuntimeError(detail)

            # 코드 주입 (코드 값은 로그에 남기지 않음)
            os.write(fd, (st["code"] + "\n").encode())
            st["state"] = "INJECTED"

            # 결과 드레인
            end = time.monotonic() + 15.0
            while time.monotonic() < end:
                r, _, _ = select.select([fd], [], [], 0.3)
                if fd in r:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk.decode(errors="replace")

            exit_code = _reap(pid)
            pid = None
            if exit_code == 0:
                success = True
                detail = "로그인 성공."
            else:
                success = False
                detail = f"로그인 실패 (exit {exit_code}) — 코드 오류/만료 가능."
        except Exception as e:
            success = False
            if not detail:
                detail = f"릴레이 오류: {e}"
        finally:
            # 어떤 경로로든 자식 정리(고아 방지)
            if pid is not None:
                _reap(pid)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                cred_path = cred_file if os.path.exists(cred_file) else None
                # 성공 시 on_done 가 격리 HOME 정리 전에 creds 복사하도록 먼저 호출
                on_done(target, success, cred_path, detail)
            except Exception:
                pass
            shutil.rmtree(probe_home, ignore_errors=True)
            self._finish()
