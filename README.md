# BAR Settings Transfer

Move a player's Beyond All Reason settings and keybinds from their home PC to another PC
(LAN event machine) without breaking that machine's screen resolution, display mode or
sound device. Windows and Linux. Nothing to install.

**Download:** grab the zip for your OS from the
[Releases page](https://github.com/PtaQQ/bar-settings-transfer/releases), unzip it anywhere
(Desktop, USB stick), double-click the EXPORT / IMPORT file.

- Windows: `EXPORT BAR settings.bat`, `IMPORT BAR settings.bat`, `RESTORE BAR settings.bat`
  (PowerShell 5.1, ships with Windows 10/11).
- Linux / Steam Deck desktop mode: `EXPORT-BAR-settings.sh`, `IMPORT-BAR-settings.sh`,
  `RESTORE-BAR-settings.sh` (needs `python3`, present on every mainstream distro).
  If a double-click only opens the file in an editor, right-click > Run, or run it from a
  terminal.

A zip exported on Windows imports on Linux and the other way round.

## For players (home PC)

1. Close Beyond All Reason (game and lobby).
2. Double-click `EXPORT BAR settings.bat`.
3. It prints where your file is. The file is called `BAR-settings-<yourname>-<date>.zip`.
   It lands on your Desktop, or next to the `.bat` if you ran it from a USB stick.
4. Bring that one file to the event (USB stick, Discord DM to yourself, whatever).

## For players (event PC)

1. Make sure Beyond All Reason is closed.
2. Put your `BAR-settings-*.zip` on the Desktop, in Downloads, on the USB stick, or next to
   the `.bat` files.
3. Double-click `IMPORT BAR settings.bat`. It finds the zip by itself (asks if there are
   several, opens a file picker if there are none).
4. Start the game. Done.

`RESTORE BAR settings.bat` puts back everything from before the last import, so the next
player at the same seat, or the organizer, can undo it in one click.

## For organizers

- Drop the four files from this folder on every USB stick / on every machine's Desktop.
  They need nothing else.
- The event machine's resolution, fullscreen mode, monitor choice, sound device and
  thread settings are never overwritten. The lobby re-applies the window mode on every
  launch anyway.
- By default the player's graphics quality choices (shadows, water, MSAA, particles,
  preset) are imported too. To keep the event machine's graphics and import only
  controls / UI / sound / keybinds, run the import with the switch:

  ```
  powershell -NoProfile -ExecutionPolicy Bypass -File bar-settings-transfer.ps1 -Mode import -KeepLocalGraphics
  ```

  Or edit `IMPORT BAR settings.bat` and append ` -KeepLocalGraphics` to its powershell line
  before copying it to the sticks.
- Scripted use (no prompts): `-NoPrompt -DataDir <path\to\data> -Bundle <zip>`.
  Linux equivalents: `python3 bar-settings-transfer.py import --keep-local-graphics`,
  `--no-prompt --data-dir <dir> --bundle <zip>`.
- The tool refuses to run while `spring.exe` or the launcher is running. Close them; the
  engine rewrites `springsettings.cfg` on exit and would clobber the import otherwise.
