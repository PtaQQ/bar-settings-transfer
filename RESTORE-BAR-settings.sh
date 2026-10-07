#!/usr/bin/env bash
# Double-click (or run) to restore Beyond All Reason settings. Needs python3 only.
cd "$(dirname "$0")"
python3 ./bar-settings-transfer.py restore
echo
read -r -p "Press Enter to close..." _
