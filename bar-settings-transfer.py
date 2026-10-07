#!/usr/bin/env python3
"""BAR Settings Transfer, Linux / macOS implementation (Python 3.6+, standard library only).

Exports a player's Beyond All Reason settings into one zip and imports such a zip into
another install while leaving that machine's display and hardware settings alone.
The Windows implementation (bar-settings-transfer.ps1) writes and reads the same bundle.

    bar-settings-transfer.py export  [--data-dir DIR] [--out-dir DIR]
    bar-settings-transfer.py import  [--data-dir DIR] [--bundle ZIP] [--keep-local-graphics]
    bar-settings-transfer.py restore [--data-dir DIR]
    bar-settings-transfer.py         (menu)

What travels and what stays is defined in transfer-rules.json next to this file.
"""

import argparse
import datetime
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import zipfile
from collections import OrderedDict

TOOL_VERSION = "1.1"
RULES_FILE = "transfer-rules.json"

MANIFEST_NAME = "manifest.txt"
LOBBY_EXPORT_NAME = "lobby_settings.txt"
BUNDLE_PREFIX = "BAR-settings-"

# A bundle is a handful of text files; anything bigger is not a settings bundle.
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_LINES = 10

# Engine config: "Key = Value" lines. Keys are identifiers; values are single printable lines.
CFG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
CFG_VALUE_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,1024}$")
CFG_LINE_RE = re.compile(r"^\s*([^=\s#;][^=]*?)\s*=\s*(.*?)\s*$")

# Lobby config (table.save output): only identifier keys with literal values may be written,
# because the file is executed as Lua by the lobby.
LOBBY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
LOBBY_VALUE_RE = re.compile(r'^(?:true|false|-?\d{1,15}(?:\.\d{1,15})?|"(?:[^"\\\x00-\x1f]|\\[\\"nrt])*")$')
LOBBY_SCALAR_RE = re.compile(r'^\t\t(?:\["([^"]+)"\]|([A-Za-z_][A-Za-z0-9_]*))\s*=\s*(.*?),\s*$')

# A custom keybind file named by KeybindingFile: a plain file name in the data dir root.
BIND_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.txt$")

PRINTABLE_RE = re.compile(r"[^\x20-\x7e]")


