[CmdletBinding()]
param([string]$Scenario)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Repo = Join-Path $PSScriptRoot '../..'
$Arguments = @('-B', '-m', 'deploysafe', 'demo')
if ($Scenario) {
    $Arguments += @('--scenario', $Scenario)
} else {
    $Arguments += '--all'
}
Push-Location -LiteralPath $Repo
try {
    & python @Arguments
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Code
