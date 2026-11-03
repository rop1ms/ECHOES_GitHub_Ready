#Requires -Version 5.1
<#
    ECHOES — установщик для Windows 10/11 (64-бит).
    Ставит ВСЁ сам, без прав администратора:
      * собственный Python (встраиваемый, не мешает другим Python на компьютере)
      * все библиотеки Python (PyQt6, numpy, yt-dlp, ...)
      * VLC (движок воспроизведения) и ffmpeg (для загрузчика) — портативно
      * ярлыки на рабочем столе и в меню «Пуск», пункт в «Установленных приложениях»
    Повторный запуск = обновление (уже скачанное заново не качается).
#>
# Параметры для ECHOES Launcher (установка из окна, без вопросов в консоли):
#   -Auto  без вопросов;  -NoDesktop / -NoStartMenu — без ярлыков;  -NoSpotify — без spotdl;
#   -Autostart — запускать ECHOES вместе с Windows;  -NoLaunch — не открывать плеер в конце
param([string]$InstallDir = "", [switch]$Auto, [switch]$NoDesktop, [switch]$NoStartMenu, [switch]$NoSpotify,
      [switch]$Autostart, [switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
try { $Host.UI.RawUI.WindowTitle = 'ECHOES — установка' } catch {}
try { $Host.UI.RawUI.BackgroundColor = 'Black'; $Host.UI.RawUI.ForegroundColor = 'Gray' } catch {}
try {
    $raw = $Host.UI.RawUI
    $w = [Math]::Min(100, $raw.MaxPhysicalWindowSize.Width)
    if ($raw.BufferSize.Width -lt $w) { $raw.BufferSize = New-Object Management.Automation.Host.Size($w, 3000) }
    $raw.WindowSize = New-Object Management.Automation.Host.Size($w, [Math]::Min(42, $raw.MaxPhysicalWindowSize.Height))
} catch {}
Clear-Host

$APP_NAME    = 'ECHOES'
$APP_VERSION = '1.0'
$SRC_ROOT    = Split-Path -Parent $PSScriptRoot          # папка ECHOES_Setup
$SRC_APP     = Join-Path $SRC_ROOT 'app'
$sourceVersion = Join-Path $SRC_APP 'version.txt'
if (Test-Path -LiteralPath $sourceVersion) { $APP_VERSION = (Get-Content -LiteralPath $sourceVersion -Raw).Trim() }
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\ECHOES' }

$PY_URLS = @(
    'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip',
    'https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip',
    'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip'
)
$GETPIP_URLS = @('https://bootstrap.pypa.io/get-pip.py', 'https://bootstrap.pypa.io/pip/get-pip.py')
$VLC_URLS = @(
    'https://download.videolan.org/pub/videolan/vlc/3.0.21/win64/vlc-3.0.21-win64.zip',
    'https://get.videolan.org/vlc/3.0.21/win64/vlc-3.0.21-win64.zip',
    'https://download.videolan.org/pub/videolan/vlc/3.0.20/win64/vlc-3.0.20-win64.zip'
)
$FFMPEG_URLS = @(
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip',
    'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
)
# обязательные — без них плеер не запустится
$PKG_CORE  = @('PyQt6', 'python-vlc', 'numpy', 'scipy', 'mutagen', 'requests')
# всё остальное — тексты, загрузчик, онлайн-поиск, Discord, GIF
$PKG_EXTRA = @('yt-dlp', 'ytmusicapi', 'syncedlyrics', 'beautifulsoup4', 'lyricsgenius', 'Pillow', 'pypresence', 'sounddevice', 'pycryptodomex', 'websockets', 'av',
               'winrt-runtime', 'winrt-Windows.Foundation', 'winrt-Windows.Foundation.Collections', 'winrt-Windows.Media', 'winrt-Windows.Media.Playback', 'winrt-Windows.Storage', 'winrt-Windows.Storage.Streams')
$PKG_SPOT  = @('spotdl')

$TOTAL_STEPS = 7

# ═══════════════════════════════ оформление ═══════════════════════════════ #

function Show-Logo {
    $logo = @(
        '   ███████╗ ██████╗██╗  ██╗ ██████╗ ███████╗███████╗',
        '   ██╔════╝██╔════╝██║  ██║██╔═══██╗██╔════╝██╔════╝',
        '   █████╗  ██║     ███████║██║   ██║█████╗  ███████╗',
        '   ██╔══╝  ██║     ██╔══██║██║   ██║██╔══╝  ╚════██║',
        '   ███████╗╚██████╗██║  ██║╚██████╔╝███████╗███████║',
        '   ╚══════╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝'
    )
    $cols = @('Yellow', 'Yellow', 'Yellow', 'DarkYellow', 'DarkYellow', 'DarkGray')
    Write-Host ''
    for ($i = 0; $i -lt $logo.Count; $i++) { Write-Host $logo[$i] -ForegroundColor $cols[$i] }
    Write-Host ''
    Write-Host '   музыкальный плеер  ·  ' -NoNewline -ForegroundColor Gray
    Write-Host "установщик v$APP_VERSION" -ForegroundColor DarkYellow
    Write-Host '   ─────────────────────────────────────────────────────────' -ForegroundColor DarkGray
    Write-Host ''
}

function Step([int]$n, [string]$text) {
    Write-Host ''
    Write-Host "  [$n/$TOTAL_STEPS] " -NoNewline -ForegroundColor Black -BackgroundColor Yellow
    Write-Host " $text" -ForegroundColor White
}
function Ok([string]$t)   { Write-Host "      √ $t" -ForegroundColor Green }
function Info([string]$t) { Write-Host "      · $t" -ForegroundColor DarkGray }
function Warn([string]$t) { Write-Host "      ! $t" -ForegroundColor Yellow }
function Bad([string]$t)  { Write-Host "      × $t" -ForegroundColor Red }

function Show-Bar([long]$done, $total) {
    $mb = '{0:N1}' -f ($done / 1MB)
    if ($total -and $total -gt 0) {
        $pct = [Math]::Min(100, [int](100 * $done / $total))
        $fill = [int]($pct / 4)
        $bar = ('█' * $fill) + ('░' * (25 - $fill))
        $tot = '{0:N1}' -f ($total / 1MB)
        $line = "      $bar $pct%  $mb / $tot МБ   "
    } else {
        $line = "      скачано $mb МБ…   "
    }
    [Console]::Write("`r$line")
}

function Fail([string]$msg) {
    Write-Host ''
    Write-Host '  ╔══════════════════════════════════════════════════════╗' -ForegroundColor Red
    Write-Host '  ║   Установка не завершена                             ║' -ForegroundColor Red
    Write-Host '  ╚══════════════════════════════════════════════════════╝' -ForegroundColor Red
    Write-Host ''
    Write-Host "  $msg" -ForegroundColor White
    Write-Host ''
    Write-Host '  Что можно сделать:' -ForegroundColor Gray
    Write-Host '   1. Проверьте интернет и запустите установщик ещё раз —' -ForegroundColor Gray
    Write-Host '      уже скачанное повторно качаться не будет.' -ForegroundColor Gray
    Write-Host '   2. Временно отключите VPN / прокси, если они есть.' -ForegroundColor Gray
    if ($script:LogFile) { Write-Host "   3. Подробный журнал: $script:LogFile" -ForegroundColor Gray }
    Write-Host ''
    try { Stop-Transcript | Out-Null } catch {}
    if (-not $Auto) { Read-Host '  Нажмите Enter, чтобы закрыть' }
    exit 1
}

# ═══════════════════════════════ утилиты ═══════════════════════════════ #

function Get-File([string[]]$Urls, [string]$Dest) {
    Add-Type -AssemblyName System.Net.Http
    foreach ($u in $Urls) {
        $hostName = ([Uri]$u).Host
        for ($try = 1; $try -le 3; $try++) {
            $client = $null; $in = $null; $out = $null
            try {
                Info "источник: $hostName$(if ($try -gt 1) { " (попытка $try)" })"
                $h = New-Object System.Net.Http.HttpClientHandler
                $h.AllowAutoRedirect = $true
                $client = New-Object System.Net.Http.HttpClient($h)
                $client.Timeout = [TimeSpan]::FromMinutes(40)
                $client.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) ECHOES-Setup/1.0')
                $resp = $client.GetAsync($u, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
                if (-not $resp.IsSuccessStatusCode) { throw "сервер ответил $([int]$resp.StatusCode)" }
                $total = $resp.Content.Headers.ContentLength
                $in  = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                $out = [IO.File]::Create($Dest)
                $buf = New-Object byte[] 262144
                [long]$done = 0
                $sw = [Diagnostics.Stopwatch]::StartNew()
                while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                    $out.Write($buf, 0, $n)
                    $done += $n
                    if ($sw.ElapsedMilliseconds -gt 150) { Show-Bar $done $total; $sw.Restart() }
                }
                Show-Bar $done $total
                [Console]::WriteLine()
                $out.Close(); $out = $null
                if ($done -lt 4096) { throw 'получен пустой файл' }
                if ($total -and $done -lt $total) { throw 'загрузка оборвалась' }
                return $true
            } catch {
                [Console]::WriteLine()
                Warn "не получилось: $($_.Exception.Message)"
                if ($out) { try { $out.Close() } catch {} }
                Remove-Item $Dest -Force -ErrorAction SilentlyContinue
                if ($_.Exception.Message -match 'ответил 4\d\d') { break }   # файла там нет — сразу к следующему источнику
                Start-Sleep -Seconds (2 * $try)
            } finally {
                if ($in) { try { $in.Close() } catch {} }
                if ($client) { try { $client.Dispose() } catch {} }
            }
        }
        # запасной способ (умеет редиректы https→http и системный прокси)
        try {
            Info "пробую другим способом: $hostName"
            $wc = New-Object Net.WebClient
            $wc.Headers.Add('User-Agent', 'Mozilla/5.0 ECHOES-Setup/1.0')
            $wc.Proxy = [Net.WebRequest]::GetSystemWebProxy()
            $wc.Proxy.Credentials = [Net.CredentialCache]::DefaultCredentials
            $wc.DownloadFile($u, $Dest)
            if ((Get-Item $Dest).Length -gt 4096) { Ok 'скачано'; return $true }
        } catch {
            Warn "не получилось: $($_.Exception.Message)"
            Remove-Item $Dest -Force -ErrorAction SilentlyContinue
        }
    }
    return $false
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Expand-Zip([string]$Zip, [string]$To) {
    if (Test-Path $To) { Remove-Item $To -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($Zip, $To)
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$ShowPip) {
    # pip пишет предупреждения в stderr — в PowerShell 5 это не должно считаться падением
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $line = "$_"
            if ($script:LogFile) { Add-Content -Path $script:LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue }
            if ($ShowPip) {
                if ($line -match '^\s*Collecting (\S+)') { Info "загружаю $($Matches[1])" }
                elseif ($line -match '^Successfully installed') { Ok 'установлено' }
                elseif ($line -match '^(ERROR|error):') { Bad $line.Trim() }
            }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
}

function Stop-Running {
    try {
        Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) } |
            ForEach-Object { Warn 'закрываю запущенный ECHOES…'; $_ | Stop-Process -Force; Start-Sleep 1 }
    } catch {}
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkDir, [string]$Icon, [string]$Desc) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($Path)
    $s.TargetPath = $Target
    $s.Arguments = $Arguments
    $s.WorkingDirectory = $WorkDir
    $s.IconLocation = "$Icon,0"
    $s.Description = $Desc
    $s.Save()
}

