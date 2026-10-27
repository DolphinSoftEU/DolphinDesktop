#requires -Version 7.0
# Dot-source to select the locked compiler independently of runner VS servicing.
param([Parameter(Mandatory = $true)][string]$ToolchainRoot)
$ErrorActionPreference = 'Stop'
$lockPath = Join-Path $PSScriptRoot '..\toolchains\msvc.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$installed = Get-Content -LiteralPath (Join-Path $ToolchainRoot 'toolchain.json') -Raw | ConvertFrom-Json
if (($installed | ConvertTo-Json -Depth 8 -Compress) -cne
    ($lock | ConvertTo-Json -Depth 8 -Compress)) {
    throw 'Installed MSVC payloads do not match the checked-in toolchain lock'
}
$tools = (Resolve-Path (Join-Path $ToolchainRoot "VC\Tools\MSVC\$($lock.tools_directory)")).Path
$sdk = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots').KitsRoot10
$version = $lock.windows_sdk
foreach ($relative in @("Include\$version\ucrt", "Include\$version\shared", "Include\$version\um",
                        "Lib\$version\ucrt\x64", "Lib\$version\um\x64", "bin\$version\x64")) {
    if (-not (Test-Path -LiteralPath (Join-Path $sdk $relative) -PathType Container)) {
        throw "Windows SDK $version is incomplete: $relative"
    }
}
$env:VCToolsInstallDir = "$tools\"
$env:VCToolsVersion = $lock.tools_directory
$env:WindowsSdkDir = $sdk
$env:WindowsSDKVersion = "$version\"
$env:VSCMD_ARG_TGT_ARCH = 'x64'
$env:INCLUDE = "$tools\include;$sdk\Include\$version\ucrt;$sdk\Include\$version\shared;$sdk\Include\$version\um;$sdk\Include\$version\winrt"
$env:LIB = "$tools\lib\x64;$sdk\Lib\$version\ucrt\x64;$sdk\Lib\$version\um\x64"
$env:PATH = "$tools\bin\Hostx64\x64;$sdk\bin\$version\x64;$env:PATH"
$compiler = (& cl.exe 2>&1 | Out-String)
if ($compiler -notmatch ('(?<![0-9.])' + [regex]::Escape($lock.compiler) + '(?![0-9.])')) {
    throw "Expected locked MSVC $($lock.compiler), found: $compiler"
}
Write-Host "Selected downloaded MSVC $($lock.compiler): $tools"
