<#
.SYNOPSIS
    Runs tests/qgis/ (the QGIS-dependent test suite) under the QGIS-bundled Python
    interpreter, headless. tests/ (the pure core) is NOT run here — use plain
    `pytest` from a venv for that; see README.md.

.DESCRIPTION
    QGIS's own Python has no pytest (confirmed: only nose2 ships with 3.44.8), and it's
    not on PATH. This script locates the QGIS 3.44 launcher, installs pytest into the
    interpreter's --user site-packages (no admin rights needed - avoids touching
    "C:\Program Files"), and runs the QGIS-dependent suite through it.
#>
param(
    [string]$QgisRoot = "C:\Program Files\QGIS 3.44.8"
)

$ErrorActionPreference = "Stop"

$Launcher = Join-Path $QgisRoot "bin\python-qgis-ltr.bat"
if (-not (Test-Path $Launcher)) {
    throw "QGIS launcher not found at $Launcher. Pass -QgisRoot if QGIS is installed elsewhere."
}

$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Ensuring pytest is available in the QGIS interpreter (--user, no elevation)..."
& $Launcher -m pip install --user --quiet pytest
if ($LASTEXITCODE -ne 0) { throw "pip install --user pytest failed (exit $LASTEXITCODE)" }

Write-Host "Running tests/qgis/ under the QGIS interpreter..."
Push-Location $RepoRoot
try {
    & $Launcher -m pytest tests/qgis @args
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $exitCode
