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
    --onedir `
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

$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "release\GdlScrape"))
$InternalRoot = [IO.Path]::GetFullPath((Join-Path $ReleaseRoot "_internal"))
$ReleasePrefix = $ReleaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

if (-not $InternalRoot.StartsWith($ReleasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean runtime files outside the release directory."
}

# A Poppler installation on PATH may cause PyInstaller to collect incompatible
# ICU DLLs. Qt uses the Windows system ICU API, so those bundled copies make
# PySide6.QtGui fail at startup with WinError 127.
$BundledIcuFiles = Get-ChildItem -LiteralPath $InternalRoot -File |
    Where-Object { $_.Name -match '^icu(?:uc|in|dt).*\.dll$' }

foreach ($File in $BundledIcuFiles) {
    $ResolvedFile = [IO.Path]::GetFullPath($File.FullName)
    if ([IO.Path]::GetDirectoryName($ResolvedFile) -ne $InternalRoot) {
        throw "Refusing to remove an ICU DLL outside the generated runtime directory."
    }
    Remove-Item -LiteralPath $ResolvedFile -Force
}

$ExecutablePath = Join-Path $ReleaseRoot "GdlScrape.exe"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "The build completed without producing GdlScrape.exe."
}

Write-Host "Build complete: $ExecutablePath"
