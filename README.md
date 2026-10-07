# BAR Settings Transfer

Move a player's Beyond All Reason settings and keybinds from their home PC to another PC
(LAN event machine) without breaking that machine's screen resolution, display mode or
sound device. Windows only. Nothing to install: one PowerShell script, three double-click
`.bat` files.

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
- The tool refuses to run while `spring.exe` or the launcher is running. Close them; the
  engine rewrites `springsettings.cfg` on exit and would clobber the import otherwise.
- Every import writes a backup to `data\settings-transfer-backup\<timestamp>\`.
- The tool finds the game at the installer default
  (`%LOCALAPPDATA%\Programs\Beyond-All-Reason\data`), via the registry uninstall entry, or
  next to itself if you drop it inside the install folder. Anything else and it opens a
  folder picker asking for the `data` folder.

## What is in the zip

| File in the zip | What it is | On import |
|---|---|---|
| `springsettings.cfg` | every in-game option (controls, camera, UI, sound, graphics, language, keyboard layout) | merged: machine keys below are skipped, everything else overwrites |
| `uikeys.txt` | the active keybinds the engine loads | copied |
| `LuaUI/Config/keybind_profiles.json` | the keybind editor's profile store (custom profiles, active profile) | copied |
| `LuaUI/Config/BYAR.lua` | every widget's saved state and which widgets are enabled / disabled | copied |
| `LuaUI/Config/blueprints.json` | the player's blueprints | copied |
| `favourite_maps.txt` | lobby favourite maps | copied |
| `lobby_settings.txt` | lobby preferences (language, chat font, menu volume, filters, login name) | merged into `LuaMenu/Config/IGL_data.lua` |
| `manifest.txt` | who exported it, when, from what resolution | shown on import |
| a custom bind file | only when `KeybindingFile` points at a file other than `uikeys.txt` | copied, key kept |

Never exported: lobby password, account id, server address, Steam link, hardware
fingerprint (analytics section), window positions.

Never imported from `springsettings.cfg` (the target PC keeps its own):

- display: `XResolution`, `YResolution`, `XResolutionWindowed`, `YResolutionWindowed`,
  `WindowPosX`, `WindowPosY`, `WindowState`, `Fullscreen`, `WindowBorderless`,
  `MinimizeOnFocusLoss`, `SelectedDisplay`, `SelectedScreenMode`, `DualScreen*`,
  `BlockCompositing`, `DWMFlush`
- hardware: `snd_device`, `SetCoreAffinity`, `WorkerThreadCount`, `PathingThreadCount`,
  `ThreadPinPolicy`, `TextureMemPoolSize`, `GLContext*`, `UseHighResTimer`
- paths, identity, bookkeeping: `SpringData`, `name`, `address`, `FirstRun`,
  `ChobbyLaunchesCount`, `OpenSkillSnapshot*`, `Version`, log settings, skirmish picks

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

Not covered: Linux installs (same files, same layout, `~/.local/state/Beyond-All-Reason`
or the install's `data`; the merge logic is identical if someone ports the script),
replays, saves, chat logs, cache.
