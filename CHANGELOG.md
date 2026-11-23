# Kit changelog

Every change to kit\ or template\ gets a line here and a version bump in kit\__init__.py, so `gamekit sync`
knows a game folder's copy is out of date.

## 1.1.1 (2026-11-23) - second review
- setup can't loop: it re-runs inside the new .venv once (GAMEKIT_IN_VENV) and stops with a clear message if the
  packages still don't import; opencv-python-headless (no libGL needed on Linux).
- gamekit.sh, run.sh, probe.sh are executable in git.
- A win/lose screen seen again after an ad isn't counted twice; the "is this an ad?" check (uiautomator) runs at
  most every 4 s on an unknown screen; bot exit code is 1 after STOP, 2 after a crash, 130 after Ctrl+C.
- probe times: package validated and quoted; game names may use letters of any language.
- Removed the dead "skip_learned" path (bottom-right zone uses curated skip templates only).
- Shared lesson about templates now says: cut them from live probe snaps, never from the recorded video.

## 1.1.0 (2026-11-23) - fixes from a code review
- Safety: wins/losses counted once per level (on arrival of the screen); the ad ladder runs only on confirmed ads
  (ad SDK screen, another app, or an ad view in Android's screen tree), unknown game screens are never tapped (picture
  + restart after max(20 s, cold start)); a close button is learned only after its tap really ended the ad; learned
  buttons need a 0.92 match; tap guard fails closed (missing danger template, no picture), checks a fresh frame,
  covers swipe/path/pinch/back; dry run sends nothing; danger matching at half size and cached per frame.
- Recorder stops at once on Enter (kills getevent) and always restores show_touches ("null" handled).
- Rotation: screenshots follow the phone's size; distill rotates each touch by the rotation at that moment.
- sync copies only dated [general] lesson lines, updates bot.py/probe.py/regress_test.py/launchers too, fixes a
  moved kit link; bot warns when the kit link is broken; pack leaves out .git.
- Phone serial (setup asks when several are connected, also used by the stream); package names validated and quoted;
  game names can't escape the base folder; --package no longer rejected for "launcher" in the name.
- macOS/Linux launchers (gamekit.sh, run.sh, probe.sh; untested); setup continues inside the new .venv by itself.
- probe uses the same live stream frames as the bot (--png for screencap); regress_test fails with zero frames.
- Bot: crash handler (log + picture + clean close), overall watchdog (no level ended in max_level_minutes), solver
  loop limit, level number in report.csv, hints_allowed/safe_mode passed to the solver, save_screens=all honoured,
  ads=offline skips the ad ladder.
- Low: ordered fallback taps, touch screen picked by INPUT_PROP_DIRECT, learned bottom-right skip buttons matched,
  scrcpy jar removed from the phone on disconnect, scrcpy license + notice shipped, requirements pinned, lint clean.

## 1.0.0 (2026-11-23)
- First version: setup (detect phone/adb/Python, ask the rest), new, record (scrcpy stream + getevent +
  activity/uiautomator), distill (triplets, scenes, sheets, timeline, dropped list), frame, sync, pack, list, doctor.
- Ad close ladder (kit\ads.py) with shared, self-growing close-button library and store-jump blacklist.
- Bot skeleton: data-driven screens.json, never_tap guard on every tap, watchdogs, logs + report.csv, regress tests.
- Reused from the Arrow Sort Master bot: scrcpy stream (kit\stream.py), uiautomator/X-glyph rules (kit\adclose.py),
  5 proven ad close templates.
