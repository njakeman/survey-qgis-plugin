<#
.SYNOPSIS
    Installs (or updates) the Field Survey Import plugin into the QGIS 3.44 "default"
    profile's plugin directory, so QGIS picks up local changes on next restart / plugin
    reload (Plugin Reloader, if installed, avoids the restart).

.PARAMETER Profile
    QGIS profile name. Defaults to "default" (C:\Users\neil_\AppData\Roaming\QGIS\QGIS3\
    profiles\default\python\plugins), matching the profile confirmed present on this
    machine. Change if you deploy to a different profile (e.g. a clean test profile).

.PARAMETER Symlink
    Create a symlink instead of copying. Requires Developer Mode enabled or an elevated
    shell (New-Item -ItemType SymbolicLink needs the SeCreateSymbolicLinkPrivilege).
    Default is a plain copy, which needs no special privilege and is the safe default.
#>
param(
    [string]$Profile = "default",
    [switch]$Symlink
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $RepoRoot "field_survey_import"
$PluginsDir = Join-Path $env:APPDATA "QGIS\QGIS3\profiles\$Profile\python\plugins"
$Target = Join-Path $PluginsDir "field_survey_import"

if (-not (Test-Path $Source)) {
    throw "Plugin source not found at $Source"
}
if (-not (Test-Path $PluginsDir)) {
    New-Item -ItemType Directory -Path $PluginsDir -Force | Out-Null
}

if (Test-Path $Target) {
    $item = Get-Item $Target -Force
    if ($item.LinkType -eq "SymbolicLink") {
        Remove-Item $Target -Force
    } else {
        Remove-Item $Target -Recurse -Force
    }
}

if ($Symlink) {
    New-Item -ItemType SymbolicLink -Path $Target -Target $Source | Out-Null
    Write-Host "Symlinked $Target -> $Source"
} else {
    Copy-Item -Path $Source -Destination $Target -Recurse -Force `
        -Exclude "__pycache__" -Container
    Get-ChildItem -Path $Target -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Write-Host "Copied $Source -> $Target"
}

Write-Host "Restart QGIS (or use Plugin Reloader) and enable 'Field Survey Import' in Plugins > Manage and Install Plugins."
