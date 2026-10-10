function Assert-FreePort([int]$Port) {
 $socket=[System.Net.Sockets.TcpClient]::new()
 try {$socket.Connect('127.0.0.1',$Port);throw "Port $Port already in use; no services started."}
 catch [System.Net.Sockets.SocketException] {}
 finally {$socket.Dispose()}
}
function Wait-LocalReady([string]$Url,[System.Diagnostics.Process]$Process,[int]$Seconds=60) {
 $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
 do {
  $Process.Refresh()
  if($Process.HasExited){throw "New process $($Process.Id) exited before readiness."}
  try { $result=Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing; if($result.StatusCode -eq 200){return} } catch {}
  Start-Sleep -Milliseconds 500
 } while([DateTime]::UtcNow -lt $deadline)
 throw "Readiness timeout for $Url"
}
function Save-StartManifest {
 $script:processes | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $script:runtimeDir 'processes.json') -Encoding utf8
 @{run_id=$env:ERJI_RUN_ID;status=$script:startStatus;processes=@($script:processes);updated_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $script:runDir 'startup.json') -Encoding utf8
}
function Record-Started([string]$Name,[System.Diagnostics.Process]$Process) {
 $script:processes+=@{name=$Name;pid=$Process.Id;started_ticks=$Process.StartTime.ToUniversalTime().Ticks.ToString();run_id=$env:ERJI_RUN_ID;log_dir=$script:runDir}
 Save-StartManifest
}
function Wait-WorkerReady([string]$Role,[System.Diagnostics.Process]$Process,[int]$Seconds=60) {
 $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
 do {
  $Process.Refresh()
  if($Process.HasExited){throw "Worker $Role exited before readiness."}
  try {
   $health=Invoke-RestMethod -Uri 'http://127.0.0.1:18771/v1/health' -TimeoutSec 2
   if(@($health.workers | Where-Object {$_.role -eq $Role -and $_.run_id -eq $env:ERJI_RUN_ID -and $_.fresh -and $_.state -notin @('error','reconnecting')}).Count){return}
  } catch {}
  Start-Sleep -Milliseconds 500
 } while([DateTime]::UtcNow -lt $deadline)
 throw "Worker $Role did not report this run's readiness."
}
function Stop-OwnedProcess($Item) {
 $p=Get-Process -Id $Item.pid -ErrorAction SilentlyContinue
 if(-not $p -or $p.StartTime.ToUniversalTime().Ticks.ToString() -ne $Item.started_ticks){return}
 $snapshot=@(Get-CimInstance Win32_Process)
 $children=[System.Collections.Generic.List[object]]::new()
 $parents=[System.Collections.Generic.Queue[int]]::new();$parents.Enqueue($Item.pid)
 while($parents.Count){
  $parent=$parents.Dequeue()
  foreach($child in $snapshot | Where-Object ParentProcessId -eq $parent){
   $owned=Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
   if($owned -and $owned.StartTime.ToUniversalTime().Ticks -ge [long]$Item.started_ticks){
    $children.Add(@{pid=$owned.Id;started_ticks=$owned.StartTime.ToUniversalTime().Ticks.ToString()});$parents.Enqueue($owned.Id)
   }
  }
 }
 for($index=$children.Count-1;$index -ge 0;$index--){
  $child=$children[$index];$current=Get-Process -Id $child.pid -ErrorAction SilentlyContinue
  if($current -and $current.StartTime.ToUniversalTime().Ticks.ToString() -eq $child.started_ticks){Stop-Process -InputObject $current}
 }
 $p=Get-Process -Id $Item.pid -ErrorAction SilentlyContinue
 if($p -and $p.StartTime.ToUniversalTime().Ticks.ToString() -eq $Item.started_ticks){Stop-Process -InputObject $p}
}
function Stop-NewlyStarted {
 foreach($item in $script:processes){
  Stop-OwnedProcess $item
 }
}
