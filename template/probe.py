"""Small live tools for learning the game on the real phone (used by the agent in the probe phase).

  probe.py snap NAME                  save the live screen as model\\shots\\NAME.png (real PNG, for templates)
  probe.py crop NAME X1 Y1 X2 Y2 TPL  cut model\\templates\\TPL.png out of shot NAME
  probe.py test                       which screen is this? scores for every screen + visible danger boxes
  probe.py tap X Y                    tap (danger list still applies), wait until still, report what changed
  probe.py swipe X1 Y1 X2 Y2 MS       same for a swipe
  probe.py watch SECONDS              print screen name and motion 5x a second (measures animation/transition times)
  probe.py times                      measure cold start and warm resume of the game, saved into game.json
  probe.py regress NAME EXPECTED      save the live screen as regress\\NAME__EXPECTED.png (a test frame)
  probe.py ui                         save Android's view of the screen to model\\shots\\ui_<time>.xml
Pictures come from the same live stream the bot uses, so templates and scores match what the bot sees at run time
(add --png to use Android's screencap instead). Never use these on money buttons or reward-ad buttons."""
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kit import config  # noqa: E402
from kit.device import Device  # noqa: E402
from kit.screens import Screens, crop, diff_score  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    png = "--png" in argv
    argv = [x for x in argv if x != "--png"]
    cmd, a = argv[0], argv[1:]
    game = config.load_game(HERE)
    shots = os.path.join(HERE, "model", "shots")
    os.makedirs(shots, exist_ok=True)
    if cmd == "crop":
        out = crop(os.path.join(shots, a[0] + ".png"), list(map(int, a[1:5])),
                   os.path.join(HERE, "model", "templates", a[5] + ".png"))
        print("saved", out)
        return 0
    dev = Device(use_stream=not png)
    scr = Screens(HERE)
    dev.guard = scr.guard_for(dev)
    if cmd == "snap":
        p = os.path.join(shots, a[0] + ".png")
        cv2.imwrite(p, dev.screenshot())
        print("saved", p, f"({dev.w}x{dev.h})")
    elif cmd == "test":
        img = dev.screenshot()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for s in scr.spec["screens"]:
            m = scr.find(img, s.get("template"), s.get("region"), gray)
            print(f"  {s['name']:<16} {m[2] if m else 0:.3f}  (needs {s.get('min_score', 0.85)})")
        print("screen:", scr.name(img), "| app:", dev.foreground())
        for box, note in scr.danger_boxes(img):
            print("  DANGER box", box, note)
    elif cmd in ("tap", "swipe"):
        before = dev.screenshot()
        t0 = time.time()
        ok = dev.tap(int(a[0]), int(a[1])) if cmd == "tap" else dev.swipe(*map(int, a[:5]))
        after = dev.wait_still(max_wait=5)
        print(f"{'done' if ok else 'refused'}; settled after {time.time() - t0:.2f}s; change {diff_score(before, after):.1f};"
              f" now: {scr.name(after)}")
    elif cmd == "watch":
        prev, t0 = dev.screenshot(), time.time()
        while time.time() - t0 < float(a[0]):
            cur = dev.screenshot()
            print(f"  {time.time() - t0:6.2f}s  {scr.name(cur):<14} motion {diff_score(prev, cur):5.1f}")
            prev = cur
            time.sleep(0.2)
    elif cmd == "times":
        pkg = game["package"]
        dev.shell(f"am force-stop {pkg}")
        time.sleep(1)
        t0 = time.time()
        dev.resume(pkg)
        while time.time() - t0 < 120 and scr.name(dev.screenshot()) == "unknown":
            time.sleep(0.3)
        cold = time.time() - t0
        dev.home()
        time.sleep(2)
        t0 = time.time()
        dev.resume(pkg)
        while time.time() - t0 < 60 and scr.name(dev.screenshot()) == "unknown":
            time.sleep(0.2)
        warm = time.time() - t0
        game["cold_start_s"], game["warm_resume_s"] = round(cold, 1), round(warm, 1)
        config.save_game(HERE, game)
        print(f"cold start {cold:.1f}s, warm resume {warm:.1f}s (saved in game.json; the ad ladder waits "
              f"max(20, cold start) before a restart)")
    elif cmd == "regress":
        os.makedirs(os.path.join(HERE, "regress"), exist_ok=True)
        p = os.path.join(HERE, "regress", f"{a[0]}__{a[1]}.png")
        cv2.imwrite(p, dev.screenshot())
        print("saved", p)
    elif cmd == "ui":
        p = os.path.join(shots, f"ui_{int(time.time())}.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(dev.ui_dump())
        print("saved", p, "| app:", dev.foreground())
    else:
        print(__doc__)
        return 1
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
