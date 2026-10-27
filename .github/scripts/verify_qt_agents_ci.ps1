#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$installation = & $vswhere -latest -products '*' -property installationPath
if (-not $installation) { throw 'Visual Studio is required' }
$vcvars = Join-Path $installation 'VC\Auxiliary\Build\vcvars64.bat'
$environment = & cmd.exe /d /s /c "`"$vcvars`" 10.0.26100.0 -vcvars_ver=14.50.35717 >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'The pinned MSVC/Windows SDK environment is unavailable' }
foreach ($line in $environment) {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
# vcvars can put bundled CMake/Ninja ahead of the locked Python tools.
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
& (Join-Path $PSScriptRoot 'verify_qt_agents.ps1')
