#!/usr/bin/env python3
"""BAR Settings Transfer (Linux / macOS / any Python 3.6+).

Exports a player's Beyond All Reason settings (options, keybinds, widget config, lobby
preferences, blueprints) into one zip, and imports such a zip into another install without
touching that machine's display / hardware settings. Same zip format as the Windows
PowerShell tool, so a bundle made on one OS imports on the other.

    bar-settings-transfer.py export  [--data-dir DIR] [--out-dir DIR]
    bar-settings-transfer.py import  [--data-dir DIR] [--bundle ZIP] [--keep-local-graphics]
    bar-settings-transfer.py restore [--data-dir DIR]
    bar-settings-transfer.py         (menu)

No dependencies beyond the Python standard library.
"""

import argparse
import datetime
import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from collections import OrderedDict

TOOL_VERSION = "1.0"

# ---------------------------------------------------------------------------
# What travels and what stays (kept in sync with bar-settings-transfer.ps1)
# ---------------------------------------------------------------------------

MACHINE_KEYS = {
    # display
    "XResolution", "YResolution", "XResolutionWindowed", "YResolutionWindowed",
    "WindowPosX", "WindowPosY", "WindowState", "Fullscreen", "WindowBorderless",
    "MinimizeOnFocusLoss", "SelectedDisplay", "SelectedScreenMode",
    "DualScreenMode", "DualScreenMiniMapOnLeft", "DualScreenMiniMapAspectRatio",
    "BlockCompositing", "DWMFlush",
    # hardware
    "snd_device", "SetCoreAffinity", "WorkerThreadCount", "PathingThreadCount",
    "ThreadPinPolicy", "TextureMemPoolSize", "GLContextMajorVersion", "GLContextMinorVersion",
    "UseHighResTimer",
    # paths / identity / bookkeeping
    "SpringData", "SpringDataRoot", "DefaultStartScript", "MenuArchive", "SplashScreenDir",
    "name", "address", "FirstRun", "ChobbyLaunchesCount", "WelcomeMessagePlayed",
    "OpenSkillSnapshotLastFetch", "OpenSkillSnapshotLastSuccessfulFetch",
    "Version", "version", "LogFlush", "LogFlushLevel", "RotateLogFiles", "VerboseLevel",
    "RapidTagResolutionOrder", "skirmish_gameType_choice", "skirmish_map_choice",
    "WindowsPausedAtFrame",
}

GRAPHICS_KEYS = {
    "graphicsPreset", "Shadows", "ShadowMapSize", "ShadowQuality", "MSAA", "MSAALevel",
    "Water", "water", "ReflectiveWater", "BumpWaterAnisotropy", "BumpWaterBlurReflection",
    "BumpWaterDepthBits", "BumpWaterReflection", "BumpWaterRefraction", "BumpWaterTexSizeReflection",
    "BumpWaterDynamicWaves", "BumpWaterEndlessOcean", "BumpWaterOcclusionQuery",
    "BumpWaterUseDepthTexture", "BumpWaterUseUniforms", "BumpWaterShoreWaves",
    "CubeTexSizeReflection", "CubeTexSizeSpecular", "CubeTexGenerateMipMaps", "CubeTexSpecularExponent",
    "GroundDecals", "GroundDetail", "GrassDetail", "TreeRadius", "TreeWind",
    "MaxParticles", "MaxNanoParticles", "NanoParticlesGL4", "NanoParticleMode",
    "AdvMapShading", "AdvModelShading", "AdvUnitShading", "NormalMapping",
    "UnitLodDist", "LODScale", "LODScaleReflection", "LODScaleRefraction", "LODScaleShadow",
    "FeatureDrawDistance", "FeatureFadeDistance", "HighQualityDecals", "MinSampleShadingRate",
    "cus2", "LuaShaders", "AllowDeferredMapRendering", "AllowDeferredModelRendering",
    "AllowCombinedMapRendering", "ui_rendertotexture", "VSync", "VSyncFraction", "VSyncGame",
    "LimitIdleFps", "MinDrawFPS", "MinSimDrawBalance",
}

