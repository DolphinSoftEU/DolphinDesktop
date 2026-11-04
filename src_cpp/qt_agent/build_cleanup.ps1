#requires -Version 7.0

function ConvertTo-QtAgentBuildPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ($IsWindows) {
        $Path = $Path.Replace('/', '\')
        if ($Path.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
            $Path = '\\' + $Path.Substring(8)
        } elseif ($Path.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
            $Path = $Path.Substring(4)
        }
    }
    return [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($Path))
}

function Remove-QtAgentBuildDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Junctions = @(),
        [System.Management.Automation.ErrorRecord]$PrimaryError
    )
    try {
        $cleanupPath = ConvertTo-QtAgentBuildPath $Path
        $expectedParent = ConvertTo-QtAgentBuildPath $Parent
        $actualParent = ConvertTo-QtAgentBuildPath (Split-Path $cleanupPath -Parent)
        $comparison = if ($IsWindows) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
        if (-not [string]::Equals($actualParent, $expectedParent, $comparison) -or
            -not [string]::Equals((Split-Path $cleanupPath -Leaf), $Name, $comparison)) {
            throw "Refusing to remove unexpected temporary directory: $cleanupPath (expected parent: $expectedParent)"
        }
        # SDK targets live outside the disposable checkout. Unlink each checked
        # junction before removing the checkout, without recursing into the SDK.
        foreach ($link in $Junctions) {
            $linkPath = ConvertTo-QtAgentBuildPath $link
            if (-not $linkPath.StartsWith($cleanupPath + [IO.Path]::DirectorySeparatorChar, $comparison)) {
                throw "Refusing to remove unexpected SDK junction: $linkPath"
            }
            if (Test-Path -LiteralPath $linkPath) {
                $item = Get-Item -LiteralPath $linkPath
                if ($item.LinkType -ne 'Junction') { throw "Expected an SDK junction: $linkPath" }
                Remove-Item -LiteralPath $linkPath -Force -ErrorAction Stop
            }
        }
        if (Test-Path -LiteralPath $cleanupPath) {
            Remove-Item -LiteralPath $cleanupPath -Recurse -Force -ErrorAction Stop
        }
    } catch {
        if ($null -eq $PrimaryError) { throw }
        # A secondary cleanup failure must not replace the build/verification
        # error already being rethrown, even with WarningPreference = Stop.
        Write-Warning -WarningAction Continue "Cleanup failed for '$Path': $($_.Exception.Message). Original error preserved: $($PrimaryError.Exception.Message)"
    }
}
