# Build the IE/Trident WPF sample app.
# Usage: .\build.ps1
# Requires: .NET SDK with net48 support  (https://dot.net/download)
#           .NET Framework 4.8 (pre-installed on Windows 10/11)

$proj = Join-Path $PSScriptRoot "IESampleApp\IESampleApp.csproj"
dotnet build $proj -f net48 -c Debug
if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed. Ensure .NET SDK is installed: https://dot.net/download"
    exit 1
}
Write-Host "Build OK — run the tests with: pytest examples\legacy_ie\ -v"
