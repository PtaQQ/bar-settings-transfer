# BAR Settings Transfer, Windows implementation (Windows PowerShell 5.1, no dependencies).
#
# Exports a player's Beyond All Reason settings into one zip and imports such a zip into
# another install while leaving that machine's display and hardware settings alone.
# The Linux implementation (bar-settings-transfer.py) writes and reads the same bundle.
#
#   bar-settings-transfer.ps1 -Mode export  [-DataDir <path>] [-OutDir <path>]
#   bar-settings-transfer.ps1 -Mode import  [-DataDir <path>] [-Bundle <zip>] [-KeepLocalGraphics]
#   bar-settings-transfer.ps1 -Mode restore [-DataDir <path>]
#   bar-settings-transfer.ps1               (menu)
#
# What travels and what stays is defined in transfer-rules.json next to this script.

[CmdletBinding()]
param(
	[ValidateSet("export", "import", "restore", "menu")]
	[string]$Mode = "menu",
	[string]$DataDir = "",
	[string]$Bundle = "",
	[string]$OutDir = "",
	# Keep this machine's graphics quality settings; import only controls / UI / sound / keybinds.
	[switch]$KeepLocalGraphics,
	# Skip the running-game refusal.
	[switch]$Force,
	# Never ask; fail instead.
	[switch]$NoPrompt
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$script:ToolVersion = "1.1"
$script:RulesFile = "transfer-rules.json"
$script:ManifestName = "manifest.txt"
$script:LobbyExportName = "lobby_settings.txt"
$script:BundlePrefix = "BAR-settings-"

# A bundle is a handful of text files; anything bigger is not a settings bundle.
$script:MaxEntryBytes = 32MB
$script:MaxBundleBytes = 64MB
$script:MaxManifestLines = 10

# Engine config: "Key = Value" lines. Keys are identifiers; values are single printable lines.
$script:CfgKeyRe = '^[A-Za-z_][A-Za-z0-9_]{0,63}$'
$script:CfgValueRe = '^[^\x00-\x1f\x7f]{0,1024}$'
$script:CfgLineRe = '^\s*([^=\s#;][^=]*?)\s*=\s*(.*?)\s*$'

# Lobby config (table.save output): only identifier keys with literal values may be written,
# because the file is executed as Lua by the lobby.
$script:LobbyKeyRe = '^[A-Za-z_][A-Za-z0-9_]{0,63}$'
$script:LobbyValueRe = '^(?:true|false|-?\d{1,15}(?:\.\d{1,15})?|"(?:[^"\\\x00-\x1f]|\\[\\"nrt])*")$'
$script:LobbyScalarRe = '^\t\t(?:\["([^"]+)"\]|([A-Za-z_][A-Za-z0-9_]*))\s*=\s*(.*?),\s*$'

# A custom keybind file named by KeybindingFile: a plain file name in the data dir root.
$script:BindFileRe = '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.txt$'

$script:Utf8 = New-Object System.Text.UTF8Encoding($false)

class TransferError : System.Exception {
	TransferError([string]$message) : base($message) {}
}

function Test-Re($text, $pattern) { return [regex]::IsMatch([string]$text, $pattern) }

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

function Test-SafeRelativePath($rel) {
	if ([string]::IsNullOrEmpty($rel)) { return $false }
	if ($rel.StartsWith("/") -or $rel.Contains("\") -or $rel.Contains([char]0)) { return $false }
	if (Test-Re $rel '^[A-Za-z]:') { return $false }
	foreach ($part in $rel.Split("/")) {
		if ($part -eq "" -or $part -eq "." -or $part -eq "..") { return $false }
	}
	return $true
}

function Read-Rules($directory) {
	$path = Join-Path $directory $script:RulesFile
	if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
		throw [TransferError]::new("$($script:RulesFile) is missing next to the script. Unzip the whole download, not just one file.")
	}
	try { $raw = [System.IO.File]::ReadAllText($path, $script:Utf8) | ConvertFrom-Json }
	catch { throw [TransferError]::new("$($script:RulesFile) is not valid JSON: $($_.Exception.Message)") }

	$required = @("dataDirMarkers", "gameProcesses", "settingsFile", "lobbyFile", "lobbySection", "backupDir",
	              "plainFiles", "machineKeys", "graphicsKeys", "lobbyExcludedKeys", "lobbyExcludedPrefixes")
	$missing = @($required | Where-Object { -not ($raw.PSObject.Properties.Name -contains $_) })
	if ($missing.Count -gt 0) { throw [TransferError]::new("$($script:RulesFile) is missing: $($missing -join ', ')") }

	$plain = New-Object System.Collections.Specialized.OrderedDictionary
	foreach ($f in $raw.plainFiles) { $plain[[string]$f.path] = [string]$f.label }
	$rules = [pscustomobject]@{
		DataDirMarkers        = @($raw.dataDirMarkers)
		GameProcesses         = New-Object System.Collections.Generic.HashSet[string] (, [string[]]@($raw.gameProcesses))
		SettingsFile          = [string]$raw.settingsFile
		LobbyFile             = [string]$raw.lobbyFile
		LobbySection          = [string]$raw.lobbySection
		BackupDir             = [string]$raw.backupDir
		PlainFiles            = $plain
		MachineKeys           = New-Object System.Collections.Generic.HashSet[string] (, [string[]]@($raw.machineKeys))
		GraphicsKeys          = New-Object System.Collections.Generic.HashSet[string] (, [string[]]@($raw.graphicsKeys))
		LobbyExcludedKeys     = New-Object System.Collections.Generic.HashSet[string] (, [string[]]@($raw.lobbyExcludedKeys))
		LobbyExcludedPrefixes = @($raw.lobbyExcludedPrefixes)
	}
	foreach ($p in @($plain.Keys) + @($rules.SettingsFile, $rules.LobbyFile)) {
		if (-not (Test-SafeRelativePath $p)) { throw [TransferError]::new("$($script:RulesFile) lists an unsafe path: $p") }
	}
	return $rules
}

function Test-LobbyKeyAllowed($rules, $key) {
	if ($rules.LobbyExcludedKeys.Contains($key)) { return $false }
	foreach ($p in $rules.LobbyExcludedPrefixes) { if ($key.StartsWith($p)) { return $false } }
	return $true
}

function Get-BundleNames($rules) {
	# Every file name a bundle may contain, besides a validated custom keybind file.
	$names = New-Object System.Collections.Generic.HashSet[string]
	foreach ($p in $rules.PlainFiles.Keys) { [void]$names.Add($p) }
	[void]$names.Add($rules.SettingsFile)
	[void]$names.Add($script:ManifestName)
	[void]$names.Add($script:LobbyExportName)
	return $names
}

# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Big($text, $color = "Cyan") { Write-Host ""; Write-Host ("  " + $text) -ForegroundColor $color; Write-Host "" }

function Ask($question, $default) {
	if ($NoPrompt) { return $default }
	$answer = Read-Host ("  " + $question)
	if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
	return $answer
}

function Pick($question, $count) {
	# 1-based choice among $count items; anything unparsable means the first.
	$n = 0
	if (-not [int]::TryParse((Ask $question "1"), [ref]$n) -or $n -lt 1 -or $n -gt $count) { $n = 1 }
	return $n
}

function Get-Sanitized($text, $limit = 120) {
	# Printable ASCII only, truncated: for echoing text that came from a bundle.
	$clean = [regex]::Replace([string]$text, '[^\x20-\x7e]', '?')
	if ($clean.Length -le $limit) { return $clean }
	return $clean.Substring(0, $limit) + "..."
}

# ---------------------------------------------------------------------------
# Engine config file
# ---------------------------------------------------------------------------
# A parsed config is a List of @{ key; value; raw }; raw holds comment / blank lines (key = $null).
# Lists are returned with the unary comma so PowerShell does not unroll them.

function ConvertFrom-CfgText($text) {
	$lines = New-Object System.Collections.Generic.List[object]
	foreach ($raw in [string]$text -split "`r?`n") {
		$m = [regex]::Match($raw, $script:CfgLineRe)
		if ($m.Success) { $lines.Add(@{ key = $m.Groups[1].Value; value = $m.Groups[2].Value; raw = $null }) }
		else { $lines.Add(@{ key = $null; value = $null; raw = $raw }) }
	}
	# ReadAllText leaves a trailing empty element after the final newline; drop it.
	if ($lines.Count -gt 0 -and $null -eq $lines[$lines.Count - 1].key -and $lines[$lines.Count - 1].raw -eq "") { $lines.RemoveAt($lines.Count - 1) }
	return ,$lines
}

function ConvertTo-CfgText($lines, $newline = "`r`n") {
	$out = foreach ($l in $lines) { if ($null -ne $l.key) { $l.key + " = " + $l.value } else { $l.raw } }
	return (@($out) -join $newline) + $newline
}

function Get-CfgValue($lines, $key) {
	foreach ($l in $lines) { if ($l.key -ceq $key) { return $l.value } }
	return $null
}

function Set-CfgValue($lines, $key, $value) {
	foreach ($l in $lines) { if ($l.key -ceq $key) { $l.value = $value; return } }
	$lines.Add(@{ key = $key; value = $value; raw = $null })
}

function Test-CfgPair($key, $value) { return (Test-Re $key $script:CfgKeyRe) -and (Test-Re $value $script:CfgValueRe) }

# ---------------------------------------------------------------------------
# Lobby config file (Lua table written by table.save)
# ---------------------------------------------------------------------------

function Get-SectionOpenRe($section) { return '^\t\["' + [regex]::Escape($section) + '"\]\s*=\s*\{\s*$' }

function Find-SectionStart($lines, $section) {
	$re = Get-SectionOpenRe $section
	for ($i = 0; $i -lt $lines.Count; $i++) { if (Test-Re $lines[$i] $re) { return $i } }
	return -1
}

function Walk-Section($lines, $start) {
	# Depth-0 scalar lines of the block opened at $start, plus the index of its closing line (-1 if none).
	$found = New-Object System.Collections.Generic.List[object]
	$depth = 0
	for ($i = $start + 1; $i -lt $lines.Count; $i++) {
		$line = $lines[$i]
		if ($depth -eq 0 -and (Test-Re $line '^\t\},?\s*$')) { return @{ Found = $found; End = $i } }
		if ($depth -gt 0) {
			if (Test-Re $line '\{\s*$') { $depth++ } elseif (Test-Re $line '^\s*\},?\s*$') { $depth-- }
		} elseif (Test-Re $line '\{\s*$') {
			$depth = 1
		} else {
			$m = [regex]::Match($line, $script:LobbyScalarRe)
			if ($m.Success) {
				$k = $m.Groups[1].Value
				if (-not $k) { $k = $m.Groups[2].Value }
				$found.Add(@{ Index = $i; Key = $k; Value = $m.Groups[3].Value })
			}
		}
	}
	return @{ Found = $found; End = -1 }
}

function Read-LobbyScalars($text, $section) {
	$result = New-Object System.Collections.Specialized.OrderedDictionary
	$lines = New-Object System.Collections.Generic.List[string]
	foreach ($l in ([string]$text -split "`r?`n")) { $lines.Add($l) }
	$start = Find-SectionStart $lines $section
	if ($start -lt 0) { return $result }
	foreach ($f in (Walk-Section $lines $start).Found) { $result[$f.Key] = $f.Value }
	return $result
}

function Test-LobbyPair($key, $value) { return (Test-Re $key $script:LobbyKeyRe) -and (Test-Re $value $script:LobbyValueRe) }

function Merge-LobbyScalars($text, $section, $values) {
	# Returns @{ Text; Changed } with $values merged into the section's scalar lines.
	# Creates the file body / the section when absent. Callers validate $values first.
	$newline = if ([string]$text -match "`r`n") { "`r`n" } else { "`n" }
	$lines = New-Object System.Collections.Generic.List[string]
	if (-not [string]::IsNullOrWhiteSpace($text)) {
		foreach ($l in ([string]$text -split "`r?`n")) { $lines.Add($l) }
		if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq "") { $lines.RemoveAt($lines.Count - 1) }
	} else {
		$lines.Add("-- Addon Custom Data"); $lines.Add("return {"); $lines.Add("}")
	}
	$start = Find-SectionStart $lines $section
	if ($start -lt 0) {
		$close = -1
		for ($i = $lines.Count - 1; $i -ge 0; $i--) { if (Test-Re $lines[$i] '^\}\s*$') { $close = $i; break } }
		if ($close -lt 0) { throw [TransferError]::new("the lobby config has no closing brace; leaving it alone") }
		$lines.Insert($close, ("`t[`"" + $section + "`"] = {"))
		$lines.Insert($close + 1, "`t},")
		$start = $close
	}
	$walk = Walk-Section $lines $start
	$end = $walk.End
	if ($end -lt 0) { throw [TransferError]::new("the lobby config section never closes; leaving it alone") }
	$changed = 0
	$seen = New-Object System.Collections.Generic.HashSet[string]
	foreach ($f in $walk.Found) {
		if ($values.Contains($f.Key)) {
			[void]$seen.Add($f.Key)
			if ($f.Value -cne $values[$f.Key]) { $lines[$f.Index] = "`t`t" + $f.Key + " = " + $values[$f.Key] + ","; $changed++ }
		}
	}
	foreach ($k in $values.Keys) {
		if (-not $seen.Contains($k)) { $lines.Insert($end, "`t`t" + $k + " = " + $values[$k] + ","); $end++; $changed++ }
	}
	return @{ Text = (($lines -join $newline) + $newline); Changed = $changed }
}

# ---------------------------------------------------------------------------
# Bundle (the zip)
# ---------------------------------------------------------------------------
# A bundle is @{ Files = OrderedDictionary<rel, byte[]>; Skipped = List<string> }.

function New-Bundle { return @{ Files = (New-Object System.Collections.Specialized.OrderedDictionary); Skipped = (New-Object System.Collections.Generic.List[string]) } }

function Add-BundleText($bundle, $rel, $lines) { $bundle.Files[$rel] = $script:Utf8.GetBytes((@($lines) -join "`n") + "`n") }

function Get-BundleText($bundle, $rel) { return $script:Utf8.GetString($bundle.Files[$rel]) }

function Write-Bundle($bundle, $path) {
	if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
	$zip = [System.IO.Compression.ZipFile]::Open($path, [System.IO.Compression.ZipArchiveMode]::Create)
	try {
		foreach ($rel in $bundle.Files.Keys) {
			$entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
			$stream = $entry.Open()
			try { $bytes = $bundle.Files[$rel]; $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
		}
	} finally { $zip.Dispose() }
}

function Read-ZipEntryBytes($entry) {
	$stream = $entry.Open()
	try {
		$ms = New-Object System.IO.MemoryStream
		$stream.CopyTo($ms)
		return ,$ms.ToArray()
	} finally { $stream.Dispose() }
}

function Get-CustomBindName($zip, $settingsFile) {
	# The custom keybind file the bundle's config points at, if it names a sane file.
	foreach ($entry in $zip.Entries) {
		if ($entry.FullName.Replace("\", "/") -eq $settingsFile -and $entry.Length -le $script:MaxEntryBytes) {
			$cfg = ConvertFrom-CfgText ($script:Utf8.GetString((Read-ZipEntryBytes $entry)))
			$value = Get-CfgValue $cfg "KeybindingFile"
			if ($value -and $value -ne "uikeys.txt" -and (Test-Re $value $script:BindFileRe)) { return $value }
		}
	}
	return $null
}

function Read-Bundle($path, $rules) {
	# Load only the files a bundle may contain; refuse anything oversized.
	$bundle = New-Bundle
	$allowed = Get-BundleNames $rules
	$total = 0
	try { $zip = [System.IO.Compression.ZipFile]::OpenRead($path) }
	catch { throw [TransferError]::new("'" + (Split-Path $path -Leaf) + "' is not a zip file.") }
	try {
		$custom = Get-CustomBindName $zip $rules.SettingsFile
		if ($custom) { [void]$allowed.Add($custom) }
		foreach ($entry in $zip.Entries) {
			$rel = $entry.FullName.Replace("\", "/")
			if ($rel.EndsWith("/")) { continue }
			if (-not (Test-SafeRelativePath $rel) -or -not $allowed.Contains($rel)) { $bundle.Skipped.Add($rel); continue }
			if ($entry.Length -gt $script:MaxEntryBytes) { throw [TransferError]::new("'" + (Get-Sanitized $rel) + "' inside the zip is far too large for a settings file") }
			$total += $entry.Length
			if ($total -gt $script:MaxBundleBytes) { throw [TransferError]::new("the zip is far too large for a settings bundle") }
			$bundle.Files[$rel] = Read-ZipEntryBytes $entry
		}
	} finally { $zip.Dispose() }
	if (-not $bundle.Files.Contains($script:ManifestName)) {
		throw [TransferError]::new("that zip was not made by BAR Settings Transfer (no $($script:ManifestName) inside)")
	}
	return $bundle
}

# ---------------------------------------------------------------------------
# Locating things
# ---------------------------------------------------------------------------

function Test-GameRunning($rules) {
	$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $rules.GameProcesses.Contains($_.ProcessName) }
	return (@($running).Count -gt 0)
}

function Test-DataDir($path, $rules) {
	if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path -PathType Container)) { return $false }
	foreach ($marker in $rules.DataDirMarkers) { if (Test-Path -LiteralPath (Join-Path $path $marker)) { return $true } }
	return $false
}

function Get-InstallLocationsFromRegistry {
	$found = @()
	foreach ($hive in @("HKCU:", "HKLM:")) {
		foreach ($key in @("$hive\Software\Microsoft\Windows\CurrentVersion\Uninstall",
		                   "$hive\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")) {
			if (-not (Test-Path $key)) { continue }
			foreach ($item in (Get-ChildItem $key -ErrorAction SilentlyContinue)) {
				$p = Get-ItemProperty $item.PSPath -ErrorAction SilentlyContinue
				if ($p -and ($p.PSObject.Properties.Name -contains "DisplayName") -and ($p.DisplayName -like "Beyond*All*Reason*") `
				    -and ($p.PSObject.Properties.Name -contains "InstallLocation") -and $p.InstallLocation) {
					$found += (Join-Path $p.InstallLocation "data")
				}
			}
		}
	}
	return $found
}

function Find-DataDir($given, $rules) {
	if ($given) {
		if (Test-DataDir $given $rules) { return (Resolve-Path -LiteralPath $given).Path }
		throw [TransferError]::new("'$given' is not a Beyond All Reason data folder.")
	}
	$candidates = @((Join-Path $env:LOCALAPPDATA "Programs\Beyond-All-Reason\data"))
	$candidates += Get-InstallLocationsFromRegistry
	$candidates += @((Join-Path $PSScriptRoot "data"), (Join-Path (Split-Path $PSScriptRoot -Parent) "data"), $PSScriptRoot)
	foreach ($drive in (Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root })) {
		$candidates += @((Join-Path $drive "Games\Beyond-All-Reason\data"), (Join-Path $drive "Beyond-All-Reason\data"), (Join-Path $drive "BAR\data"))
	}
	foreach ($c in $candidates) { if (Test-DataDir $c $rules) { return (Resolve-Path -LiteralPath $c).Path } }

	if ($NoPrompt) { throw [TransferError]::new("Could not find the Beyond All Reason data folder. Pass -DataDir.") }
	Big "I could not find your Beyond All Reason folder automatically." "Yellow"
	Say "  A window will open. Pick the 'data' folder inside your Beyond-All-Reason install"
	Say "  (the one that contains springsettings.cfg)."
	Add-Type -AssemblyName System.Windows.Forms
	$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
	$dlg.Description = "Pick the Beyond-All-Reason 'data' folder (contains springsettings.cfg)"
	$dlg.ShowNewFolderButton = $false
	if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw [TransferError]::new("No folder picked.") }
	foreach ($candidate in @($dlg.SelectedPath, (Join-Path $dlg.SelectedPath "data"))) {
		if (Test-DataDir $candidate $rules) { return $candidate }
	}
	throw [TransferError]::new("'$($dlg.SelectedPath)' does not look like the BAR data folder (no springsettings.cfg / engine / games inside).")
}

function Test-RemovableDrive($path) {
	try {
		$drive = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($path))
		return ($drive.DriveType -eq [System.IO.DriveType]::Removable)
	} catch { return $false }
}

function Find-Bundle($given) {
	if ($given) {
		if (Test-Path -LiteralPath $given -PathType Leaf) { return (Resolve-Path -LiteralPath $given).Path }
		throw [TransferError]::new("Settings file not found: $given")
	}
	$places = @($PSScriptRoot, [Environment]::GetFolderPath("Desktop"), (Join-Path $env:USERPROFILE "Downloads"))
	foreach ($d in (Get-PSDrive -PSProvider FileSystem)) { if (Test-RemovableDrive $d.Root) { $places += $d.Root } }
	$found = @()
	foreach ($p in $places) {
		if (Test-Path -LiteralPath $p) { $found += @(Get-ChildItem -LiteralPath $p -Filter ($script:BundlePrefix + "*.zip") -File -ErrorAction SilentlyContinue) }
	}
	$found = @($found | Sort-Object LastWriteTime -Descending | Select-Object -Unique)
	if ($found.Count -eq 1 -or ($found.Count -gt 1 -and $NoPrompt)) { return $found[0].FullName }
	if ($found.Count -gt 1) {
		Say "  Several settings files found:"
		for ($i = 0; $i -lt $found.Count; $i++) {
			Say ("    [" + ($i + 1) + "] " + $found[$i].Name + "   (" + $found[$i].LastWriteTime.ToString("yyyy-MM-dd HH:mm") + ")  " + $found[$i].DirectoryName) "DarkGray"
		}
		return $found[(Pick "Which one? (number, Enter = newest)" $found.Count) - 1].FullName
	}
	if ($NoPrompt) { throw [TransferError]::new("No $($script:BundlePrefix)*.zip found. Pass -Bundle.") }
	Big "I could not find a $($script:BundlePrefix)*.zip next to this tool, on the Desktop, in Downloads or on a USB stick." "Yellow"
	Say "  A window will open: pick the settings zip."
	Add-Type -AssemblyName System.Windows.Forms
	$dlg = New-Object System.Windows.Forms.OpenFileDialog
	$dlg.Title = "Pick the BAR settings zip"
	$dlg.Filter = "BAR settings ($($script:BundlePrefix)*.zip)|$($script:BundlePrefix)*.zip|Zip files (*.zip)|*.zip"
	if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw [TransferError]::new("No file picked.") }
	return $dlg.FileName
}

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

function Read-TextFile($path) { return [System.IO.File]::ReadAllText($path) }

function Write-BytesFile($path, [byte[]]$bytes) {
	$dir = Split-Path $path -Parent
	if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
	[System.IO.File]::WriteAllBytes($path, $bytes)
}

function Copy-Into($src, $dst) {
	$dir = Split-Path $dst -Parent
	if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
	Copy-Item -LiteralPath $src -Destination $dst -Force
}

function Get-Stamp { return (Get-Date).ToString("yyyy-MM-dd_HH-mm-ss") }

function Assert-GameClosed($rules, $what) {
	if ((Test-GameRunning $rules) -and -not $Force) {
		throw [TransferError]::new("Beyond All Reason is running. Close the game and the lobby, then run $what again.")
	}
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

function Export-Settings($rules) {
	$data = Find-DataDir $DataDir $rules
	Big ("Exporting settings from: " + $data)
	Assert-GameClosed $rules "EXPORT"

	$cfgPath = Join-Path $data $rules.SettingsFile
	if (-not (Test-Path -LiteralPath $cfgPath -PathType Leaf)) {
		throw [TransferError]::new("No $($rules.SettingsFile) here. Start the game once, change any setting, quit, then export.")
	}

	$bundle = New-Bundle
	$included = New-Object System.Collections.Generic.List[string]
	$cfg = ConvertFrom-CfgText (Read-TextFile $cfgPath)
	$bundle.Files[$rules.SettingsFile] = [System.IO.File]::ReadAllBytes($cfgPath)
	$included.Add($rules.SettingsFile + "  (game options)")

	$keyFile = Get-CfgValue $cfg "KeybindingFile"
	if ($keyFile -and $keyFile -ne "uikeys.txt" -and (Test-Re $keyFile $script:BindFileRe)) {
		$custom = Join-Path $data $keyFile
		if (Test-Path -LiteralPath $custom -PathType Leaf) {
			$bundle.Files[$keyFile] = [System.IO.File]::ReadAllBytes($custom)
			$included.Add($keyFile + "  (custom keybind file)")
		}
	}

	foreach ($rel in $rules.PlainFiles.Keys) {
		$src = Join-Path $data $rel
		if (Test-Path -LiteralPath $src -PathType Leaf) {
			$bundle.Files[$rel] = [System.IO.File]::ReadAllBytes($src)
			$included.Add($rel + "  (" + $rules.PlainFiles[$rel] + ")")
		}
	}

	$lobbyPath = Join-Path $data $rules.LobbyFile
	if (Test-Path -LiteralPath $lobbyPath -PathType Leaf) {
		$scalars = Read-LobbyScalars (Read-TextFile $lobbyPath) $rules.LobbySection
		$kept = New-Object System.Collections.Generic.List[string]
		foreach ($k in $scalars.Keys) {
			if ((Test-LobbyKeyAllowed $rules $k) -and (Test-LobbyPair $k $scalars[$k])) { $kept.Add($k + " = " + $scalars[$k]) }
		}
		if ($kept.Count -gt 0) {
			Add-BundleText $bundle $script:LobbyExportName $kept
			$included.Add($script:LobbyExportName + "  (" + $kept.Count + " lobby preferences, no login details)")
		}
	}

	$xres = Get-CfgValue $cfg "XResolution"; if (-not $xres) { $xres = "?" }
	$yres = Get-CfgValue $cfg "YResolution"; if (-not $yres) { $yres = "?" }
	Add-BundleText $bundle $script:ManifestName @(
		("tool = BAR Settings Transfer " + $script:ToolVersion + " (powershell)"),
		"format = 1",
		("exported = " + (Get-Date).ToString("s")),
		("machine = " + $env:COMPUTERNAME),
		("user = " + $env:USERNAME),
		("sourceResolution = " + $xres + "x" + $yres)
	)

	$target = $OutDir
	if (-not $target) {
		if (Test-RemovableDrive $PSScriptRoot) { $target = $PSScriptRoot } else { $target = [Environment]::GetFolderPath("Desktop") }
	}
	if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
	$safeUser = ($env:USERNAME -replace '[^A-Za-z0-9_-]', '_')
	if (-not $safeUser) { $safeUser = "player" }
	$zipPath = Join-Path $target ($script:BundlePrefix + $safeUser + "-" + (Get-Date).ToString("yyyy-MM-dd") + ".zip")
	Write-Bundle $bundle $zipPath

	Say "  Included:"
	foreach ($i in $included) { Say ("    - " + $i) "DarkGray" }
	Big ("DONE. Your settings file is: " + $zipPath) "Green"
	Say "  Copy that one file to a USB stick (or send it to yourself) and run IMPORT on the other PC."
}

function Import-Settings($rules) {
	$data = Find-DataDir $DataDir $rules
	$zipPath = Find-Bundle $Bundle
	Big ("Importing " + (Split-Path $zipPath -Leaf) + "  into  " + $data)
	Assert-GameClosed $rules "IMPORT"

	$bundle = Read-Bundle $zipPath $rules
	$manifestLines = @((Get-BundleText $bundle $script:ManifestName) -split "`r?`n" | Where-Object { $_ -ne "" })
	foreach ($l in ($manifestLines | Select-Object -First $script:MaxManifestLines)) { Say ("    " + (Get-Sanitized $l)) "DarkGray" }
	foreach ($rel in $bundle.Skipped) { Say ("  ignored '" + (Get-Sanitized $rel) + "' (not a settings file)") "Yellow" }

	# Back up every file this import may replace.
	$backup = Join-Path (Join-Path $data $rules.BackupDir) (Get-Stamp)
	$targets = New-Object System.Collections.Generic.List[string]
	$targets.Add($rules.SettingsFile); $targets.Add($rules.LobbyFile)
	foreach ($rel in $bundle.Files.Keys) { if ($rel -notin @($script:ManifestName, $script:LobbyExportName) -and -not $targets.Contains($rel)) { $targets.Add($rel) } }
	$backedUp = 0
	foreach ($rel in $targets) {
		$src = Join-Path $data $rel
		if (Test-Path -LiteralPath $src -PathType Leaf) { Copy-Into $src (Join-Path $backup $rel); $backedUp++ }
	}
	Say ("  Backed up " + $backedUp + " current file(s) to " + $backup) "DarkGray"

	# 1. Engine config: merge, keeping this machine's own keys.
	$cfgPath = Join-Path $data $rules.SettingsFile
	$local = if (Test-Path -LiteralPath $cfgPath -PathType Leaf) { ConvertFrom-CfgText (Read-TextFile $cfgPath) } else { New-Object System.Collections.Generic.List[object] }
	$incoming = if ($bundle.Files.Contains($rules.SettingsFile)) { ConvertFrom-CfgText (Get-BundleText $bundle $rules.SettingsFile) } else { New-Object System.Collections.Generic.List[object] }
	$skip = New-Object System.Collections.Generic.HashSet[string] ($rules.MachineKeys)
	if ($KeepLocalGraphics) { $skip.UnionWith($rules.GraphicsKeys) }
	$applied = 0; $skipped = 0; $rejected = 0
	foreach ($l in $incoming) {
		if ($null -eq $l.key) { continue }
		if ($skip.Contains($l.key)) { $skipped++ }
		elseif (-not (Test-CfgPair $l.key $l.value)) { $rejected++ }
		else { Set-CfgValue $local $l.key $l.value; $applied++ }
	}
	# The lobby pushes its default settings table over the config at the first battle start
	# on a fresh install; mark that as done so the import survives.
	Set-CfgValue $local "FirstRun" "0"
	$bindFile = Get-CfgValue $incoming "KeybindingFile"
	if ($bindFile -and $bindFile -ne "uikeys.txt" -and (Test-Re $bindFile $script:BindFileRe) -and $bundle.Files.Contains($bindFile)) {
		Set-CfgValue $local "KeybindingFile" $bindFile
	} elseif ($bundle.Files.Contains("uikeys.txt")) {
		Set-CfgValue $local "KeybindingFile" "uikeys.txt"
	}
	Write-BytesFile $cfgPath $script:Utf8.GetBytes((ConvertTo-CfgText $local))
	$note = if ($KeepLocalGraphics) { " (graphics kept local)" } else { "" }
	$rejectedNote = if ($rejected) { ", $rejected malformed line(s) dropped" } else { "" }
	Say ("  " + $rules.SettingsFile + ": " + $applied + " settings applied, " + $skipped + " machine-specific ones kept from this PC" + $note + $rejectedNote)

	# 2. Plain files and the custom keybind file.
	foreach ($rel in $bundle.Files.Keys) {
		if ($rel -in @($script:ManifestName, $script:LobbyExportName, $rules.SettingsFile)) { continue }
		Write-BytesFile (Join-Path $data $rel) $bundle.Files[$rel]
		Say ("  installed " + $rel) "DarkGray"
	}

	# 3. Lobby preferences: literal values only, into the one section we own.
	if ($bundle.Files.Contains($script:LobbyExportName)) {
		$values = New-Object System.Collections.Specialized.OrderedDictionary
		$dropped = 0
		foreach ($line in ((Get-BundleText $bundle $script:LobbyExportName) -split "`r?`n")) {
			$m = [regex]::Match($line, '^([^=\s]+) = (.*)$')
			if (-not $m.Success) { continue }
			$k = $m.Groups[1].Value; $v = $m.Groups[2].Value
			if ((Test-LobbyKeyAllowed $rules $k) -and (Test-LobbyPair $k $v)) { $values[$k] = $v } else { $dropped++ }
		}
		$lobbyPath = Join-Path $data $rules.LobbyFile
		try {
			$text = if (Test-Path -LiteralPath $lobbyPath -PathType Leaf) { Read-TextFile $lobbyPath } else { "" }
			$merged = Merge-LobbyScalars $text $rules.LobbySection $values
			Write-BytesFile $lobbyPath $script:Utf8.GetBytes($merged.Text)
			$droppedNote = if ($dropped) { ", $dropped dropped" } else { "" }
			Say ("  lobby preferences: " + $merged.Changed + " value(s) updated" + $droppedNote)
		} catch [TransferError] {
			Say ("  lobby preferences skipped: " + $_.Exception.Message) "Yellow"
		}
	}

	Big "DONE. Start Beyond All Reason; your settings and keybinds are in place." "Green"
	Say ("  Changed your mind? Run RESTORE to put back the " + $backedUp + " file(s) from before this import.")
}

function Restore-Settings($rules) {
	$data = Find-DataDir $DataDir $rules
	Assert-GameClosed $rules "RESTORE"
	$root = Join-Path $data $rules.BackupDir
	$backups = @()
	if (Test-Path -LiteralPath $root) { $backups = @(Get-ChildItem -LiteralPath $root -Directory | Sort-Object Name -Descending) }
	if ($backups.Count -eq 0) { throw [TransferError]::new("No backups here (nothing was ever imported into this install).") }
	$pick = $backups[0]
	if ($backups.Count -gt 1 -and -not $NoPrompt) {
		Say "  Backups (newest first):"
		for ($i = 0; $i -lt $backups.Count; $i++) { Say ("    [" + ($i + 1) + "] " + $backups[$i].Name) "DarkGray" }
		$pick = $backups[(Pick "Which one? (number, Enter = newest)" $backups.Count) - 1]
	}
	Big ("Restoring files from " + $pick.FullName)
	$files = @(Get-ChildItem -LiteralPath $pick.FullName -Recurse -File)
	foreach ($f in $files) {
		$rel = $f.FullName.Substring($pick.FullName.Length + 1)
		Copy-Into $f.FullName (Join-Path $data $rel)
		Say ("  restored " + $rel) "DarkGray"
	}
	Big ("DONE. " + $files.Count + " file(s) restored.") "Green"
}

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  BAR Settings Transfer $($script:ToolVersion)" -ForegroundColor White
Write-Host "  ---------------------------" -ForegroundColor DarkGray

try {
	$rules = Read-Rules $PSScriptRoot
	$chosen = $Mode
	if ($chosen -eq "menu") {
		Say "  [1] EXPORT  my settings from this PC into one zip file"
		Say "  [2] IMPORT  a settings zip into this PC (keeps this PC's screen / hardware settings)"
		Say "  [3] RESTORE this PC's settings from before the last import"
		$chosen = @("export", "import", "restore")[(Pick "What do you want to do? (1/2/3)" 3) - 1]
	}
	switch ($chosen) {
		"export"  { Export-Settings $rules }
		"import"  { Import-Settings $rules }
		"restore" { Restore-Settings $rules }
	}
	exit 0
} catch [TransferError] {
	Write-Host ""
	Write-Host ("  PROBLEM: " + $_.Exception.Message) -ForegroundColor Red
	Write-Host ""
	exit 1
}
