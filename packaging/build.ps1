# Builds dist\BookReader\ (folder with BookReader.exe), verifies it, and packs it into
#   dist\BookReader-<wersja>.7z           (archiwum, LZMA2 ultra)
#   dist\BookReader-<wersja>-setup.exe    (samorozpakowujący się, 7-Zip SFX)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "dist"
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
Set-Location $root

Write-Host "== ikona"
& $py packaging\make_icon.py

# Processes that still use files of the previous build (BookReader itself, or programs it started
# with its folder on PATH, e.g. an Ollama runner) would make the rebuild fail with "Access denied".
$app = Join-Path $dist "BookReader"
$lockers = Get-Process | Where-Object { try { $_.Path -like "$app*" -or [bool]($_.Modules | Where-Object { $_.FileName -like "$app*" }) } catch { $false } }
foreach ($p in $lockers) {
    Write-Host ("== zamykam proces używający plików programu: {0} ({1})" -f $p.ProcessName, $p.Id)
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
if ($lockers) { Start-Sleep -Seconds 2 }

# Portable user data (dist\BookReader\data) survives rebuilds and never goes into the archives.
$data = Join-Path $dist "BookReader\data"
$keep = Join-Path $dist "_data_keep"
if (Test-Path $data) { Move-Item $data $keep }

Write-Host "== PyInstaller"
& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $root "build") packaging\BookReader.spec
if ($LASTEXITCODE -ne 0) { if (Test-Path $keep) { New-Item -ItemType Directory -Force (Join-Path $dist "BookReader") | Out-Null; Move-Item $keep $data }; throw "PyInstaller failed" }

Write-Host "== autotest spakowanego programu"
$report = Join-Path $dist "selftest.txt"
$p = Start-Process -FilePath (Join-Path $dist "BookReader\BookReader.exe") -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru
Get-Content $report -Encoding UTF8
if ($p.ExitCode -ne 0) { throw "Autotest nie przeszedł" }

if (Test-Path $keep) { Move-Item $keep $data }
$size = (Get-ChildItem (Join-Path $dist "BookReader") -Recurse | Where-Object { $_.FullName -notlike "$data*" } | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("== rozmiar folderu: {0:N0} MB" -f $size)

if (Test-Path $sevenZip) {
    $stamp = (& $py -c "import bookreader; print(bookreader.__version__)").Trim()
    $archive = Join-Path $dist "BookReader-$stamp.7z"
    $sfx = Join-Path $dist "BookReader-$stamp-setup.exe"
    Remove-Item $archive, $sfx -ErrorAction SilentlyContinue
    Write-Host "== 7z (LZMA2, ultra)"
    & $sevenZip a -t7z -m0=lzma2 -mx=9 -mmt=on -bso0 -bsp0 "-xr!data" $archive (Join-Path $dist "BookReader")
    Write-Host "== samorozpakowujący się exe"
    & $sevenZip a -t7z -m0=lzma2 -mx=9 -mmt=on "-sfx$(Join-Path (Split-Path $sevenZip) '7z.sfx')" -bso0 -bsp0 "-xr!data" $sfx (Join-Path $dist "BookReader")
    Get-Item $archive, $sfx | ForEach-Object { "{0}  {1:N0} MB" -f $_.Name, ($_.Length / 1MB) }
} else {
    Write-Host "7-Zip nie znaleziony — pakuję do ZIP"
    Compress-Archive -Path (Join-Path $dist "BookReader") -DestinationPath (Join-Path $dist "BookReader.zip") -Force
}
