# Creates a "Haystacks" shortcut on the desktop.
# Run once from the folder that holds Haystacks.pyw and .venv:
#     powershell -ExecutionPolicy Bypass -File .\make_shortcut.ps1

$app = Join-Path $PSScriptRoot "Haystacks.pyw"
$pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $app)) { Write-Error "Haystacks.pyw not found next to this script."; exit 1 }
if (-not (Test-Path $pythonw)) { Write-Error "Python venv not found at $pythonw"; exit 1 }

$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "Haystacks.lnk"
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = $pythonw
$s.Arguments = "`"$app`""
$s.WorkingDirectory = $PSScriptRoot
$s.Description = "Transcribe folders of videos and search them"
$s.Save()
Write-Host "Created $link"
