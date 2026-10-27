#requires -Version 7.0
$ErrorActionPreference = 'Stop'
if (-not $env:DOLPHIN_MSVC_ROOT) { throw 'DOLPHIN_MSVC_ROOT must identify the downloaded compiler' }
. (Join-Path $PSScriptRoot 'enter_msvc.ps1') -ToolchainRoot $env:DOLPHIN_MSVC_ROOT
# Select the locked Python build tools before any runner-bundled CMake/Ninja.
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
& (Join-Path $PSScriptRoot 'verify_qt_agents.ps1')
