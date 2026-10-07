"""End-to-end tests for both implementations.

Every test runs against the Python tool, and against the PowerShell tool when a
PowerShell is available (powershell.exe on Windows, pwsh elsewhere). Bundles made by
one implementation are imported by the other, so the shared format stays honest.

    python -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_TOOL = os.path.join(ROOT, "bar-settings-transfer.py")
PS_TOOL = os.path.join(ROOT, "bar-settings-transfer.ps1")

HOME_CFG = """\
FirstRun = 0
XResolution = 3840
YResolution = 2160
Fullscreen = 0
snd_device = Home Speakers
name = homeplayer
Shadows = 1
ShadowMapSize = 6051
ui_scale = 0.87
CamMode = 3
KeyboardLayout = azerty
snd_volmaster = 42
KeybindingFile = uikeys.txt
"""

LAN_CFG = """\
FirstRun = 1
XResolution = 1920
YResolution = 1080
Fullscreen = 1
snd_device = LAN Speakers
name = lanpc01
Shadows = 0
ShadowMapSize = 1024
ui_scale = 1
"""

HOME_LOBBY = """\
-- Addon Custom Data
return {
\t["Analytics Handler"] = {
\t\tonetimeEvents = {
\t\t\t["hardware:macAddrHash"] = "123",
\t\t},
\t},
\t["Chili lobby"] = {
\t\tautoLogin = true,
\t\tchatFontSize = 21,
\t\tlanguage = "de",
\t\tmenuMusicVolume = 0,
\t\tpassword = "hunter2",
\t\trememberPassword = true,
\t\tuserName = "HomePlayer",
\t\twindow_WindowPosX = -3840,
\t\tchannels = {
\t\t\tmain = true,
\t\t},
\t},
}
"""

LAN_LOBBY = """\
-- Addon Custom Data
return {
\t["Chili lobby"] = {
\t\tchatFontSize = 11,
\t\tuserName = "LanPc01",
\t\tserverAddress = "server4.beyondallreason.info",
\t},
}
"""


def implementations():
    impls = [("python", [sys.executable, PY_TOOL])]
    for exe in ("powershell", "pwsh"):
        path = shutil.which(exe)
        if path:
            impls.append(("powershell", [path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS_TOOL]))
            break
    return impls


def run_tool(cmd, mode, data_dir, bundle=None, out_dir=None, extra=()):
    py = cmd[1] == PY_TOOL
    args = list(cmd)
    if py:
        args += [mode, "--data-dir", data_dir, "--no-prompt", "--force"]
        if bundle:
            args += ["--bundle", bundle]
        if out_dir:
            args += ["--out-dir", out_dir]
    else:
        args += ["-Mode", mode, "-DataDir", data_dir, "-NoPrompt", "-Force"]
        if bundle:
            args += ["-Bundle", bundle]
        if out_dir:
            args += ["-OutDir", out_dir]
    args += [a if py else {"--keep-local-graphics": "-KeepLocalGraphics"}[a] for a in extra]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + result.stderr


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def cfg_dict(path):
    out = {}
    for line in read(path).splitlines():
        if " = " in line:
            k, v = line.split(" = ", 1)
            out[k.strip()] = v.strip()
    return out


class Fixture:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="bst-test-")
        self.home = os.path.join(self.root, "home")
        self.lan = os.path.join(self.root, "lan")
        self.out = os.path.join(self.root, "out")
        write(os.path.join(self.home, "springsettings.cfg"), HOME_CFG)
        write(os.path.join(self.home, "uikeys.txt"), "unbindall\nbind enter chat\n")
        write(os.path.join(self.home, "LuaUI", "Config", "keybind_profiles.json"), '{"version":2,"profiles":[]}')
        write(os.path.join(self.home, "LuaUI", "Config", "BYAR.lua"), "return { data = {}, order = {} }\n")
        write(os.path.join(self.home, "LuaMenu", "Config", "IGL_data.lua"), HOME_LOBBY)
        write(os.path.join(self.lan, "springsettings.cfg"), LAN_CFG)
        os.makedirs(os.path.join(self.lan, "engine"))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def only_zip(self):
        zips = [f for f in os.listdir(self.out) if f.endswith(".zip")]
        assert len(zips) == 1, zips
        return os.path.join(self.out, zips[0])


class TransferTests(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def export(self, impl):
        code, out = run_tool(impl, "export", self.fx.home, out_dir=self.fx.out)
        self.assertEqual(code, 0, out)
        return self.fx.only_zip()

    def test_export_strips_credentials(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                shutil.rmtree(self.fx.out, ignore_errors=True)
                zpath = self.export(impl)
                with zipfile.ZipFile(zpath) as z:
                    names = {n.replace("\\", "/") for n in z.namelist()}
                    lobby = z.read("lobby_settings.txt").decode()
                self.assertIn("springsettings.cfg", names)
                self.assertIn("uikeys.txt", names)
                self.assertIn("LuaUI/Config/BYAR.lua", names)
                for secret in ("hunter2", "password", "userName", "autoLogin", "window_", "macAddrHash"):
                    self.assertNotIn(secret, lobby)
                self.assertIn('language = "de"', lobby)

    def test_cross_import_keeps_machine_keys(self):
        impls = implementations()
        for exp_name, exp in impls:
            for imp_name, imp in impls:
                with self.subTest(export=exp_name, import_=imp_name):
                    self.fx.cleanup()
                    self.fx = Fixture()
                    zpath = self.export(exp)
                    code, out = run_tool(imp, "import", self.fx.lan, bundle=zpath)
                    self.assertEqual(code, 0, out)
                    cfg = cfg_dict(os.path.join(self.fx.lan, "springsettings.cfg"))
                    self.assertEqual(cfg["XResolution"], "1920")
                    self.assertEqual(cfg["Fullscreen"], "1")
                    self.assertEqual(cfg["snd_device"], "LAN Speakers")
                    self.assertEqual(cfg["name"], "lanpc01")
                    self.assertEqual(cfg["FirstRun"], "0")
                    self.assertEqual(cfg["ui_scale"], "0.87")
                    self.assertEqual(cfg["Shadows"], "1")
                    self.assertEqual(cfg["KeyboardLayout"], "azerty")
                    self.assertEqual(cfg["KeybindingFile"], "uikeys.txt")
                    self.assertTrue(os.path.isfile(os.path.join(self.fx.lan, "uikeys.txt")))
                    lobby = read(os.path.join(self.fx.lan, "LuaMenu", "Config", "IGL_data.lua"))
                    self.assertIn('\t\tlanguage = "de",', lobby)
                    self.assertNotIn("hunter2", lobby)

    def test_keep_local_graphics(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                self.fx.cleanup()
                self.fx = Fixture()
                zpath = self.export(impl)
                code, out = run_tool(impl, "import", self.fx.lan, bundle=zpath, extra=("--keep-local-graphics",))
                self.assertEqual(code, 0, out)
                cfg = cfg_dict(os.path.join(self.fx.lan, "springsettings.cfg"))
                self.assertEqual(cfg["Shadows"], "0")
                self.assertEqual(cfg["ShadowMapSize"], "1024")
                self.assertEqual(cfg["ui_scale"], "0.87")

    def test_merge_into_existing_lobby_file(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                self.fx.cleanup()
                self.fx = Fixture()
                lan_lobby = os.path.join(self.fx.lan, "LuaMenu", "Config", "IGL_data.lua")
                write(lan_lobby, LAN_LOBBY)
                zpath = self.export(impl)
                code, out = run_tool(impl, "import", self.fx.lan, bundle=zpath)
                self.assertEqual(code, 0, out)
                lobby = read(lan_lobby)
                self.assertIn("\t\tchatFontSize = 21,", lobby)
                self.assertIn('\t\tuserName = "LanPc01",', lobby)
                self.assertIn('\t\tserverAddress = "server4.beyondallreason.info",', lobby)
                self.assertEqual(lobby.count('["Chili lobby"]'), 1)

    def test_restore_undoes_import(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                self.fx.cleanup()
                self.fx = Fixture()
                zpath = self.export(impl)
                self.assertEqual(run_tool(impl, "import", self.fx.lan, bundle=zpath)[0], 0)
                code, out = run_tool(impl, "restore", self.fx.lan)
                self.assertEqual(code, 0, out)
                self.assertEqual(cfg_dict(os.path.join(self.fx.lan, "springsettings.cfg"))["FirstRun"], "1")

    def test_hostile_bundle_is_contained(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                self.fx.cleanup()
                self.fx = Fixture()
                evil = os.path.join(self.fx.root, "BAR-settings-evil.zip")
                with zipfile.ZipFile(evil, "w") as z:
                    z.writestr("manifest.txt", "tool = evil\x1b[2J\n")
                    z.writestr("springsettings.cfg",
                               "Shadows = 1\nSpringData = /tmp/evil\nKeybindingFile = ../../evil.txt\n"
                               "bad key = 1\nui_scale = 0.5\x07\n")
                    z.writestr("../escape.txt", "x")
                    z.writestr("LuaUI/Widgets/evil_widget.lua", "os.execute('x')")
                    z.writestr("lobby_settings.txt",
                               'language = os.execute("x")\nchatFontSize = 12\n'
                               'menuMusicVolume = 1 } os.execute("x") --\npassword = "x"\n')
                code, out = run_tool(impl, "import", self.fx.lan, bundle=evil)
                self.assertEqual(code, 0, out)
                self.assertFalse(os.path.exists(os.path.join(self.fx.root, "escape.txt")))
                self.assertFalse(os.path.exists(os.path.join(self.fx.lan, "LuaUI", "Widgets", "evil_widget.lua")))
                cfg = cfg_dict(os.path.join(self.fx.lan, "springsettings.cfg"))
                self.assertNotIn("SpringData", cfg)
                self.assertNotIn("bad key", cfg)
                self.assertEqual(cfg["ui_scale"], "1")
                self.assertNotEqual(cfg.get("KeybindingFile"), "../../evil.txt")
                lobby = read(os.path.join(self.fx.lan, "LuaMenu", "Config", "IGL_data.lua"))
                self.assertNotIn("os.execute", lobby)
                self.assertNotIn("password", lobby)
                self.assertIn("\t\tchatFontSize = 12,", lobby)
                self.assertNotIn("\x1b", out)

    def test_not_a_bundle(self):
        for name, impl in implementations():
            with self.subTest(impl=name):
                junk = os.path.join(self.fx.root, "BAR-settings-junk.zip")
                write(junk, "not a zip")
                code, out = run_tool(impl, "import", self.fx.lan, bundle=junk)
                self.assertEqual(code, 1, out)
                self.assertIn("PROBLEM", out)
                no_manifest = os.path.join(self.fx.root, "BAR-settings-nomanifest.zip")
                with zipfile.ZipFile(no_manifest, "w") as z:
                    z.writestr("springsettings.cfg", "Shadows = 1\n")
                code, out = run_tool(impl, "import", self.fx.lan, bundle=no_manifest)
                self.assertEqual(code, 1, out)
                self.assertIn("manifest", out)


if __name__ == "__main__":
    unittest.main()
