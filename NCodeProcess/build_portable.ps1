param(
    [string]$CondaEnvironment = 'python38',
    [string]$SigningCertificateThumbprint = '',
    [string]$TimestampServer = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$dist = Join-Path $root 'dist'
$privateBuild = Join-Path $root '.hardened-build'
$workPath = Join-Path $privateBuild 'work'

# The key is generated for this build only and is never written to the project
# or release package. PyInstaller embeds it in its encrypted loader at build
# time; this prevents casual PYZ extraction while keeping the build repeatable.
$keyBytes = New-Object byte[] 16
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($keyBytes) } finally { $rng.Dispose() }
$env:NCODEPROCESS_PYI_KEY = ([BitConverter]::ToString($keyBytes) -replace '-', '').Substring(0, 16).ToLowerInvariant()
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONHASHSEED = 'random'
$env:PYTHONUSERBASE = Join-Path $root '.pyuser'
New-Item -ItemType Directory -Path $env:PYTHONUSERBASE -Force | Out-Null

function Sign-Executable([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) { return }
    $thumbprint = $SigningCertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    $certificate = Get-ChildItem -Path "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $certificate) { throw "Signing certificate not found: $thumbprint" }
    $params = @{ FilePath = $Path; Certificate = $certificate; HashAlgorithm = 'SHA256' }
    if (-not [string]::IsNullOrWhiteSpace($TimestampServer)) { $params.TimestampServer = $TimestampServer }
    $signature = Set-AuthenticodeSignature @params
    if ($signature.Status -ne 'Valid') { throw "Authenticode signing failed: $($signature.Status)" }
}

try {
    Remove-Item -LiteralPath $privateBuild -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $workPath -Force | Out-Null
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $distTargets = @(
        (Join-Path $dist 'NCodeProcess.exe'),
        (Join-Path $dist 'NCodeProcess-Package'),
        (Join-Path $dist 'NCodeProcess-Windows7-Portable.zip')
    )
    foreach ($target in $distTargets) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }

    $spec = Join-Path $root 'NCodeProcess.spec'
    $pyInstallerArgs = @(
        '-OO', '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', $dist, '--workpath', $workPath, $spec
    )
    # WP-S3: optional UPX support - add tools\upx to PATH when present so PyInstaller compresses.
    $upxDir = Join-Path $root 'tools\upx'
    if (Test-Path (Join-Path $upxDir 'upx.exe')) {
        $env:PATH = "$upxDir;$env:PATH"
    }
    conda run -n $CondaEnvironment python @pyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    $exe = Join-Path $dist 'NCodeProcess.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "Build output not found: $exe" }
    Sign-Executable $exe

    $portable = Join-Path $dist 'NCodeProcess-Package'
    if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
    New-Item -ItemType Directory -Path $portable -Force | Out-Null
    Copy-Item -LiteralPath $exe -Destination (Join-Path $portable 'NCodeProcess.exe') -Force
    Copy-Item -LiteralPath (Join-Path $root 'VERSION.txt') -Destination (Join-Path $portable 'VERSION.txt') -Force
    Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination (Join-Path $portable 'README.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'SECURITY.md') -Destination (Join-Path $portable 'SECURITY.md') -Force
    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  NCodeProcess.exe" | Set-Content -LiteralPath (Join-Path $portable 'SHA256SUMS.txt') -Encoding ASCII
    if (-not [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        Get-AuthenticodeSignature -LiteralPath $exe | Format-List Status,SignerCertificate | Out-File -LiteralPath (Join-Path $portable 'AUTHENTICODE.txt') -Encoding UTF8
    }

    $zip = Join-Path $dist 'NCodeProcess-Windows7-Portable.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -LiteralPath $portable -DestinationPath $zip
    Write-Host 'Hardened executable: dist\NCodeProcess.exe'
    Write-Host 'Portable folder: dist\NCodeProcess-Package\'
    Write-Host 'Portable archive: dist\NCodeProcess-Windows7-Portable.zip'
    if ([string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        Write-Warning 'No Authenticode certificate supplied; use -SigningCertificateThumbprint for trusted publisher signing.'
    }
}
finally {
    Remove-Item -LiteralPath $privateBuild -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\NCODEPROCESS_PYI_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:\PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
    Remove-Item Env:\PYTHONHASHSEED -ErrorAction SilentlyContinue
}
