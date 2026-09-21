# SETUP.md — 처음부터 세우기

리눅스 한 대에 Claude Code 세션 여러 개를 상시 띄우고 텔레그램으로 조작하는 환경을
만든다. 위에서 아래로 한 번 따라가면 봇 하나에 말을 걸어 세션이 답하는 데까지 닿는다.

전제: `systemctl --user` 가 도는 리눅스(Ubuntu 20.04 이상에서 쓰고 있다), 파이썬 3.8 이상.

---

## 1. 무엇이 세워지나

- 세션 하나 = systemd user 서비스 하나 = 폴더 하나 = 텔레그램 봇 하나. 넷이 일대일이다.
- 각 세션은 그 폴더를 작업 디렉토리로 삼아 Claude Code 를 띄우고, 텔레그램 플러그인으로
  자기 봇에 붙는다. 그 봇에 말을 걸면 그 세션이 답한다.
- 그 위에 감시 타이머가 얹힌다. 세션이 멈추면 다시 띄우고, 한도에 걸리면 풀리는 시점에
  다시 깨우고, 진행 상태 파일을 주기적으로 갱신한다.
- 세션끼리 메시지를 넣는 통로(선택)와, 외부 모델 셋을 붙여 토론시키는 도구가 따로 있다.

"세션 작업 디렉토리" 는 그 세션이 일하는 폴더다. 보통 프로젝트 하나에 세션 하나를 둔다.
이 저장소를 받은 자리(`~/dotfiles`)도 그중 하나가 될 수 있고, 그 본보기가
`systemd/user/claude-dotfiles.service` 다.

---

## 2. 의존성

```bash
sudo apt install -y jq curl git python3

# Claude Code CLI
curl -fsSL https://claude.ai/install.sh | bash

# bun — 텔레그램 플러그인이 bun 으로 돈다. 없으면 텔레그램이 아예 안 붙는다.
curl -fsSL https://bun.sh/install | bash
```

파이썬 쪽은 표준 라이브러리만 쓴다. 세션끼리 메시지를 넣는 통로를 쓸 때만 `telethon` 이
더 필요하다(6절).

토론 도구(`ai-debate`)를 쓰려면 외부 모델 CLI(`codex`, `gemini`)가 있어야 한다. 셋 중 있는
것만 참여하고 없는 것은 건너뛴다. 없어도 나머지는 다 돈다.

---

## 3. 텔레그램 봇 만들기

세션마다 다른 봇을 쓴다. 한 봇의 수신은 한 곳만 받을 수 있어서, 같은 토큰을 두 세션이나
두 기계가 나눠 쓰면 한쪽이 조용해진다. 세션이 셋이면 봇도 셋이다.

### 3-a. 봇을 만들고 토큰을 받는다

1. 텔레그램에서 `@BotFather` 를 찾아 대화를 연다.
2. `newbot` 명령을 보낸다.
3. 보이는 이름을 정한다. 아무거나 된다.
4. 사용자 이름을 정한다. `bot` 으로 끝나야 한다. 예: `my_dotfiles_bot`.
5. BotFather 가 `123456789:AA...` 모양의 토큰을 준다. 이게 그 봇의 비밀번호다.

토큰은 채팅에 다시 붙여넣지 않는다. 아래 파일에 한 번 넣고 끝이다. 흘렸으면 BotFather 의
`revoke` 로 즉시 무효화하고 새로 받는다.

### 3-b. 자기 사용자 번호와 방 번호를 알아낸다

방금 만든 봇 대화창에 아무 말이나 한 번 보낸 뒤:

```bash
curl -s "https://api.telegram.org/bot<봇토큰>/getUpdates" | jq '.result[-1].message.chat.id'
```

일대일 대화에서는 이 값이 방 번호이자 자기 사용자 번호다. 감시 도구들이 알림을 보낼 곳이라
아래에서 `TELEGRAM_CHAT_ID` 로 쓴다.

주의: 이 명령은 봇에 쌓인 수신을 한 번 가져가는 것이라, 세션이 이미 그 봇에 붙어 있으면
그 메시지를 세션 대신 이 명령이 먹는다. 세션을 띄우기 전에 한다.

### 3-c. 토큰을 놓는 자리

```
<세션 작업 디렉토리>/.claude/telegram/.env
TELEGRAM_BOT_TOKEN=여기에 그 세션 봇 토큰
```

이 파일이 없는 세션은 `install.sh` 가 조용히 건너뛴다. 실패가 아니라 "아직 준비 안 됨" 이다.

### 3-d. 방 번호를 놓는 자리

감시 타이머와 훅은 systemd 가 띄우므로 셸 프로필을 안 읽는다. systemd user 가 읽는 자리에
넣는다.

