"""운영자 텔레그램 방 번호를 한 곳에서 읽는다.

왜 파일로 빼나: 그 번호는 운영자를 가리키는 값이라 저장소에 박아 두지 않는다(R76).
서른 군데에 같은 숫자가 흩어져 있으면 한 군데만 고쳐도 나머지가 옛 값으로 남는다.

왜 기본값을 안 두나: 기본값이 있으면 설정을 안 만든 기계가 조용히 남의 방으로 보낸다.
없으면 거절한다 — 알림이 안 가는 것은 화면에 안 보이므로, 안 가는 이유를 시끄럽게 남긴다.
"""
import json
import os

CONFIG = os.path.expanduser("~/.config/dotfiles-private/telegram.json")
KEY = "operator_chat_id"
ENV = "CLAUDE_OPERATOR_CHAT_ID"

HOWTO = (
    "운영자 텔레그램 방 번호가 없다. %s 에 다음을 적어라 (권한 600).\n"
    '  {"%s": "<방 번호>"}\n'
    "환경변수 %s 로도 넘길 수 있다." % (CONFIG, KEY, ENV)
)


class MissingOperatorChatId(RuntimeError):
    """설정이 없을 때. 메시지에 고치는 법이 들어 있다."""


def chat_id():
    """방 번호를 문자열로 돌려준다. 없으면 MissingOperatorChatId."""
    env = os.environ.get(ENV, "").strip()
    if env:
        return env
    try:
        with open(CONFIG, encoding="utf-8") as f:
            value = str(json.load(f).get(KEY, "")).strip()
    except (OSError, ValueError):
        value = ""
    if not value:
        raise MissingOperatorChatId(HOWTO)
    return value