- Every import writes a backup to `data\settings-transfer-backup\<timestamp>\`.
- The tool finds the game at the installer default
  (Windows: `%LOCALAPPDATA%\Programs\Beyond-All-Reason\data`, also via the registry
  uninstall entry; Linux: `~/.local/state/Beyond All Reason`, or `~/Documents/Beyond All
  Reason` on older installs), or next to itself if you drop it inside the install folder.
  Anything else and it asks for the `data` folder (folder picker on Windows, typed path on
  Linux).

## What is in the zip

| File in the zip | What it is | On import |
|---|---|---|
| `springsettings.cfg` | every in-game option (controls, camera, UI, sound, graphics, language, keyboard layout) | merged: machine keys below are skipped, everything else overwrites |
| `uikeys.txt` | the active keybinds the engine loads | copied |
| `LuaUI/Config/keybind_profiles.json` | the keybind editor's profile store (custom profiles, active profile) | copied |
| `LuaUI/Config/BYAR.lua` | every widget's saved state and which widgets are enabled / disabled | copied |
| `LuaUI/Config/blueprints.json` | the player's blueprints | copied |
| `favourite_maps.txt` | lobby favourite maps | copied |
| `lobby_settings.txt` | lobby preferences (language, chat font, menu volume, filters) | merged into `LuaMenu/Config/IGL_data.lua` |
| `manifest.txt` | who exported it, when, from what resolution | shown on import |
| a custom bind file | only when `KeybindingFile` points at a file other than `uikeys.txt` | copied, key kept |

Never exported: lobby login name and password, account id, server address, Steam link,
hardware fingerprint (analytics section), window positions.

Never imported from `springsettings.cfg` (the target PC keeps its own; the full list is
`machineKeys` in `transfer-rules.json`):

- display: `XResolution`, `YResolution`, `XResolutionWindowed`, `YResolutionWindowed`,
  `WindowPosX`, `WindowPosY`, `WindowState`, `Fullscreen`, `WindowBorderless`,
  `MinimizeOnFocusLoss`, `SelectedDisplay`, `SelectedScreenMode`, `DualScreen*`,
  `BlockCompositing`, `DWMFlush`
- hardware: `snd_device`, `SetCoreAffinity`, `WorkerThreadCount`, `PathingThreadCount`,
  `ThreadPinPolicy`, `TextureMemPoolSize`, `GLContext*`, `UseHighResTimer`
- paths, identity, bookkeeping: `SpringData`, `FontFile`, `SmallFontFile`, `name`,
  `address`, `FirstRun`, `ChobbyLaunchesCount`, `OpenSkillSnapshot*`, `Version`, log
  settings, skirmish picks
- `KeybindingFile` is set by the import itself, pointing at the keybind file that came
  with the bundle.

## What an import will not do

A settings zip is treated as untrusted input, since players pass them around:

- Only the files listed above are read out of a zip. Anything else, such as a widget
  `.lua` file or a path with `..` in it, is ignored and reported, never written.
- Config lines must be a plain identifier key with a single-line value; malformed lines
  are dropped.
- Lobby values must be Lua literals (`true`, `false`, a number or a quoted string),
  because the lobby runs its config file as Lua. Anything else is dropped.
- Zips over 64 MB, or files inside them over 32 MB, are refused.
- Text from the zip is echoed with control characters removed.

Widget positions inside `BYAR.lua` are safe across resolutions: BAR widgets store them as
screen fractions or re-scale them from the stored screen size on load.

## Why `FirstRun` is forced to 0

On a fresh install the lobby pushes its own default settings table over
`springsettings.cfg` the first time a battle starts (`FirstRun = 1` check in Chobby's
settings window). Importing onto a never-played install and then starting a game would
lose most of the imported options. The import writes `FirstRun = 0` so the lobby treats
the install as already configured.

## Where the game keeps all of this (BAR master, Recoil 2026.07)

All paths relative to the launcher's `data` folder:

| File | Written by | Holds |
|---|---|---|
| `springsettings.cfg` | engine, on `Spring.SetConfig*` and at exit | every option in the in-game Settings menu, keyboard layout, language, `KeybindingFile`, display mode |
| `uikeys.txt` | `luaui/Include/keybind_profiles.lua` (materialized from the active profile) | engine bind lines |
| `LuaUI/Config/keybind_profiles.json` | same | the keybind editor's profile store |
| `LuaUI/Config/BYAR.lua` | `luaui/barwidgets.lua` (`table.save`) | widget `GetConfigData()` tables + enable order |
| `LuaUI/Config/blueprints.json` | `cmd_blueprint.lua` | blueprints |
| `LuaMenu/Config/IGL_data.lua` | Chobby addon handler | lobby `Configuration` (section `"Chili lobby"`), analytics, mission progress |
| `favourite_maps.txt` | Chobby | favourite maps |
| `chobby_config.json`, `launcher_cfg.json`, `config.json` | launcher | server address and setup; not a player setting |

On Linux the launcher (AppImage) keeps the same layout under `~/.local/state/Beyond All
Reason` (`$XDG_STATE_HOME` honoured; older installs used `~/Documents/Beyond All Reason`).
Verified against a fresh AppImage install under WSL2 / Ubuntu 24.04 with Recoil 2026.07.04.

Not covered: replays, saves, chat logs, cache, launcher config.

## Files in this repo

| File | Role |
|---|---|
| `bar-settings-transfer.ps1` | Windows implementation (PowerShell 5.1) |
| `*.bat` | Windows one-click launchers |
| `bar-settings-transfer.py` | Linux / macOS implementation (Python 3, stdlib only); same zip format |
| `*.sh` | Linux one-click launchers |
| `transfer-rules.json` | what travels and what stays, shared by both implementations; must sit next to the scripts |
| `tests/` | end-to-end tests: both implementations, cross-OS bundles, a hostile bundle (`python -m unittest discover -s tests`) |

To change what travels, edit `transfer-rules.json` only, then run the tests. CI runs them
on Windows (both implementations) and Ubuntu.
| `tools/build_release.py` | builds the two release zips: `python tools/build_release.py v1.1.0 dist` |