class TransferError(Exception):
    """A problem the user has to fix; reported without a traceback."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class Rules:
    """The shared allow / deny lists, loaded from transfer-rules.json."""

    REQUIRED = ("dataDirMarkers", "gameProcesses", "settingsFile", "lobbyFile", "lobbySection",
                "backupDir", "plainFiles", "machineKeys", "graphicsKeys",
                "lobbyExcludedKeys", "lobbyExcludedPrefixes")

    def __init__(self, raw):
        missing = [k for k in self.REQUIRED if k not in raw]
        if missing:
            raise TransferError("%s is missing: %s" % (RULES_FILE, ", ".join(missing)))
        self.data_dir_markers = list(raw["dataDirMarkers"])
        self.game_processes = set(raw["gameProcesses"])
        self.settings_file = raw["settingsFile"]
        self.lobby_file = raw["lobbyFile"]
        self.lobby_section = raw["lobbySection"]
        self.backup_dir = raw["backupDir"]
        self.plain_files = OrderedDict((f["path"], f["label"]) for f in raw["plainFiles"])
        self.machine_keys = set(raw["machineKeys"])
        self.graphics_keys = set(raw["graphicsKeys"])
        self.lobby_excluded_keys = set(raw["lobbyExcludedKeys"])
        self.lobby_excluded_prefixes = tuple(raw["lobbyExcludedPrefixes"])
        for path in list(self.plain_files) + [self.settings_file, self.lobby_file]:
            if not safe_relative_path(path):
                raise TransferError("%s lists an unsafe path: %s" % (RULES_FILE, path))

    @classmethod
    def load(cls, directory):
        path = os.path.join(directory, RULES_FILE)
        if not os.path.isfile(path):
            raise TransferError("%s is missing next to the script. Unzip the whole download, not just one file." % RULES_FILE)
        with open(path, encoding="utf-8") as f:
            try:
                return cls(json.load(f))
            except ValueError as e:
                raise TransferError("%s is not valid JSON: %s" % (RULES_FILE, e))

    def lobby_key_allowed(self, key):
        return key not in self.lobby_excluded_keys and not key.startswith(self.lobby_excluded_prefixes)

    def bundle_names(self):
        """Every file name a bundle may contain, besides a validated custom keybind file."""
        return set(self.plain_files) | {self.settings_file, MANIFEST_NAME, LOBBY_EXPORT_NAME}


def safe_relative_path(rel):
    """True for a forward-slash relative path that cannot escape its root."""
    if not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if re.match(r"^[A-Za-z]:", rel):
        return False
    return True


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

class Console:
    def __init__(self, no_prompt):
        self.no_prompt = no_prompt

    @staticmethod
    def say(text=""):
        print(text)

    @staticmethod
    def big(text):
        print()
        print("  " + text)
        print()

    def ask(self, question, default):
        if self.no_prompt:
            return default
        try:
            answer = input("  " + question + " ").strip()
        except EOFError:
            return default
        return answer or default

    def pick(self, question, count):
        """1-based choice among `count` items; anything unparsable means the first."""
        try:
            n = int(self.ask(question, "1"))
        except ValueError:
            n = 1
        return n if 1 <= n <= count else 1


def sanitized(text, limit=120):
    """Printable ASCII only, truncated: for echoing text that came from a bundle."""
    text = PRINTABLE_RE.sub("?", text)
    return text if len(text) <= limit else text[:limit] + "..."


# ---------------------------------------------------------------------------
# Engine config file
# ---------------------------------------------------------------------------

class CfgLine:
    __slots__ = ("key", "value", "raw")

    def __init__(self, key, value, raw=None):
        self.key, self.value, self.raw = key, value, raw


def parse_cfg(text):
    lines = []
    for raw in text.splitlines():
        m = CFG_LINE_RE.match(raw)
        if m:
            lines.append(CfgLine(m.group(1), m.group(2)))
        else:
            lines.append(CfgLine(None, None, raw))
    return lines


def render_cfg(lines, newline="\n"):
    out = [(l.key + " = " + l.value) if l.key is not None else l.raw for l in lines]
    return newline.join(out) + newline


def cfg_get(lines, key):
    for l in lines:
        if l.key == key:
            return l.value
    return None


def cfg_set(lines, key, value):
    for l in lines:
        if l.key == key:
            l.value = value
            return
    lines.append(CfgLine(key, value))


def valid_cfg_pair(key, value):
    return bool(CFG_KEY_RE.match(key) and CFG_VALUE_RE.match(value))


# ---------------------------------------------------------------------------
# Lobby config file (Lua table written by table.save)
# ---------------------------------------------------------------------------

def _section_open_re(section):
    return re.compile(r'^\t\["' + re.escape(section) + r'"\]\s*=\s*\{\s*$')


def _walk_section(lines, start):
    """Collect (index, key, value) for the depth-0 scalar lines of the block opened at
    `start`. Returns (found, index of the block's closing line or -1)."""
    depth = 0
    i = start + 1
    found = []
    while i < len(lines):
        line = lines[i]
        if depth == 0 and re.match(r"^\t\},?\s*$", line):
            return found, i
        if depth > 0:
            if re.search(r"\{\s*$", line):
                depth += 1
            elif re.match(r"^\s*\},?\s*$", line):
                depth -= 1
        elif re.search(r"\{\s*$", line):
            depth = 1
        else:
            m = LOBBY_SCALAR_RE.match(line)
            if m:
                found.append((i, m.group(1) or m.group(2), m.group(3)))
        i += 1
    return found, -1


def read_lobby_scalars(text, section):
    lines = text.splitlines()
    opener = _section_open_re(section)
    start = next((i for i, l in enumerate(lines) if opener.match(l)), -1)
    if start < 0:
        return OrderedDict()
    found, _end = _walk_section(lines, start)
    return OrderedDict((k, v) for _i, k, v in found)


def valid_lobby_pair(key, value):
    return bool(LOBBY_KEY_RE.match(key) and LOBBY_VALUE_RE.match(value))


def merge_lobby_scalars(text, section, values):
    """Return (new_text, changed) with `values` merged into the section's scalar lines.
    Creates the file body / the section when absent. Callers validate `values` first."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines() if text.strip() else ["-- Addon Custom Data", "return {", "}"]
    opener = _section_open_re(section)
    start = next((i for i, l in enumerate(lines) if opener.match(l)), -1)
    if start < 0:
        close = next((i for i in range(len(lines) - 1, -1, -1) if re.match(r"^\}\s*$", lines[i])), -1)
        if close < 0:
            raise TransferError("the lobby config has no closing brace; leaving it alone")
        lines[close:close] = ['\t["%s"] = {' % section, "\t},"]
        start = close
    found, end = _walk_section(lines, start)
    if end < 0:
        raise TransferError("the lobby config section never closes; leaving it alone")
    changed = 0
    seen = set()
    for i, key, old in found:
        if key in values:
            seen.add(key)
            if old != values[key]:
                lines[i] = "\t\t%s = %s," % (key, values[key])
                changed += 1
    for key, value in values.items():
        if key not in seen:
            lines.insert(end, "\t\t%s = %s," % (key, value))
            end += 1
            changed += 1
    return newline.join(lines) + newline, changed


# ---------------------------------------------------------------------------
# Bundle (the zip)
# ---------------------------------------------------------------------------

class Bundle:
    """Files of a settings bundle, held in memory: {relative path: bytes}."""

    def __init__(self):
        self.files = OrderedDict()
        self.skipped = []

    def add(self, rel, data):
        self.files[rel] = data

    def add_text(self, rel, lines):
        self.add(rel, ("\n".join(lines) + "\n").encode("utf-8"))

    def text(self, rel):
        return self.files[rel].decode("utf-8", errors="replace")

    def write(self, path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, data in self.files.items():
                z.writestr(rel, data)

    @classmethod
    def read(cls, path, rules):
        """Load only the files a bundle may contain; refuse anything oversized."""
        bundle = cls()
        allowed = rules.bundle_names()
        total = 0
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            custom_bind = cls._custom_bind_name(z, infos, rules.settings_file)
            if custom_bind:
                allowed.add(custom_bind)
            for info in infos:
                rel = info.filename.replace("\\", "/")
                if rel.endswith("/"):
                    continue
                if not safe_relative_path(rel) or rel not in allowed:
                    bundle.skipped.append(rel)
                    continue
                if info.file_size > MAX_ENTRY_BYTES:
                    raise TransferError("'%s' inside the zip is far too large for a settings file" % sanitized(rel))
                total += info.file_size
                if total > MAX_BUNDLE_BYTES:
                    raise TransferError("the zip is far too large for a settings bundle")
                bundle.add(rel, z.read(info))
        if MANIFEST_NAME not in bundle.files:
            raise TransferError("that zip was not made by BAR Settings Transfer (no %s inside)" % MANIFEST_NAME)
        return bundle

    @staticmethod
    def _custom_bind_name(z, infos, settings_file):
        """The custom keybind file the bundle's config points at, if it names a sane file."""
        for info in infos:
            if info.filename.replace("\\", "/") == settings_file and info.file_size <= MAX_ENTRY_BYTES:
                value = cfg_get(parse_cfg(z.read(info).decode("utf-8", errors="replace")), "KeybindingFile")
                if value and value != "uikeys.txt" and BIND_FILE_RE.match(value):
                    return value
        return None


# ---------------------------------------------------------------------------
# Locating things
# ---------------------------------------------------------------------------

def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def game_running(rules):
    """True when the engine or the launcher is running."""
    names = rules.game_processes
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
        out = subprocess.run(["pgrep", "-x", "|".join(sorted(names))], capture_output=True, text=True)
        return out.returncode == 0 and out.stdout.strip() != ""
    except OSError:
        return False


def is_data_dir(path, rules):
    if not path or not os.path.isdir(path):
        return False
    return any(os.path.exists(os.path.join(path, m)) for m in rules.data_dir_markers)


def find_data_dir(given, rules, console):
    if given:
        if is_data_dir(given, rules):
            return os.path.abspath(given)
        raise TransferError("'%s' is not a Beyond All Reason data folder." % given)

    home = os.path.expanduser("~")
    here = script_dir()
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    candidates = [
        # spring-launcher on Linux: $XDG_STATE_HOME/<title>; older installs under ~/Documents/<title>
        os.path.join(state_home, "Beyond All Reason"),
        os.path.join(home, "Documents", "Beyond All Reason"),
        os.path.join(home, ".var", "app", "info.beyondallreason.bar", "data", "Beyond All Reason"),
        # the tool sits inside or next to an install
        os.path.join(here, "data"),
        os.path.join(os.path.dirname(here), "data"),
        here,
        os.path.join(home, "Beyond All Reason"),
        os.path.join(home, "Games", "Beyond-All-Reason", "data"),
    ]
    for c in candidates:
        if is_data_dir(c, rules):
            return os.path.abspath(c)

    if console.no_prompt:
        raise TransferError("Could not find the Beyond All Reason data folder. Pass --data-dir.")
    console.big("I could not find your Beyond All Reason folder automatically.")
    console.say("  It is usually ~/.local/state/Beyond All Reason  (contains springsettings.cfg).")
    typed = os.path.expanduser(console.ask("Type the full path to it:", "").strip().strip("\"'"))
    for candidate in (typed, os.path.join(typed, "data")):
        if is_data_dir(candidate, rules):
            return os.path.abspath(candidate)
    raise TransferError("'%s' does not look like the BAR data folder." % typed)


def desktop_dir():
    home = os.path.expanduser("~")
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


def is_removable(path):
    """Best effort: media mounted under /media, /run/media or /Volumes counts as a stick.
    /mnt is left out on purpose: WSL and many distros mount fixed drives there."""
    return os.path.abspath(path).startswith(("/media/", "/run/media/", "/Volumes/"))


def bundle_candidates():
    """Folders a player is likely to drop a bundle into: next to the tool, Desktop, Downloads,
    home, and the top two levels of the usual removable-media mount points."""
    home = os.path.expanduser("~")
    places = [script_dir(), desktop_dir(), os.path.join(home, "Downloads"), home]
    for mount_root in ("/media", "/run/media", "/mnt"):
        if not os.path.isdir(mount_root):
            continue
        for root, dirs, _files in os.walk(mount_root):
            if root.count(os.sep) - mount_root.count(os.sep) >= 2:
                dirs[:] = []
            places.append(root)
    return places


def find_bundle(given, console):
    if given:
        if os.path.isfile(given):
            return os.path.abspath(given)
        raise TransferError("Settings file not found: " + given)
    found = {}
    for place in bundle_candidates():
        try:
            names = os.listdir(place)
        except OSError:
            continue
        for name in names:
            if name.startswith(BUNDLE_PREFIX) and name.endswith(".zip"):
                full = os.path.join(place, name)
                if os.path.isfile(full):
                    found[full] = os.path.getmtime(full)
    found = sorted(found.items(), key=lambda kv: kv[1], reverse=True)
    if len(found) == 1 or (found and console.no_prompt):
        return found[0][0]
    if found:
        console.say("  Several settings files found:")
        for i, (path, mtime) in enumerate(found):
            when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            console.say("    [%d] %s   (%s)  %s" % (i + 1, os.path.basename(path), when, os.path.dirname(path)))
        return found[console.pick("Which one? (number, Enter = newest)", len(found)) - 1][0]
    if console.no_prompt:
        raise TransferError("No %s*.zip found. Pass --bundle." % BUNDLE_PREFIX)
    console.big("I could not find a %s*.zip next to this tool, on the Desktop, in Downloads or on a USB stick." % BUNDLE_PREFIX)
    typed = os.path.expanduser(console.ask("Type the full path to the zip:", "").strip().strip("\"'"))
    if os.path.isfile(typed):
        return os.path.abspath(typed)
    raise TransferError("No file at " + typed)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def copy_into(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def require_game_closed(rules, opts, what):
    if game_running(rules) and not opts.force:
        raise TransferError("Beyond All Reason is running. Close the game and the lobby, then run %s again." % what)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def export_settings(opts, rules, console):
    data = find_data_dir(opts.data_dir, rules, console)
    console.big("Exporting settings from: " + data)
    require_game_closed(rules, opts, "EXPORT")

    cfg_path = os.path.join(data, rules.settings_file)
    if not os.path.isfile(cfg_path):
        raise TransferError("No %s here. Start the game once, change any setting, quit, then export." % rules.settings_file)

    bundle = Bundle()
    included = []
    cfg = parse_cfg(read_text(cfg_path))
    bundle.add(rules.settings_file, read_bytes(cfg_path))
    included.append(rules.settings_file + "  (game options)")

    key_file = cfg_get(cfg, "KeybindingFile")
    if key_file and key_file != "uikeys.txt" and BIND_FILE_RE.match(key_file):
        custom = os.path.join(data, key_file)
        if os.path.isfile(custom):
            bundle.add(key_file, read_bytes(custom))
            included.append(key_file + "  (custom keybind file)")

    for rel, label in rules.plain_files.items():
        src = os.path.join(data, rel)
        if os.path.isfile(src):
            bundle.add(rel, read_bytes(src))
            included.append("%s  (%s)" % (rel, label))

    lobby_path = os.path.join(data, rules.lobby_file)
    if os.path.isfile(lobby_path):
        scalars = read_lobby_scalars(read_text(lobby_path), rules.lobby_section)
        kept = ["%s = %s" % (k, v) for k, v in scalars.items()
                if rules.lobby_key_allowed(k) and valid_lobby_pair(k, v)]
        if kept:
            bundle.add_text(LOBBY_EXPORT_NAME, kept)
            included.append("%s  (%d lobby preferences, no login details)" % (LOBBY_EXPORT_NAME, len(kept)))

    bundle.add_text(MANIFEST_NAME, [
        "tool = BAR Settings Transfer %s (python)" % TOOL_VERSION,
        "format = 1",
        "exported = " + datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "machine = " + socket.gethostname(),
        "user = " + getpass.getuser(),
        "sourceResolution = %sx%s" % (cfg_get(cfg, "XResolution") or "?", cfg_get(cfg, "YResolution") or "?"),
    ])

    here = script_dir()
    target = opts.out_dir or (here if is_removable(here) else desktop_dir())
    os.makedirs(target, exist_ok=True)
    safe_user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser()) or "player"
    zip_path = os.path.join(target, "%s%s-%s.zip" % (BUNDLE_PREFIX, safe_user, datetime.date.today().isoformat()))
    bundle.write(zip_path)

    console.say("  Included:")
    for line in included:
        console.say("    - " + line)
    console.big("DONE. Your settings file is: " + zip_path)
    console.say("  Copy that one file to a USB stick (or send it to yourself) and run IMPORT on the other PC.")
    return zip_path


def import_settings(opts, rules, console):
    data = find_data_dir(opts.data_dir, rules, console)
    zip_path = find_bundle(opts.bundle, console)
    console.big("Importing %s  into  %s" % (os.path.basename(zip_path), data))
    require_game_closed(rules, opts, "IMPORT")

    try:
        bundle = Bundle.read(zip_path, rules)
    except zipfile.BadZipFile:
        raise TransferError("'%s' is not a zip file." % os.path.basename(zip_path))
    for line in bundle.text(MANIFEST_NAME).splitlines()[:MAX_MANIFEST_LINES]:
        console.say("    " + sanitized(line))
    for rel in bundle.skipped:
        console.say("  ignored '%s' (not a settings file)" % sanitized(rel))

    # Back up every file this import may replace.
    backup = os.path.join(data, rules.backup_dir, stamp())
    targets = [rules.settings_file, rules.lobby_file] + [r for r in bundle.files if r not in (MANIFEST_NAME, LOBBY_EXPORT_NAME)]
    backed_up = 0
    for rel in OrderedDict.fromkeys(targets):
        src = os.path.join(data, rel)
        if os.path.isfile(src):
            copy_into(src, os.path.join(backup, rel))
            backed_up += 1
    console.say("  Backed up %d current file(s) to %s" % (backed_up, backup))

    # 1. Engine config: merge, keeping this machine's own keys.
    cfg_path = os.path.join(data, rules.settings_file)
    local = parse_cfg(read_text(cfg_path)) if os.path.isfile(cfg_path) else []
    incoming = parse_cfg(bundle.text(rules.settings_file)) if rules.settings_file in bundle.files else []
    skip = set(rules.machine_keys)
    if opts.keep_local_graphics:
        skip |= rules.graphics_keys
    applied = skipped = rejected = 0
    for line in incoming:
        if line.key is None:
            continue
        if line.key in skip:
            skipped += 1
        elif not valid_cfg_pair(line.key, line.value):
            rejected += 1
        else:
            cfg_set(local, line.key, line.value)
            applied += 1
    # The lobby pushes its default settings table over the config at the first battle start
    # on a fresh install; mark that as done so the import survives.
    cfg_set(local, "FirstRun", "0")
    bind_file = cfg_get(incoming, "KeybindingFile")
    if bind_file and bind_file != "uikeys.txt" and BIND_FILE_RE.match(bind_file) and bind_file in bundle.files:
        cfg_set(local, "KeybindingFile", bind_file)
    elif "uikeys.txt" in bundle.files:
        cfg_set(local, "KeybindingFile", "uikeys.txt")
    write_bytes(cfg_path, render_cfg(local).encode("utf-8"))
    note = " (graphics kept local)" if opts.keep_local_graphics else ""
    rejected_note = ", %d malformed line(s) dropped" % rejected if rejected else ""
    console.say("  %s: %d settings applied, %d machine-specific ones kept from this PC%s%s"
                % (rules.settings_file, applied, skipped, note, rejected_note))

    # 2. Plain files and the custom keybind file.
    for rel, content in bundle.files.items():
        if rel in (MANIFEST_NAME, LOBBY_EXPORT_NAME, rules.settings_file):
            continue
        write_bytes(os.path.join(data, rel), content)
        console.say("  installed " + rel)

    # 3. Lobby preferences: literal values only, into the one section we own.
    if LOBBY_EXPORT_NAME in bundle.files:
        values = OrderedDict()
        dropped = 0
        for line in bundle.text(LOBBY_EXPORT_NAME).splitlines():
            m = re.match(r"^([^=\s]+) = (.*)$", line)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            if rules.lobby_key_allowed(key) and valid_lobby_pair(key, value):
                values[key] = value
            else:
                dropped += 1
        lobby_path = os.path.join(data, rules.lobby_file)
        try:
            text = read_text(lobby_path) if os.path.isfile(lobby_path) else ""
            merged, changed = merge_lobby_scalars(text, rules.lobby_section, values)
            write_bytes(lobby_path, merged.encode("utf-8"))
            console.say("  lobby preferences: %d value(s) updated%s" % (changed, ", %d dropped" % dropped if dropped else ""))
        except TransferError as e:
            console.say("  lobby preferences skipped: " + str(e))

    console.big("DONE. Start Beyond All Reason; your settings and keybinds are in place.")
    console.say("  Changed your mind? Run RESTORE to put back the %d file(s) from before this import." % backed_up)


def restore_settings(opts, rules, console):
    data = find_data_dir(opts.data_dir, rules, console)
    require_game_closed(rules, opts, "RESTORE")
    root = os.path.join(data, rules.backup_dir)
    backups = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True) if os.path.isdir(root) else []
    if not backups:
        raise TransferError("No backups here (nothing was ever imported into this install).")
    pick = backups[0]
    if len(backups) > 1 and not console.no_prompt:
        console.say("  Backups (newest first):")
        for i, b in enumerate(backups):
            console.say("    [%d] %s" % (i + 1, b))
        pick = backups[console.pick("Which one? (number, Enter = newest)", len(backups)) - 1]
    src_root = os.path.join(root, pick)
    console.big("Restoring files from " + src_root)
    count = 0
    for r, _dirs, files in os.walk(src_root):
        for name in files:
            full = os.path.join(r, name)
            rel = os.path.relpath(full, src_root)
            copy_into(full, os.path.join(data, rel))
            console.say("  restored " + rel)
            count += 1
    console.big("DONE. %d file(s) restored." % count)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

