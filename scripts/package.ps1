<#
.SYNOPSIS
    Builds a distributable plugin zip (field_survey_import-<version>.zip) suitable for
    QGIS's Plugins > Install from ZIP, containing only the field_survey_import/ package
    with its version read from metadata.txt.
#>
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $RepoRoot "field_survey_import"
$MetadataPath = Join-Path $Source "metadata.txt"

$versionLine = Select-String -Path $MetadataPath -Pattern "^version=" | Select-Object -First 1
if (-not $versionLine) { throw "Could not find version= in $MetadataPath" }
$version = ($versionLine.Line -split "=", 2)[1].Trim()

$DistDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
$OutZip = Join-Path $DistDir "field_survey_import-$version.zip"

if (Test-Path $OutZip) { Remove-Item $OutZip -Force }

$StagingDir = Join-Path $env:TEMP "field_survey_import_package_staging"
if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir | Out-Null
$StagedPlugin = Join-Path $StagingDir "field_survey_import"

Copy-Item -Path $Source -Destination $StagedPlugin -Recurse -Force
Get-ChildItem -Path $StagedPlugin -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

Compress-Archive -Path $StagedPlugin -DestinationPath $OutZip
Remove-Item $StagingDir -Recurse -Force

Write-Host "Built $OutZip"
Write-Host "Verify with: QGIS > Plugins > Manage and Install Plugins > Install from ZIP"
