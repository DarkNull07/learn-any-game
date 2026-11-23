# Shared lessons (every game)

Read this before building any new game's bot. Add a line whenever a fix would help other games too
(`gamekit sync "<Game>"` copies [general] lines from a game's model\LESSONS.md here).
Format: date | tag | lesson. Tags: safety, ads, vision, input, speed, flow, phone.

2026-11-01 | safety | A generic "find any X" on a game screen tapped inside a real-money offer. Generic close finders run only on confirmed ad screens; game popups use the game's own templates + never_tap list.
2026-11-01 | ads | Ad buttons can open Play Store or a browser. Press Back, then warm-resume the game (monkey launcher intent); a cold restart is a last resort.
2026-11-01 | ads | Known ad close buttons match at 0.91-0.97; nothing else scored above 0.81. Use 0.88 as the cut-off and compare only the inner X (ring/background vary).
2026-11-01 | flow | Never tap based on a fixed wait: re-read the screen until the previous move's result has finished (moving pieces looked "gone" too early and lives were lost).
2026-11-01 | flow | Count a level as won only on the real win screen; a revive/retry screen is not a win.
2026-11-01 | flow | Never skip the settle check at level start: intro animations were read as the board.
2026-11-04 | vision | Templates cut from saved JPEG/video frames failed on live screenshots. Cut templates only from live frames of the same source the bot uses: `probe snap` (live stream frame saved losslessly as PNG), never from the recorded video.
2026-11-04 | vision | Hard rule before the first move: what was read must agree with what the game shows (piece count = counter, etc.).
2026-11-04 | vision | Some levels open zoomed in. Pinch out with two fingers until nothing touches the screen edges.
2026-11-04 | vision | A wrongly tapped piece can change look (striped/grey) instead of leaving. Treat changed-look pieces as obstacles, not gone.
2026-11-04 | phone | Google Play Games side bar on the right edge eats taps. Keep taps away from edges or move the content first.
2026-11-04 | phone | Turn on Do Not Disturb and Stay awake before long runs (call banners and screen-off broke runs).
2026-11-04 | flow | Stuck watchdog: restart the game when stuck, at most 3 times per 10 minutes, then stop with pictures.
2026-11-04 | process | Never drop a feature an earlier version proved on the phone. Keep a regress frame per fixed problem and run regress_test.py before every phone run.
2026-11-05 | speed | The scrcpy server stream (nothing installed on the phone) gives frames in ~30 ms and taps in milliseconds; adb screencap is ~0.5 s per picture.
2026-11-09 | input | Word games: only real dictionary words (3 wrong words cost a heart); fast swipes are fine.
