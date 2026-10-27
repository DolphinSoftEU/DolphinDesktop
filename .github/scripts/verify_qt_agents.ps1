#requires -Version 7.0
param(
    [string]$Revision = 'HEAD',
    [string]$Qt5Root = $env:DOLPHIN_QT5_ROOT,
    [string]$Qt6Root = $env:DOLPHIN_QT6_ROOT
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$evidence = Join-Path $repoRoot 'artifacts\qt-agent-reproducibility'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$scratch = Join-Path $env:TEMP "dolphin-rebuild-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $scratch | Out-Null
$archive = Join-Path $scratch 'source.zip'
& git -C $repoRoot archive --format=zip "--output=$archive" $Revision
if ($LASTEXITCODE -ne 0) { throw 'Unable to archive the requested source revision' }

# Both builds start from identical, clean Git inputs, including the shipped
# DLLs and manifest. Different checkout, build and SDK paths expose path leaks.
foreach ($location in @('short', 'second checkout with spaces and a longer path')) {
    $checkout = Join-Path $scratch $location
    Expand-Archive -LiteralPath $archive -DestinationPath $checkout
    & git -C $checkout init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Unable to initialize verification checkout' }
    & git -C $checkout add src_cpp/qt_agent
    if ($LASTEXITCODE -ne 0) { throw 'Unable to index native source for verification' }
    foreach ($major in @('5', '6')) {
        $sdk = if ($major -eq '5') { $Qt5Root } else { $Qt6Root }
        $sdkLink = Join-Path $checkout "qt$major sdk"
        New-Item -ItemType Junction -Path $sdkLink -Target (Resolve-Path $sdk).Path | Out-Null
        $package = Join-Path $checkout 'src\dolphin_desktop\_qt_agent'
        $files = @(
            (Join-Path $package "dolphin_qt${major}_agent.dll"),
            (Join-Path $package 'agent_manifest.json')
        )
        $before = @($files | ForEach-Object { (Get-FileHash -LiteralPath $_).Hash })
        $log = Join-Path $evidence "$location-qt$major.log"
        & (Join-Path $checkout 'src_cpp\qt_agent\build_agent.ps1') `
            -QtMajor $major -QtRoot $sdkLink -Verify *>&1 | Tee-Object -FilePath $log
        $after = @($files | ForEach-Object { (Get-FileHash -LiteralPath $_).Hash })
        if (Compare-Object $before $after) { throw 'Verification modified shipped inputs' }
    }
}
Write-Host "Both shipped ABIs reproduced in two clean locations. Logs: $evidence"
