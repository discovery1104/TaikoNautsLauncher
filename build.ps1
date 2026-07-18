Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BuildDir = Join-Path $RepoRoot "build"
$SourcePath = Join-Path $RepoRoot "launcher_qt.pyw"
$CythonSource = Join-Path $BuildDir "launcher_core.pyx"
$SpecPath = Join-Path $RepoRoot "TaikoNautsLauncher-Portable.spec"
$DistDir = Join-Path $RepoRoot "dist"
$WorkDir = Join-Path $BuildDir "pyinstaller_work"

foreach ($required in @($SourcePath, $SpecPath, (Join-Path $BuildDir "setup_cython.py"))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required build input is missing: $required"
    }
}

Copy-Item -LiteralPath $SourcePath -Destination $CythonSource -Force

& py (Join-Path $BuildDir "make_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed with exit code $LASTEXITCODE"
}

Push-Location $BuildDir
try {
    & py .\setup_cython.py build_ext --inplace --force
    if ($LASTEXITCODE -ne 0) {
        throw "Cython compilation failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

& py -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistDir `
    --workpath $WorkDir `
    $SpecPath

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Executable = Join-Path $DistDir "TaikoNautsLauncher-Portable.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Expected executable was not produced: $Executable"
}

$Artifact = Get-Item -LiteralPath $Executable
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Executable
Write-Host ("Built {0:N2} MB: {1}" -f ($Artifact.Length / 1MB), $Artifact.FullName)
Write-Host "SHA256: $($Hash.Hash)"
