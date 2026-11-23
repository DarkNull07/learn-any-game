"""gamekit: learn any Android phone game from one recorded play session, then build a bot for it.

Run `gamekit help` for the steps."""
import argparse
import datetime as dt
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from kit import __version__, config  # noqa: E402

HELP = r"""
gamekit {ver}  -  learn any phone game from one recorded play session, then build a bot for it.
Kit folder: {root}

FIRST TIME (once per computer)
  1. Phone: Settings > About phone > tap "Build number" 7 times. Then Developer options > turn on USB debugging.
     (Some phones also need "USB debugging (Security settings)" to allow taps.)
  2. Connect the phone by USB and accept "Allow USB debugging" on the phone.
  3. Run:   gamekit setup
     It finds Python, adb and your phone by itself (model, screen size, touch screen) and asks only a few
     questions with defaults (Enter = default): where game folders go, ads, hints, safe mode.

FOR EACH NEW GAME (about 15 minutes of your time)
  4. Open the game on the phone, then run:   gamekit new "Game Name"
     This creates the game's folder next to this one with everything inside it.
  5. Fill the 6 lines of  <Game Name>\model\card.txt  (goal, how you lose, popups you decline, paid things).
  6. Run:   gamekit record "Game Name"     and play 10-15 levels normally. Press Enter here when done.
     If you can: lose once, let one ad play and close it, open a popup you normally decline, play one hard level.
  7. Run:   gamekit distill "Game Name"    (local, takes a minute; makes the pictures and timeline for the agent)
  8. Ask your agent:  "/learn-new-game build <Game Name>"   It reads the distilled session, writes
     model\GAME_MODEL.md, tests what it is unsure about on the phone, and writes the bot.
  9. Play with the bot:   <Game Name>\run.bat --levels 20      (--dry-run to only watch and log)

AFTER EACH RUN
 10. The bot saves logs\run_*.log, logs\report.csv and pictures of anything unknown in logs\unknown\.
     Ask the agent: "/learn-new-game improve <Game Name>". It fixes from the logs, not from long explanations.
 11. gamekit sync "Game Name"   shares what this game taught (ad buttons, general lessons) with every other game,
     and updates the game's copy of the kit.

OTHER COMMANDS
  gamekit doctor               check phone, adb, screen stream
  gamekit list                 games and their last results
  gamekit frame "Game" SEC     pull the picture at SEC seconds of the latest session (nothing is ever lost)
  gamekit pack                 zip this kit to share it (your config, logs and Python env are left out)

On macOS/Linux use ./gamekit.sh instead of gamekit, and run.sh / probe.sh in game folders.

RULES THE BOTS FOLLOW: never buy anything, never watch reward ads, close ads fast (and learn new close buttons),
detailed console output plus a log and report, pictures saved only when something goes wrong.
"""


def say(msg=""):
    print(msg, flush=True)


def ask(question, default, yes):
    if yes:
        return default
    try:
        a = input(f"  {question} [{default}]: ").strip()
    except EOFError:
        a = ""
    return a or default


def ask_bool(question, default, yes):
    a = ask(question + " (y/n)", "y" if default else "n", yes).lower()
    return a.startswith("y")