# ═══════════════════════════════ установка ═══════════════════════════════ #

Show-Logo

if (-not [Environment]::Is64BitOperatingSystem) {
    Fail 'Нужна 64-битная Windows 10 или 11. На 32-битной системе ECHOES не работает.'
}
if (-not (Test-Path (Join-Path $SRC_APP 'main.py'))) {
    Fail "Не найдена папка app рядом с установщиком.`n  Распакуйте архив ECHOES_Setup.zip ЦЕЛИКОМ и запустите «УСТАНОВИТЬ ECHOES.bat» из распакованной папки."
}

Write-Host '   Куда установить:  ' -NoNewline -ForegroundColor Gray
Write-Host $InstallDir -ForegroundColor White
Write-Host '   Нужно ~1 ГБ места и интернет. Права администратора не нужны.' -ForegroundColor DarkGray
Write-Host ''
$ans = if ($Auto) { '' } else { Read-Host '   Enter — установить сюда,  или вставьте другой путь' }
if ($ans -and $ans.Trim()) {
    $InstallDir = $ans.Trim().Trim('"')
    if (-not ($InstallDir -match 'ECHOES\\?$')) { $InstallDir = Join-Path $InstallDir 'ECHOES' }
}

try {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
} catch {
    Fail "Не удалось создать папку $InstallDir. Выберите другую (например, на диске D:)."
}
$script:LogFile = Join-Path $InstallDir 'setup.log'
try { Start-Transcript -Path (Join-Path $InstallDir 'setup-transcript.log') -Force | Out-Null } catch {}
"=== ECHOES setup $(Get-Date) ===" | Set-Content -Path $script:LogFile -Encoding UTF8