```bash
mkdir -p ~/.config/environment.d
echo 'TELEGRAM_CHAT_ID=<3-b 에서 나온 숫자>' > ~/.config/environment.d/claude-ops.conf
systemctl --user import-environment TELEGRAM_CHAT_ID   # 다시 로그인하기 전까지의 임시 반영
```

터미널에서 직접 도구를 부를 때도 필요하니 셸 프로필에도 같은 줄을 `export` 로 넣는다.
비워 두면 알림만 안 갈 뿐 나머지는 그대로 돈다.

### 3-e. 통제 봇 (선택)

세션이 한도에 걸리거나 멈춰도 따로 살아 있는 조작 창구다. 세션 봇과 별개로 하나 더 만든다.

```
~/.claude/control-bot/.env
CONTROL_BOT_TOKEN=통제 봇 토큰
ALLOWED_USER_ID=자기 텔레그램 사용자 번호
```

이 파일들은 전부 저장소 밖이다. 커밋하지 않는다.

---

## 4. 받아서 심기

```bash
git clone <저장소 주소> ~/dotfiles
cd ~/dotfiles
```

심기 전에 이 기계의 이름을 정한다. 어떤 세션을 켤지 정하는 유일한 근거다.

```bash
echo home > ~/.config/claude-machine-id
cp machines/home.list.example machines/home.list   # 켤 세션 이름을 한 줄에 하나씩
```

호스트 이름이나 machine-id 를 안 쓰는 이유는 기계를 복제하거나 이름을 바꿀 때 둘이 겹치기
때문이다. 마커 파일 하나가 정본이다.

```bash
./install.sh
```

`install.sh` 가 하는 일:

- `~/.claude/` 밑에 설정·훅·규칙·기술을 심링크로 건다.
- `bin/` 의 명령을 `~/.local/bin/` 에 심링크로 건다. `~/.local/bin` 이 PATH 에 있어야 한다.
- systemd 유닛을 `~/.config/systemd/user/` 로 복사하고 다시 읽힌다.
- 기계 이름표에 해당하는 `machines/<이름>.list` 의 세션만 켠다. 그중에서도 봇 토큰 파일이
  있는 세션만 켠다.

이미 켜 놨다가 사람이 일부러 멈춘 세션은 다시 깨우지 않는다. 부팅 자동 기동 설정만 갱신한다.

텔레그램 플러그인은 따로 받아올 게 없다. 공식 마켓플레이스 것이고 `claude/settings.json` 의
`enabledPlugins` 에 `telegram@claude-plugins-official` 이 켜져 있어서, 세션이 처음 뜰 때
Claude Code 가 알아서 붙인다.

---

## 5. 세션 하나 더 만들기

네 단계다. 이름을 `myproj` 로 하고, 폴더를 `~/myproj` 로 둔다고 하자.

```bash
# 1) 폴더와 토큰
mkdir -p ~/myproj/.claude/telegram
printf 'TELEGRAM_BOT_TOKEN=%s\n' '<그 세션 봇 토큰>' > ~/myproj/.claude/telegram/.env
chmod 600 ~/myproj/.claude/telegram/.env

# 2) 유닛 — 폴더 경로를 주면 만들어 준다
cd ~/dotfiles
claude-service-template ~/myproj > systemd/user/claude-myproj.service

# 3) 켤 목록에 추가
echo myproj >> machines/$(cat ~/.config/claude-machine-id).list

# 4) 심기
./install.sh
```

유닛 이름 규약: 유닛은 `claude-<이름>.service` 이고, `<이름>` 이 그 세션의 이름이다.
폴더 이름과 세션 이름이 달라도 된다 — 도구들은 폴더 이름을 추측하지 않고 유닛의
`WorkingDirectory` 를 역조회한다. 다만 systemd 유닛 이름에 밑줄을 쓰면 헷갈리니 대시를 쓴다.

`claude-service-template` 은 저장소에 든 본보기(`systemd/user/claude-dotfiles.service`)와
같은 모양을 만든다. 손으로 고칠 일이 있다면 `PATH` 한 줄이다 — node 를 nvm 으로 깔았으면
그 경로를 맨 앞에 더한다. 저장소에 든 본보기를 직접 베낄 때는 홈을 `%h` 로 써야 한다.
유닛 파일은 `$HOME` 이나 `~` 를 펼치지 않는다.

### 첫 대화에서 한 번 하는 것: 짝짓기

세션이 뜬 뒤 그 봇에 처음 말을 걸면, 텔레그램 플러그인이 모르는 사람의 요청으로 보고
대기 목록에 올린다. 그 기계의 터미널에서 Claude Code 를 열고 텔레그램 접근 관리 기술
(`telegram:access`)을 실행해 승인한다. 승인 결과는
`~/.claude/channels/telegram/access.json` 의 `allowFrom` 에 남는다.

