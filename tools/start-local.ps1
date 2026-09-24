param([switch]$SkipWorker)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonExe tools\local_db.py start
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed' }
& $pythonExe -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
$runtimeDir=Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$processes=@()
$targets=@(
  @{Name='api';Exe=$pythonExe;Arguments=@('-m','uvicorn','services.api.main:app','--host','127.0.0.1','--port','18771');Port=18771},
  @{Name='web';Exe=(Get-Command node).Source;Arguments=@('node_modules/next/dist/bin/next','dev','apps/web','--hostname','127.0.0.1','--port','18770');Port=18770}
)
foreach($target in $targets) {
  $socket=[System.Net.Sockets.TcpClient]::new()
  try { $socket.Connect('127.0.0.1',$target.Port); throw "Port $($target.Port) already in use; reuse current ERJI process or stop it explicitly." }
  catch [System.Net.Sockets.SocketException] {}
  finally { $socket.Dispose() }
  $p=Start-Process -FilePath $target.Exe -ArgumentList $target.Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "$($target.Name).out.log") -RedirectStandardError (Join-Path $runtimeDir "$($target.Name).err.log")
  $processes+=@{name=$target.Name;pid=$p.Id;started_ticks=$p.StartTime.ToUniversalTime().Ticks.ToString()}
}
if(-not $SkipWorker) {
  # streams owns native liquidation/mark streams; without it the UI can only show stale snapshots.
  foreach($role in @('live','jobs','streams','notify')){
    $p=Start-Process -FilePath $pythonExe -ArgumentList @('-m','services.worker.main','--role',$role) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "worker-$role.out.log") -RedirectStandardError (Join-Path $runtimeDir "worker-$role.err.log")
    $processes+=@{name="worker-$role";pid=$p.Id;started_ticks=$p.StartTime.ToUniversalTime().Ticks.ToString()}
  }
}
$processes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir 'processes.json') -Encoding utf8
Write-Output 'ERJI: http://127.0.0.1:18770/markets'
Write-Output 'API:  http://127.0.0.1:18771/docs'
