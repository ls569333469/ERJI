[CmdletBinding()]
param(
    [switch]$Once,
    [ValidateRange(1024,65535)][int]$Port = 15432,
    [switch]$NoRestart
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$project = (Resolve-Path (Join-Path $repo "../../..")).Path
$python = Join-Path $project ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "ERJI Python environment is missing" }
$key = Join-Path $env:USERPROFILE ".ssh/xiexiu_xbot_ed25519"
$server = "root@107.172.78.150"
$sshArgs = @("-i",$key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=10")
$tunnel = $null
$previousUrl = [Environment]::GetEnvironmentVariable("ERJI_DATABASE_URL", "Process")
$previousAuth = [Environment]::GetEnvironmentVariable("ERJI_REQUIRE_AUTH", "Process")
$previousMode = [Environment]::GetEnvironmentVariable("ERJI_MODE", "Process")
$mutex = [System.Threading.Mutex]::new($false, "Local\ERJI_Binance_Bridge")
$ownsMutex = $false
try { $ownsMutex = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
if (-not $ownsMutex) { $mutex.Dispose(); throw "ERJI Binance bridge is already running" }
try {
do {
try {
    $ip = (& ssh @sshArgs $server "docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' erji-db-1").Trim()
    if ($LASTEXITCODE -ne 0 -or $ip -notmatch "^[0-9]{1,3}([.][0-9]{1,3}){3}$") { throw "cannot read database container address" }
    $dbUrl = (& ssh @sshArgs $server 'docker exec erji-api-1 sh -c ''printf "%s" "$ERJI_DATABASE_URL"''').Trim()
    if ($LASTEXITCODE -ne 0 -or $dbUrl -notmatch '^postgresql[+]psycopg://[^@]+@[^/]+/erji$') { throw "cannot read database connection config" }
    $localDbUrl = $dbUrl -replace '@[^/]+/erji$', ("@127.0.0.1:" + $Port + "/erji")
    $forward = "127.0.0.1:$($Port):$($ip):5432"
    $tunnel = Start-Process -FilePath "ssh.exe" -ArgumentList ($sshArgs + @("-o","ExitOnForwardFailure=yes","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3","-N","-L",$forward,$server)) -PassThru -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($tunnel.HasExited) { throw "SSH database tunnel failed" }
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            try {
                $client.Connect("127.0.0.1", $Port)
                $ready = $true
                break
            }
            finally { $client.Dispose() }
        }
        catch [System.Net.Sockets.SocketException] { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "SSH database tunnel did not become ready" }
    $env:ERJI_DATABASE_URL = $localDbUrl
    $env:ERJI_REQUIRE_AUTH = "false"
    $env:ERJI_MODE = "research_live"
    $args = @("-m","services.worker.main","--role","binance-bridge")
    if ($Once) { $args += "--once" }
    Push-Location $repo
    try { & $python @args }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "local Binance bridge exited with code $LASTEXITCODE" }
}
catch {
    if ($Once -or $NoRestart) { throw }
    Write-Warning "Bridge disconnected; reconnecting in 15 seconds."
}
finally {
    if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
    [Environment]::SetEnvironmentVariable("ERJI_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("ERJI_REQUIRE_AUTH", $previousAuth, "Process")
    [Environment]::SetEnvironmentVariable("ERJI_MODE", $previousMode, "Process")
}
if (-not $Once -and -not $NoRestart) { Start-Sleep -Seconds 15 }
} while (-not $Once -and -not $NoRestart)
}
finally { if ($ownsMutex) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
