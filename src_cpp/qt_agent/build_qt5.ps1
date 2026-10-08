# Build the authenticated Qt 5 agent from a clean source commit.
param([string]$QtRoot = $env:DOLPHIN_QT5_ROOT)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build_agent.ps1') -QtMajor 5 -QtRoot $QtRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
