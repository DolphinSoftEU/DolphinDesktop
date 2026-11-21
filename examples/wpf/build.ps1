# Build the standalone WPF sample with the .NET 10 SDK.
$project = Join-Path $PSScriptRoot 'WpfSampleApp/WpfSampleApp.csproj'
dotnet build $project -c Debug
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Run: uv run --no-sync pytest examples/wpf/ -v'
