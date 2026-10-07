# BAR Settings Transfer
# Exports a player's Beyond All Reason settings (options, keybinds, widget config,
# lobby preferences, blueprints) into one zip, and imports such a zip into another
# install without touching that machine's display / hardware settings.
#
# Usage (normally via the .bat files next to this script):
#   bar-settings-transfer.ps1 -Mode export  [-DataDir <path>] [-OutDir <path>]
#   bar-settings-transfer.ps1 -Mode import  [-DataDir <path>] [-Bundle <zip>] [-KeepLocalGraphics]
#   bar-settings-transfer.ps1 -Mode restore [-DataDir <path>]
#
# Works with Windows PowerShell 5.1 (ships with Windows 10/11). No other dependencies.

[CmdletBinding()]
param(
	[ValidateSet("export", "import", "restore", "menu")]
	[string]$Mode = "menu",
	[string]$DataDir = "",
	[string]$Bundle = "",
	[string]$OutDir = "",
	# Organizer switch: keep the LAN machine's graphics quality settings (shadows, water,
	# MSAA, particles, preset ...) and import only controls / UI / sound / keybinds.
	[switch]$KeepLocalGraphics,
	# Skip the "is the game running" refusal (not recommended).
	[switch]$Force,
	# No interactive prompts; fail instead of asking.
	[switch]$NoPrompt
)

$ErrorActionPreference = "Stop"
$ToolVersion = "1.0"

# ---------------------------------------------------------------------------
# What travels and what stays
# ---------------------------------------------------------------------------

# springsettings.cfg keys that describe THIS machine (screen, GPU, CPU, paths, identity,
# bookkeeping). Never imported; the LAN machine keeps its own values.
$MachineKeys = @(
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
	"WindowsPausedAtFrame"
)

# Graphics quality keys. Imported by default (they are the player's choice); the organizer
# can keep the LAN machine's own with -KeepLocalGraphics.
$GraphicsKeys = @(
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
	"LimitIdleFps", "MinDrawFPS", "MinSimDrawBalance"
)

# Lobby ("Chili lobby" block of LuaMenu/Config/IGL_data.lua) keys that stay home:
# credentials, server, window geometry, developer switches.
$LobbyExcludedKeys = @(
	"password", "rememberPassword", "autoLogin", "myAccountID", "serverAddress", "serverPort",
	"steamLinkComplete", "suggestedNameFromSteam", "wantAuthenticateWithSteam",
	"firstLoginEver", "firstBattleStarted", "lastStartedBattleID", "gameConfigName",
	"game_fullscreen", "lobby_fullscreen", "agressivelySetBorderlessWindowed",
	"multiplayerDifferentEngine", "useWrongEngine", "multiplayerLaunchNewSpring", "useSpringRestart",
	"doNotSetAnySpringSettings", "debugMode", "enableProfiler", "enableInspector",
	"activeDebugConsole", "campaignSpawnDebug", "editCampaign", "loadLocalWidgets",
	"autoUpdateWidgets", "lobbyIdleSleep", "enableCacheRapidPool", "pluginsInstallDisclaimerAccepted"
)
$LobbyExcludedPrefixes = @("window_")

# Files copied verbatim (relative to the data dir). Missing ones are skipped.
$PlainFiles = @(
	"uikeys.txt",
	"LuaUI/Config/keybind_profiles.json",
	"LuaUI/Config/BYAR.lua",
	"LuaUI/Config/blueprints.json",
	"favourite_maps.txt"
)

$LobbyFile = "LuaMenu/Config/IGL_data.lua"
$LobbySection = "Chili lobby"
$BackupRoot = "settings-transfer-backup"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Big($text, $color = "Cyan") { Write-Host ""; Write-Host ("  " + $text) -ForegroundColor $color; Write-Host "" }

function Fail($text) {
	Write-Host ""
	Write-Host ("  PROBLEM: " + $text) -ForegroundColor Red
	Write-Host ""
	exit 1
}

function Ask($question, $default) {
	if ($NoPrompt) { return $default }
	$answer = Read-Host ("  " + $question)
	if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
	return $answer
}

