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
$GalleryDlPath = Join-Path $ProjectRoot "release\GdlScrape\gallery-dl.exe"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "The build completed without producing GdlScrape.exe."
}
if (-not (Test-Path -LiteralPath $GalleryDlPath -PathType Leaf)) {
    throw "The build completed without producing the bundled gallery-dl.exe."
}

$GalleryDlVersion = & $GalleryDlPath --version
if ($LASTEXITCODE -ne 0 -or $GalleryDlVersion -notmatch '^\d+\.\d+') {
    throw "The bundled gallery-dl.exe did not pass its version check."
}

$ChecksumPath = Join-Path $ProjectRoot "release\GdlScrape\SHA256SUMS.txt"
@($ExecutablePath, $GalleryDlPath) | ForEach-Object {
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
Write-Host "Distribute the archive or entire release\GdlScrape folder. Both EXEs and _internal are required."
