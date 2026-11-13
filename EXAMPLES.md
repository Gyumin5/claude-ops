# EXAMPLES.md — 실제로 해 보는 여섯 가지

SETUP.md 대로 한 번 세운 뒤, 이 도구로 보통 하는 일 여섯이다. 위에서 아래로 읽을 필요는
없다 — 지금 하려는 것부터 보면 된다.

각 항목은 같은 칸을 쓴다. 목적 · 준비 · 명령 · 기대 결과 · 정리 방법 · 대응 파일.
"정리 방법" 은 그 일을 하다 만 자리에서 되돌리는 법이고, "대응 파일" 은 그 일이 실제로
어느 파일에서 일어나는지다.

여기 적힌 명령은 전부 이 저장소에 든 것이다. 돌기 전에 `~/.local/bin` 이 PATH 에 있어야
한다.

---

## 1. 설치가 끝났는지 본다

**목적** — SETUP.md 를 따라 세운 뒤 "어디까지 된 건가" 를 한 번에 본다. 세션이 안 뜰
때 무엇부터 보면 되는지도 이 명령이 짚는다.

**준비** — 없다. 이 명령은 고치지 않고 보기만 한다.

**명령**

```bash
claude-ops-doctor
```

**기대 결과** — 절마다 판정이 줄로 나온다. `OK` 는 이대로면 되는 것, `경고` 는 없어도
나머지는 도는 것, `실패` 는 이대로는 세션이 안 뜨는 것이다. 맨 끝에 `실패 N · 경고 M`
이 나오고, 실패가 하나라도 있으면 종료코드가 1 이다.

```
== 바탕 ==
OK    파이썬 3.10
OK    Claude Code 명령이 PATH 에 있다
...
== 세션 ==
OK    [myproj] 봇 토큰 파일이 제자리에 있다
경고  [myproj] 안 돌고 있다 (inactive)
      systemctl --user start claude-myproj.service

실패 0 · 경고 1
```

각 실패 줄에는 SETUP.md 의 몇 절을 다시 보면 되는지가 같이 적힌다.

**정리 방법** — 없다. 이 명령은 아무것도 안 고치므로 돌린 흔적이 남지 않는다.

**대응 파일** — `bin/claude-ops-doctor`, 그 계약을 고정하는 `tests/test-claude-ops-doctor.py`.

---

## 2. 세션 하나를 더 띄운다

**목적** — 프로젝트 폴더 하나에 세션 하나를 붙인다. 세션마다 자기 텔레그램 봇이 있어서,
그 봇에 말을 걸면 그 폴더에서 일하는 세션이 답한다.

**준비** — 그 세션이 쓸 봇 토큰(SETUP.md 3절)과 일할 폴더.

**명령**

```bash
mkdir -p ~/myproj/.claude/telegram
printf 'TELEGRAM_BOT_TOKEN=%s\n' '<그 세션 봇 토큰>' > ~/myproj/.claude/telegram/.env
chmod 600 ~/myproj/.claude/telegram/.env

cd ~/dotfiles
claude-service-template ~/myproj > systemd/user/claude-myproj.service
echo myproj >> machines/$(cat ~/.config/claude-machine-id).list
./install.sh

claude-sessions list
```

**기대 결과** — `claude-sessions list` 에 `myproj` 가 `active` 로 나온다. 그 봇 대화창에
아무 말이나 보내면 처음 한 번은 승인 대기로 잡히고(SETUP.md 5절의 짝짓기), 터미널에서
승인한 뒤부터 답이 온다.

**정리 방법** — 세 자리를 되돌린다. 켤 목록에서 그 줄을 지우고, 유닛을 멈추고 지운다.

```bash
systemctl --user disable --now claude-myproj.service
rm ~/.config/systemd/user/claude-myproj.service systemd/user/claude-myproj.service
sed -i '/^myproj$/d' machines/$(cat ~/.config/claude-machine-id).list
systemctl --user daemon-reload
```

폴더와 토큰 파일은 남는다. 토큰을 더 안 쓸 거면 그 봇을 BotFather 에서 지운다.

**대응 파일** — `bin/claude-service-template`, `machines/<기계이름>.list`,
`systemd/user/claude-<이름>.service`, `install.sh`.

---

## 3. 멈춘 세션을 찾아 되살린다

**목적** — 답이 없는 세션이 정말 멈춘 것인지, 한도에 걸린 것인지, 그냥 긴 일을 하는
중인지 가른 뒤 되살린다.

**준비** — 없다. 먼저 보는 명령은 아무것도 안 바꾼다.

**명령**

```bash
claude-sessions health                 # 멈췄거나 한도에 걸린 세션만 추린다
claude-sessions restart myproj --dry-run   # 무엇이 바뀔지만 본다
claude-sessions restart myproj         # 대화 기록을 유지한 채 다시 띄운다
```