function Test-GameRunning {
	$names = @("spring", "Beyond-All-Reason", "spring-headless", "spring-dedicated")
	$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.ProcessName }
	return ($null -ne $running -and @($running).Count -gt 0)
}

function Test-DataDir($path) {
	if ([string]::IsNullOrWhiteSpace($path)) { return $false }
	if (-not (Test-Path -LiteralPath $path -PathType Container)) { return $false }
	foreach ($marker in @("springsettings.cfg", "launcher_cfg.json", "engine", "games", "LuaUI")) {
		if (Test-Path -LiteralPath (Join-Path $path $marker)) { return $true }
	}
	return $false
}

function Find-DataDir {
	param([string]$given)
	if ($given) {
		if (Test-DataDir $given) { return (Resolve-Path -LiteralPath $given).Path }
		Fail ("'" + $given + "' is not a Beyond All Reason data folder.")
	}

	$candidates = New-Object System.Collections.ArrayList
	# 1. default installer location
	[void]$candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Beyond-All-Reason\data"))
	# 2. registry (installer writes InstallLocation)
	foreach ($hive in @("HKCU:", "HKLM:")) {
		foreach ($key in @("$hive\Software\Microsoft\Windows\CurrentVersion\Uninstall",
		                   "$hive\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")) {
			if (-not (Test-Path $key)) { continue }
			Get-ChildItem $key -ErrorAction SilentlyContinue | ForEach-Object {
				$p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
				if ($p -and $p.DisplayName -and ($p.DisplayName -like "Beyond*All*Reason*") -and $p.InstallLocation) {
					[void]$candidates.Add((Join-Path $p.InstallLocation "data"))
				}
			}
		}
	}
	# 3. the script lives inside / next to an install
	[void]$candidates.Add((Join-Path $PSScriptRoot "data"))
	[void]$candidates.Add((Join-Path (Split-Path $PSScriptRoot -Parent) "data"))
	[void]$candidates.Add($PSScriptRoot)
	# 4. a few common manual spots
	foreach ($drive in (Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root })) {
		[void]$candidates.Add((Join-Path $drive "Games\Beyond-All-Reason\data"))
		[void]$candidates.Add((Join-Path $drive "Beyond-All-Reason\data"))
		[void]$candidates.Add((Join-Path $drive "BAR\data"))
	}

	foreach ($c in $candidates) {
		if (Test-DataDir $c) { return (Resolve-Path -LiteralPath $c).Path }
	}

	if ($NoPrompt) { Fail "Could not find the Beyond All Reason data folder. Pass -DataDir." }

	Big "I could not find your Beyond All Reason folder automatically." "Yellow"
	Say "  A window will open. Pick the 'data' folder inside your Beyond-All-Reason install"
	Say "  (the one that contains springsettings.cfg)."
	Say ""
	Add-Type -AssemblyName System.Windows.Forms
	$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
	$dlg.Description = "Pick the Beyond-All-Reason 'data' folder (contains springsettings.cfg)"
	$dlg.ShowNewFolderButton = $false
	if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Fail "No folder picked." }
	$picked = $dlg.SelectedPath
	if (Test-DataDir $picked) { return $picked }
	if (Test-DataDir (Join-Path $picked "data")) { return (Join-Path $picked "data") }
	Fail ("'" + $picked + "' does not look like the BAR data folder (no springsettings.cfg / engine / games inside).")
}

# springsettings.cfg <-> ordered list of [key, value]; comments / blank lines kept as-is.
function Read-Cfg($path) {
	$entries = New-Object System.Collections.ArrayList
	if (-not (Test-Path -LiteralPath $path)) { return ,$entries }
	foreach ($line in [System.IO.File]::ReadAllLines($path)) {
		$m = [regex]::Match($line, '^\s*([^=\s#;][^=]*?)\s*=\s*(.*?)\s*$')
		if ($m.Success) {
			[void]$entries.Add(@{ key = $m.Groups[1].Value; value = $m.Groups[2].Value; raw = $null })
		} else {
			[void]$entries.Add(@{ key = $null; value = $null; raw = $line })
		}
	}
	return ,$entries
}