# ------------------------------------------------------------------ setup / doctor
def cmd_setup(a):
    cfg = config.load()
    say(f"gamekit {__version__} setup\n")
    say(f"Python {sys.version.split()[0]} at {sys.executable}")
    missing = []
    for mod, pipname in (("cv2", "opencv-python"), ("numpy", "numpy"), ("av", "av")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pipname)
    venv_py = _venv_python()
    if missing:
        say(f"Missing Python packages: {', '.join(missing)}")
        if os.path.abspath(sys.executable) == os.path.abspath(venv_py):
            subprocess.run([venv_py, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")], check=True)
        elif ask_bool(f"Create a private Python environment in {os.path.join(ROOT, '.venv')} and install them",
                      True, a.yes):
            if not os.path.exists(venv_py):
                subprocess.run([sys.executable, "-m", "venv", os.path.join(ROOT, ".venv")], check=True)
            subprocess.run([venv_py, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")], check=True)
        else:
            return 1
        say("Installed. Continuing setup inside that environment (gamekit uses it from now on).\n")
        return subprocess.call([venv_py, os.path.abspath(__file__), "setup"] + (["--yes"] if a.yes else []))
    adb = config.find_adb(cfg)
    if not adb:
        say("adb (Android platform-tools) was not found.")
        if ask_bool("Download Google's platform-tools into this kit folder (about 7 MB)", True, a.yes):
            adb = _get_platform_tools()
        if not adb:
            say("Install platform-tools from https://developer.android.com/tools/releases/platform-tools and run setup again.")
            return 1
    cfg["adb"] = adb
    say(f"adb: {adb}")
    out = subprocess.run([adb, "devices"], capture_output=True, text=True).stdout
    devs = [ln.split("\t") for ln in out.splitlines()[1:] if "\t" in ln]
    if not devs:
        say("No phone found. Connect it by USB, turn on USB debugging, accept the prompt on the phone, run setup again.")
        return 1
    ready = [d[0] for d in devs if d[1].strip() == "device"]
    if not ready:
        say(f"Phone found but it says '{devs[0][1].strip()}'. Accept 'Allow USB debugging' on the phone, run setup again.")
        return 1
    cfg["serial"] = None
    if len(ready) > 1:
        say("More than one phone/emulator is connected: " + ", ".join(ready))
        pick = ask("Serial of the phone to use", cfg.get("serial") if cfg.get("serial") in ready else ready[0], a.yes)
        cfg["serial"] = pick if pick in ready else ready[0]
    from kit.device import Device
    dev = Device(cfg, use_stream=False, log=say)
    props = dev.props()
    say("(Setup reads the phone only; it never taps anything.)")
    path, mx, my = dev.touch_device()
    cfg["phone"] = {**props, "width": dev.w, "height": dev.h, "density": dev.density(), "touch_device": path,
                    "touch_max": [mx, my]}
    say(f"Phone: {props.get('brand')} {props.get('model')}, Android {props.get('android')}, {dev.w}x{dev.h}, "
        f"touch {path}")
    say("\nA few questions (Enter keeps the default):")
    cfg["base_folder"] = ask("Folder where each game's folder is created", cfg["base_folder"], a.yes)
    cfg["ads"] = ask("Ads: 'close' (bot closes them fast) or 'offline' (you turn internet off)", cfg["ads"], a.yes)
    cfg["hints_allowed"] = ask_bool("May the bot use free hints/boosters", cfg["hints_allowed"], a.yes)
    cfg["safe_mode"] = ask_bool("Safe (slow, extra-careful) mode by default", cfg["safe_mode"], a.yes)
    cfg["save_screens"] = ask("Save pictures: 'failures' or 'all'", cfg["save_screens"], a.yes)
    cfg["never_buy"] = True
    cfg["never_watch_reward_ads"] = True
    cfg["setup_done"] = dt.datetime.now().isoformat(timespec="seconds")
    config.save(cfg)
    dev.close()
    say(f"\nSaved {config.CONFIG_PATH}. Never buying and never watching reward ads are always on.")
    say("Tip: turn on Do Not Disturb and Developer options > Stay awake before long runs.")
    say('Next: open a game on the phone and run  gamekit new "Game Name"')
    return 0


def _get_platform_tools():
    import urllib.request
    plat = {"nt": "windows", "posix": "darwin" if sys.platform == "darwin" else "linux"}[os.name]
    url = f"https://dl.google.com/android/repository/platform-tools-latest-{plat}.zip"
    dest = os.path.join(ROOT, "tools")
    os.makedirs(dest, exist_ok=True)
    z = os.path.join(dest, "platform-tools.zip")
    say(f"Downloading {url}")
    urllib.request.urlretrieve(url, z)
    with zipfile.ZipFile(z) as f:
        f.extractall(dest)
    os.remove(z)
    adb = os.path.join(dest, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
    if os.name != "nt" and os.path.exists(adb):
        os.chmod(adb, 0o755)
    return adb if os.path.exists(adb) else None


def cmd_doctor(a):
    import time
    from kit.device import Device
    cfg = config.load()
    say(f"Config: {config.CONFIG_PATH} ({'found' if os.path.exists(config.CONFIG_PATH) else 'missing: run gamekit setup'})")
    dev = Device(cfg, log=say)
    say(f"Phone {dev.props().get('model')} {dev.w}x{dev.h}, in front: {dev.foreground()}")
    t0, n = time.time(), 0
    while time.time() - t0 < 2:
        dev.screenshot()
        n += 1
    say(f"Screenshots: {n / 2:.1f} per second ({'live stream' if dev.live() else 'adb screencap'})")
    dev.close()
    return 0


# ------------------------------------------------------------------ games
def _game(name, must_exist=True):
    folder = config.game_dir(_check_name(name))
    if must_exist and not os.path.exists(os.path.join(folder, "game.json")):
        say(f'No game "{name}" in {config.load()["base_folder"]}. Create it with: gamekit new "{name}"')
        sys.exit(1)
    return folder


def _venv_python():
    if os.name == "nt":
        return os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return os.path.join(ROOT, ".venv", "bin", "python")


def _python_for_games():
    venv_py = _venv_python()
    return venv_py if os.path.exists(venv_py) else sys.executable


NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()'&+-]{0,59}$")


def _check_name(name):
    """A game name becomes a folder name: no paths, no tricks."""
    if not NAME_OK.match(name) or ".." in name or name.strip() != name or name.endswith("."):
        say(f'"{name}" can\'t be a game folder name. Use letters, numbers, spaces and - _ . ( ) only.')
        sys.exit(1)
    return name


def _copy_kit(dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(os.path.join(ROOT, "kit"), dest, ignore=shutil.ignore_patterns("__pycache__"))


def cmd_new(a):
    cfg = config.load()
    if not cfg.get("setup_done"):
        say("Run gamekit setup first.")
        return 1
    folder = _game(a.name, must_exist=False)
    if os.path.exists(os.path.join(folder, "game.json")):
        say(f"{folder} already exists. Nothing changed.")
        return 1
    from kit.device import PACKAGE
    pkg = a.package
    if pkg and not PACKAGE.match(pkg):
        say(f"{pkg} is not an Android package name (like com.company.game).")
        return 1
    if not pkg:
        try:
            from kit.device import Device
            dev = Device(cfg, use_stream=False, log=say)
            pkg, act = dev.foreground()
            dev.close()
            say(f"Game in front on the phone: {pkg}")
        except Exception as e:  # noqa: BLE001
            say(f"Could not ask the phone ({e}). Pass --package com.example.game")
            return 1
        if not pkg or "launcher" in pkg.lower() or pkg in ("com.android.systemui", "com.android.settings"):
            say("The phone shows the home screen or a system screen. Open the game first, or pass "
                "--package com.example.game")
            return 1
    os.makedirs(folder, exist_ok=True)
    tpl = os.path.join(ROOT, "template")
    for item in os.listdir(tpl):
        src, dst = os.path.join(tpl, item), os.path.join(folder, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    _copy_kit(os.path.join(folder, "kit"))
    for sub in ("sessions", "logs", "regress", "backups", "waste", os.path.join("model", "shots"),
                os.path.join("model", "templates")):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    _write_launchers(folder)
    gm = os.path.join(folder, "model", "GAME_MODEL.md")
    with open(gm, encoding="utf-8") as f:
        s = f.read().replace("<Game Name>", a.name)
    with open(gm, "w", encoding="utf-8") as f:
        f.write(s)
    config.save_game(folder, {"name": a.name, "package": pkg, "kit_root": ROOT, "kit_version": __version__,
                              "created": dt.datetime.now().isoformat(timespec="seconds"),
                              "cold_start_s": None, "warm_resume_s": None, "stop_after_losses": 2})
    say(f"Created {folder}")
    say(_tree(folder))
    say(f'Next: fill model\\card.txt, then  gamekit record "{a.name}"  and play 10-15 levels.')
    return 0


def _write_launchers(folder):
    """run.bat/probe.bat (Windows) and run.sh/probe.sh (macOS/Linux) with this kit's Python baked in."""
    py = _python_for_games()
    for name in ("run.bat", "probe.bat", "run.sh", "probe.sh"):
        src = os.path.join(ROOT, "template", name)
        p = os.path.join(folder, name)
        if not os.path.exists(src):
            continue
        with open(src, encoding="utf-8") as f:
            s = f.read().replace("@@PYTHON@@", py)
        with open(p, "w", encoding="utf-8", newline="\r\n" if name.endswith(".bat") else "\n") as f:
            f.write(s.replace("\r\n", "\n"))
        if name.endswith(".sh"):
            os.chmod(p, 0o755)


def _tree(folder):
    lines = []
    for item in sorted(os.listdir(folder)):
        lines.append(f"  {item}{os.sep if os.path.isdir(os.path.join(folder, item)) else ''}")
    return "\n".join(lines)


def cmd_record(a):
    from kit.recorder import Recorder
    folder = _game(a.name)
    Recorder(folder, minutes=a.minutes, log=say).run()
    return 0


def _latest_session(folder, which=None):
    root = os.path.join(folder, "sessions")
    if which:
        return os.path.join(root, which)
    ss = sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "meta.json")))
    if not ss:
        say(f'No recorded session yet. Run: gamekit record "{os.path.basename(folder)}"')
        sys.exit(1)
    return os.path.join(root, ss[-1])


def cmd_distill(a):
    from kit import distill
    folder = _game(a.name)
    out = distill.run(_latest_session(folder, a.session), log=say)
    if out:
        say(f'Next: ask your agent  "/learn-new-game build {a.name}"')
    return 0


def cmd_frame(a):
    from kit import distill
    folder = _game(a.name)
    p = distill.frame_at(_latest_session(folder, a.session), a.seconds)
    say(f"Saved {p}" if p else "No frame found.")
    return 0


def cmd_list(a):
    base = config.load()["base_folder"]
    for d in sorted(os.listdir(base)):
        gj = os.path.join(base, d, "game.json")
        if not os.path.exists(gj):
            continue
        g = config.load_game(os.path.join(base, d))
        rep = os.path.join(base, d, "logs", "report.csv")
        last = ""
        if os.path.exists(rep):
            with open(rep, encoding="utf-8") as f:
                rows = f.read().strip().splitlines()[1:]
            won = sum(1 for r in rows if ",won," in r)
            last = f"{won} levels won in {len(rows)} logged"
        sess = len(os.listdir(os.path.join(base, d, "sessions"))) if os.path.isdir(os.path.join(base, d, "sessions")) else 0
        say(f"  {d:<28} {g.get('package', ''):<36} kit {g.get('kit_version')}  sessions {sess}  {last}")
    return 0


def cmd_sync(a):
    folder = _game(a.name)
    g = config.load_game(folder)
    # 1. general lessons from the game -> shared library
    shared = os.path.join(config.LIBRARY, "LESSONS.md")
    with open(shared, encoding="utf-8") as f:
        have = f.read()
    added = 0
    gl = os.path.join(folder, "model", "LESSONS.md")
    if os.path.exists(gl):
        with open(gl, encoding="utf-8") as f:
            for line in f:
                # only real lesson lines: "YYYY-MM-DD | ... [general]" (never headers or other text)
                if re.match(r"^\d{4}-\d{2}-\d{2}\s*\|", line) and "[general]" in line:
                    text = line.replace("[general]", "").strip()
                    if text and text not in have:
                        with open(shared, "a", encoding="utf-8") as s:
                            s.write(f"{text}  (from {a.name})\n")
                        added += 1
    say(f"Lessons shared with every game: {added} new")
    # 2. the kit copy in the game folder
    if g.get("kit_version") != __version__ or a.force:
        old = g.get("kit_version")
        backup = os.path.join(folder, "backups", f"kit_{old}_{dt.datetime.now():%Y%m%d_%H%M%S}")
        shutil.copytree(os.path.join(folder, "kit"), backup, ignore=shutil.ignore_patterns("__pycache__"))
        _copy_kit(os.path.join(folder, "kit"))
        # the generic template files too (never solver.py or model\: those belong to the game)
        for name in GENERIC_FILES:
            src, dst = os.path.join(ROOT, "template", name), os.path.join(folder, name)
            if os.path.exists(dst):
                shutil.copy2(dst, os.path.join(backup, name))
            if os.path.exists(src) and not name.endswith((".bat", ".sh")):
                shutil.copy2(src, dst)
        _write_launchers(folder)
        g["kit_version"] = __version__
        say(f"Kit and bot skeleton updated {old} -> {__version__} (old copies in {backup}). Run regress_test.py now.")
    else:
        say(f"Kit in the game folder is current ({__version__}).")
    if g.get("kit_root") != ROOT:
        say(f"Kit folder link fixed: {g.get('kit_root')} -> {ROOT}")
    g["kit_root"] = ROOT
    config.save_game(folder, g)
    n = len([f for f in os.listdir(os.path.join(config.LIBRARY, "ads", "close_templates")) if f.endswith(".png")])
    say(f"Shared ad close buttons known: {n}")
    return 0


GENERIC_FILES = ("bot.py", "probe.py", "regress_test.py", "run.bat", "probe.bat", "run.sh", "probe.sh")


def cmd_pack(a):
    skip = [".git", ".github", ".venv", "__pycache__", "config.json", "tools", "*.zip", "ads_log.csv", "blacklist"]
    out = os.path.join(os.path.dirname(ROOT), f"AnyGame_kit_{__version__}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in skip)]
            for fn in files:
                if any(fnmatch.fnmatch(fn, p) for p in skip):
                    continue
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.join("Any Game", os.path.relpath(full, ROOT)))
    say(f"Saved {out}. Give it with SKILL.md; the other person unzips it and runs gamekit setup.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gamekit", add_help=False)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("help")
    p = sub.add_parser("setup")
    p.add_argument("--yes", action="store_true", help="accept all defaults (for agents)")
    sub.add_parser("doctor")
    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--package")
    p = sub.add_parser("record")
    p.add_argument("name")
    p.add_argument("--minutes", type=float)
    p = sub.add_parser("distill")
    p.add_argument("name")
    p.add_argument("session", nargs="?")
    p = sub.add_parser("frame")
    p.add_argument("name")
    p.add_argument("seconds", type=float)
    p.add_argument("session", nargs="?")
    sub.add_parser("list")
    p = sub.add_parser("sync")
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    sub.add_parser("pack")
    a = ap.parse_args(argv)
    if a.cmd in (None, "help"):
        say(HELP.format(ver=__version__, root=ROOT))
        return 0
    return {"setup": cmd_setup, "doctor": cmd_doctor, "new": cmd_new, "record": cmd_record, "distill": cmd_distill,
            "frame": cmd_frame, "list": cmd_list, "sync": cmd_sync, "pack": cmd_pack}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