**기대 결과** — `health` 가 세션마다 한 줄로 판정을 낸다. `restart` 는 기록을 유지한 채
다시 띄우므로, 그 세션이 하던 맥락은 그대로다. 유틸 서비스와 목록에 없는 이름은 거절한다.

한도에 걸린 세션은 재시작해도 안 풀린다 — 한도는 계정에 걸린 것이라 프로세스를 바꿔도
같다. 그건 풀리는 시각까지 기다리는 쪽이 맞고, 감시 타이머가 그 시각에 알아서 깨운다.

**정리 방법** — 재시작은 되돌릴 것이 없다. 일부러 멈춰 둔 세션을 깨우고 싶지 않았다면
다시 멈춘다(`systemctl --user stop claude-myproj`). 사람이 멈춘 세션은 설치 스크립트가
다시 깨우지 않는다.

**대응 파일** — `bin/claude-sessions`, `bin/claude-watchdog`,
`systemd/user/claude-session-healthcheck.timer`.

---

## 4. 세션 기록을 보관하고 새로 시작한다

**목적** — 한 세션이 오래 돌아 맥락이 엉켰을 때, 지금까지의 기록을 보관함으로 옮기고
빈 맥락으로 다시 시작한다. 재시작과 다르다 — 재시작은 기록을 들고 가고, 이건 끊는다.

**준비** — 인계 노트. 맥락을 끊는 것이므로 다음 세션이 읽을 글이 없으면 하던 일이
통째로 사라진다. 그래서 30분 안에 쓴 `handoff.md` 가 없으면 거절한다.

**명령**

```bash
claude-sessions rotate myproj
```

**기대 결과** — 그 세션의 대화 기록 파일이 `archive/` 로 옮겨지고, 세션이 빈 맥락으로
다시 뜬다. 인계 노트가 없거나 오래됐으면 아무것도 안 하고 그 사유를 찍는다.

**정리 방법** — 맥락을 버리는 쪽은 되돌릴 수 없다. 다만 기록 파일은 지워지지 않고
보관함에 그대로 있으므로, 나중에 읽거나 grep 할 수 있다. 맥락을 꼭 들고 가야 하면
rotate 가 아니라 `restart` 를 쓴다.

**대응 파일** — `bin/claude-sessions`, `bin/claude-rotate-archive`.

---

## 5. 토큰이 어디로 가는지 잰다

**목적** — 사용량이 빨리 닳을 때 무엇이 먹고 있는지 본다. 상주 규칙 문서인지, 큰 도구
결과인지, 캐시가 자꾸 빗나가는 것인지.

**준비** — 없다. 기록 파일만 읽으므로 이 명령 자체는 사용량을 안 쓴다.

**명령**

```bash
claude-token-audit 7      # 최근 7일
claude-turn-audit         # 턴마다 무엇을 했나
```

**기대 결과** — 세션별로 입력·출력·캐시 읽기 토큰이 집계된다. `claude-turn-audit` 쪽은
산출이 없던 턴의 비율과 같은 파일을 짧은 간격에 다시 읽은 비율을 같이 낸다 — 둘 다
임계를 넘으면 줄이 표시된다.

**정리 방법** — 없다. 읽기만 한다.

**대응 파일** — `bin/claude-token-audit`, `bin/claude-turn-audit`, `bin/claude-ctx-metrics`.

---

## 6. 두 번째 기계를 같은 설정으로 맞춘다

**목적** — 기계가 둘 이상일 때 같은 저장소로 같은 설정을 심는다. 켜는 세션만 기계마다
다르게 둔다.

**준비** — 그 기계에도 Claude Code CLI 와 bun 이 깔려 있어야 한다(SETUP.md 2절).

**명령**

```bash
git clone <저장소 주소> ~/dotfiles
cd ~/dotfiles

echo work > ~/.config/claude-machine-id        # 이 기계의 이름
cp machines/home.list.example machines/work.list
$EDITOR machines/work.list                     # 이 기계에서 켤 세션만 남긴다

./install.sh
claude-ops-doctor
```

**기대 결과** — 설정·훅·규칙은 두 기계가 같고, 켜지는 세션은 `machines/<이름>.list` 에
적힌 것만이다. 나중에 저장소를 고쳤으면 그 기계에서 `git pull` 뒤 `./install.sh` 를
한 번 더 돌린다 — 새 훅 파일은 받기만 해서는 안 붙는다.

**정리 방법** — 그 기계에서 `machines/work.list` 를 비우고 `./install.sh` 를 돌리면
아무 세션도 안 켠다. 이미 도는 세션은 따로 멈춘다.

**대응 파일** — `install.sh`, `machines/<기계이름>.list`, `~/.config/claude-machine-id`.

---

## 다음에 볼 것

- 처음부터 세우는 절차: `SETUP.md`
- 무엇이 들어 있나: `README.md`
- 세션들이 따르는 작업 규칙과 그 근거: `claude/CLAUDE.md`, `claude/RATIONALE.md`
