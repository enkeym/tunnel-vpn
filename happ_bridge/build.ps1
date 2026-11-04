$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $Root 'upstream'
$Revision = '8ae0171535a1622f100a1535a216afbe7675d58d'
if (-not (Test-Path (Join-Path $Source '.git'))) {
    git clone https://github.com/useruserdev/happwn.git $Source
    if ($LASTEXITCODE -ne 0) { throw 'Failed to clone Happ decoder' }
}
git -C $Source checkout --detach $Revision
if ($LASTEXITCODE -ne 0) { throw 'Failed to select Happ decoder revision' }
cargo build --manifest-path (Join-Path $Root 'Cargo.toml') --release
if ($LASTEXITCODE -ne 0) { throw 'Failed to build Happ decoder' }
$Bin = Join-Path (Split-Path -Parent $Root) 'lib\bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item (Join-Path $Root 'target\release\happwn.dll') $Bin -Force
