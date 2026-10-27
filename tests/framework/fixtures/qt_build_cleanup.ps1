param([string]$Repository, [string]$Sandbox, [string]$Scenario)
$ErrorActionPreference = 'Stop'
. (Join-Path $Repository 'src_cpp/qt_agent/build_cleanup.ps1')
$name = 'dolphin-test-directory'
$directory = Join-Path $Sandbox $name
New-Item -ItemType Directory -Path $directory | Out-Null

if ($Scenario -eq 'unc') {
    $expected = ConvertTo-QtAgentBuildPath '\\server\share\folder'
    foreach ($spelling in @('\\server\share\folder\', '//SERVER/share/folder/./', '\\?\UNC\server\share\folder\')) {
        if (-not [string]::Equals((ConvertTo-QtAgentBuildPath $spelling), $expected,
                                 [StringComparison]::OrdinalIgnoreCase)) {
            throw "UNC paths differ: $spelling"
        }
    }
    Remove-QtAgentBuildDirectory -Path $directory -Parent $Sandbox -Name $name
    Write-Output 'PASS'
    return
}

if ($Scenario -in @('trailing', 'slashes', 'case', 'extended', 'dots')) {
    $parent = switch ($Scenario) {
        'trailing' { $Sandbox + '\' }
        'slashes' { $Sandbox.Replace('\', '/') + '/' }
        'case' { $Sandbox.ToUpperInvariant() }
        'extended' { '\\?\' + $Sandbox + '\' }
        'dots' { $Sandbox + '\.\' }
    }
    Remove-QtAgentBuildDirectory -Path ($directory + '\') -Parent $parent -Name $name
    if (Test-Path $directory) { throw 'Equivalent Windows paths did not clean up' }
    if ((ConvertTo-QtAgentBuildPath 'C:\') -ne 'C:\') { throw 'Drive root was truncated' }
    Write-Output 'PASS'
    return
}
if ($Scenario -in @('wrong-parent', 'wrong-name', 'outside-junction')) {
    $arguments = @{ Path = $directory; Parent = $Sandbox; Name = $name }
    switch ($Scenario) {
        'wrong-parent' { $arguments.Parent = $Sandbox + '-other' }
        'wrong-name' { $arguments.Name = 'different-directory' }
        'outside-junction' { $arguments.Junctions = @(Join-Path $Sandbox 'outside') }
    }
    try { Remove-QtAgentBuildDirectory @arguments; throw 'Unsafe cleanup was accepted' }
    catch { if ($_.Exception.Message -notmatch '^Refusing to remove unexpected') { throw } }
    if (-not (Test-Path $directory)) { throw 'Guard removed the directory' }
    Remove-QtAgentBuildDirectory -Path $directory -Parent $Sandbox -Name $name
    Write-Output 'PASS'
    return
}
if ($Scenario -eq 'junction') {
    $sdk = Join-Path $Sandbox 'installed-sdk'
    New-Item -ItemType Directory -Path $sdk | Out-Null
    'keep' | Set-Content (Join-Path $sdk 'library.lib')
    $link = Join-Path $directory 'sdk-link'
    New-Item -ItemType Junction -Path $link -Target $sdk | Out-Null
    Remove-QtAgentBuildDirectory -Path $directory -Parent ($Sandbox + '\') -Name $name -Junctions $link
    if ((Test-Path $directory) -or -not (Test-Path (Join-Path $sdk 'library.lib'))) {
        throw 'Cleanup removed an SDK target or left the checkout'
    }
    Write-Output 'PASS'
    return
}

# A deterministic deletion failure tests exception priority without changing
# ACLs, requiring elevation or relying on another process to hold a file open.
function Remove-Item { throw 'controlled cleanup failure' }
$WarningPreference = 'Stop'
try {
    if ($Scenario -eq 'cleanup-only-error') {
        try { Remove-QtAgentBuildDirectory -Path $directory -Parent $Sandbox -Name $name }
        catch {
            if ($_.Exception.Message -ne 'controlled cleanup failure') { throw }
            Write-Output 'PASS'
            return
        }
        throw 'Successful build ignored its cleanup failure'
    }
    if ($Scenario -in @('primary-error', 'primary-guard-error')) {
        $primary = $null
        $parent = if ($Scenario -eq 'primary-guard-error') { $Sandbox + '-different' } else { $Sandbox }
        try {
            try { throw 'original build failure' }
            catch { $primary = $_; throw }
            finally { Remove-QtAgentBuildDirectory -Path $directory -Parent $parent -Name $name -PrimaryError $primary }
        } catch {
            if ($_.Exception.Message -ne 'original build failure') { throw }
            Write-Output 'PASS'
            return
        }
    }
    $env:TEMP = $Sandbox + '\'
    if ($Scenario -eq 'verification-error') {
        try {
            & (Join-Path $Repository '.github/scripts/verify_qt_agents.ps1') -Revision 'nonexistent-cleanup-test-revision'
        } catch {
            if ($_.Exception.Message -ne 'Unable to archive the requested source revision') { throw }
            Write-Output 'PASS'
            return
        }
        throw 'Verification failure was lost'
    }
    if ($Scenario -eq 'build-and-log-error') {
        $qt = Join-Path $Sandbox 'qt'
        $core = Join-Path $qt 'lib/cmake/Qt5Core'
        New-Item -ItemType Directory -Path $core -Force | Out-Null
        '5.15.2' | Set-Content (Join-Path $core 'Qt5CoreConfigVersion.cmake')
        $cmakeShim = Join-Path $Sandbox 'cmake.ps1'
        @'
if ($args[0] -eq '--version') { 'cmake version 3.31.10' }
else {
    $buildIndex = [array]::IndexOf($args, '-B')
    New-Item -ItemType Directory -Path $args[$buildIndex + 1] | Out-Null
    & cmd.exe /c exit 17
}
'@ | Set-Content $cmakeShim
        $ninjaShim = Join-Path $Sandbox 'ninja.ps1'
        "'1.11.1.git.kitware.jobserver-1'" | Set-Content $ninjaShim
        function cl.exe { 'MSVC 19.50.35726' }
        function Get-Command {
            param($Name, $ErrorAction)
            if ($Name -eq 'cmake.exe') { return [pscustomobject]@{ Source = $cmakeShim } }
            if ($Name -eq 'ninja.exe') { return [pscustomobject]@{ Source = $ninjaShim } }
            Microsoft.PowerShell.Core\Get-Command $Name -ErrorAction $ErrorAction
        }
        function Add-Content { throw 'controlled diagnostics failure' }
        foreach ($variable in @('CL', '_CL_', 'LINK', '_LINK_', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS')) {
            Microsoft.PowerShell.Management\Remove-Item -LiteralPath "Env:$variable" -ErrorAction SilentlyContinue
        }
        $env:VCToolsVersion = '14.50.35717'
        $env:WindowsSDKVersion = '10.0.26100.0\'
        $env:VSCMD_ARG_TGT_ARCH = 'x64'
        try {
            & (Join-Path $Repository 'src_cpp/qt_agent/build_agent.ps1') -QtMajor 5 -QtRoot $qt -LogDirectory (Join-Path $Sandbox 'logs')
        } catch {
            if ($_.Exception.Message -ne 'CMake configure failed (exit 17)') { throw }
            Write-Output 'PASS'
            return
        }
        throw 'Build failure was lost'
    }
    throw "Unknown cleanup test: $Scenario"
} finally {
    Microsoft.PowerShell.Management\Remove-Item Function:\Remove-Item
    # Test-induced failures deliberately left disposable directories; remove
    # only the exact fixture children through the checked production helper.
    Get-ChildItem -LiteralPath $Sandbox -Directory | Where-Object {
        $_.Name -eq $name -or $_.Name -like 'dolphin-rebuild-*' -or $_.Name -like 'dolphin-qt-agent-*'
    } | ForEach-Object {
        Remove-QtAgentBuildDirectory -Path $_.FullName -Parent $Sandbox -Name $_.Name
    }
}