$TMP = Join-Path $InstallDir '_download'
New-Item -ItemType Directory -Path $TMP -Force | Out-Null
$PY_DIR  = Join-Path $InstallDir 'python'
$APP_DIR = Join-Path $InstallDir 'app'
$VLC_DIR = Join-Path $InstallDir 'vlc'
$FF_DIR  = Join-Path $InstallDir 'ffmpeg'
$PY      = Join-Path $PY_DIR 'python.exe'
$PYW     = Join-Path $PY_DIR 'pythonw.exe'

# место на диске
try {
    $drive = (Get-Item $InstallDir).PSDrive
    $freeGB = [Math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt 1.2) { Warn "на диске $($drive.Name): свободно всего $freeGB ГБ — может не хватить" }
} catch {}

# ── 1. файлы плеера ───────────────────────────────────────────────────────
Step 1 'Копирую файлы плеера'
Stop-Running
New-Item -ItemType Directory -Path $APP_DIR -Force | Out-Null
Get-ChildItem -Path $SRC_APP -File | ForEach-Object {
    Copy-Item $_.FullName -Destination $APP_DIR -Force
}
foreach ($assetFolder in @('assets', 'discord_assets', 'dsl_themes', 'montage_native', 'sounds')) {
    $assetSource = Join-Path $SRC_APP $assetFolder
    if (Test-Path -LiteralPath $assetSource) {
        $assetTarget = Join-Path $APP_DIR $assetFolder
        New-Item -ItemType Directory -Path $assetTarget -Force | Out-Null
        Get-ChildItem -LiteralPath $assetSource | Copy-Item -Destination $assetTarget -Recurse -Force
    }
}
Get-ChildItem -Path $APP_DIR -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
Remove-Item (Join-Path $APP_DIR '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination $InstallDir -Force
Ok "файлы плеера → $APP_DIR"

# ── 2. Python ─────────────────────────────────────────────────────────────
Step 2 'Python (свой, отдельный — другие версии на компьютере не трогаются)'
if ((Test-Path $PY) -and (Test-Path $PYW)) {
    Ok 'уже установлен'
} else {
    $zip = Join-Path $TMP 'python.zip'
    if (-not (Get-File $PY_URLS $zip)) { Fail 'Не удалось скачать Python с python.org.' }
    Expand-Zip $zip $PY_DIR
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Ok 'Python распакован'
}
# путь к плееру и site-packages — во встраиваемом Python задаются файлом ._pth
$pth = Get-ChildItem -Path $PY_DIR -Filter 'python3*._pth' | Select-Object -First 1
if (-not $pth) { Fail 'Python распакован неправильно (нет файла ._pth). Удалите папку python и запустите ещё раз.' }
$zipName = ($pth.BaseName) + '.zip'
$pthText = "$zipName`r`n.`r`n..\app`r`nLib\site-packages`r`nimport site`r`n"
[IO.File]::WriteAllText($pth.FullName, $pthText, (New-Object Text.ASCIIEncoding))

# ── 3. pip ────────────────────────────────────────────────────────────────
Step 3 'Менеджер пакетов pip'
$code = Invoke-Native $PY @('-m', 'pip', '--version')
if ($code -eq 0) {
    Ok 'уже есть'
} else {
    $gp = Join-Path $TMP 'get-pip.py'
    if (-not (Get-File $GETPIP_URLS $gp)) { Fail 'Не удалось скачать установщик pip.' }
    $code = Invoke-Native $PY @($gp, '--no-warn-script-location', '--disable-pip-version-check')
    if ($code -ne 0) { Fail 'pip не установился. Подробности — в журнале.' }
    Ok 'pip установлен'
}

# ── 4. библиотеки ─────────────────────────────────────────────────────────
Step 4 'Библиотеки Python (это самый долгий шаг — 2-10 минут)'
$pipBase = @('-m', 'pip', 'install', '--upgrade', '--prefer-binary', '--no-warn-script-location',
             '--disable-pip-version-check', '--timeout', '60', '--retries', '5')
$ok = $false
for ($i = 1; $i -le 3 -and -not $ok; $i++) {
    if ($i -gt 1) { Warn "ещё одна попытка ($i/3)…"; Start-Sleep 3 }
    $ok = ((Invoke-Native $PY ($pipBase + $PKG_CORE) -ShowPip) -eq 0)
}
if (-not $ok) { Fail 'Не удалось установить основные библиотеки (PyQt6, numpy, …).' }
Ok 'основные библиотеки готовы'

if (-not $NoSpotify) {
    Info 'Spotify-загрузчик (необязательно)…'
    if ((Invoke-Native $PY ($pipBase + $PKG_SPOT) -ShowPip) -ne 0) { Warn 'spotdl не встал — импорт из Spotify всё равно работает через встроенную утилиту' }
}

$failed = @()
if ((Invoke-Native $PY ($pipBase + $PKG_EXTRA) -ShowPip) -ne 0) {
    foreach ($p in $PKG_EXTRA) {
        if ((Invoke-Native $PY ($pipBase + @($p)) -ShowPip) -ne 0) { $failed += $p }
    }
}
if ($failed.Count) { Warn "не установились: $($failed -join ', ') — эти функции будут недоступны" }
else { Ok 'тексты, загрузчик, онлайн-поиск, Discord — готовы' }

# ── 5. VLC ────────────────────────────────────────────────────────────────
Step 5 'VLC — движок воспроизведения'
if (Test-Path (Join-Path $VLC_DIR 'libvlc.dll')) {
    Ok 'уже установлен'
} else {
    $zip = Join-Path $TMP 'vlc.zip'
    if (-not (Get-File $VLC_URLS $zip)) {
        if (Test-Path 'C:\Program Files\VideoLAN\VLC\libvlc.dll') {
            Warn 'скачать не вышло, но на компьютере уже есть VLC — плеер возьмёт его'
        } else {
            Fail 'Не удалось скачать VLC с videolan.org.'
        }
    } else {
        Info 'распаковываю…'
        $tmpV = Join-Path $TMP 'vlc_x'
        Expand-Zip $zip $tmpV
        $inner = Get-ChildItem -Path $tmpV -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'libvlc.dll') } | Select-Object -First 1
        if (-not $inner) { Fail 'Архив VLC повреждён. Запустите установщик ещё раз.' }
        if (Test-Path $VLC_DIR) { Remove-Item $VLC_DIR -Recurse -Force }
        Move-Item $inner.FullName $VLC_DIR
        Remove-Item $tmpV -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Ok 'VLC готов'
    }
}
# кэш плагинов: без plugins.dat VLC при каждом запуске плеера грузит все ~365 плагинов (медленный старт)
$cacheGen = Join-Path $VLC_DIR 'vlc-cache-gen.exe'
if (Test-Path $cacheGen) {
    & $cacheGen (Join-Path $VLC_DIR 'plugins') 2>&1 | Out-Null
}

