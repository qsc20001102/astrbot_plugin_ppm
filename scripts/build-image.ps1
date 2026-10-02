param([string]$Tag = "ppm-standalone:1.2.2")
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    New-Item -ItemType Directory -Force -Path dist | Out-Null
    docker build --platform linux/amd64 -t $Tag .
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
    docker save -o dist/ppm-standalone-1.2.2-amd64.tar $Tag
    if ($LASTEXITCODE -ne 0) { throw 'Docker save failed' }
} finally { Pop-Location }
