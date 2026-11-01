$ErrorActionPreference = 'Stop'
$montageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$qtRoot = 'C:\msys64\ucrt64'
$montageOut = Join-Path $montageRoot 'app\montage_native'
New-Item -ItemType Directory -Force $montageOut | Out-Null
$montageOldPath = $env:PATH
# Native tools write warnings to stderr; PowerShell 5.1 must not treat them as failures.
$ErrorActionPreference = 'Continue'
try {
    $env:PATH = "$qtRoot\bin;" + $env:PATH
    & "$qtRoot\bin\g++.exe" -std=c++20 -O2 -Wall -Wextra -Wno-misleading-indentation -finput-charset=UTF-8 "-I$qtRoot\include\qt6" "-I$qtRoot\include\qt6\QtWidgets" "-I$qtRoot\include\qt6\QtGui" "-I$qtRoot\include\qt6\QtCore" "-I$qtRoot\include\qt6\QtTest" (Join-Path $PSScriptRoot 'montage.cpp') "-L$qtRoot\lib" -lQt6Widgets -lQt6Gui -lQt6Test -lQt6Core -o (Join-Path $montageOut 'echoes_montage.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Montage C++ compilation failed' }
    & "$qtRoot\bin\windeployqt.exe" --no-translations --no-opengl-sw --no-system-d3d-compiler --no-compiler-runtime (Join-Path $montageOut 'echoes_montage.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Qt deployment failed' }
    # Deploy the transitive MinGW and Qt dependencies, without requiring MSYS2 on the user's PC.
    $montagePending = [System.Collections.Generic.Queue[string]]::new()
    Get-ChildItem -LiteralPath $montageOut -Recurse -File | Where-Object { $_.Extension -in '.exe','.dll' } | ForEach-Object { $montagePending.Enqueue($_.FullName) }
    $montageSeen = @{}
    while ($montagePending.Count -gt 0) {
        $montageFile = $montagePending.Dequeue()
        if ($montageSeen.ContainsKey($montageFile)) { continue }
        $montageSeen[$montageFile] = $true
        $montageDeps = & "$qtRoot\bin\objdump.exe" -p $montageFile
        foreach ($montageLine in $montageDeps) {
            if ($montageLine -match 'DLL Name:\s*(\S+)') {
                $montageDll = $Matches[1]
                $montageSource = Join-Path "$qtRoot\bin" $montageDll
                $montageTarget = Join-Path $montageOut $montageDll
                if ((Test-Path -LiteralPath $montageSource) -and !(Test-Path -LiteralPath $montageTarget)) {
                    Copy-Item -LiteralPath $montageSource -Destination $montageTarget
                    $montagePending.Enqueue($montageTarget)
                }
            }
        }
    }
    # Qt's plugin search is private to this native process.
    Set-Content -LiteralPath (Join-Path $montageOut 'qt.conf') -Encoding ascii -Value "[Paths]`nPlugins=."
    Write-Output "Built: $montageOut\echoes_montage.exe"
} finally { $env:PATH = $montageOldPath }