# ── 6. ffmpeg ─────────────────────────────────────────────────────────────
Step 6 'ffmpeg — для скачивания музыки и эффектов'
if (Test-Path (Join-Path $FF_DIR 'ffmpeg.exe')) {
    Ok 'уже установлен'
} else {
    $zip = Join-Path $TMP 'ffmpeg.zip'
    if (-not (Get-File $FFMPEG_URLS $zip)) {
        Warn 'ffmpeg скачать не удалось — плеер работает, но скачивание треков и Slowed+Reverb будут недоступны.'
        Warn 'Запустите установщик позже ещё раз, чтобы докачать.'
    } else {
        Info 'распаковываю…'
        New-Item -ItemType Directory -Path $FF_DIR -Force | Out-Null
        $za = [IO.Compression.ZipFile]::OpenRead($zip)
        try {
            foreach ($e in $za.Entries) {
                if ($e.FullName -match '(^|/)bin/(ffmpeg|ffprobe)\.exe$') {
                    [IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $FF_DIR $e.Name), $true)
                }
            }
        } finally { $za.Dispose() }
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        if (Test-Path (Join-Path $FF_DIR 'ffmpeg.exe')) { Ok 'ffmpeg готов' }
        else { Warn 'в архиве не нашлось ffmpeg.exe — запустите установщик ещё раз' }
    }
}

