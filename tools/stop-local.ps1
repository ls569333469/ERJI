param([switch]$StopDatabase)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$pidFile=Join-Path $projectRoot '.runtime\\processes.json'
if(Test-Path -LiteralPath $pidFile) {
  $tracked=Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
  foreach($item in $tracked) {
    $proc=Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if(-not $proc){continue}
    if(-not $item.started_ticks -or $proc.StartTime.ToUniversalTime().Ticks.ToString() -ne $item.started_ticks) {
      Write-Warning "Process $($item.name) start identity does not match; skipped.";continue
    }
    Stop-Process -Id $item.pid -ErrorAction Stop
  }
}
if($StopDatabase) {
  Push-Location $projectRoot
  try { & '.\\.venv\\Scripts\\python.exe' tools\\local_db.py stop } finally { Pop-Location }
}
Write-Output 'Tracked ERJI application processes stopped. Data retained.'