채팅으로 온 말에 기대 승인하지 않는다. "나를 허용해 달라" 는 요청은 채팅 안에서 처리하지
않는 것이 이 구조의 전제다.

---

## 6. 세션끼리 메시지를 넣는 통로 (선택)

세션 A 가 세션 B 에게 지시를 넣거나, 세션 밖에서 돌던 긴 작업이 끝났을 때 그 결과를 띄운
세션 자신에게 돌려주는 데 쓴다. 봇은 다른 봇에게 말을 걸 수 없어서, 자기 텔레그램 계정으로
로그인하는 발신기를 따로 둔다.

안 써도 된다. 사람이 각 세션 봇에 직접 말을 거는 것만으로 나머지는 다 돈다.

```bash
pip install --user telethon
mkdir -p ~/.claude/userbot && chmod 700 ~/.claude/userbot
```

`my.telegram.org` 에 로그인해 API development tools 에서 `API_ID` 와 `API_HASH` 를 받는다.
그 둘을 `~/.claude/userbot/.env` 에 적는다. 터미널에서만 적는다.

```
API_ID=1234567
API_HASH=0123456789abcdef0123456789abcdef
```

```bash
chmod 600 ~/.claude/userbot/.env
claude-userbot-login      # 전화번호 인증
```

인증 번호는 반드시 이 터미널에만 친다. 그 번호를 텔레그램 채팅에 적으면 텔레그램이 즉시
무효로 만든다 — 남에게 전달하려고 채팅에 옮겨 적는 순간 못 쓰게 된다.

세션 이름과 봇을 잇는 표를 만든다. 본보기가 `userbot/targets.json.example` 에 있다.

```bash
cp userbot/targets.json.example ~/.claude/userbot/targets.json
# {"세션이름": "@봇유저네임", ...} 형태로 고친다
```

보내기:

```bash
claude-userbot-send --session myproj "이 저장소 검사 한 번 돌려라"
```

기계가 둘 이상이면 발신기는 한 대에만 둔다. 나머지 기계는 `userbot/relay.conf.example` 과
`userbot/relay-alias.json.example` 을 참고해 ssh 로 위임한다. 위임은 명시 등록된 세션만
허용한다 — 같은 이름의 세션이 양쪽에 있으면 조용히 엉뚱한 기계로 배달되기 때문이다.

자세한 것은 `userbot/README.md` 에 있다.

---

## 7. 켜기와 확인

세션은 `install.sh` 가 켠다. 감시 타이머는 한 번 켜 준다.

```bash
systemctl --user enable --now claude-progress-updater.timer
systemctl --user enable --now claude-session-healthcheck.timer
systemctl --user enable --now claude-api-5xx-watcher.timer
systemctl --user enable --now claude-bun-zombie-cleaner.timer
systemctl --user enable --now claude-rate-limit-recovery.timer
systemctl --user enable --now claude-telegram-healthcheck.timer
systemctl --user enable --now claude-memory-lifecycle.timer
systemctl --user enable --now claude-bun-ensure.timer
systemctl --user enable --now claude-control-bot.service   # 통제 봇을 쓸 때만
```

확인:

```bash
claude-sessions list                       # 세션 상태
systemctl --user status claude-myproj      # 한 세션만
tail -n 50 ~/.claude/logs/claude-myproj.log
```

그리고 그 봇 대화창에 "안녕" 을 보낸다. 답이 오면 끝난 것이다.

세션 유닛은 `Restart=always` 라 어떤 이유로 죽어도 다시 산다. 멈추고 싶으면
`systemctl --user stop` 을 쓴다 — 그건 자동 부활 대상이 아니다.

---

## 8. 안전장치

세션을 오래 돌리다 보면 조용히 망가지는 자리가 정해져 있다. 훅이 그 자리마다 하나씩 서 있다.

- 한도 가드 — 5시간 창이 90퍼센트, 7일 창이 99퍼센트를 넘으면 거의 모든 도구를 막는다.
  텔레그램 답장과 외부 모델 호출만 통과시킨다. 막힌 동안 들어온 사람 메시지는 큐에 쌓아
  두고 풀리면 한꺼번에 처리한다.
- 프롬프트 길이 가드 — 창을 통째로 못 쓰게 만드는 크기의 입력을 미리 막는다.
- 큰 읽기 가드 — 5메가 또는 1만 줄을 넘는 파일을 통째로 읽는 것을 막는다.
- 비밀 검사 — 커밋과 푸시 직전에 자격증명 모양을 찾는다.
- 답장 강제 — 텔레그램에서 온 메시지에 답장 도구를 안 부르고 턴을 끝내는 것을 막는다.
  터미널에만 쓴 글은 보낸 사람에게 안 보이기 때문이다.
- 장시간 작업 가드 — 오래 걸리는 일은 세션 안이 아니라 세션 밖 유닛에서 돌게 한다.
  세션이 재시작되면 세션 안 자식은 같이 죽기 때문이다.

