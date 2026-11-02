# Сборка app\echoes_ui.dll: MSVC 2022 (Build Tools) + Qt 6.11.2 msvc2022_64 (только qtbase,
# download.qt.io …/qt6_6112/qt6_6112_msvc2022_64/, распаковать в C:\Qt\6.11.2\msvc2022_64).
# DLL подгружается в процесс PyQt6: Qt там должен быть той же или более новой версии Qt 6.
param([string]$Qt = 'C:\Qt\6.11.2\msvc2022_64')
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $here)
$out = Join-Path $root 'app\echoes_ui.dll'
$obj = Join-Path $env:TEMP 'echoes_ui_build'
New-Item -ItemType Directory -Force $obj | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'MSVC Build Tools not found' }
# окружение компилятора (vcvars64) — в текущий процесс (vcvars сам ищет vswhere в PATH)
$env:PATH = (Split-Path -Parent $vswhere) + ';' + $env:PATH
$ErrorActionPreference = 'Continue'
cmd /c "`"$vs\VC\Auxiliary\Build\vcvars64.bat`" >nul 2>nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
$ErrorActionPreference = 'Stop'
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'vcvars64 failed: cl.exe not in PATH' }

$srcs = Get-ChildItem $here -Filter *.cpp | ForEach-Object FullName
# moc — для классов с Q_OBJECT (сигналы); файлы без Q_OBJECT moc пропускает
$mocs = @()
foreach ($h in Get-ChildItem $here -Filter *.h) {
    if (Select-String -Path $h.FullName -Pattern 'Q_OBJECT' -Quiet) {
        $m = Join-Path $obj ('moc_' + $h.BaseName + '.cpp')
        & "$Qt\bin\moc.exe" $h.FullName -o $m
        if ($LASTEXITCODE -ne 0) { throw "moc failed: $($h.Name)" }
        $mocs += $m
    }
}
$inc = @("/I$Qt\include", "/I$Qt\include\QtCore", "/I$Qt\include\QtGui", "/I$Qt\include\QtWidgets", "/I$here")
$libs = @("$Qt\lib\Qt6Core.lib", "$Qt\lib\Qt6Gui.lib", "$Qt\lib\Qt6Widgets.lib")
& cl.exe /nologo /std:c++17 /permissive- /Zc:__cplusplus /EHsc /O2 /MD /utf-8 /W3 /DNDEBUG /DQT_NO_DEBUG `
    /DUNICODE /D_UNICODE /DNOMINMAX $inc $srcs $mocs /Fo"$obj\\" /LD /Fe"$out" /link /NOLOGO $libs
if ($LASTEXITCODE -ne 0) { throw 'echoes_ui build failed' }
Remove-Item (Join-Path $root 'app\echoes_ui.lib'), (Join-Path $root 'app\echoes_ui.exp') -ErrorAction SilentlyContinue
Get-Item $out | Select-Object Name, Length