# ── 7. ярлыки ─────────────────────────────────────────────────────────────
Step 7 'Ярлыки и регистрация в Windows'
$main = Join-Path $APP_DIR 'main.py'
$ico  = Join-Path $APP_DIR 'icon.ico'
$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$uninstArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'uninstall.ps1')`""
# GUI launcher is optional for older distributions; keep the direct player shortcut too.
$launcherSource = Join-Path $SRC_ROOT 'ECHOES Launcher.exe'
$launcherTarget = Join-Path $InstallDir 'ECHOES Launcher.exe'
if (Test-Path -LiteralPath $launcherSource) {
    try {
        Copy-Item -LiteralPath $launcherSource -Destination $launcherTarget -Force
        if (-not $NoDesktop) { New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ECHOES Launcher.lnk') $launcherTarget '' $InstallDir $ico 'ECHOES — запуск и обновления' }
        $launcherMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'ECHOES'
        New-Item -ItemType Directory -Path $launcherMenu -Force | Out-Null
        New-Shortcut (Join-Path $launcherMenu 'ECHOES Launcher.lnk') $launcherTarget '' $InstallDir $ico 'ECHOES — запуск и обновления'
    } catch { Warn "лаунчер не скопирован: $($_.Exception.Message)" }
}
if (-not $NoDesktop) { try {
    $desk = [Environment]::GetFolderPath('Desktop')
    New-Shortcut (Join-Path $desk 'ECHOES.lnk') $PYW "`"$main`"" $APP_DIR $ico 'ECHOES — музыкальный плеер'
    Ok 'ярлык на рабочем столе'
} catch { Warn "ярлык на рабочем столе не создан: $($_.Exception.Message)" } }
if ($Autostart) { try {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'ECHOES.lnk') $PYW "`"$main`"" $APP_DIR $ico 'ECHOES — музыкальный плеер'
    Ok 'запуск вместе с Windows'
} catch { Warn "автозапуск не настроен: $($_.Exception.Message)" } }
if (-not $NoStartMenu) { try {
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'ECHOES'
    New-Item -ItemType Directory -Path $menu -Force | Out-Null
    New-Shortcut (Join-Path $menu 'ECHOES.lnk') $PYW "`"$main`"" $APP_DIR $ico 'ECHOES — музыкальный плеер'
    New-Shortcut (Join-Path $menu 'Удалить ECHOES.lnk') $psExe $uninstArgs $InstallDir $ico 'Удалить ECHOES'
    Ok 'меню «Пуск»'
} catch { Warn "ярлык в меню «Пуск» не создан: $($_.Exception.Message)" } }
try {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ECHOES'
    New-Item -Path $key -Force | Out-Null
    $size = 0
    try { $size = [int]((Get-ChildItem $InstallDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1KB) } catch {}
    $props = @{
        DisplayName = 'ECHOES'; DisplayVersion = $APP_VERSION; Publisher = 'ECHOES';
        DisplayIcon = $ico; InstallLocation = $InstallDir;
        UninstallString = "`"$psExe`" $uninstArgs"; NoModify = 1; NoRepair = 1; EstimatedSize = $size
    }
    foreach ($k in $props.Keys) {
        $type = if ($props[$k] -is [int]) { 'DWord' } else { 'String' }
        New-ItemProperty -Path $key -Name $k -Value $props[$k] -PropertyType $type -Force | Out-Null
    }
    Ok 'ECHOES появился в «Установленных приложениях» (оттуда же удаляется)'
} catch { Warn 'не удалось зарегистрировать в списке программ' }

Remove-Item $TMP -Recurse -Force -ErrorAction SilentlyContinue
try { Stop-Transcript | Out-Null } catch {}

# ── готово ────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════════════╗' -ForegroundColor Green
Write-Host '  ║                                                      ║' -ForegroundColor Green
Write-Host '  ║          ECHOES  УСТАНОВЛЕН  —  ВСЁ ГОТОВО  ♪        ║' -ForegroundColor Green
Write-Host '  ║                                                      ║' -ForegroundColor Green
Write-Host '  ╚══════════════════════════════════════════════════════╝' -ForegroundColor Green
Write-Host ''
Write-Host '   Запускайте через ярлык ' -NoNewline -ForegroundColor Gray
Write-Host 'ECHOES' -NoNewline -ForegroundColor Yellow
Write-Host ' на рабочем столе или в меню «Пуск».' -ForegroundColor Gray
Write-Host '   Папку ECHOES_Setup теперь можно удалить.' -ForegroundColor DarkGray
Write-Host ''
$go = if ($NoLaunch) { 'n' } elseif ($Auto) { '' } else { Read-Host '   Запустить ECHOES сейчас? [Enter — да / n — нет]' }
if ($go -notmatch '^(n|н|no|нет)$') {
    Start-Process -FilePath $PYW -ArgumentList "`"$main`"" -WorkingDirectory $APP_DIR
}
exit 0
