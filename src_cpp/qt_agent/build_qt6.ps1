# Build the authenticated Qt 6 agent from a clean source commit.
param([string]$QtRoot = $env:DOLPHIN_QT6_ROOT)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build_agent.ps1') -QtMajor 6 -QtRoot $QtRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
