# Build the authenticated Qt 6 agent from a clean source commit.
param([string]$QtRoot = $env:DOLPHIN_QT6_ROOT, [switch]$Verify)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build_agent.ps1') -QtMajor 6 -QtRoot $QtRoot -Verify:$Verify
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
