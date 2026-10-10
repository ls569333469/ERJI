param([switch]$StopDatabase)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$pidFile=Join-Path $projectRoot '.runtime\\processes.json'

function Get-DescendantProcessIds([int]$RootPid) {
  $snapshot=Get-CimInstance Win32_Process
  $pending=[System.Collections.Generic.Queue[int]]::new()
  $pending.Enqueue($RootPid)
  $result=[System.Collections.Generic.List[int]]::new()
  while($pending.Count -gt 0) {
    $parent=$pending.Dequeue()
    foreach($child in $snapshot | Where-Object { $_.ParentProcessId -eq $parent }) {
      $pending.Enqueue([int]$child.ProcessId)
      $result.Add([int]$child.ProcessId)
    }
  }
  return $result
}

if(Test-Path -LiteralPath $pidFile) {
  $tracked=Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
  foreach($item in $tracked) {
    $proc=Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if(-not $proc){continue}
    if(-not $item.started_ticks -or $proc.StartTime.ToUniversalTime().Ticks.ToString() -ne $item.started_ticks) {
      Write-Warning "Process $($item.name) start identity does not match; skipped.";continue
    }
    $descendants=@(Get-DescendantProcessIds -RootPid $item.pid)
    [array]::Reverse($descendants)
    foreach($childPid in $descendants) {
      Stop-Process -Id $childPid -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $item.pid -ErrorAction Stop
  }
}
if($StopDatabase) {
  Push-Location $projectRoot
  try { & '.\\.venv\\Scripts\\python.exe' tools\\local_db.py stop } finally { Pop-Location }
}
Write-Output 'Tracked ERJI application processes stopped. Data retained.'
