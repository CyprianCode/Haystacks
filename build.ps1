# Builds the Haystacks installer: dist\Haystacks-Setup-<version>.exe
# Needs the .venv set up from requirements.txt, and Inno Setup 6
# (winget install JRSoftware.InnoSetup). Run from the project folder:
#     powershell -ExecutionPolicy Bypass -File .\build.ps1
# Releases are built the same way by .github\workflows\release.yml.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv here. Set it up first (see README.md, Run from source)." }

$version = (& $py -c "from version import __version__; print(__version__)").Trim()
Write-Host "Building Haystacks $version"

& $py -m pip install --quiet --upgrade pyinstaller
& $py tools\third_party.py
& $py -m PyInstaller --noconfirm --clean haystacks.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
foreach ($candidate in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                       "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                       "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
    if (-not $iscc -and (Test-Path $candidate)) { $iscc = $candidate }
}
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }
& $iscc /Q "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$setup = "dist\Haystacks-Setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  Haystacks-Setup-$version.exe" | Out-File -Encoding ascii "$setup.sha256"
Write-Host "Done: $setup ($([math]::Round((Get-Item $setup).Length / 1MB)) MB)"