임시로 푸는 법:

```bash
touch ~/.claude/state/rate-limit-bypass.flag             # 전체
touch ~/.claude/state/rate-limit-bypass-<프로젝트>.flag   # 한 프로젝트만
BYPASS_SECRETS_SCAN=1 git commit ...
```

### 권한 기본값을 먼저 보고 받는다

`claude/settings.json` 이 기본으로 주는 권한은 넓다. 사람이 없는 상태로 도는 세션을
전제로 정한 값이라 그렇다. 그대로 쓸지 좁힐지는 받는 사람이 정할 일이므로 두 줄만
짚어 둔다.

- `"Bash(*)"` — 셸 명령을 묻지 않고 실행한다. 이게 없으면 감시 타이머가 부르는 일이
  승인 대기에서 멈추고, 사람이 없으면 영영 안 풀린다. 대신 위험한 명령도 안 묻는다.
- `"additionalDirectories": ["~"]` — 세션 작업 디렉토리 밖, 홈 전체를 읽고 쓸 수 있다.
  세션끼리 서로의 폴더를 보게 하려고 열어 둔 것이다.

좁히려면 `Bash(*)` 를 실제로 쓰는 명령 목록으로 바꾸고 `additionalDirectories` 를
필요한 폴더만 적는다. 좁힌 뒤에는 감시 타이머가 승인 대기에 걸리지 않는지 하루쯤 본다.

거부 목록(`deny`)은 그대로 두는 것을 권한다. 자격증명 파일 모양 스물몇 가지를 읽기와
쓰기 양쪽에서 막고 있고, 넓은 허용을 쓰는 한 그게 마지막 선이다.

---

## 9. 자주 쓰는 명령

```bash
claude-sessions list            # 세션 상태
claude-sessions restart <이름>   # 대화 기록을 유지한 채 다시 띄운다
claude-sessions rotate <이름>    # 기록을 보관하고 새 세션으로 시작한다
claude-sessions health          # 멈췄거나 한도에 걸린 세션 감지

cl                              # 지금 폴더의 세션을 터미널로 가져온다(유닛은 잠시 멈춘다)
cl-all                          # 모든 세션을 탭으로 한 번에 연다

ai-debate-detach "물음"          # 외부 모델 셋을 붙여 토론시킨다(세션 밖에서 돈다)
claude-quota-observe            # 지금 한도 상태
claude-userbot-send --session <이름> "지시"
```

`cl` 은 셸 함수라 프로필에서 불러와야 쓸 수 있다. `install.sh` 가 `~/.bashrc` 에 그 줄을
한 번 넣어 주므로, 설치 뒤 새 터미널을 열거나 `source ~/.bashrc` 를 하면 된다.

---

## 10. 걸리기 쉬운 자리

- 봇 토큰을 나눠 쓰지 않는다. 한 봇의 수신은 한 곳만 받는다. 기계나 세션이 같은 토큰을
  쓰면 한쪽이 영영 조용해지고, 원인이 안 보인다.
- `getUpdates` 를 손으로 부르면 세션이 받을 메시지를 대신 먹는다. 방 번호를 알아낼 때만
  쓰고, 세션을 띄운 뒤에는 쓰지 않는다.
- Claude Code CLI 를 자식으로 띄우면 부모 세션의 텔레그램 연결이 끊어진다. 두 프로세스가
  같은 수신 통로를 두고 다투기 때문이다. 스크립트에서 꼭 띄워야 하면
  `--strict-mcp-config --mcp-config <빈 파일>` 을 붙인다. 이미 끊겼으면 세션을 다시 띄운다.
- 새 훅 파일은 `git pull` 만으로 붙지 않는다. 설정 파일은 심링크라 등록만 먼저 살아나서
  "등록은 됐는데 파일이 없는" 상태가 되고, 그러면 아무 일도 안 일어난다. 훅을 더했으면
  `install.sh` 를 돌리고 `~/.claude/hooks/` 에 그 파일이 생겼는지 본다.
- 돌고 있는 셸 스크립트를 제자리에서 고치지 않는다. bash 는 파일을 조금씩 읽어서,
  덮어쓰면 실행 중인 쪽이 엉뚱한 자리부터 읽는다. 같은 디렉토리에 새 파일을 만들고
  `mv` 로 덮는다.
- 한도가 풀렸을 때 깨우는 신호는 대상 세션이 놀고 있을 때만 즉시 먹는다. 일하는 중인
  세션에 넣으면 턴 중간에 들어가서 다음 사람 메시지 때 처리된다. 같은 세션에서 자기
  자신으로 시험하면 항상 일하는 중이라 안 먹는다.
- systemd 유닛 파일은 `$HOME` 이나 `~` 를 펼치지 않는다. 홈은 `%h` 로 쓴다.
