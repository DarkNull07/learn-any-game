"""Bot for this game. Generic flow from the kit; the game-specific parts are model\\screens.json (what each screen is
and what to tap there) and solver.py (how to play a level). Don't put game logic here: `gamekit sync` may update
this file.

    run.bat --levels 20          play 20 levels
    run.bat --levels 5 --dry-run look and log only, nothing at all is sent to the phone

Rules built in: never buys, never watches reward ads (danger list + ad ladder), closes ads fast (only on screens
confirmed to be ads), logs everything (logs\\run_*.log, logs\\report.csv) and saves a picture only when something
goes wrong (logs\\unknown\\), unless config.json says save_screens = "all"."""
import argparse
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kit import adclose, config  # noqa: E402
from kit.ads import AdCloser  # noqa: E402
from kit.device import Device, PhoneError  # noqa: E402
from kit.log import Logger  # noqa: E402
from kit.screens import Screens  # noqa: E402
import solver  # noqa: E402

UNKNOWN_GRACE = 1.5     # s: an unknown screen this short is just a transition
MAX_RESTARTS = 3        # per 10 minutes
SAME_SCREEN_LIMIT = 6   # the same action on the same screen this often with no change = stuck
SOLVES_PER_LEVEL = 3    # solver called this often without the level ending = stuck


