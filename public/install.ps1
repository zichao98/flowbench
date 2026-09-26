# Flowbench installer for Windows.
#   irm https://flowbench.zichaoleng55.workers.dev/install.ps1 | iex
# Installs into %LOCALAPPDATA%\Flowbench (no admin rights, your own Python is not modified):
#   runtime\      a private virtual environment for the runner (websockets, pywinpty)
#   runner.py     the local runner
#   config.json   your folders, the Python that runs your code, the port
#   bin\flowbench.cmd + flowbench.ps1   the `flowbench` command (added to your PATH)
# plus "Flowbench" shortcuts on the Desktop and in the Start menu.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Site = if ($env:FLOWBENCH_SITE) { $env:FLOWBENCH_SITE } else { 'https://flowbench.zichaoleng55.workers.dev' }
$FB = if ($env:FLOWBENCH_HOME) { $env:FLOWBENCH_HOME } else { Join-Path $env:LOCALAPPDATA 'Flowbench' }
$Bin = Join-Path $FB 'bin'
$UA = @{ 'User-Agent' = 'flowbench-installer/1.0' }

function Say($msg) { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n> $msg" -ForegroundColor White }

Write-Host "`n  FLOWBENCH installer" -ForegroundColor White
Write-Host "  ----------------------------------------"

# 1. Find the Python that runs YOUR code (your packages live there)
Step 'Looking for Python'
$py = $null
foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
  try {
    $exe = & $cand[0] $cand[1..9] -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe) -and ($exe -notmatch 'WindowsApps')) { $py = $exe.Trim(); break }
  } catch { }
}
if (-not $py) {
  Write-Host "  Python was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again." -ForegroundColor Red
  return
}
$ver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.9') { Write-Host "  Python $ver is too old; Flowbench needs 3.9 or newer." -ForegroundColor Red; return }
Say "Using $py (Python $ver)"

# 2. Private environment for the runner
Step 'Setting up the runner environment'
New-Item -ItemType Directory -Force -Path $FB, $Bin | Out-Null
$rt = Join-Path $FB 'runtime'
$rtPy = Join-Path $rt 'Scripts\python.exe'
if (-not (Test-Path $rtPy)) { & $py -m venv $rt; if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment' } }
& $rtPy -m pip install --quiet --disable-pip-version-check --upgrade websockets pywinpty
if ($LASTEXITCODE -ne 0) { throw 'pip install failed (check your internet connection)' }
Say 'websockets + pywinpty installed (private, your Python is untouched)'

# 3. Runner and launcher
Step 'Downloading Flowbench'
Invoke-WebRequest -UseBasicParsing -Headers $UA -Uri "$Site/runner.py" -OutFile (Join-Path $FB 'runner.py')
Invoke-WebRequest -UseBasicParsing -Headers $UA -Uri "$Site/flowbench.ps1" -OutFile (Join-Path $Bin 'flowbench.ps1')
Set-Content -Encoding ASCII -Path (Join-Path $Bin 'flowbench.cmd') -Value "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0flowbench.ps1`" %*"
Say 'runner.py and the flowbench command are ready'

# 4. Config (keeps your folders if you reinstall)
$cfgPath = Join-Path $FB 'config.json'
if (Test-Path $cfgPath) {
  $cfg = Get-Content -Raw $cfgPath | ConvertFrom-Json
  $cfg | Add-Member -NotePropertyName python -NotePropertyValue $py -Force
} else {
  $desk = [Environment]::GetFolderPath('Desktop')
  $cfg = [ordered]@{ roots = @($desk); python = $py; port = 8765 }
}
$cfg | ConvertTo-Json | Set-Content -Encoding UTF8 $cfgPath
Say ("Folders: " + (@($cfg.roots) -join ', '))

# 5. PATH + shortcuts (FLOWBENCH_NO_SYSTEM=1 skips this, for testing)
if (-not $env:FLOWBENCH_NO_SYSTEM) {
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $userPath) { $userPath = '' }
if (($userPath -split ';') -notcontains $Bin) {
  [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $Bin).TrimStart(';'), 'User')
  Say "Added $Bin to your PATH (open a new terminal to use 'flowbench')"
}
$env:Path = "$env:Path;$Bin"
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Flowbench.lnk'
foreach ($lnkPath in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Flowbench.lnk'), $startMenu)) {
  $lnk = $shell.CreateShortcut($lnkPath)
  $lnk.TargetPath = Join-Path $Bin 'flowbench.cmd'
  $lnk.WorkingDirectory = $FB
  $lnk.WindowStyle = 7
  $lnk.IconLocation = "$py,0"
  $lnk.Description = 'Flowbench: visual Python workflows'
  $lnk.Save()
}
Say 'Shortcuts created on the Desktop and in the Start menu'
}

Write-Host "`n  Done! Double-click 'Flowbench' on your Desktop, or type 'flowbench' in a new terminal." -ForegroundColor Green
Write-Host "  Add another code folder:  flowbench add `"C:\path\to\code`"     Help:  flowbench help`n"
& (Join-Path $Bin 'flowbench.cmd')
