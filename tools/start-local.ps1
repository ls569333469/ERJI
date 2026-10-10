param([switch]$SkipWorker,[switch]$EnableNotify)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'local_start_helpers.ps1')
Set-Location $projectRoot
$sharedRoot = (Resolve-Path (Join-Path $projectRoot '..\..\..')).Path
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if(-not (Test-Path -LiteralPath $pythonExe)){$pythonExe=Join-Path $sharedRoot '.venv\Scripts\python.exe'}
$nextScript = Join-Path $projectRoot 'node_modules\next\dist\bin\next'
if(-not (Test-Path -LiteralPath $nextScript)){throw 'Install this checkout dependencies with npm.cmd ci before starting; shared Next.js dependencies can load a second React instance.'}
# Match worker restart: shared database settings first, checkout overrides last.
foreach($envFile in @((Join-Path $sharedRoot '.env'),(Join-Path $projectRoot '.env'))){
 if(Test-Path -LiteralPath $envFile){
  foreach($line in Get-Content -LiteralPath $envFile){
   if($line -match '^\s*([^#=\s]+)=(.*)$'){
    [Environment]::SetEnvironmentVariable($matches[1],$matches[2].Trim('"').Trim("'"),'Process')
   }
  }
 }
}
foreach($p39DisabledKey in @('ERJI_CMC_KEY_FILE','ERJI_BYKARANTELI_KEY_FILE')){[Environment]::SetEnvironmentVariable($p39DisabledKey,$null,'Process')}
$dbTool = Join-Path $sharedRoot 'tools\local_db.py'
if(-not (Test-Path -LiteralPath $dbTool)){$dbTool=Join-Path $projectRoot 'tools\local_db.py'}
if(-not (Test-Path -LiteralPath $pythonExe) -or -not (Test-Path -LiteralPath $dbTool)){throw 'Missing Python/database prerequisite'}
foreach($port in @(18770,18771,18772)){Assert-FreePort $port}
$runtimeDir=Join-Path $projectRoot '.runtime'
$env:ERJI_RUN_ID=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$runDir=Join-Path $runtimeDir ('runs\'+$env:ERJI_RUN_ID)
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$processes=@();$startStatus='starting';Save-StartManifest
try {
& $pythonExe $dbTool start
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed' }
& $pythonExe -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
$runtimeDir=Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$processes=@()
$env:ERJI_BUDGET_OWNER='1'
$budgetProcess=Start-Process -FilePath $pythonExe -ArgumentList @('-m','services.worker.main','--role','budget') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir 'worker-budget.out.log') -RedirectStandardError (Join-Path $runDir 'worker-budget.err.log')
Record-Started 'worker-budget' $budgetProcess
Wait-LocalReady 'http://127.0.0.1:18772/health' $budgetProcess
$targets=@(
  @{Name='api';Exe=$pythonExe;Arguments=@('-m','uvicorn','services.api.main:app','--host','127.0.0.1','--port','18771');Port=18771},
  @{Name='web';Exe=(Get-Command node).Source;Arguments=@($nextScript,'dev','apps/web','--webpack','--hostname','127.0.0.1','--port','18770');Port=18770}
)
foreach($target in $targets) {
  $socket=[System.Net.Sockets.TcpClient]::new()
  try { $socket.Connect('127.0.0.1',$target.Port); throw "Port $($target.Port) already in use; reuse current ERJI process or stop it explicitly." }
  catch [System.Net.Sockets.SocketException] {}
  finally { $socket.Dispose() }
  $p=Start-Process -FilePath $target.Exe -ArgumentList $target.Arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "$($target.Name).out.log") -RedirectStandardError (Join-Path $runDir "$($target.Name).err.log")
  Record-Started $target.Name $p
  Wait-LocalReady $(if($target.Name -eq 'api'){'http://127.0.0.1:18771/v1/health'}else{'http://127.0.0.1:18770/signals'}) $p
}
if(-not $SkipWorker) {
  # streams owns native liquidation/mark streams; without it the UI can only show stale snapshots.
  $roles=@('live','jobs','streams')
  if($EnableNotify){$roles+='notify'}
  foreach($role in $roles){
    $p=Start-Process -FilePath $pythonExe -ArgumentList @('-m','services.worker.main','--role',$role) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "worker-$role.out.log") -RedirectStandardError (Join-Path $runDir "worker-$role.err.log")
    Record-Started "worker-$role" $p
    Wait-WorkerReady $role $p
  }
}
$startStatus='ready';Save-StartManifest
} catch {
 $startStatus='failed';Save-StartManifest;Stop-NewlyStarted;throw
}
Write-Output 'ERJI: http://127.0.0.1:18770/signals'
Write-Output 'API:  http://127.0.0.1:18771/docs'
