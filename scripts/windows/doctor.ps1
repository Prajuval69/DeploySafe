[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Repo = Join-Path $PSScriptRoot '../..'
$Arguments = @('-B', '-m', 'deploysafe', 'doctor')
Push-Location -LiteralPath $Repo
try {
    & python @Arguments
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Code
