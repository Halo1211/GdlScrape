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

$ExecutablePath = Join-Path $ProjectRoot "release\GdlScrape\GdlScrape.exe"
$LegacyEnginePath = Join-Path $ProjectRoot "release\GdlScrape\gallery-dl.exe"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "The build completed without producing GdlScrape.exe."
}
if (Test-Path -LiteralPath $LegacyEnginePath) {
    throw "The bundle must not contain a separate gallery-dl.exe."
}

$GalleryDlVersion = & $ExecutablePath --gallery-dl --version
if ($LASTEXITCODE -ne 0 -or $GalleryDlVersion -notmatch '^\d+\.\d+') {
    throw "The internal download engine did not pass its version check."
}

$EngineLicensePath = & py -c "import importlib.metadata as m; d = m.distribution('gallery-dl'); print(d.locate_file(next(p for p in d.files if str(p).replace(chr(92), '/').endswith('/licenses/LICENSE'))))"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $EngineLicensePath -PathType Leaf)) {
    throw "Could not locate the bundled gallery-dl license."
}
$BundlePath = Split-Path -Parent $ExecutablePath
Copy-Item -LiteralPath "$ProjectRoot\LICENSE" -Destination "$BundlePath\LICENSE.txt"
Copy-Item -LiteralPath $EngineLicensePath -Destination "$BundlePath\gallery-dl-LICENSE.txt"
@"
GdlScrape is an independent application maintained at:
https://github.com/Halo1211/GdlScrape
Its source code is licensed under MIT; see LICENSE.txt.

The internal download engine is gallery-dl $GalleryDlVersion by Mike Faehrmann
and contributors. gallery-dl is a separate third-party project licensed under
GPL-2.0-only; see gallery-dl-LICENSE.txt. Its corresponding source is available at:
https://github.com/mikf/gallery-dl/archive/refs/tags/v$GalleryDlVersion.tar.gz

Other bundled dependencies retain their respective licenses and copyrights.
"@ | Set-Content -LiteralPath "$BundlePath\THIRD_PARTY_NOTICES.txt" -Encoding utf8

$ChecksumPath = Join-Path $ProjectRoot "release\GdlScrape\SHA256SUMS.txt"
@($ExecutablePath) | ForEach-Object {
    $File = Get-Item -LiteralPath $_
    $Hash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $($File.Name)"
} | Set-Content -LiteralPath $ChecksumPath -Encoding ascii

$AppVersion = & py -c "from gallery_dl_app.core import APP_VERSION; print(APP_VERSION)"
if ($LASTEXITCODE -ne 0 -or $AppVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "Could not determine a valid release version."
}
$ArchivePath = Join-Path $ProjectRoot "release\GdlScrape-v$AppVersion-win64.zip"
Compress-Archive -LiteralPath (Join-Path $ProjectRoot "release\GdlScrape") `
    -DestinationPath $ArchivePath -CompressionLevel Optimal -Force
$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$ReleaseChecksumPath = Join-Path $ProjectRoot "release\SHA256SUMS.txt"
"$ArchiveHash  $(Split-Path -Leaf $ArchivePath)" |
    Set-Content -LiteralPath $ReleaseChecksumPath -Encoding ascii

Write-Host "Build complete: $ExecutablePath"
Write-Host "Bundled gallery-dl version: $GalleryDlVersion"
Write-Host "Checksums: $ChecksumPath"
Write-Host "Release archive: $ArchivePath"
Write-Host "Archive checksum: $ReleaseChecksumPath"
Write-Host "Distribute the archive or entire release\GdlScrape folder. GdlScrape.exe and _internal are required."
