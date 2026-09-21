# userbot — 세션끼리 메시지를 넣는 통로

세션 A 가 세션 B 에게 지시를 넣거나, 세션 밖에서 돌던 긴 작업이 끝났을 때 그 결과를
띄운 세션 자신에게 돌려주는 데 쓴다. 자기 텔레그램 계정으로 상대 세션의 봇 채팅에
메시지를 보내는 발신기라서, 사람이 그 봇에 말을 건 것과 똑같이 그 세션 안으로 들어간다.

봇으로는 이걸 못 한다. 봇은 다른 봇에게 말을 걸 수 없다. 그래서 사람 계정으로 로그인하는
telethon 발신기가 따로 필요하다.

안 써도 된다. 사람이 각 세션 봇에 직접 말을 거는 것만으로 이 저장소의 나머지는 다 돈다.

## 여기 있는 것

이 폴더에는 본보기만 있다. 실제 파일은 전부 `~/.claude/userbot/` 아래, 저장소 밖이다.

- `targets.json.example` — 세션 이름과 그 세션 봇을 잇는 표.
  `claude-userbot-send --session <이름>` 이 이 표를 보고 어느 봇에 보낼지 정한다.
  사본 자리: `~/.claude/userbot/targets.json`
- `relay.conf.example` — 발신기가 없는 기계가 있는 기계에 ssh 로 위임할 때 쓴다.
  사본 자리: `~/.claude/userbot/relay.conf`
- `relay-alias.json.example` — 위임을 허용할 세션 이름 목록. 여기 없는 이름은 거절한다.
  사본 자리: `~/.claude/userbot/relay-alias.json`

## 왜 위임 목록이 따로 있나

기계가 여럿이면 같은 이름의 세션이 양쪽에 있을 수 있다. 이름을 그대로 넘기면 엉뚱한
기계의 동명 세션으로 조용히 배달된다. 그래서 미등록 이름은 보내지 않고 거절한다.
받는 쪽이 없는 것보다 엉뚱한 곳에 도착하는 쪽이 나쁘다.

## 자격증명

`~/.claude/userbot/.env` 에 my.telegram.org 에서 받은 API_ID 와 API_HASH 를 넣고,
`claude-userbot-login` 으로 전화번호 인증을 한 번 한다. 인증 번호는 반드시 기계 앞
터미널에만 친다 — 그 번호를 텔레그램 채팅에 적으면 텔레그램이 즉시 무효로 만든다.

`.env`, `userbot.session`, `relay.conf` 는 권한 600 으로 둔다.
