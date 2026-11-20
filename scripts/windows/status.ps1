[CmdletBinding()]
param([switch]$Json)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Repo = Join-Path $PSScriptRoot '../..'
$Arguments = @('-B', '-m', 'deploysafe', 'status')
if ($Json) {
    $Arguments += '--json'
}
Push-Location -LiteralPath $Repo
try {
    & python @Arguments
    $Code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Code
