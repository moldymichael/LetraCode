[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("install", "uninstall")][string]$Action,
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$probe = "import sys; assert sys.platform == 'win32' and sys.version_info >= (3, 11); print(getattr(sys, '_base_executable', sys.executable))"
if (-not $Python) { $Python = $env:LETRACODE_PYTHON }
$explicitPython = [bool]$Python
$candidates = if ($explicitPython) { @($Python) } else { @("py", "python", "python3") }
$selected = $null
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) { continue }
    $probeArguments = @("-I", "-X", "utf8", "-c", $probe)
    if (-not $explicitPython -and $candidate -eq "py") { $probeArguments = @("-3") + $probeArguments }
    try {
        $result = & $command.Source @probeArguments 2>$null
        if ($LASTEXITCODE -eq 0 -and $result) {
            $selected = [string]($result | Select-Object -Last 1)
            break
        }
    } catch {
        # Try the next installed interpreter. Explicit selections are never replaced.
    }
}
if (-not $selected) {
    Write-Error "LetraCode requires native Windows Python 3.11 or newer. Install Python from python.org with pip and venv, then rerun this script. Use -Python 'C:\Path To Python\python.exe' to select it."
    exit 1
}
# Use the base interpreter, so uninstall can remove the app's private venv.
# Windows holds each process's current directory open. Move both the PowerShell
# location and its native process directory out of the app before removal.
if ($Action -eq "uninstall" -and $env:LOCALAPPDATA -and (Test-Path -LiteralPath $env:LOCALAPPDATA -PathType Container)) {
    Set-Location -LiteralPath $env:LOCALAPPDATA
    [Environment]::CurrentDirectory = (Get-Location).ProviderPath
}
& $selected -I -X utf8 (Join-Path $PSScriptRoot "windows_install.py") $Action --source $Source
exit $LASTEXITCODE
