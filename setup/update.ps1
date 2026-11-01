#Requires -Version 5.1
<#
    ECHOES — обновление уже установленного плеера.
    Находит, куда ECHOES был установлен (даже если в другую папку),
    закрывает его, заменяет файлы программы на новые и обновляет
    загрузчик yt-dlp. Плейлисты, библиотека и настройки не трогаются.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { $Host.UI.RawUI.WindowTitle = 'ECHOES — обновление' } catch {}
try { $Host.UI.RawUI.BackgroundColor = 'Black'; $Host.UI.RawUI.ForegroundColor = 'Gray' } catch {}
Clear-Host

$SRC_APP = Join-Path (Split-Path -Parent $PSScriptRoot) 'app'

function Ok([string]$t)   { Write-Host "      √ $t" -ForegroundColor Green }
function Info([string]$t) { Write-Host "      · $t" -ForegroundColor DarkGray }
function Warn([string]$t) { Write-Host "      ! $t" -ForegroundColor Yellow }
function Step([string]$t) { Write-Host ''; Write-Host "  » $t" -ForegroundColor White }
function Done([int]$code) {
    Write-Host ''
    Read-Host '  Нажмите Enter, чтобы закрыть' | Out-Null
    exit $code
}
function Fail([string]$msg) {
    Write-Host ''
    Write-Host "  × $msg" -ForegroundColor Red
    Done 1
}

Write-Host ''
Write-Host '   ███████╗ ██████╗██╗  ██╗ ██████╗ ███████╗███████╗' -ForegroundColor Yellow
Write-Host '   ██╔════╝██╔════╝██║  ██║██╔═══██╗██╔════╝██╔════╝' -ForegroundColor Yellow
Write-Host '   █████╗  ██║     ███████║██║   ██║█████╗  ███████╗' -ForegroundColor DarkYellow
Write-Host '   ███████╗╚██████╗██║  ██║╚██████╔╝███████╗███████║' -ForegroundColor DarkYellow
Write-Host '   ╚══════╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝' -ForegroundColor DarkGray
Write-Host '                     обновление' -ForegroundColor Gray

if (-not (Test-Path (Join-Path $SRC_APP 'main.py'))) {
    Fail 'Рядом нет папки app с новой версией. Распакуйте архив ЦЕЛИКОМ и запустите файл из распакованной папки.'
}

# ── 1. где установлен ECHOES ─────────────────────────────────────────────
Step 'Ищу установленный ECHOES'
$cands = New-Object System.Collections.Generic.List[string]
try {
    $loc = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ECHOES' -ErrorAction Stop).InstallLocation
    if ($loc) { $cands.Add($loc) }
} catch {}
try {
    $ws = New-Object -ComObject WScript.Shell
    foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'ECHOES.lnk'),
                       (Join-Path ([Environment]::GetFolderPath('Programs')) 'ECHOES\ECHOES.lnk'))) {
        if (Test-Path $lnk) {
            $t = $ws.CreateShortcut($lnk).TargetPath            # ...\ECHOES\python\pythonw.exe
            if ($t) { $cands.Add((Split-Path -Parent (Split-Path -Parent $t))) }
        }
    }
} catch {}
$cands.Add((Join-Path $env:LOCALAPPDATA 'Programs\ECHOES'))
$InstallDir = $null
foreach ($c in $cands) {
    if ($c -and (Test-Path (Join-Path $c 'app\main.py')) -and (Test-Path (Join-Path $c 'python\python.exe'))) {
        $InstallDir = $c; break
    }
}
if (-not $InstallDir) {
    Fail 'ECHOES не найден на этом компьютере. Сначала установите его: «УСТАНОВИТЬ ECHOES.bat».'
}
$APP = Join-Path $InstallDir 'app'
$PY  = Join-Path $InstallDir 'python\python.exe'
Ok "найден: $InstallDir"

$verFile = Join-Path $APP 'version.txt'
$oldVer = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { 'старая' }
$newVerFile = Join-Path $SRC_APP 'version.txt'
$newVer = if (Test-Path $newVerFile) { (Get-Content $newVerFile -Raw).Trim() } else { 'новая' }
Info "версия: $oldVer  →  $newVer"

# ── 2. закрыть плеер ─────────────────────────────────────────────────────
Step 'Закрываю ECHOES, если он открыт'
$killed = 0
try {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
        Where-Object { ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase)) -or
                       ($_.CommandLine -and $_.CommandLine -like "*$APP*") } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed++ }
} catch {
    Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { $_ | Stop-Process -Force; $killed++ }
}
if ($killed) { Start-Sleep -Milliseconds 1200; Ok 'закрыт' } else { Ok 'не был запущен' }

