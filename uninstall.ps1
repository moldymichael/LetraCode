<#
.SYNOPSIS
Remove the current user's LetraCode installation while keeping chats and settings.
.DESCRIPTION
Close LetraCode first. Requires native Windows Python 3.11+.
.PARAMETER Python
Optional path to Python. LETRACODE_PYTHON also selects an interpreter.
#>
[CmdletBinding()]
param([string]$Python = "")

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "packaging\windows-bootstrap.ps1") -Action uninstall -Source $PSScriptRoot -Python $Python
exit $LASTEXITCODE
