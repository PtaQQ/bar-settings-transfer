Move your Beyond All Reason settings and keybinds to another PC (LAN event machine) without breaking that machine's screen, display mode or sound device.

## Download

- **Windows:** the `-windows.zip` below.
- **Linux / Steam Deck (desktop mode):** the `-linux.zip` below (needs `python3` 3.7 or newer, already on every mainstream distro).

Unzip the whole thing anywhere (Desktop, USB stick) and keep the files together. Nothing to install.

## Home PC

1. Close Beyond All Reason.
2. Double-click **EXPORT BAR settings** (Windows: `.bat`, Linux: `.sh`).
3. It tells you where your `BAR-settings-<name>-<date>.zip` is. Bring that one file.

## Event PC

1. Close Beyond All Reason.
2. Put your zip on the Desktop, in Downloads, on the USB stick, or next to the tool.
3. Double-click **IMPORT BAR settings**. Start the game.

**RESTORE BAR settings** puts the PC back exactly as it was before the last import.

## Changes in this version

- Restore now also removes files the import added, so a PC really goes back to how it was.
- Linux: the tool now notices when the BAR launcher is still open, and finds the game folder in the same place the launcher does.
- Settings files are written safely: an interrupted import can no longer leave a half-written config.
- Two imports right after each other keep separate backups.
- Lobby settings with very small numbers (for example volumes close to zero) are no longer dropped.
