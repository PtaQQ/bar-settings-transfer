#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 ./bar-settings-transfer.py
echo
read -r -p "Press Enter to close..." _
