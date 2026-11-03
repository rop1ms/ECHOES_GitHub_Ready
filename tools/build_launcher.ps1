$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$icon = Join-Path $root 'app/icon.ico'
& (Join-Path $PSScriptRoot 'launcher_native/build.ps1')
if($LASTEXITCODE -ne 0){throw 'Native launcher compilation failed'}
$pythonExecutable = (& py -c 'import sys; print(sys.executable)').Trim()
$savedBuildPath = $env:PATH
try {
    # Avoid bundling incompatible ICU/UCRT DLLs from unrelated tools on PATH.
    $env:PATH = "$(Split-Path $pythonExecutable);$env:SystemRoot\System32;$env:SystemRoot"
    & $pythonExecutable -m PyInstaller --noconfirm --clean --onefile --windowed --name 'ECHOES Launcher' --icon $icon --add-data "$icon;." --add-binary "$root/tools/launcher_native/echoes_launcher.dll;." --paths launcher --hidden-import core --hidden-import psutil --distpath . --workpath tools/launcher-build --specpath tools tools/launcher_native/host.py
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
} finally { $env:PATH = $savedBuildPath }
