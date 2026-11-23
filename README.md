# learn-any-game (gamekit + the learn-new-game skill)

Teach an AI agent any Android phone game by playing it once while this kit records, then let the agent build a bot.
No long explanations: the recording shows the buttons, flow, timings and your tricks.
Needs: a Windows PC (macOS/Linux launchers are included but untested) with Python 3.10+, an Android phone with USB debugging, any coding agent that can run
commands (Claude Code, Antigravity, ...).

## Install with your agent (copy this to it)

> Install the agent skill from https://github.com/DarkNull07/learn-any-game : clone it into my automation folder as
> "Any Game" (ask me where; default Desktop\Game Automation), add its SKILL.md to your skills as `learn-new-game`,
> then follow the skill's setup.

Or by hand:
1. `git clone https://github.com/DarkNull07/learn-any-game "Any Game"` (or Code > Download ZIP and unzip as "Any Game").
2. Give your agent the skill: copy `SKILL.md` into a folder named `learn-new-game` in its skills folder
   (Claude Code: `~/.claude/skills/learn-new-game/SKILL.md`; claude.ai: zip that folder and upload it under
   Settings > Skills; other agents: see their docs, or just tell the agent "read Any Game\SKILL.md and follow it").
3. In the "Any Game" folder run `gamekit setup` (macOS/Linux: `./gamekit.sh setup`). Then `/learn-new-game help` (or `gamekit help`) shows the steps.

## Quick start

| Step | You do | Time |
|---|---|---|
| 1 | Phone: Settings > About phone > tap Build number 7x. Developer options > USB debugging on. Plug in, tap Allow. | once |
| 2 | In this folder: `gamekit setup` (finds Python, adb, the phone; a few questions, Enter = default) | once |
| 3 | Open the game on the phone, then `gamekit new "Game Name"` | 10 s |
| 4 | Fill the 6 lines of `Game Name\model\card.txt` | 2 min |
| 5 | `gamekit record "Game Name"`, play 10-15 levels, press Enter | 10-15 min |
| 6 | `gamekit distill "Game Name"` | 1 min |
| 7 | Ask the agent: `/learn-new-game build Game Name` | |
| 8 | `Game Name\run.bat --levels 20` | |
| 9 | After a run: `/learn-new-game improve Game Name`, then `gamekit sync "Game Name"` | |

While recording, if you can: lose once, let one ad play and close it, open a popup you normally decline, play one
hard level. `gamekit help` shows all steps and commands.

## What is where

```
Any Game\                  this kit (share it with `gamekit pack`)
  gamekit.bat / .py        all commands: help, setup, doctor, new, record, distill, frame, list, sync, pack
  kit\                     shared code: phone (adb + live stream), recorder, distiller, ad ladder, screens, logs
  template\                the bot skeleton copied into each new game
  library\                 knowledge that grows with every game
    LESSONS.md             lessons every new game reads first
    ads\close_templates\   ad close buttons that really worked (new ones are learned automatically)
    ads\blacklist\         buttons that opened the store instead of closing
    ads\sdk_activities.txt names of ad screens
    ads\ads_log.csv        every ad: game, ad screen, how it was closed, seconds
  SKILL.md                 the agent skill (give it with the kit)
  config.json              your answers + phone details (made by setup, never shared)

<Game Name>\               one folder per game, everything inside
  run.bat, bot.py          the bot (run.bat --levels N, --dry-run)
  solver.py                how to play a level (written by the agent)
  probe.bat, probe.py      live tools the agent uses to test things on the phone
  regress_test.py          offline checks every version must pass
  game.json                package, start-up times, kit version
  kit\                     this game's own copy of the kit code
  model\                   card.txt, GAME_MODEL.md, screens.json, templates\, shots\, LESSONS.md
  sessions\                your recorded sessions and their distilled pictures (out\)
  logs\                    run logs, report.csv, unknown\ (pictures of anything that went wrong)
  regress\  backups\  waste\
```

## Safety rules built in
- Never buys anything, never taps real-money offers or reward-ad buttons: every tap, swipe, pinch and Back is checked
  against the game's `never_tap` list on a fresh picture, and the bot refuses to run if that list is broken.
- Closes ads only on confirmed ad screens (ad SDK screen, another app, or an ad view Android reports). Unknown game
  screens are never tapped: the bot saves a picture and restarts the game if it stays.
- Learns a new close button only after its tap really ended the ad; buttons that opened the store are blacklisted.
- Comes back from the Play Store without restarting; restarts a game only when an ad is stuck longer than the game
  takes to start.
- `--dry-run` sends nothing at all to the phone.

## Sharing
Share the GitHub link above. Offline: `gamekit pack` makes `AnyGame_kit_<version>.zip` next to this folder without
your config, logs or Python environment (SKILL.md is inside); the other person unzips it and runs `gamekit setup`.

## Credits
`kit/scrcpy/scrcpy-server-v5.0.1` is the unmodified server of [scrcpy](https://github.com/Genymobile/scrcpy)
(Apache License 2.0, see `kit/scrcpy/LICENSE-scrcpy.txt` and `NOTICE.txt`), used for the fast live screen stream.
It is copied to the phone's temporary folder only while connected and removed on disconnect.
