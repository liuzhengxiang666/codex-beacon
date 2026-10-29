[CmdletBinding()]
param([switch]$DryRun, [ValidateSet('auto','ask','manual')][string]$PanelMode)

$ErrorActionPreference = 'Stop'
$panelRoot = Split-Path -Parent $PSScriptRoot
$panelPython = (Get-Command python -ErrorAction Stop).Source
$panelCodex = (Get-Command codex -ErrorAction Stop).Source
$panelServer = Join-Path $panelRoot 'server.py'
if (-not (Test-Path -LiteralPath $panelServer)) { throw 'server.py is missing. Run from a complete checkout.' }
$panelTemp = Join-Path ([System.IO.Path]::GetTempPath()) 'codex-execution-panel'
$panelArguments = @('mcp', 'add', 'execution-panel', '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONUTF8=1', '--env', "TEMP=$panelTemp", '--env', "TMP=$panelTemp", '--', $panelPython, '-B', '-u', $panelServer, 'stdio')
if ($DryRun) {
    [pscustomobject]@{ Command = $panelCodex; Arguments = $panelArguments; StateDirectory = (Join-Path $panelRoot 'state') }
    return
}
$panelSetup = Join-Path $panelRoot 'scripts/configure-panel.py'
if ($PanelMode) { & $panelPython -B $panelSetup --mode $PanelMode } else { & $panelPython -B $panelSetup }
if ($LASTEXITCODE -ne 0) { throw 'Execution-panel preference was not configured. Registration was not changed.' }
New-Item -ItemType Directory -Path $panelTemp -Force | Out-Null
$panelConfigRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$panelConfig = Join-Path $panelConfigRoot 'config.toml'
if (Test-Path -LiteralPath $panelConfig) {
    $panelBackup = Join-Path $panelRoot 'work/config-backups'
    New-Item -ItemType Directory -Path $panelBackup -Force | Out-Null
    Copy-Item -LiteralPath $panelConfig -Destination (Join-Path $panelBackup ('config-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.toml'))
}
& $panelCodex @panelArguments
if ($LASTEXITCODE -ne 0) { throw 'MCP registration failed. If present, the config backup is in work/config-backups/.' }
Write-Output "Registered execution-panel from $panelRoot. Reload the MCP connection; the client was not restarted."
