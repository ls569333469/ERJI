$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'local_start_helpers.ps1')
$checks=@()
foreach($file in @('local_start_helpers.ps1','start-local.ps1','restart-local-workers.ps1')){
 $parseErrors=$null;$parseTokens=$null
 $null=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $file),[ref]$parseTokens,[ref]$parseErrors)
 if($parseErrors.Count){throw "Syntax error in $file"}
 $checks+="parsed $file"
}
$listener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0);$listener.Start()
$port=$listener.LocalEndpoint.Port;$rejected=$false
try {try{Assert-FreePort $port}catch{$rejected=$true}}finally{$listener.Stop()}
if(-not $rejected){throw 'Occupied test port accepted'};$checks+='occupied disposable port rejected before startup'
# All process cmdlets below are fakes. No real process is started or stopped.
$born=[DateTime]::UtcNow
$script:fakeProcesses=@{870001=[pscustomobject]@{Id=870001;StartTime=$born};870002=[pscustomobject]@{Id=870002;StartTime=$born.AddSeconds(1)};870003=[pscustomobject]@{Id=870003;StartTime=$born.AddSeconds(2)}}
$script:stopped=@()
function Get-Process {param($Id,$ErrorAction) $script:fakeProcesses[[int]$Id]}
function Get-CimInstance {param($ClassName) @([pscustomobject]@{ProcessId=870002;ParentProcessId=870001},[pscustomobject]@{ProcessId=870003;ParentProcessId=999999})}
function Stop-Process {param($InputObject) $script:stopped+=$InputObject.Id;$script:fakeProcesses.Remove([int]$InputObject.Id)}
Stop-OwnedProcess @{pid=870001;started_ticks=$born.AddSeconds(-1).Ticks.ToString()}
if($script:stopped.Count){throw 'PID birth mismatch was stopped'};$checks+='PID birth mismatch untouched'
$script:processes=@(@{pid=870001;started_ticks=$born.Ticks.ToString()})
Stop-NewlyStarted
if(($script:stopped -join ',') -ne '870002,870001' -or -not $script:fakeProcesses.ContainsKey(870003)){throw 'Ownership cleanup failed'}
$checks+='owned descendants cleaned before parent; unrelated process untouched'
$report=Join-Path (Split-Path $PSScriptRoot -Parent) 'docs/p39-validation/startup-smoke.json'
@{status='passed';checks=$checks;real_processes_started=0;real_processes_stopped=0;production_ports_accessed=@()} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $report -Encoding utf8
$checks | ConvertTo-Json -Compress