function Write-Cfg($path, $entries) {
	$lines = foreach ($e in $entries) {
		if ($null -ne $e.key) { $e.key + " = " + $e.value } else { $e.raw }
	}
	# Recoil writes CRLF on Windows; match it.
	[System.IO.File]::WriteAllText($path, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
}

function Set-CfgValue($entries, $key, $value) {
	foreach ($e in $entries) {
		if ($e.key -eq $key) { $e.value = $value; return }
	}
	[void]$entries.Add(@{ key = $key; value = $value; raw = $null })
}

# Scalar lines of the "Chili lobby" block in IGL_data.lua (table.save format: one key per line,
# nested tables open with "{" and are skipped).
function Read-LobbyScalars($path) {
	$result = New-Object System.Collections.Specialized.OrderedDictionary
	if (-not (Test-Path -LiteralPath $path)) { return $result }
	$inBlock = $false
	$depth = 0
	foreach ($line in [System.IO.File]::ReadAllLines($path)) {
		if (-not $inBlock) {
			if ($line -match ('^\t\["' + [regex]::Escape($LobbySection) + '"\]\s*=\s*\{\s*$')) { $inBlock = $true; $depth = 0 }
			continue
		}
		if ($depth -eq 0 -and $line -match '^\t\},?\s*$') { break }
		if ($depth -gt 0) {
			if ($line -match '\{\s*$') { $depth++ }
			elseif ($line -match '^\s*\},?\s*$') { $depth-- }
			continue
		}
		if ($line -match '\{\s*$') { $depth = 1; continue }
		$m = [regex]::Match($line, '^\t\t(?:\["([^"]+)"\]|([A-Za-z_][A-Za-z_0-9]*))\s*=\s*(.*?),\s*$')
		if ($m.Success) {
			$k = $m.Groups[1].Value
			if (-not $k) { $k = $m.Groups[2].Value }
			$result[$k] = $m.Groups[3].Value
		}
	}
	return $result
}

function Test-LobbyKeyAllowed($key) {
	if ($LobbyExcludedKeys -contains $key) { return $false }
	foreach ($p in $LobbyExcludedPrefixes) { if ($key.StartsWith($p)) { return $false } }
	return $true
}

function Format-LobbyLine($key, $value) {
	if ($key -match '^[A-Za-z_][A-Za-z_0-9]*$') { return "`t`t" + $key + " = " + $value + "," }
	return "`t`t[`"" + $key + "`"] = " + $value + ","
}

# Merge scalar values into the local IGL_data.lua, creating the file / block when absent.
function Merge-LobbyScalars($path, $values) {
	if ($values.Count -eq 0) { return 0 }
	$lines = New-Object System.Collections.ArrayList
	if (Test-Path -LiteralPath $path) {
		foreach ($l in [System.IO.File]::ReadAllLines($path)) { [void]$lines.Add($l) }
	}
	if ($lines.Count -eq 0) {
		[void]$lines.Add("-- Addon Custom Data")
		[void]$lines.Add("return {")
		[void]$lines.Add("}")
	}

	# locate block
	$start = -1
	for ($i = 0; $i -lt $lines.Count; $i++) {
		if ($lines[$i] -match ('^\t\["' + [regex]::Escape($LobbySection) + '"\]\s*=\s*\{\s*$')) { $start = $i; break }
	}
	if ($start -lt 0) {
		# insert an empty block right before the closing brace of the returned table
		$close = -1
		for ($i = $lines.Count - 1; $i -ge 0; $i--) { if ($lines[$i] -match '^\}\s*$') { $close = $i; break } }
		if ($close -lt 0) { throw "IGL_data.lua has no closing brace; refusing to edit it" }
		$lines.Insert($close, ("`t[`"" + $LobbySection + "`"] = {"))
		$lines.Insert($close + 1, "`t},")
		$start = $close
	}
	# find block end and scalar lines inside it (depth 0 only)
	$end = -1
	$depth = 0
	$seen = @{}
	$changed = 0
	for ($i = $start + 1; $i -lt $lines.Count; $i++) {
		$line = $lines[$i]
		if ($depth -eq 0 -and $line -match '^\t\},?\s*$') { $end = $i; break }
		if ($depth -gt 0) {
			if ($line -match '\{\s*$') { $depth++ } elseif ($line -match '^\s*\},?\s*$') { $depth-- }
			continue
		}
		if ($line -match '\{\s*$') { $depth = 1; continue }
		$m = [regex]::Match($line, '^\t\t(?:\["([^"]+)"\]|([A-Za-z_][A-Za-z_0-9]*))\s*=\s*(.*?),\s*$')
		if ($m.Success) {
			$k = $m.Groups[1].Value
			if (-not $k) { $k = $m.Groups[2].Value }
			if ($values.Contains($k)) {
				$seen[$k] = $true
				if ($m.Groups[3].Value -ne $values[$k]) { $lines[$i] = (Format-LobbyLine $k $values[$k]); $changed++ }
			}
		}
	}
	if ($end -lt 0) { throw "IGL_data.lua lobby block never closes; refusing to edit it" }
	foreach ($k in $values.Keys) {
		if (-not $seen.ContainsKey($k)) { $lines.Insert($end, (Format-LobbyLine $k $values[$k])); $end++; $changed++ }
	}
	$dir = Split-Path $path -Parent
	if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
	[System.IO.File]::WriteAllText($path, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
	return $changed
}