COMMANDS = {"export": export_settings, "import": import_settings, "restore": restore_settings}


def parse_args(argv):
    ap = argparse.ArgumentParser(description="BAR Settings Transfer")
    ap.add_argument("mode", nargs="?", default="menu", choices=sorted(COMMANDS) + ["menu"])
    ap.add_argument("--data-dir", default="", help="the game's data folder (auto-detected when omitted)")
    ap.add_argument("--bundle", default="", help="the settings zip to import (auto-detected when omitted)")
    ap.add_argument("--out-dir", default="", help="where export writes the zip (Desktop by default)")
    ap.add_argument("--keep-local-graphics", action="store_true",
                    help="keep this machine's graphics quality settings; import only controls / UI / sound / keybinds")
    ap.add_argument("--force", action="store_true", help="skip the running-game refusal")
    ap.add_argument("--no-prompt", action="store_true", help="never ask; fail instead")
    return ap.parse_args(argv)


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    console = Console(opts.no_prompt)
    print()
    print("  BAR Settings Transfer " + TOOL_VERSION)
    print("  ---------------------------")
    try:
        rules = Rules.load(script_dir())
        mode = opts.mode
        if mode == "menu":
            console.say("  [1] EXPORT  my settings from this PC into one zip file")
            console.say("  [2] IMPORT  a settings zip into this PC (keeps this PC's screen / hardware settings)")
            console.say("  [3] RESTORE this PC's settings from before the last import")
            mode = ("export", "import", "restore")[console.pick("What do you want to do? (1/2/3)", 3) - 1]
        COMMANDS[mode](opts, rules, console)
    except TransferError as e:
        print()
        print("  PROBLEM: " + str(e))
        print()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