LOBBY_EXCLUDED_KEYS = {
    # userName stays home too: a fresh install has autoLogin on, and a name without a
    # password makes the lobby attempt a login and error out.
    "userName", "password", "rememberPassword", "autoLogin", "myAccountID", "serverAddress", "serverPort",
    "steamLinkComplete", "suggestedNameFromSteam", "wantAuthenticateWithSteam",
    "firstLoginEver", "firstBattleStarted", "lastStartedBattleID", "gameConfigName",
    "game_fullscreen", "lobby_fullscreen", "agressivelySetBorderlessWindowed",
    "multiplayerDifferentEngine", "useWrongEngine", "multiplayerLaunchNewSpring", "useSpringRestart",
    "doNotSetAnySpringSettings", "debugMode", "enableProfiler", "enableInspector",
    "activeDebugConsole", "campaignSpawnDebug", "editCampaign", "loadLocalWidgets",
    "autoUpdateWidgets", "lobbyIdleSleep", "enableCacheRapidPool", "pluginsInstallDisclaimerAccepted",
}
LOBBY_EXCLUDED_PREFIXES = ("window_",)

PLAIN_FILES = [
    ("uikeys.txt", "active keybinds"),
    ("LuaUI/Config/keybind_profiles.json", "keybind profiles"),
    ("LuaUI/Config/BYAR.lua", "widget settings + enabled widgets"),
    ("LuaUI/Config/blueprints.json", "blueprints"),
    ("favourite_maps.txt", "favourite maps"),
]

LOBBY_FILE = "LuaMenu/Config/IGL_data.lua"
LOBBY_SECTION = "Chili lobby"
BACKUP_ROOT = "settings-transfer-backup"
GAME_PROCESSES = ("spring", "spring-headless", "spring-dedicated", "Beyond-All-Reason", "beyond-all-reason")

NO_PROMPT = False
FORCE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def say(text=""):
    print(text)


def big(text):
    print()
    print("  " + text)
    print()


def fail(text):
    print()
    print("  PROBLEM: " + text)
    print()
    sys.exit(1)


def ask(question, default):
    if NO_PROMPT:
        return default
    try:
        answer = input("  " + question + " ").strip()
    except EOFError:
        return default
    return answer or default


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def game_running():
    """True when the engine or the launcher is running (Linux /proc scan, pgrep elsewhere)."""
    names = set(GAME_PROCESSES)
    if os.path.isdir("/proc"):
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid) as f:
                    comm = f.read().strip()
            except OSError:
                continue
            if comm in names or comm.lower().startswith("beyond-all-reason"):
                return True
        return False
    try:
        out = subprocess.run(["pgrep", "-x", "|".join(names)], capture_output=True, text=True)
        return out.returncode == 0 and out.stdout.strip() != ""
    except OSError:
        return False


def is_data_dir(path):
    if not path or not os.path.isdir(path):
        return False
    for marker in ("springsettings.cfg", "launcher_cfg.json", "engine", "games", "LuaUI"):
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


def find_data_dir(given):
    if given:
        if is_data_dir(given):
            return os.path.abspath(given)
        fail("'%s' is not a Beyond All Reason data folder." % given)

    home = os.path.expanduser("~")
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # spring-launcher on Linux: $XDG_STATE_HOME/<title>, older installs under ~/Documents/<title>
        os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), "Beyond All Reason"),
        os.path.join(home, "Documents", "Beyond All Reason"),
        os.path.join(home, ".local", "state", "Beyond-All-Reason"),
        # flatpak / steam deck style sandboxes
        os.path.join(home, ".var", "app", "info.beyondallreason.bar", "data", "Beyond All Reason"),
        # the script sits inside or next to an install
        os.path.join(here, "data"),
        os.path.join(os.path.dirname(here), "data"),
        here,
        os.path.join(home, "Beyond All Reason"),
        os.path.join(home, "Games", "Beyond-All-Reason", "data"),
    ]
    for c in candidates:
        if is_data_dir(c):
            return os.path.abspath(c)

    if NO_PROMPT:
        fail("Could not find the Beyond All Reason data folder. Pass --data-dir.")
    big("I could not find your Beyond All Reason folder automatically.")
    say("  It is usually ~/.local/state/Beyond All Reason  (contains springsettings.cfg).")
    typed = ask("Type the full path to it:", "")
    typed = os.path.expanduser(typed.strip().strip('"').strip("'"))
    if is_data_dir(typed):
        return os.path.abspath(typed)
    if is_data_dir(os.path.join(typed, "data")):
        return os.path.abspath(os.path.join(typed, "data"))
    fail("'%s' does not look like the BAR data folder." % typed)


