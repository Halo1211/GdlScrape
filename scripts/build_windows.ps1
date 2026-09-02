$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

py -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the build dependencies."
}

py -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath "$ProjectRoot\release" `
    --workpath "$ProjectRoot\build\pyinstaller" `
    "$ProjectRoot\packaging\GdlScrape.spec"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build GdlScrape.exe."
}

$ExecutablePath = Join-Path $ProjectRoot "release\GdlScrape.exe"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "The build completed without producing GdlScrape.exe."
}

Write-Host "Build complete: $ExecutablePath"