class Stop(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true", help="classify and log only, nothing is sent to the phone")
    args = ap.parse_args()

    cfg = config.load()
    game = config.load_game(HERE)
    log = Logger(HERE)
    log(f"=== {game['name']} bot | {args.levels} levels | kit {game.get('kit_version')} | log {log.path}")
    log(f"    shared library: {config.LIBRARY} | ads: {cfg.get('ads')} | dry run: {args.dry_run}")
    if config.WARNING:
        log(f"WARNING: {config.WARNING}")
    scr = Screens(HERE, log)
    bad = scr.problems()
    if bad:
        log("STOP: the danger list is broken, so taps can't be checked: " + "; ".join(bad))
        return 1
    try:
        dev = Device(cfg, log=log, dry_run=args.dry_run)
    except PhoneError as e:
        log(f"STOP: {e}")
        return 1
    dev.guard = scr.guard_for(dev)
    ads = AdCloser(dev, game, log)
    ctx = {"game": game, "cfg": cfg, "log": log, "screens": scr, "dry_run": args.dry_run, "level_no": None,
           "hints_allowed": cfg.get("hints_allowed", False), "safe_mode": cfg.get("safe_mode", False)}
    st = {"done": 0, "restarts": [], "level_t0": time.time(), "ads": 0, "ad_s": 0.0, "level": 0}
    try:
        run(args, cfg, game, log, dev, scr, ads, ctx, st)
    except Stop as e:
        log(f"STOP: {e}")
    except KeyboardInterrupt:
        log("Stopped by you (Ctrl+C).")
    except Exception:  # noqa: BLE001  any crash: log it with a picture, never leave the phone connection open
        log("CRASH:\n" + traceback.format_exc())
        try:
            p = log.failure(dev.last_img, "crash")
            log(f"Picture of the screen at the crash: {p}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        log(f"=== Finished: {st['done']} levels won. Report: {os.path.join(HERE, 'logs', 'report.csv')}")
        dev.close()
    return 0


def restart(dev, game, log, st, why, img=None):
    if img is not None:
        p = log.failure(img, "restart")
        log(f"  Picture: {p}")
    st["restarts"] = [t for t in st["restarts"] if time.time() - t < 600] + [time.time()]
    if len(st["restarts"]) > MAX_RESTARTS:
        raise Stop(f"had to restart the game {len(st['restarts'])} times in 10 minutes (last: {why}). "
                   f"See logs\\unknown\\.")
    log(f"Restarting the game: {why}.")
    dev.restart(game["package"])
    time.sleep(float(game.get("cold_start_s") or 15))


def is_ad(dev, ads, game, log):
    """Is the unknown screen a confirmed ad? Ad SDK screen in front, another app in front, or Android's view of
    the screen shows an ad view. Anything else is treated as a game screen we don't know yet: never tapped."""
    pkg, act = dev.foreground()
    if pkg and pkg != game["package"]:
        return f"another app in front ({pkg})"
    if ads.is_ad_activity(act):
        return f"ad screen in front ({act})"
    xml = dev.ui_dump()
    if xml:
        surface, named, _ = adclose.ui_candidates(xml, width=dev.w, height=dev.h)
        if surface:
            return "Android shows an ad view inside the game"
    return None


def run(args, cfg, game, log, dev, scr, ads, ctx, st):
    stuck_limit = max(20.0, float(game.get("cold_start_s") or 20))
    level_limit = float(game.get("max_level_minutes", 10)) * 60
    t_unknown, unknown_saved = None, False
    prev_name, last_action, same_count = None, None, 0
    lost_in_row = stuck_solver = solves = 0
    t_progress = time.time()        # last time a level ended (overall watchdog)

    while st["done"] < args.levels:
        img = dev.screenshot()
        s, score = scr.classify(img)

        if time.time() - t_progress > level_limit:
            restart(dev, game, log, st, f"no level ended in {level_limit / 60:.0f} minutes", img)
            t_progress, prev_name = time.time(), None
            continue

        if s is None:   # not a known screen: a transition, an ad, or a game screen we don't know yet
            t_unknown = t_unknown or time.time()
            waited = time.time() - t_unknown
            if waited < UNKNOWN_GRACE:
                time.sleep(0.1)
                continue
            why = is_ad(dev, ads, game, log) if cfg.get("ads") != "offline" else None
            if why:
                prev_name = None
                if args.dry_run:
                    log(f"  Ad ({why}); dry run: not closing it.")
                    time.sleep(2)
                    continue
                t_ad = time.time()
                result = ads.handle(lambda im: scr.classify(im)[0] is not None, why=why)
                st["ads"] += 1
                st["ad_s"] += time.time() - t_ad
                if result == "restarted":
                    st["restarts"] = [t for t in st["restarts"] if time.time() - t < 600] + [time.time()]
                    if len(st["restarts"]) > MAX_RESTARTS:
                        raise Stop("had to restart the game too often in 10 minutes. See logs\\unknown\\.")
                t_unknown, unknown_saved = None, False
                continue
            if waited > 4 and not unknown_saved:
                unknown_saved = True
                p = log.failure(img, "unknown_screen")
                log(f"Unknown game screen (not an ad). Not tapping anything. Picture for the next fix: {p}")
            if waited > stuck_limit and not args.dry_run:
                restart(dev, game, log, st, f"unknown screen for {waited:.0f}s")
                t_unknown, unknown_saved, prev_name = None, False, None
                continue
            time.sleep(0.3)
            continue
        t_unknown, unknown_saved = None, False
        name, act = s["name"], s.get("action", "wait")
        arrived = name != prev_name      # first sight of this screen (not the same screen seen again)
        prev_name = name

        if act == "solve":
            solves += 1
            if solves > SOLVES_PER_LEVEL:
                solves = 0
                restart(dev, game, log, st, f"the solver ran {SOLVES_PER_LEVEL} times without the level ending", img)
                prev_name = None
                continue
            if st.get("new_level", True):
                st["level"] += 1
                st["new_level"] = False
                ctx["level_no"] = st["level"]      # the solver may overwrite this with the level read from screen
            log(f"Level screen ({name}, match {score:.2f}). Solving (run level {st['level']}).")
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
                    raise Stop("the solver is stuck on this level. The pictures in logs\\unknown\\ show why.")
            else:
                stuck_solver = 0
            dev.wait_still(max_wait=3)
            prev_name = None if res.get("result") == "done" else prev_name
            continue

        marks = s.get("marks")       # "won" / "lost": counted once, when the screen first appears
        if marks and arrived:
            solves = 0
            st["new_level"] = True
            t_progress = time.time()
            st["done"] += marks == "won"
            lost_in_row = 0 if marks == "won" else lost_in_row + 1
            secs = time.time() - st["level_t0"]
            res = ctx.pop("last_result", {})
            log(f"Level {marks.upper()} in {secs:.1f}s ({st['done']}/{args.levels} won). Ads this level: {st['ads']}")
            log.report(level=ctx.get("level_no") or "", result=marks, seconds=round(secs, 1), ads=st["ads"],
                       moves=res.get("moves", ""), mistakes=res.get("mistakes", ""), note=res.get("note", ""),
                       ad_seconds=round(st["ad_s"], 1), restarts=len(st["restarts"]))
            if cfg.get("save_screens") == "all":
                log.picture(img, f"level{ctx.get('level_no')}_{marks}")
            st["level_t0"], st["ads"], st["ad_s"] = time.time(), 0, 0.0
            if lost_in_row >= game.get("stop_after_losses", 2):
                log.failure(img, "lost_in_row")
                raise Stop(f"lost {lost_in_row} levels in a row. Pictures in logs\\unknown\\.")
            if st["done"] >= args.levels:
                break

        if act == "stop":
            raise Stop(f"screen '{name}' says stop ({s.get('note', '')}).")
        if act == "wait":
            time.sleep(0.2)
            continue

        key = (name, str(act))
        same_count = same_count + 1 if key == last_action else 1
        last_action = key
        if same_count > SAME_SCREEN_LIMIT:
            same_count = 0
            restart(dev, game, log, st, f"stuck on '{name}' (same action {SAME_SCREEN_LIMIT} times)", img)
            prev_name = None
            continue

        if isinstance(act, dict) and "tap" in act:
            x, y = act["tap"]
            log(f"'{name}': tapping ({x},{y}) {s.get('note', '')}")
            dev.tap(x, y)
        elif isinstance(act, dict) and "tap_template" in act:
            m = scr.find(img, act["tap_template"], act.get("region"))
            if m and m[2] >= act.get("min_score", 0.85):
                log(f"'{name}': tapping {act['tap_template']} at ({m[0]},{m[1]}) {s.get('note', '')}")
                dev.tap(m[0], m[1])
            else:
                log(f"'{name}': button {act['tap_template']} not found (best {m[2] if m else 0:.2f})")
        elif isinstance(act, dict) and "swipe" in act:
            dev.swipe(*act["swipe"])
        dev.wait_still(max_wait=2.5)
        if dev.dry_run:
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
