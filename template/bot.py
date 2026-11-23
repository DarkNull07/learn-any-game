"""Bot for this game. Generic flow from the kit; the game-specific parts are model\\screens.json (what each screen is
and what to tap there) and solver.py (how to play a level).

    run.bat --levels 20          play 20 levels
    run.bat --levels 5 --dry-run look and log only, never touch the phone

Rules built in: never buys, never watches reward ads (danger list + ad ladder), closes ads fast, logs everything
(logs\\run_*.log, logs\\report.csv) and saves a picture only when something goes wrong (logs\\unknown\\)."""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kit import config  # noqa: E402
from kit.ads import AdCloser  # noqa: E402
from kit.device import Device, PhoneError  # noqa: E402
from kit.log import Logger  # noqa: E402
from kit.screens import Screens  # noqa: E402
import solver  # noqa: E402

UNKNOWN_GRACE = 1.5     # s: an unknown screen this short is just a transition
MAX_RESTARTS = 3        # per 10 minutes
SAME_SCREEN_LIMIT = 6   # the same action on the same screen this often with no change = stuck


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true", help="classify and log only, no taps")
    args = ap.parse_args()

    game = config.load_game(HERE)
    log = Logger(HERE)
    log(f"=== {game['name']} bot | {args.levels} levels | kit {game.get('kit_version')} | log {log.path}")
    try:
        dev = Device(log=log)
    except PhoneError as e:
        log(f"STOP: {e}")
        return 1
    scr = Screens(HERE, log)
    dev.guard = scr.guard_for(dev)
    if args.dry_run:
        dev.guard = lambda x, y: "dry run"
    ads = AdCloser(dev, game, log)
    ctx = {"game": game, "log": log, "screens": scr, "dry_run": args.dry_run, "level": None}

    done = lost_in_row = stuck_solver = 0
    restarts = []
    t_unknown = None
    last_action, same_count = None, 0
    level_t0, level_ads, level_ad_s = time.time(), 0, 0.0

    while done < args.levels:
        img = dev.screenshot()
        s, score = scr.classify(img)

        if s is None:   # not a known screen: a transition, an ad, or something new
            t_unknown = t_unknown or time.time()
            if time.time() - t_unknown < UNKNOWN_GRACE:
                time.sleep(0.1)
                continue
            pkg, act = dev.foreground()
            looks_ad = ads.is_ad_activity(act) or (pkg and pkg != game.get("package"))
            if not looks_ad and time.time() - t_unknown < 4.0:
                time.sleep(0.2)
                continue
            if not looks_ad:
                p = log.failure(img, "unknown_screen")
                log(f"Unknown screen for 4s. Picture for the next fix: {p}")
            if args.dry_run:
                log("  (dry run: not closing anything)")
                time.sleep(2)
                t_unknown = None
                continue
            t_ad = time.time()
            result = ads.handle(lambda im: scr.classify(im)[0] is not None,
                                why="ad screen in front" if looks_ad else "unknown screen")
            level_ads += 1
            level_ad_s += time.time() - t_ad
            if result == "restarted":
                restarts = [t for t in restarts if time.time() - t < 600] + [time.time()]
                if len(restarts) > MAX_RESTARTS:
                    log("STOP: had to restart the game too often in 10 minutes. See logs\\unknown\\.")
                    break
            t_unknown = None
            continue
        t_unknown = None
        act = s.get("action", "wait")

        if act == "solve":
            ctx["level"] = s.get("name")
            log(f"Level screen ({s['name']}, match {score:.2f}). Solving.")
            t_play = time.time()
            res = solver.play(dev, img, ctx) or {}
            ctx["last_result"] = res
            log(f"  Solver: {res.get('result', '?')} | moves {res.get('moves', '?')} | "
                f"mistakes {res.get('mistakes', 0)} | {time.time() - t_play:.1f}s", 1)
            if res.get("result") == "stuck":
                stuck_solver += 1
                p = log.failure(dev.screenshot(), "solver_stuck")
                log(f"  Solver stuck ({stuck_solver}x). Picture: {p}", 1)
                if stuck_solver >= 2:
                    log("STOP: the solver is stuck on this level. The pictures in logs\\unknown\\ show why.")
                    break
            else:
                stuck_solver = 0
            dev.wait_still(max_wait=3)
            continue

        marks = s.get("marks")       # "won" / "lost": a level ended
        if marks:
            done += marks == "won"
            lost_in_row = 0 if marks == "won" else lost_in_row + 1
            secs = time.time() - level_t0
            log(f"Level {marks.upper()} in {secs:.1f}s ({done}/{args.levels} won). Ads this level: {level_ads}")
            res = ctx.pop("last_result", {})
            log.report(level=ctx.get("level_no", ""), result=marks, seconds=round(secs, 1), ads=level_ads,
                       moves=res.get("moves", ""), mistakes=res.get("mistakes", ""), note=res.get("note", ""),
                       ad_seconds=round(level_ad_s, 1), restarts=len(restarts))
            level_t0, level_ads, level_ad_s = time.time(), 0, 0.0
            if lost_in_row >= game.get("stop_after_losses", 2):
                log(f"STOP: lost {lost_in_row} levels in a row. Pictures in logs\\unknown\\.")
                log.failure(img, "lost_in_row")
                break

        if act == "stop":
            log(f"STOP: screen '{s['name']}' says stop ({s.get('note', '')}).")
            break
        if act == "wait":
            time.sleep(0.2)
            continue

        key = (s["name"], str(act))
        same_count = same_count + 1 if key == last_action else 1
        last_action = key
        if same_count > SAME_SCREEN_LIMIT:
            p = log.failure(img, f"stuck_{s['name']}")
            log(f"Stuck on '{s['name']}' (same action {same_count} times). Restarting the game. Picture: {p}")
            dev.restart(game["package"])
            time.sleep(float(game.get("cold_start_s") or 15))
            restarts = [t for t in restarts if time.time() - t < 600] + [time.time()]
            same_count = 0
            if len(restarts) > MAX_RESTARTS:
                log("STOP: had to restart the game too often in 10 minutes.")
                break
            continue

        if isinstance(act, dict) and "tap" in act:
            x, y = act["tap"]
            log(f"'{s['name']}': tapping ({x},{y}) {s.get('note', '')}")
            dev.tap(x, y)
        elif isinstance(act, dict) and "tap_template" in act:
            m = scr.find(img, act["tap_template"], act.get("region"))
            if m and m[2] >= act.get("min_score", 0.85):
                log(f"'{s['name']}': tapping {act['tap_template']} at ({m[0]},{m[1]}) {s.get('note', '')}")
                dev.tap(m[0], m[1])
            else:
                log(f"'{s['name']}': button {act['tap_template']} not found (best {m[2] if m else 0:.2f})")
        elif isinstance(act, dict) and "swipe" in act:
            dev.swipe(*act["swipe"])
        dev.wait_still(max_wait=2.5)

    log(f"=== Finished: {done} levels won. Report: {os.path.join(HERE, 'logs', 'report.csv')}")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
