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
    --onefile `
    --windowed `
    --name "GdlScrape" `
    --icon "$ProjectRoot\packaging\gdlscrape.ico" `
    --version-file "$ProjectRoot\packaging\version_info.txt" `
    --add-data "$ProjectRoot\gallery_dl_app\assets;gallery_dl_app\assets" `
    --distpath "$ProjectRoot\release" `
    --workpath "$ProjectRoot\build\pyinstaller" `
    --specpath "$ProjectRoot\build" `
    "$ProjectRoot\gdlscrape.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build GdlScrape.exe."
}

Write-Host "Build complete: $ProjectRoot\release\GdlScrape.exe"
