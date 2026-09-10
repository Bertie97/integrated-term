$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $ProjectRoot

py -m pip install --upgrade pyinstaller pillow
py -m PyInstaller --clean --noconfirm packaging/windows/integrated-term.spec

Write-Host "Built: $ProjectRoot\dist\集成终端控制台.exe"