# springsettings.cfg <-> ordered list of [key, value, raw]; comments / blanks kept as-is.
CFG_LINE = re.compile(r"^\s*([^=\s#;][^=]*?)\s*=\s*(.*?)\s*$")


def read_cfg(path):
    entries = []
    if not os.path.isfile(path):
        return entries
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines():
            m = CFG_LINE.match(line)
            if m:
                entries.append([m.group(1), m.group(2), None])
            else:
                entries.append([None, None, line])
    return entries


def write_cfg(path, entries):
    # Recoil writes plain "\n" on Linux.
    lines = [(e[0] + " = " + e[1]) if e[0] is not None else e[2] for e in entries]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def set_cfg_value(entries, key, value):
    for e in entries:
        if e[0] == key:
            e[1] = value
            return
    entries.append([key, value, None])


def cfg_get(entries, key):
    for e in entries:
        if e[0] == key:
            return e[1]
    return None


LOBBY_OPEN = re.compile(r'^\t\["' + re.escape(LOBBY_SECTION) + r'"\]\s*=\s*\{\s*$')
LOBBY_SCALAR = re.compile(r'^\t\t(?:\["([^"]+)"\]|([A-Za-z_][A-Za-z_0-9]*))\s*=\s*(.*?),\s*$')


def read_lobby_scalars(path):
    """Scalar lines of the "Chili lobby" block (table.save format, nested tables skipped)."""
    result = OrderedDict()
    if not os.path.isfile(path):
        return result
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    in_block = False
    depth = 0
    for line in lines:
        if not in_block:
            if LOBBY_OPEN.match(line):
                in_block = True
                depth = 0
            continue
        if depth == 0 and re.match(r"^\t\},?\s*$", line):
            break
        if depth > 0:
            if re.search(r"\{\s*$", line):
                depth += 1
            elif re.match(r"^\s*\},?\s*$", line):
                depth -= 1
            continue
        if re.search(r"\{\s*$", line):
            depth = 1
            continue
        m = LOBBY_SCALAR.match(line)
        if m:
            result[m.group(1) or m.group(2)] = m.group(3)
    return result


def lobby_key_allowed(key):
    if key in LOBBY_EXCLUDED_KEYS:
        return False
    return not key.startswith(LOBBY_EXCLUDED_PREFIXES)


def format_lobby_line(key, value):
    if re.match(r"^[A-Za-z_][A-Za-z_0-9]*$", key):
        return "\t\t%s = %s," % (key, value)
    return '\t\t["%s"] = %s,' % (key, value)


def merge_lobby_scalars(path, values):
    """Merge scalar values into the local IGL_data.lua, creating file / block when absent."""
    if not values:
        return 0
    lines = []
    newline = "\n"
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        if "\r\n" in raw:
            newline = "\r\n"
        lines = raw.splitlines()
    if not lines:
        lines = ["-- Addon Custom Data", "return {", "}"]

    start = next((i for i, l in enumerate(lines) if LOBBY_OPEN.match(l)), -1)
    if start < 0:
        close = next((i for i in range(len(lines) - 1, -1, -1) if re.match(r"^\}\s*$", lines[i])), -1)
        if close < 0:
            raise ValueError("IGL_data.lua has no closing brace; refusing to edit it")
        lines.insert(close, '\t["%s"] = {' % LOBBY_SECTION)
        lines.insert(close + 1, "\t},")
        start = close

    end = -1
    depth = 0
    seen = set()
    changed = 0
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if depth == 0 and re.match(r"^\t\},?\s*$", line):
            end = i
            break
        if depth > 0:
            if re.search(r"\{\s*$", line):
                depth += 1
            elif re.match(r"^\s*\},?\s*$", line):
                depth -= 1
            i += 1
            continue
        if re.search(r"\{\s*$", line):
            depth = 1
            i += 1
            continue
        m = LOBBY_SCALAR.match(line)
        if m:
            k = m.group(1) or m.group(2)
            if k in values:
                seen.add(k)
                if m.group(3) != values[k]:
                    lines[i] = format_lobby_line(k, values[k])
                    changed += 1
        i += 1
    if end < 0:
        raise ValueError("IGL_data.lua lobby block never closes; refusing to edit it")
    for k, v in values.items():
        if k not in seen:
            lines.insert(end, format_lobby_line(k, v))
            end += 1
            changed += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + newline)
    return changed


