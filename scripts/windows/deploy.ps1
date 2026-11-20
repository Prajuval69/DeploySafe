[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Release
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Repo = Join-Path $PSScriptRoot '../..'
$Arguments = @('-B', '-m', 'deploysafe', 'deploy', '--release', $Release)
Push-Location -LiteralPath $Repo
try {
    & python @Arguments
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Code