function Copy-Into($src, $dst) {
	$dir = Split-Path $dst -Parent
	if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
	Copy-Item -LiteralPath $src -Destination $dst -Force
}

function Get-Stamp { return (Get-Date).ToString("yyyy-MM-dd_HH-mm-ss") }

function Test-RemovableDrive($path) {
	try {
		$root = [System.IO.Path]::GetPathRoot($path)
		$d = New-Object System.IO.DriveInfo($root)
		return ($d.DriveType -eq [System.IO.DriveType]::Removable)
	} catch { return $false }
}

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

function Do-Export {
	$data = Find-DataDir $DataDir
	Big ("Exporting settings from: " + $data)

	if ((Test-GameRunning) -and -not $Force) {
		Fail "Beyond All Reason is running. Close the game and the lobby, then run this again."
	}

	$cfgPath = Join-Path $data "springsettings.cfg"
	if (-not (Test-Path -LiteralPath $cfgPath)) {
		Fail "No springsettings.cfg here. Start the game once, change any setting, quit, then export."
	}

	$stamp = Get-Stamp
	$work = Join-Path $env:TEMP ("bar-settings-export-" + $stamp)
	New-Item -ItemType Directory -Path $work -Force | Out-Null

	$included = New-Object System.Collections.ArrayList

	# 1. springsettings.cfg: raw copy. Filtering happens at import (the target decides what it keeps).
	Copy-Into $cfgPath (Join-Path $work "springsettings.cfg")
	[void]$included.Add("springsettings.cfg  (game options)")

	# 2. keybinds: uikeys.txt (+ a custom bind file if KeybindingFile points elsewhere)
	$cfg = Read-Cfg $cfgPath
	$keyFile = ($cfg | Where-Object { $_.key -eq "KeybindingFile" } | Select-Object -First 1)
	if ($keyFile -and $keyFile.value -and $keyFile.value -ne "uikeys.txt") {
		$custom = Join-Path $data $keyFile.value
		if ((Test-Path -LiteralPath $custom -PathType Leaf) -and ($custom -like ($data + "*"))) {
			Copy-Into $custom (Join-Path $work $keyFile.value)
			[void]$included.Add($keyFile.value + "  (custom keybind file)")
		}
	}

	# 3. plain files
	foreach ($rel in $PlainFiles) {
		$src = Join-Path $data $rel
		if (Test-Path -LiteralPath $src -PathType Leaf) {
			Copy-Into $src (Join-Path $work $rel)
			$what = switch ($rel) {
				"uikeys.txt" { "active keybinds" }
				"LuaUI/Config/keybind_profiles.json" { "keybind profiles" }
				"LuaUI/Config/BYAR.lua" { "widget settings + enabled widgets" }
				"LuaUI/Config/blueprints.json" { "blueprints" }
				"favourite_maps.txt" { "favourite maps" }
				default { "" }
			}
			[void]$included.Add($rel + "  (" + $what + ")")
		}
	}

	# 4. lobby preferences: scalars only, credentials / geometry stripped at export so the
	#    bundle never carries a password or hardware fingerprint.
	$lobby = Read-LobbyScalars (Join-Path $data $LobbyFile)
	$kept = New-Object System.Collections.ArrayList
	foreach ($k in $lobby.Keys) {
		if (Test-LobbyKeyAllowed $k) { [void]$kept.Add($k + " = " + $lobby[$k]) }
	}
	if ($kept.Count -gt 0) {
		[System.IO.File]::WriteAllText((Join-Path $work "lobby_settings.txt"), (($kept -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
		[void]$included.Add("lobby_settings.txt  (" + $kept.Count + " lobby preferences, no password)")
	}

	# 5. manifest
	$xres = ($cfg | Where-Object { $_.key -eq "XResolution" } | Select-Object -First 1)
	$yres = ($cfg | Where-Object { $_.key -eq "YResolution" } | Select-Object -First 1)
	$manifest = @(
		("tool = BAR Settings Transfer " + $ToolVersion),
		("exported = " + (Get-Date).ToString("s")),
		("machine = " + $env:COMPUTERNAME),
		("user = " + $env:USERNAME),
		("dataDir = " + $data),
		("sourceResolution = " + $(if ($xres) { $xres.value } else { "?" }) + "x" + $(if ($yres) { $yres.value } else { "?" }))
	)
	[System.IO.File]::WriteAllText((Join-Path $work "manifest.txt"), (($manifest -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

	# 6. zip: next to the script when it sits on a USB stick, otherwise on the Desktop.
	$target = $OutDir
	if (-not $target) {
		if (Test-RemovableDrive $PSScriptRoot) { $target = $PSScriptRoot }
		else { $target = [Environment]::GetFolderPath("Desktop") }
	}
	if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
	$safeUser = ($env:USERNAME -replace '[^A-Za-z0-9_-]', '_')
	$zip = Join-Path $target ("BAR-settings-" + $safeUser + "-" + (Get-Date).ToString("yyyy-MM-dd") + ".zip")
	if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
	Compress-Archive -Path (Join-Path $work "*") -DestinationPath $zip -CompressionLevel Optimal
	Remove-Item -LiteralPath $work -Recurse -Force

	Say "  Included:"
	foreach ($i in $included) { Say ("    - " + $i) "DarkGray" }
	Big ("DONE. Your settings file is: " + $zip) "Green"
	Say "  Copy that one file to a USB stick (or send it to yourself) and run IMPORT on the other PC."
	return $zip
}

# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

function Find-Bundle {
	param([string]$given)
	if ($given) {
		if (Test-Path -LiteralPath $given -PathType Leaf) { return (Resolve-Path -LiteralPath $given).Path }
		Fail ("Settings file not found: " + $given)
	}
	$places = @($PSScriptRoot, [Environment]::GetFolderPath("Desktop"), (Join-Path $env:USERPROFILE "Downloads"))
	# every removable drive root too (USB stick with the zip in its root)
	foreach ($d in (Get-PSDrive -PSProvider FileSystem)) {
		if (Test-RemovableDrive $d.Root) { $places += $d.Root }
	}
	$found = @()
	foreach ($p in $places) {
		if (-not (Test-Path -LiteralPath $p)) { continue }
		$found += Get-ChildItem -LiteralPath $p -Filter "BAR-settings-*.zip" -File -ErrorAction SilentlyContinue
	}
	$found = $found | Sort-Object LastWriteTime -Descending | Select-Object -Unique
	if (@($found).Count -eq 1) { return $found[0].FullName }
	if (@($found).Count -gt 1 -and -not $NoPrompt) {
		Say "  Several settings files found:"
		for ($i = 0; $i -lt @($found).Count; $i++) {
			Say ("    [" + ($i + 1) + "] " + $found[$i].Name + "   (" + $found[$i].LastWriteTime.ToString("yyyy-MM-dd HH:mm") + ")  " + $found[$i].DirectoryName) "DarkGray"
		}
		$pick = Ask "Which one? (number, Enter = newest)" "1"
		$n = 0
		if (-not [int]::TryParse($pick, [ref]$n) -or $n -lt 1 -or $n -gt @($found).Count) { $n = 1 }
		return $found[$n - 1].FullName
	}
	if (@($found).Count -gt 1) { return $found[0].FullName }
	if ($NoPrompt) { Fail "No BAR-settings-*.zip found. Pass -Bundle." }

	Big "I could not find a BAR-settings-*.zip next to this tool, on the Desktop, in Downloads or on a USB stick." "Yellow"
	Say "  A window will open: pick the settings zip."
	Add-Type -AssemblyName System.Windows.Forms
	$dlg = New-Object System.Windows.Forms.OpenFileDialog
	$dlg.Title = "Pick the BAR settings zip"
	$dlg.Filter = "BAR settings (BAR-settings-*.zip)|BAR-settings-*.zip|Zip files (*.zip)|*.zip"
	if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Fail "No file picked." }
	return $dlg.FileName
}

function Do-Import {
	$data = Find-DataDir $DataDir
	$zip = Find-Bundle $Bundle
	Big ("Importing " + (Split-Path $zip -Leaf) + "  into  " + $data)

	if ((Test-GameRunning) -and -not $Force) {
		Fail "Beyond All Reason is running. Close the game and the lobby, then run IMPORT again."
	}

	$stamp = Get-Stamp
	$work = Join-Path $env:TEMP ("bar-settings-import-" + $stamp)
	New-Item -ItemType Directory -Path $work -Force | Out-Null
	Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
	if (-not (Test-Path -LiteralPath (Join-Path $work "manifest.txt"))) {
		Remove-Item -LiteralPath $work -Recurse -Force
		Fail "That zip was not made by BAR Settings Transfer (no manifest.txt inside)."
	}
	$manifest = Get-Content -LiteralPath (Join-Path $work "manifest.txt")
	foreach ($l in $manifest) { Say ("    " + $l) "DarkGray" }

	# Backup everything we are about to touch.
	$backup = Join-Path (Join-Path $data $BackupRoot) $stamp
	New-Item -ItemType Directory -Path $backup -Force | Out-Null
	$touch = @("springsettings.cfg", $LobbyFile) + $PlainFiles
	$bundleFiles = Get-ChildItem -LiteralPath $work -Recurse -File | ForEach-Object { $_.FullName.Substring($work.Length + 1).Replace("\", "/") }
	foreach ($rel in $bundleFiles) { if ($rel -like "*.txt" -and $rel -ne "uikeys.txt" -and $rel -ne "favourite_maps.txt") { continue } ; if ($touch -notcontains $rel) { $touch += $rel } }
	$backedUp = 0
	foreach ($rel in $touch) {
		$src = Join-Path $data $rel
		if (Test-Path -LiteralPath $src -PathType Leaf) { Copy-Into $src (Join-Path $backup $rel); $backedUp++ }
	}
	Say ("  Backed up " + $backedUp + " current file(s) to " + $backup) "DarkGray"

	# 1. springsettings.cfg merge
	$cfgPath = Join-Path $data "springsettings.cfg"
	$local = Read-Cfg $cfgPath
	$incoming = Read-Cfg (Join-Path $work "springsettings.cfg")
	$skip = @{}
	foreach ($k in $MachineKeys) { $skip[$k] = $true }
	if ($KeepLocalGraphics) { foreach ($k in $GraphicsKeys) { $skip[$k] = $true } }
	$applied = 0
	$skipped = 0
	foreach ($e in $incoming) {
		if ($null -eq $e.key) { continue }
		if ($skip.ContainsKey($e.key)) { $skipped++; continue }
		Set-CfgValue $local $e.key $e.value
		$applied++
	}
	# The lobby pushes its own default graphics/settings table over springsettings.cfg the
	# first time a battle starts on a fresh install. Mark that as done so the import survives.
	Set-CfgValue $local "FirstRun" "0"
	# Keybinds: point the engine at the file we are about to install.
	$kf = ($incoming | Where-Object { $_.key -eq "KeybindingFile" } | Select-Object -First 1)
	if ($kf -and $kf.value -and (Test-Path -LiteralPath (Join-Path $work $kf.value) -PathType Leaf)) {
		Set-CfgValue $local "KeybindingFile" $kf.value
	} elseif (Test-Path -LiteralPath (Join-Path $work "uikeys.txt") -PathType Leaf) {
		Set-CfgValue $local "KeybindingFile" "uikeys.txt"
	}
	Write-Cfg $cfgPath $local
	Say ("  springsettings.cfg: " + $applied + " settings applied, " + $skipped + " machine-specific ones kept from this PC" + $(if ($KeepLocalGraphics) { " (graphics kept local)" } else { "" }))

	# 2. plain files (+ custom keybind file)
	$copied = 0
	foreach ($rel in $bundleFiles) {
		if ($rel -in @("manifest.txt", "lobby_settings.txt", "springsettings.cfg")) { continue }
		Copy-Into (Join-Path $work $rel) (Join-Path $data $rel)
		Say ("  installed " + $rel) "DarkGray"
		$copied++
	}

	# 3. lobby preferences
	$lobbyTxt = Join-Path $work "lobby_settings.txt"
	if (Test-Path -LiteralPath $lobbyTxt -PathType Leaf) {
		$values = New-Object System.Collections.Specialized.OrderedDictionary
		foreach ($line in [System.IO.File]::ReadAllLines($lobbyTxt)) {
			$m = [regex]::Match($line, '^([^=]+?) = (.*)$')
			if ($m.Success -and (Test-LobbyKeyAllowed $m.Groups[1].Value)) { $values[$m.Groups[1].Value] = $m.Groups[2].Value }
		}
		try {
			$n = Merge-LobbyScalars (Join-Path $data $LobbyFile) $values
			Say ("  lobby preferences: " + $n + " value(s) updated")
		} catch {
			Say ("  lobby preferences skipped: " + $_.Exception.Message) "Yellow"
		}
	}

	Remove-Item -LiteralPath $work -Recurse -Force
	Big "DONE. Start Beyond All Reason; your settings and keybinds are in place." "Green"
	Say ("  Changed your mind? Run RESTORE to put back the " + $backedUp + " file(s) from before this import.")
}

# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

function Do-Restore {
	$data = Find-DataDir $DataDir
	if ((Test-GameRunning) -and -not $Force) { Fail "Beyond All Reason is running. Close it first." }
	$root = Join-Path $data $BackupRoot
	if (-not (Test-Path -LiteralPath $root)) { Fail "No backups here (nothing was ever imported into this install)." }
	$backups = Get-ChildItem -LiteralPath $root -Directory | Sort-Object Name -Descending
	if (@($backups).Count -eq 0) { Fail "No backups here." }
	$pick = $backups[0]
	if (@($backups).Count -gt 1 -and -not $NoPrompt) {
		Say "  Backups (newest first):"
		for ($i = 0; $i -lt @($backups).Count; $i++) { Say ("    [" + ($i + 1) + "] " + $backups[$i].Name) "DarkGray" }
		$a = Ask "Which one? (number, Enter = newest)" "1"
		$n = 0
		if (-not [int]::TryParse($a, [ref]$n) -or $n -lt 1 -or $n -gt @($backups).Count) { $n = 1 }
		$pick = $backups[$n - 1]
	}
	Big ("Restoring files from " + $pick.FullName)
	$files = Get-ChildItem -LiteralPath $pick.FullName -Recurse -File
	foreach ($f in $files) {
		$rel = $f.FullName.Substring($pick.FullName.Length + 1)
		Copy-Into $f.FullName (Join-Path $data $rel)
		Say ("  restored " + $rel) "DarkGray"
	}
	Big ("DONE. " + @($files).Count + " file(s) restored.") "Green"
}

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  BAR Settings Transfer $ToolVersion" -ForegroundColor White
Write-Host "  ---------------------------" -ForegroundColor DarkGray

switch ($Mode) {
	"export"  { Do-Export | Out-Null }
	"import"  { Do-Import }
	"restore" { Do-Restore }
	"menu" {
		Say "  [1] EXPORT  my settings from this PC into one zip file"
		Say "  [2] IMPORT  a settings zip into this PC (keeps this PC's screen / hardware settings)"
		Say "  [3] RESTORE this PC's settings from before the last import"
		$choice = Ask "What do you want to do? (1/2/3)" "1"
		switch ($choice) {
			"1" { Do-Export | Out-Null }
			"2" { Do-Import }
			"3" { Do-Restore }
			default { Fail "Not a valid choice." }
		}
	}
}