def copy_into(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def write_text(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def is_removable(path):
    """Best effort: media mounted under /media, /run/media or /Volumes counts as a stick.
    (/mnt is left out on purpose: WSL and many distros mount fixed drives there.)"""
    p = os.path.abspath(path)
    return p.startswith(("/media/", "/run/media/", "/Volumes/"))


def desktop_dir():
    home = os.path.expanduser("~")
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    d = os.path.join(home, "Desktop")
    return d if os.path.isdir(d) else home


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def do_export(args):
    data = find_data_dir(args.data_dir)
    big("Exporting settings from: " + data)
    if game_running() and not FORCE:
        fail("Beyond All Reason is running. Close the game and the lobby, then run this again.")

    cfg_path = os.path.join(data, "springsettings.cfg")
    if not os.path.isfile(cfg_path):
        fail("No springsettings.cfg here. Start the game once, change any setting, quit, then export.")

    work = tempfile.mkdtemp(prefix="bar-settings-export-")
    included = []

    copy_into(cfg_path, os.path.join(work, "springsettings.cfg"))
    included.append("springsettings.cfg  (game options)")

    cfg = read_cfg(cfg_path)
    key_file = cfg_get(cfg, "KeybindingFile")
    if key_file and key_file != "uikeys.txt":
        custom = os.path.abspath(os.path.join(data, key_file))
        if os.path.isfile(custom) and custom.startswith(os.path.abspath(data) + os.sep):
            copy_into(custom, os.path.join(work, key_file))
            included.append(key_file + "  (custom keybind file)")

    for rel, what in PLAIN_FILES:
        src = os.path.join(data, rel)
        if os.path.isfile(src):
            copy_into(src, os.path.join(work, rel))
            included.append("%s  (%s)" % (rel, what))

    lobby = read_lobby_scalars(os.path.join(data, LOBBY_FILE))
    kept = ["%s = %s" % (k, v) for k, v in lobby.items() if lobby_key_allowed(k)]
    if kept:
        write_text(os.path.join(work, "lobby_settings.txt"), kept)
        included.append("lobby_settings.txt  (%d lobby preferences, no password)" % len(kept))

    xres = cfg_get(cfg, "XResolution") or "?"
    yres = cfg_get(cfg, "YResolution") or "?"
    write_text(os.path.join(work, "manifest.txt"), [
        "tool = BAR Settings Transfer " + TOOL_VERSION + " (python)",
        "exported = " + datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "machine = " + socket.gethostname(),
        "user = " + getpass.getuser(),
        "dataDir = " + data,
        "sourceResolution = %sx%s" % (xres, yres),
    ])

    here = os.path.dirname(os.path.abspath(__file__))
    target = args.out_dir or (here if is_removable(here) else desktop_dir())
    os.makedirs(target, exist_ok=True)
    safe_user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser())
    zip_path = os.path.join(target, "BAR-settings-%s-%s.zip" % (safe_user, datetime.date.today().isoformat()))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(work):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, work).replace(os.sep, "/"))
    shutil.rmtree(work, ignore_errors=True)

    say("  Included:")
    for i in included:
        say("    - " + i)
    big("DONE. Your settings file is: " + zip_path)
    say("  Copy that one file to a USB stick (or send it to yourself) and run IMPORT on the other PC.")
    return zip_path


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def find_bundle(given):
    if given:
        if os.path.isfile(given):
            return os.path.abspath(given)
        fail("Settings file not found: " + given)
    here = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    places = [here, desktop_dir(), os.path.join(home, "Downloads"), home]
    for mount_root in ("/media", "/run/media", "/mnt"):
        if os.path.isdir(mount_root):
            for root, dirs, _files in os.walk(mount_root):
                if root.count(os.sep) - mount_root.count(os.sep) >= 2:
                    dirs[:] = []
                places.append(root)
    found = {}
    for p in places:
        if not os.path.isdir(p):
            continue
        try:
            for name in os.listdir(p):
                if name.startswith("BAR-settings-") and name.endswith(".zip"):
                    full = os.path.join(p, name)
                    if os.path.isfile(full):
                        found[full] = os.path.getmtime(full)
        except OSError:
            continue
    found = sorted(found.items(), key=lambda kv: kv[1], reverse=True)
    if len(found) == 1:
        return found[0][0]
    if len(found) > 1:
        if NO_PROMPT:
            return found[0][0]
        say("  Several settings files found:")
        for i, (path, mtime) in enumerate(found):
            when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            say("    [%d] %s   (%s)  %s" % (i + 1, os.path.basename(path), when, os.path.dirname(path)))
        pick = ask("Which one? (number, Enter = newest)", "1")
        try:
            n = int(pick)
        except ValueError:
            n = 1
        if n < 1 or n > len(found):
            n = 1
        return found[n - 1][0]
    if NO_PROMPT:
        fail("No BAR-settings-*.zip found. Pass --bundle.")
    big("I could not find a BAR-settings-*.zip next to this tool, on the Desktop, in Downloads or on a USB stick.")
    typed = ask("Type the full path to the zip:", "")
    typed = os.path.expanduser(typed.strip().strip('"').strip("'"))
    if os.path.isfile(typed):
        return os.path.abspath(typed)
    fail("No file at " + typed)


