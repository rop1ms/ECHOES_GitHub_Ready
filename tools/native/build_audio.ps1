$ErrorActionPreference = 'Stop'
$audioRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$audioCompiler = 'C:\msys64\ucrt64\bin\g++.exe'
$audioBuildPath = $env:PATH
try {
    $env:PATH = (Split-Path -Parent $audioCompiler) + ';' + $env:PATH
    & $audioCompiler -std=c++17 -O2 -shared -static -static-libgcc -static-libstdc++ -s -Wall -Wextra -Wno-misleading-indentation -o (Join-Path $audioRoot 'app/echoes_audio.dll') (Join-Path $PSScriptRoot 'audio.cpp')
    if ($LASTEXITCODE -ne 0) { throw 'Native audio build failed' }
} finally { $env:PATH = $audioBuildPath }
