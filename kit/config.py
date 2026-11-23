"""Paths and settings. config.json (next to gamekit.py) holds one user's answers and detected phone values.
Game folders hold game.json (package, timings, paths). Nothing user-specific is hardcoded in the kit."""
import json
import os
import shutil

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(KIT_DIR)   # the "Any Game" folder, or a game folder holding its own copy of kit\


WARNING = ""   # set when a game folder lost its link to the kit (the bot prints it)


def _kit_root():
    """The master kit folder ("Any Game"). A game folder's game.json remembers where it is."""
    global WARNING
    if os.path.exists(os.path.join(HOME, "gamekit.py")):
        return HOME
    root = ""
    try:
        with open(os.path.join(HOME, "game.json"), encoding="utf-8") as f:
            root = json.load(f).get("kit_root", "")
        if root and os.path.exists(os.path.join(root, "gamekit.py")):
            return root
    except (OSError, ValueError):
        pass
    if os.path.exists(os.path.join(HOME, "game.json")):
        WARNING = (f"the kit folder recorded in game.json ({root or 'none'}) is gone, so the shared ad library and "
                   f"your settings can't be found. Run  gamekit sync \"<this game>\"  from the kit's new place.")
    return HOME


ROOT = _kit_root()
CONFIG_PATH = os.path.join(ROOT, "config.json")
# shared knowledge that grows with every game (ad close buttons, ad SDK screens, lessons); a game folder that lost
# its link to the kit uses its own library\ instead
LIBRARY = os.path.join(ROOT, "library") if os.path.isdir(os.path.join(ROOT, "library")) else os.path.join(HOME, "library")

DEFAULTS = {
    "base_folder": os.path.dirname(ROOT),            # where game folders are created
    "adb": "adb",
    "ads": "close",                 # close | offline (user cuts internet)
    "never_watch_reward_ads": True,
    "never_buy": True,              # fixed: the kit never taps money offers whatever this says
    "hints_allowed": False,
    "safe_mode": False,
    "save_screens": "failures",     # failures | all
    "wait_for_go": True,            # read by the agent (SKILL.md), not by the scripts: plan first, wait for "go"
    "serial": None,                 # which phone, when more than one is connected (set by setup)
    "phone": {},                    # filled by setup: model, android, width, height, density, touch
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["never_buy"] = True
    return cfg


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def find_adb(cfg=None):
    """adb from the ADB variable, config, PATH, or the usual install places."""
    cands = [os.environ.get("ADB"), (cfg or {}).get("adb"), shutil.which("adb"),
             r"C:\platform-tools\adb.exe",
             os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
             os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
             os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")]
    for c in cands:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return shutil.which(c) or c
    return None


def game_dir(name, cfg=None):
    cfg = cfg or load()
    return os.path.join(cfg["base_folder"], name)


def load_game(folder):
    p = os.path.join(folder, "game.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_game(folder, data):
    with open(os.path.join(folder, "game.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