def do_import(args):
    data = find_data_dir(args.data_dir)
    zip_path = find_bundle(args.bundle)
    big("Importing %s  into  %s" % (os.path.basename(zip_path), data))
    if game_running() and not FORCE:
        fail("Beyond All Reason is running. Close the game and the lobby, then run IMPORT again.")

    now = stamp()
    work = tempfile.mkdtemp(prefix="bar-settings-import-")
    bundle_files = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            # Windows Compress-Archive stores backslash paths; normalise.
            rel = info.filename.replace("\\", "/")
            if rel.endswith("/") or rel.startswith("/") or ".." in rel.split("/"):
                continue
            dst = os.path.join(work, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            bundle_files.append(rel)
    if "manifest.txt" not in bundle_files:
        shutil.rmtree(work, ignore_errors=True)
        fail("That zip was not made by BAR Settings Transfer (no manifest.txt inside).")
    with open(os.path.join(work, "manifest.txt"), encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines():
            say("    " + line)

    backup = os.path.join(data, BACKUP_ROOT, now)
    os.makedirs(backup, exist_ok=True)
    touch = ["springsettings.cfg", LOBBY_FILE] + [rel for rel, _ in PLAIN_FILES]
    for rel in bundle_files:
        if rel in ("manifest.txt", "lobby_settings.txt"):
            continue
        if rel not in touch:
            touch.append(rel)
    backed_up = 0
    for rel in touch:
        src = os.path.join(data, rel)
        if os.path.isfile(src):
            copy_into(src, os.path.join(backup, rel))
            backed_up += 1
    say("  Backed up %d current file(s) to %s" % (backed_up, backup))

    # 1. springsettings.cfg merge
    cfg_path = os.path.join(data, "springsettings.cfg")
    local = read_cfg(cfg_path)
    incoming = read_cfg(os.path.join(work, "springsettings.cfg"))
    skip = set(MACHINE_KEYS)
    if args.keep_local_graphics:
        skip |= GRAPHICS_KEYS
    applied = skipped = 0
    for key, value, _raw in incoming:
        if key is None:
            continue
        if key in skip:
            skipped += 1
            continue
        set_cfg_value(local, key, value)
        applied += 1
    # The lobby pushes its default settings table over springsettings.cfg at the first
    # battle start on a fresh install. Mark that as done so the import survives.
    set_cfg_value(local, "FirstRun", "0")
    kf = cfg_get(incoming, "KeybindingFile")
    if kf and os.path.isfile(os.path.join(work, kf)):
        set_cfg_value(local, "KeybindingFile", kf)
    elif os.path.isfile(os.path.join(work, "uikeys.txt")):
        set_cfg_value(local, "KeybindingFile", "uikeys.txt")
    write_cfg(cfg_path, local)
    say("  springsettings.cfg: %d settings applied, %d machine-specific ones kept from this PC%s"
        % (applied, skipped, " (graphics kept local)" if args.keep_local_graphics else ""))

    # 2. plain files (+ custom keybind file)
    for rel in bundle_files:
        if rel in ("manifest.txt", "lobby_settings.txt", "springsettings.cfg"):
            continue
        copy_into(os.path.join(work, rel), os.path.join(data, rel))
        say("  installed " + rel)

    # 3. lobby preferences
    lobby_txt = os.path.join(work, "lobby_settings.txt")
    if os.path.isfile(lobby_txt):
        values = OrderedDict()
        with open(lobby_txt, encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines():
                m = re.match(r"^([^=]+?) = (.*)$", line)
                if m and lobby_key_allowed(m.group(1)):
                    values[m.group(1)] = m.group(2)
        try:
            n = merge_lobby_scalars(os.path.join(data, LOBBY_FILE), values)
            say("  lobby preferences: %d value(s) updated" % n)
        except ValueError as e:
            say("  lobby preferences skipped: " + str(e))

    shutil.rmtree(work, ignore_errors=True)
    big("DONE. Start Beyond All Reason; your settings and keybinds are in place.")
    say("  Changed your mind? Run RESTORE to put back the %d file(s) from before this import." % backed_up)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def do_restore(args):
    data = find_data_dir(args.data_dir)
    if game_running() and not FORCE:
        fail("Beyond All Reason is running. Close it first.")
    root = os.path.join(data, BACKUP_ROOT)
    if not os.path.isdir(root):
        fail("No backups here (nothing was ever imported into this install).")
    backups = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True)
    if not backups:
        fail("No backups here.")
    pick = backups[0]
    if len(backups) > 1 and not NO_PROMPT:
        say("  Backups (newest first):")
        for i, b in enumerate(backups):
            say("    [%d] %s" % (i + 1, b))
        a = ask("Which one? (number, Enter = newest)", "1")
        try:
            n = int(a)
        except ValueError:
            n = 1
        if n < 1 or n > len(backups):
            n = 1
        pick = backups[n - 1]
    src_root = os.path.join(root, pick)
    big("Restoring files from " + src_root)
    count = 0
    for r, _dirs, files in os.walk(src_root):
        for name in files:
            full = os.path.join(r, name)
            rel = os.path.relpath(full, src_root)
            copy_into(full, os.path.join(data, rel))
            say("  restored " + rel)
            count += 1
    big("DONE. %d file(s) restored." % count)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    global NO_PROMPT, FORCE
    ap = argparse.ArgumentParser(description="BAR Settings Transfer")
    ap.add_argument("mode", nargs="?", default="menu", choices=["export", "import", "restore", "menu"])
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--bundle", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--keep-local-graphics", action="store_true",
                    help="keep this machine's graphics quality settings; import only controls / UI / sound / keybinds")
    ap.add_argument("--force", action="store_true", help="skip the running-game refusal")
    ap.add_argument("--no-prompt", action="store_true", help="never ask; fail instead")
    args = ap.parse_args()
    NO_PROMPT = args.no_prompt
    FORCE = args.force

    print()
    print("  BAR Settings Transfer " + TOOL_VERSION)
    print("  ---------------------------")
    mode = args.mode
    if mode == "menu":
        say("  [1] EXPORT  my settings from this PC into one zip file")
        say("  [2] IMPORT  a settings zip into this PC (keeps this PC's screen / hardware settings)")
        say("  [3] RESTORE this PC's settings from before the last import")
        mode = {"1": "export", "2": "import", "3": "restore"}.get(ask("What do you want to do? (1/2/3)", "1"))
        if not mode:
            fail("Not a valid choice.")
    {"export": do_export, "import": do_import, "restore": do_restore}[mode](args)


if __name__ == "__main__":
    main()
