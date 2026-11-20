# Windows PowerShell scripts

`scripts/windows/deploy.ps1` and `demo.ps1` should use safe paths based on `$PSScriptRoot`, `python -m deploysafe`, `[CmdletBinding()]`, `$ErrorActionPreference='Stop'`, explicit argument arrays and `$LASTEXITCODE`. No administrator requirement, Scheduled Tasks, Windows service changes, execution policy changes, registry writes, firewall changes or external downloads. Use PowerShell only as an optional wrapper: Python manages subprocesses. Provide exact Windows commands and caution that cross-platform scripts need testing on Windows.
