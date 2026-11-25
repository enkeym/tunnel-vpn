param(
    [switch]$TestHooks,
    [switch]$Full,
    [string]$LocalRoot
)

$ErrorActionPreference = 'Stop'
$Dev = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent (Split-Path -Parent $Dev)
if (-not $LocalRoot) { $LocalRoot = Join-Path $env:LOCALAPPDATA 'TunnelVPN\build' }
New-Item -ItemType Directory -Force -Path $LocalRoot | Out-Null

Write-Host "-> исходники: $Root"
Write-Host "-> сборка:    $LocalRoot"

$Excluded = @(
    '.git', '.opencode', '.pytest_cache', '.tokensave', '.ragsave', '.playwright-mcp',
    '__pycache__', 'dist', 'build', 'venv', 'Output', 'gen'
)
& robocopy $Root $LocalRoot /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 /XD @Excluded | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy вернул $LASTEXITCODE" }

Push-Location $LocalRoot
try {
    & (Join-Path $LocalRoot 'installer\build.ps1') -Fast:(-not $Full) -TestHooks:$TestHooks
} finally {
    Pop-Location
}

$Version = (Get-Content (Join-Path $LocalRoot 'VERSION') -Raw).Trim()
$OutSrc = Join-Path $LocalRoot 'installer\Output'
$OutDst = Join-Path $Root 'installer\Output'
New-Item -ItemType Directory -Force -Path $OutDst | Out-Null
$SetupName = if ($TestHooks) {
    "TunnelVPN-$Version-testhooks-setup.exe"
} else {
    "TunnelVPN-$Version-setup.exe"
}
Copy-Item (Join-Path $OutSrc $SetupName) $OutDst -Force
if (-not $TestHooks) {
    Copy-Item (Join-Path $OutSrc 'SHA256SUMS.txt') $OutDst -Force
    $PortableSrc = Join-Path $LocalRoot 'dist\TunnelVPN-Portable.exe'
    if ($Full -and (Test-Path $PortableSrc)) {
        $PortableDst = Join-Path $Root 'dist'
        New-Item -ItemType Directory -Force -Path $PortableDst | Out-Null
        Copy-Item $PortableSrc $PortableDst -Force
    }
}
Write-Host "-> установщик: $(Join-Path $OutDst $SetupName)"
