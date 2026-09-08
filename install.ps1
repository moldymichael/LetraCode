<#
.SYNOPSIS
Install or update LetraCode for the current Windows user.
.DESCRIPTION
Requires native Windows Python 3.11+ with venv and pip. Downloads PySide6 and
pypdf into a private virtual environment. Close LetraCode before updating.
.PARAMETER Python
Optional path to Python. LETRACODE_PYTHON also selects an interpreter.
#>
[CmdletBinding()]
param([string]$Python = "")

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "packaging\windows-bootstrap.ps1") -Action install -Source $PSScriptRoot -Python $Python
exit $LASTEXITCODE
