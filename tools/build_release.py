"""Build the per-OS release zips from the working copy.

    python tools/build_release.py <version> <out-dir>

Windows zip: bats + ps1 + rules + README. Linux zip: sh + py + rules + README with mode 0755
on the scripts and LF line endings forced.
"""
import os
import sys
import time
import zipfile

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1]
out = sys.argv[2]
os.makedirs(out, exist_ok=True)

windows = [
    "EXPORT BAR settings.bat", "IMPORT BAR settings.bat", "RESTORE BAR settings.bat",
    "BAR Settings Transfer (menu).bat", "bar-settings-transfer.ps1", "transfer-rules.json", "README.md",
]
linux = [
    "EXPORT-BAR-settings.sh", "IMPORT-BAR-settings.sh", "RESTORE-BAR-settings.sh",
    "BAR-Settings-Transfer-menu.sh", "bar-settings-transfer.py", "transfer-rules.json", "README.md",
]


def add(z, name, data, mode):
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    z.writestr(info, data)


def build(zip_name, files, exec_exts, force_lf):
    path = os.path.join(out, zip_name)
    with zipfile.ZipFile(path, "w") as z:
        for f in files:
            data = open(os.path.join(repo, f), "rb").read()
            if force_lf and f.endswith((".sh", ".py", ".md", ".json")):
                data = data.replace(b"\r\n", b"\n")
            mode = 0o755 if f.endswith(exec_exts) else 0o644
            add(z, f, data, mode)
    print(path, os.path.getsize(path), "bytes")
    for i in zipfile.ZipFile(path).infolist():
        print("   %6d  %s  %s" % (i.file_size, oct(i.external_attr >> 16), i.filename))


build("bar-settings-transfer-%s-windows.zip" % version, windows, (".bat", ".ps1"), False)
build("bar-settings-transfer-%s-linux.zip" % version, linux, (".sh", ".py"), True)
