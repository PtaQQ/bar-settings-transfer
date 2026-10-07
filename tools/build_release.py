"""Build the per-OS release zips.

    python tools/build_release.py <version> <out-dir>

Windows zip: bats + ps1 + rules + README, CRLF line endings.
Linux zip: sh + py + rules + README, LF line endings, mode 0755 on the scripts.

Output is reproducible: entry order, line endings, permissions and timestamps are fixed.
Timestamps come from SOURCE_DATE_EPOCH, or else the last commit's time.
"""
import os
import subprocess
import sys
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WINDOWS = [
    "EXPORT BAR settings.bat", "IMPORT BAR settings.bat", "RESTORE BAR settings.bat",
    "BAR Settings Transfer (menu).bat", "bar-settings-transfer.ps1", "transfer-rules.json", "README.md",
]
LINUX = [
    "EXPORT-BAR-settings.sh", "IMPORT-BAR-settings.sh", "RESTORE-BAR-settings.sh",
    "BAR-Settings-Transfer-menu.sh", "bar-settings-transfer.py", "transfer-rules.json", "README.md",
]


def source_date():
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        epoch = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=REPO,
                               capture_output=True, text=True, check=True).stdout.strip()
    return time.gmtime(int(epoch))[:6]


def build(path, files, newline, exec_exts, date_time):
    with zipfile.ZipFile(path, "w") as z:
        for name in files:
            with open(os.path.join(REPO, name), "rb") as f:
                data = f.read().replace(b"\r\n", b"\n")
            if newline == b"\r\n" and name.endswith((".bat", ".ps1")):
                data = data.replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # unix, so the mode below is honoured by unzip
            info.external_attr = ((0o100755 if name.endswith(exec_exts) else 0o100644) & 0xFFFF) << 16
            z.writestr(info, data)
    print(path, os.path.getsize(path), "bytes")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    version, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    date_time = source_date()
    build(os.path.join(out, "bar-settings-transfer-%s-windows.zip" % version), WINDOWS, b"\r\n", (".bat", ".ps1"), date_time)
    build(os.path.join(out, "bar-settings-transfer-%s-linux.zip" % version), LINUX, b"\n", (".sh", ".py"), date_time)


if __name__ == "__main__":
    main()
