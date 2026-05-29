# Build the WebView2 login sample app.
# Usage: .\build.ps1
# Requires: .NET 8 SDK  (https://dot.net/download)
#           WebView2 Runtime (pre-installed on Windows 11 / Edge-updated Windows 10)

$proj = Join-Path $PSScriptRoot "WebView2LoginSample\WebView2LoginSample.csproj"
dotnet build $proj -c Debug
if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed. Install .NET 8 SDK: https://dot.net/download"
    exit 1
}
Write-Host "Build OK — run the tests with: pytest examples\webview2_sample\ -v"
