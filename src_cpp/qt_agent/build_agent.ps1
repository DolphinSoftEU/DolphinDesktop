# Shared, pinned Qt agent build. Run from an x64 MSVC developer shell.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('5', '6')]
    [string]$QtMajor,
    [Parameter(Mandatory = $true)]
    [string]$QtRoot
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $QtRoot -PathType Container)) {
    throw "Qt $QtMajor SDK not found: $QtRoot"
}

$versions = @{
    '5' = @{ Qt = '5.15.2'; Msvc = '19.29.'; MsvcLabel = 'MSVC 14.29.30133' }
    '6' = @{ Qt = '6.11.1'; Msvc = '19.44.'; MsvcLabel = 'MSVC 14.44.35207' }
}
$expected = $versions[$QtMajor]
$qtConfig = Join-Path $QtRoot "lib\cmake\Qt${QtMajor}Core\Qt${QtMajor}CoreConfigVersion.cmake"
if (-not (Test-Path -LiteralPath $qtConfig -PathType Leaf)) {
    throw "Qt $QtMajor Core CMake package not found under $QtRoot"
}
$qtConfigText = Get-Content -LiteralPath $qtConfig -Raw
if ($qtConfigText -notmatch [regex]::Escape($expected.Qt)) {
    throw "Expected Qt $($expected.Qt), but the SDK at $QtRoot reports a different version"
}

$cl = Get-Command cl.exe -ErrorAction SilentlyContinue
if ($null -eq $cl) { throw 'cl.exe is missing; run this script from an x64 MSVC developer shell' }
$compilerLine = (& cl.exe 2>&1 | Select-Object -First 1 | Out-String).Trim()
if ($compilerLine -notmatch [regex]::Escape("Version $($expected.Msvc)")) {
    throw "Expected $($expected.MsvcLabel), found: $compilerLine"
}
$cmake = Get-Command cmake.exe -ErrorAction SilentlyContinue
$ninja = Get-Command ninja.exe -ErrorAction SilentlyContinue
if ($null -eq $cmake) { throw 'cmake.exe is missing from PATH' }
if ($null -eq $ninja) { throw 'ninja.exe is missing from PATH' }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceStatus = & git -C $repoRoot status --porcelain --untracked-files=all -- src_cpp/qt_agent
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the source revision with git' }
if ($sourceStatus) {
    throw 'Build input is modified or untracked. Commit src_cpp/qt_agent first so the artifact can be linked to an approved source commit.'
}
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'Unable to resolve the source commit for this build'
}

$buildDir = Join-Path $env:TEMP "dolphin-qt-agent-qt$QtMajor-$sourceCommit-$([guid]::NewGuid().ToString('N'))"
$cmakeArgs = @(
    '-S', $PSScriptRoot,
    '-B', $buildDir,
    '-G', 'Ninja',
    "-DCMAKE_MAKE_PROGRAM=$($ninja.Source)",
    "-DCMAKE_PREFIX_PATH=$QtRoot",
    "-DDOLPHIN_QT_MAJOR=$QtMajor",
    '-DCMAKE_BUILD_TYPE=Release'
)
& $cmake.Source @cmakeArgs
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed (exit $LASTEXITCODE)" }
& $cmake.Source --build $buildDir --config Release
if ($LASTEXITCODE -ne 0) { throw "Qt agent build failed (exit $LASTEXITCODE)" }

$dllName = "dolphin_qt${QtMajor}_agent.dll"
$dllPath = Join-Path $repoRoot "src\dolphin_desktop\_qt_agent\$dllName"
if (-not (Test-Path -LiteralPath $dllPath -PathType Leaf)) {
    throw "Build completed without producing $dllPath"
}
$exports = (& dumpbin.exe /nologo /exports $dllPath 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or
    $exports -notmatch 'dolphin_qt_agent_start_v2' -or
    $exports -notmatch 'dolphin_qt_agent_stop_v2') {
    throw "$dllName is missing the authenticated start/stop exports"
}

$manifestPath = Join-Path $repoRoot 'src\dolphin_desktop\_qt_agent\agent_manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$hash = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $dllPath).Length
$entry = [pscustomobject][ordered]@{
    sha256 = $hash
    size = $size
    protocol_version = 2
    source_commit = $sourceCommit
    qt_version = $expected.Qt
    msvc_toolset = $expected.MsvcLabel
}
$manifest | Add-Member -NotePropertyName $dllName -NotePropertyValue $entry -Force
$manifestJson = $manifest | ConvertTo-Json -Depth 5
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath, $manifestJson + [Environment]::NewLine, $utf8NoBom)

Write-Host "Built $dllName from $sourceCommit" -ForegroundColor Green
Write-Host "SHA-256: $hash ($size bytes)"
