[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$HealthOnly,
    [switch]$DryRun,
    [string]$TunnelJson,
    [double]$Timeout = 90,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
if ($Help) {
    @'
Usage: project.ps1 [-CheckOnly | -HealthOnly] [-DryRun] [-TunnelJson <path>] [-Timeout <seconds>] [-Help]

  (no switch)  start or reuse the current frontend at 5174
  -CheckOnly   offline prerequisite check only; no HTTP, no process start
  -HealthOnly  read-only GET /api/v1/health through the current tunnel
  -DryRun      validate then print the start plan; no process start
  -TunnelJson  override the external tunnel JSON (only local_port/remote_target are read)
  -Timeout     frontend readiness timeout in seconds (default 90)
'@
    exit 0
}
if ($CheckOnly -and $HealthOnly) {
    throw 'Choose -CheckOnly or -HealthOnly, not both.'
}

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$Python = Join-Path $RepoRoot '.local-cache\auth-venv\Scripts\python.exe'
$Launcher = Join-Path $RepoRoot 'scripts\launch_frontend.py'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Existing project Python environment is missing: $Python. No installation attempted."
}
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "Canonical frontend launcher is missing: $Launcher"
}

$LauncherArgs = @('-B', $Launcher)
if ($CheckOnly) { $LauncherArgs += '--check' }
if ($HealthOnly) { $LauncherArgs += '--health' }
if ($DryRun) { $LauncherArgs += '--dry-run' }
if ($PSBoundParameters.ContainsKey('TunnelJson')) {
    if ([string]::IsNullOrWhiteSpace($TunnelJson)) { throw '-TunnelJson must not be empty.' }
    $LauncherArgs += @('--tunnel-json', $TunnelJson)
}
if ($PSBoundParameters.ContainsKey('Timeout')) {
    if ($Timeout -le 0) { throw '-Timeout must be greater than zero.' }
    $LauncherArgs += @('--timeout', [string]$Timeout)
}

& $Python @LauncherArgs
exit $LASTEXITCODE
