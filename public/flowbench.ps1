# flowbench - start and manage the Flowbench runner (installed by install.ps1).
param([Parameter(Position = 0)][string]$Cmd = 'start', [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Site = if ($env:FLOWBENCH_SITE) { $env:FLOWBENCH_SITE } else { 'https://flowbench.zichaoleng55.workers.dev' }
$FB = Split-Path -Parent $PSScriptRoot
$CfgPath = Join-Path $FB 'config.json'
$RunnerPy = Join-Path $FB 'runner.py'
$RtPy = Join-Path $FB 'runtime\Scripts\python.exe'
$PidFile = Join-Path $HOME '.flowbench\runner.pid'
$TokenFile = Join-Path $HOME '.flowbench\token'

function Load-Cfg {
  if (-not (Test-Path $CfgPath)) { throw "Flowbench is not installed. Run: irm $Site/install.ps1 | iex" }
  $c = Get-Content -Raw $CfgPath | ConvertFrom-Json
  $c | Add-Member -NotePropertyName roots -NotePropertyValue @($c.roots | Where-Object { $_ }) -Force
  return $c
}
function Save-Cfg($c) { $c | ConvertTo-Json | Set-Content -Encoding UTF8 $CfgPath }
function Port { $p = (Load-Cfg).port; if ($p) { [int]$p } else { 8765 } }
function Is-Running {
  try { $t = New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1', (Port)); $t.Close(); return $true } catch { return $false }
}
function Open-Page {
  if ($env:FLOWBENCH_NO_BROWSER) { return }
  $tok = if (Test-Path $TokenFile) { (Get-Content -Raw $TokenFile).Trim() } else { '' }
  Start-Process ("http://127.0.0.1:{0}/#token={1}" -f (Port), $tok)
}
function Update-Runner([switch]$Quiet) {
  try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 6 -Headers @{ 'User-Agent' = 'flowbench-cli/1.0' } -Uri "$Site/runner.py" -OutFile "$RunnerPy.new"
    Move-Item -Force "$RunnerPy.new" $RunnerPy
    if (-not $Quiet) { Write-Host '  runner.py updated' }
  } catch { if (-not $Quiet) { Write-Host '  Could not download the latest runner (offline?). Keeping the current one.' } }
}
function Start-Runner([string[]]$Extra = @()) {
  if (Is-Running) { Write-Host '  Flowbench is already running. Opening it.'; Open-Page; return }
  Update-Runner -Quiet
  $argv = @("`"$RunnerPy`"", '--config', "`"$CfgPath`"") + $Extra
  if ($env:FLOWBENCH_NO_BROWSER) { $argv += '--no-browser' }
  Start-Process -FilePath $RtPy -ArgumentList $argv -WorkingDirectory $FB -WindowStyle Minimized
  Write-Host '  Flowbench is starting (a minimized window keeps it running; close it or run "flowbench stop" to quit).'
}
function Stop-Runner {
  $stopped = $false
  if (Test-Path $PidFile) {
    try { $info = Get-Content -Raw $PidFile | ConvertFrom-Json; & taskkill.exe /PID $info.pid /T /F *> $null; $stopped = $true } catch { }
    Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
  }
  if (-not $stopped -and (Is-Running)) {
    $conn = Get-NetTCPConnection -LocalPort (Port) -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { & taskkill.exe /PID $conn.OwningProcess /T /F *> $null; $stopped = $true }
  }
  if ($stopped) { Write-Host '  Flowbench stopped.' } else { Write-Host '  Flowbench was not running.' }
  Start-Sleep -Milliseconds 400
}
function Restart-IfRunning { if (Is-Running) { Stop-Runner; Start-Runner } }

switch ($Cmd.ToLower()) {
  { $_ -in 'start', 'open', '' } { Start-Runner }
  'add' {
    $c = Load-Cfg; $paths = if ($Rest) { $Rest } else { @('.') }
    foreach ($p in $paths) {
      $full = (Resolve-Path -LiteralPath $p).Path
      if (-not (Test-Path -LiteralPath $full -PathType Container)) { Write-Host "  Not a folder: $full"; continue }
      if (@($c.roots) -notcontains $full) { $c.roots = @($c.roots) + $full; Write-Host "  Added $full" } else { Write-Host "  Already added: $full" }
    }
    Save-Cfg $c; Restart-IfRunning
  }
  { $_ -in 'remove', 'rm' } {
    $c = Load-Cfg
    foreach ($p in $Rest) {
      $full = try { (Resolve-Path -LiteralPath $p).Path } catch { $p }
      $c.roots = @($c.roots | Where-Object { $_ -ne $full -and $_ -ne $p }); Write-Host "  Removed $p"
    }
    Save-Cfg $c; Restart-IfRunning
  }
  { $_ -in 'folders', 'list', 'ls' } { (Load-Cfg).roots | ForEach-Object { Write-Host "  $_" } }
  'stop' { Stop-Runner }
  'restart' { Stop-Runner; Start-Runner }
  'status' {
    $c = Load-Cfg
    if (Is-Running) { Write-Host ("  Running on http://127.0.0.1:{0}" -f (Port)) } else { Write-Host '  Not running.' }
    Write-Host "  Python : $($c.python)"; Write-Host ('  Folders: ' + (@($c.roots) -join ', '))
  }
  'python' {
    if (-not $Rest) { Write-Host "  Code runs with: $((Load-Cfg).python)"; break }
    if (-not (Test-Path -LiteralPath $Rest[0])) { Write-Host "  File not found: $($Rest[0])"; break }
    $exe = (Resolve-Path -LiteralPath $Rest[0]).Path
    & $exe -c "import sys" ; if ($LASTEXITCODE -ne 0) { Write-Host "  Not a working Python: $exe"; break }
    $c = Load-Cfg; $c | Add-Member -NotePropertyName python -NotePropertyValue $exe -Force; Save-Cfg $c
    Write-Host "  Your code will now run with $exe"; Restart-IfRunning
  }
  'examples' { $was = Is-Running; if ($was) { Stop-Runner }; Start-Runner @('--examples') }
  'update' {
    Update-Runner
    & $RtPy -m pip install --quiet --disable-pip-version-check --upgrade websockets pywinpty
    Invoke-WebRequest -UseBasicParsing -Headers @{ 'User-Agent' = 'flowbench-cli/1.0' } -Uri "$Site/flowbench.ps1" -OutFile "$PSCommandPath.new"
    Move-Item -Force "$PSCommandPath.new" $PSCommandPath
    Write-Host '  Flowbench is up to date.'; Restart-IfRunning
  }
  'token' { Stop-Runner; Remove-Item -Force $TokenFile -ErrorAction SilentlyContinue; Write-Host '  A new secret token will be created.'; Start-Runner }
  default {
    Write-Host @'

  flowbench                 start Flowbench (or open it if it is already running)
  flowbench add [folder]    let Flowbench read a code folder (default: the current folder)
  flowbench remove <folder> stop showing a folder
  flowbench folders         list your folders
  flowbench python <path>   choose which Python runs your code (e.g. a conda / venv python.exe)
  flowbench examples        add a small demo folder to your first folder
  flowbench status          is it running? which Python and folders?
  flowbench stop | restart  stop or restart the runner
  flowbench update          get the latest version
  flowbench token           make a new secret token

'@
  }
}