# ── 3. заменить файлы ────────────────────────────────────────────────────
Step 'Обновляю файлы программы'
Get-ChildItem -Path $APP -Filter '*.dll.old*' -File -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
$changed = 0; $same = 0; $failed = @()
Get-ChildItem -Path $SRC_APP -File | ForEach-Object {
    $dst = Join-Path $APP $_.Name
    $need = $true
    if (Test-Path $dst) {
        try {
            $need = (Get-FileHash $_.FullName -Algorithm MD5).Hash -ne (Get-FileHash $dst -Algorithm MD5).Hash
        } catch { $need = $true }
    }
    if ($need) {
        $ok = $false
        for ($i = 0; $i -lt 5 -and -not $ok; $i++) {
            try { Copy-Item $_.FullName -Destination $dst -Force; $ok = $true }
            catch { Start-Sleep -Milliseconds 600 }
        }
        if (-not $ok -and $_.Extension -eq '.dll' -and (Test-Path $dst)) {
            # занятую DLL (C++-ядро эффектов) Windows не даёт перезаписать, но даёт переименовать:
            # старая уходит в сторону и удаляется при следующем обновлении
            try {
                Rename-Item $dst ($_.Name + '.old' + (Get-Random)) -Force
                Copy-Item $_.FullName -Destination $dst -Force
                $ok = $true
            } catch {}
        }
        if ($ok) { $changed++; Info "обновлён $($_.Name)" } else { $failed += $_.Name }
    } else { $same++ }
}
foreach ($assetFolder in @('assets', 'discord_assets', 'dsl_themes', 'montage_native', 'sounds')) {
    $assetSource = Join-Path $SRC_APP $assetFolder
    if (Test-Path -LiteralPath $assetSource) {
        $assetTarget = Join-Path $APP $assetFolder
        New-Item -ItemType Directory -Path $assetTarget -Force | Out-Null
        Get-ChildItem -LiteralPath $assetSource | Copy-Item -Destination $assetTarget -Recurse -Force
    }
}
Get-ChildItem -Path $APP -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
Remove-Item (Join-Path $APP '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue
$un = Join-Path $PSScriptRoot 'uninstall.ps1'
if (Test-Path $un) { Copy-Item $un -Destination $InstallDir -Force -ErrorAction SilentlyContinue }
if ($failed.Count) {
    Fail "Не удалось заменить: $($failed -join ', '). Закройте ECHOES вручную (и в трее) и запустите обновление ещё раз."
}
Ok "обновлено файлов: $changed, без изменений: $same"

# ── 4. загрузчик и библиотеки ────────────────────────────────────────────
Step 'Обновляю загрузчик yt-dlp и поиск YouTube Music (1-2 минуты)'
$old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& $PY -m pip install --upgrade --prefer-binary --no-warn-script-location --disable-pip-version-check `
      --timeout 60 --retries 3 yt-dlp ytmusicapi syncedlyrics requests mutagen Pillow sounddevice pycryptodomex websockets av `
      winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Media `
      winrt-Windows.Media.Playback winrt-Windows.Storage winrt-Windows.Storage.Streams 2>&1 | ForEach-Object {
    $l = "$_"
    if ($l -match '^Successfully installed (.+)') { Info "обновлено: $($Matches[1])" }
}
$pipCode = $LASTEXITCODE
$ErrorActionPreference = $old
if ($pipCode -eq 0) { Ok 'готово' } else { Warn 'часть библиотек не обновилась (нет интернета?) — плеер всё равно обновлён' }

# ── готово ───────────────────────────────────────────────────────────────
try {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ECHOES'
    if (Test-Path $key) { Set-ItemProperty -Path $key -Name DisplayVersion -Value $newVer -ErrorAction SilentlyContinue }
} catch {}
Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════╗' -ForegroundColor Green
Write-Host "  ║   ECHOES ОБНОВЛЁН  ·  версия $($newVer.PadRight(16))║" -ForegroundColor Green
Write-Host '  ╚══════════════════════════════════════════════╝' -ForegroundColor Green
Write-Host ''
$go = Read-Host '   Запустить ECHOES? [Enter — да / n — нет]'
if ($go -notmatch '^(n|н|no|нет)$') {
    Start-Process -FilePath (Join-Path $InstallDir 'python\pythonw.exe') -ArgumentList "`"$(Join-Path $APP 'main.py')`"" -WorkingDirectory $APP
}
exit 0
