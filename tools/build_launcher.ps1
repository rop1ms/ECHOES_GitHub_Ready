$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$icon = Join-Path $root 'app/icon.ico'
$pythonExecutable = (& py -c 'import sys; print(sys.executable)').Trim()
$savedBuildPath = $env:PATH
try {
    # Avoid bundling incompatible ICU/UCRT DLLs from unrelated tools on PATH.
    $env:PATH = "$(Split-Path $pythonExecutable);$env:SystemRoot\System32;$env:SystemRoot"
    & $pythonExecutable -m PyInstaller --noconfirm --clean --onefile --windowed --name 'ECHOES Launcher' --icon $icon --add-data "$icon;." --hidden-import psutil --distpath . --workpath tools/launcher-build --specpath tools launcher/main.py
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
} finally { $env:PATH = $savedBuildPath }
