param([ValidateSet('budget','api','live','jobs','streams')][string[]]$Roles=@('budget','api','live','jobs','streams'))
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'local_start_helpers.ps1')
$env:ERJI_BUDGET_OWNER='1'
$p33Root=Split-Path $PSScriptRoot -Parent
$p33Runtime=Join-Path $p33Root '.runtime'
$p33Shared=(Resolve-Path (Join-Path $p33Root '../../..')).Path
$p33Python=Join-Path $p33Shared '.venv/Scripts/python.exe'
foreach($p33EnvFile in @((Join-Path $p33Shared '.env'),(Join-Path $p33Root '.env'))){
 if(Test-Path -LiteralPath $p33EnvFile){foreach($p33Line in Get-Content -LiteralPath $p33EnvFile){
  if($p33Line -match '^\s*([^#=\s]+)=(.*)$'){
   [Environment]::SetEnvironmentVariable($matches[1],$matches[2].Trim('"').Trim("'"),'Process')
  }
 }}
}
foreach($p39DisabledKey in @('ERJI_CMC_KEY_FILE','ERJI_BYKARANTELI_KEY_FILE')){[Environment]::SetEnvironmentVariable($p39DisabledKey,$null,'Process')}
$p33Records=Get-Content (Join-Path $p33Runtime 'processes.json') -Raw | ConvertFrom-Json
$p33All=@(Get-CimInstance Win32_Process)
$p33Selected=@()
foreach($p33Role in $Roles){
 $p33Name=if($p33Role -eq 'api'){'api'}else{'worker-'+$p33Role}
 $p33Entry=@($p33Records | Where-Object name -eq $p33Name)
 if($p33Entry.Count -eq 0 -and $p33Role -eq 'budget'){
  $p33New=[pscustomobject]@{name='worker-budget';pid=0;started_ticks='0'}
  $p33Records=@($p33Records)+$p33New;$p33Entry=@($p33New)
 }
 if($p33Entry.Count -ne 1){throw "Expected one registered process for $p33Name"}
 $p33Entry=$p33Entry[0]
 $p33Pattern=if($p33Role -eq 'api'){'uvicorn services.api.main:app'}else{'services.worker.main --role '+$p33Role}
 $p33Native=if($p33Entry.pid -gt 0){$p33All | Where-Object ProcessId -eq $p33Entry.pid}else{$null}
 if($p33Native){
  $p33Process=Get-Process -Id $p33Entry.pid
  if($p33Process.StartTime.ToUniversalTime().Ticks.ToString() -ne $p33Entry.started_ticks -or $p33Native.CommandLine -notlike "*$p33Pattern*"){throw "Process identity changed for $p33Name"}
 }elseif($p33All | Where-Object {$_.CommandLine -like "*$p33Pattern*" -and $_.Name -like 'python*'}){throw "Unregistered running process for $p33Name"}
 $p33Selected+=@{Role=$p33Role;Entry=$p33Entry;Native=$p33Native;Pattern=$p33Pattern}
}
$p33Order=@('budget','api','live','jobs','streams')
$p33Selected=@($p33Selected | Sort-Object {$p33Order.IndexOf($_.Role)})
if('budget' -notin $Roles){
 $p33BudgetHealth=Invoke-RestMethod -Uri 'http://127.0.0.1:18772/health' -TimeoutSec 3
 if($p33BudgetHealth.status -ne 'ok'){throw 'Existing budget owner is not ready; no selected processes stopped.'}
}
if(@($Roles | Where-Object {$_ -in @('live','jobs','streams')}).Count -and 'api' -notin $Roles){
 $null=Invoke-RestMethod -Uri 'http://127.0.0.1:18771/v1/health' -TimeoutSec 3
}
foreach($p33Target in $p33Selected){
 if($p33Target.Native){
  Stop-OwnedProcess $p33Target.Entry
 }
}
# Start only after every selected old producer has stopped.
$runtimeDir=$p33Runtime
$env:ERJI_RUN_ID=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$runDir=Join-Path $runtimeDir ('runs\'+$env:ERJI_RUN_ID)
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$processes=@();$startStatus='starting'
try {
 foreach($p33Role in @('budget','api') | Where-Object {$_ -in $Roles}){Assert-FreePort $(if($p33Role -eq 'budget'){18772}else{18771})}
foreach($p33Target in $p33Selected){
 $p33Arguments=if($p33Target.Role -eq 'api'){@('-m','uvicorn','services.api.main:app','--host','127.0.0.1','--port','18771')}else{@('-m','services.worker.main','--role',$p33Target.Role)}
 $p33Process=Start-Process -FilePath $p33Python -ArgumentList $p33Arguments -WorkingDirectory $p33Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir ($p33Target.Entry.name+'.out.log')) -RedirectStandardError (Join-Path $runDir ($p33Target.Entry.name+'.err.log'))
 $p33Target.Entry.pid=$p33Process.Id
 $p33Target.Entry.started_ticks=$p33Process.StartTime.ToUniversalTime().Ticks.ToString()
 $p33Target.Entry | Add-Member -NotePropertyName run_id -NotePropertyValue $env:ERJI_RUN_ID -Force
 $p33Target.Entry | Add-Member -NotePropertyName log_dir -NotePropertyValue $runDir -Force
 $processes+=@{name=$p33Target.Entry.name;pid=$p33Process.Id;started_ticks=$p33Target.Entry.started_ticks;run_id=$env:ERJI_RUN_ID;log_dir=$runDir}
 $p33Records | ConvertTo-Json | Set-Content (Join-Path $p33Runtime 'processes.json') -Encoding utf8
 @{run_id=$env:ERJI_RUN_ID;status=$startStatus;processes=$processes} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir 'startup.json') -Encoding utf8
 if($p33Target.Role -eq 'budget'){Wait-LocalReady 'http://127.0.0.1:18772/health' $p33Process}
 elseif($p33Target.Role -eq 'api'){Wait-LocalReady 'http://127.0.0.1:18771/v1/health' $p33Process}
 else{Wait-WorkerReady $p33Target.Role $p33Process}
}
$startStatus='ready'
@{run_id=$env:ERJI_RUN_ID;status=$startStatus;processes=$processes} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir 'startup.json') -Encoding utf8
} catch {
 Stop-NewlyStarted
 @{run_id=$env:ERJI_RUN_ID;status='failed';processes=$processes} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir 'startup.json') -Encoding utf8
 throw
}
$p33Selected | ForEach-Object {$_.Entry} | Select-Object name,pid | ConvertTo-Json -Compress
