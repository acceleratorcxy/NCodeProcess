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

    $spec = Join-Path $root 'NCodeProcessReportViewer.spec'
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

    $exe = Join-Path $dist 'NCodeProcessReportViewer.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "Build output not found: $exe" }
    Sign-Executable $exe

    $portable = Join-Path $dist 'NCodeProcessReportViewer-Package'
    if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
    New-Item -ItemType Directory -Path $portable -Force | Out-Null
    Copy-Item -LiteralPath $exe -Destination (Join-Path $portable 'NCodeProcessReportViewer.exe') -Force
    Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination (Join-Path $portable 'README.md') -Force
    Copy-Item -LiteralPath (Join-Path $root 'SECURITY.md') -Destination (Join-Path $portable 'SECURITY.md') -Force
    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  NCodeProcessReportViewer.exe" | Set-Content -LiteralPath (Join-Path $portable 'SHA256SUMS.txt') -Encoding ASCII
    if (-not [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        Get-AuthenticodeSignature -LiteralPath $exe | Format-List Status,SignerCertificate | Out-File -LiteralPath (Join-Path $portable 'AUTHENTICODE.txt') -Encoding UTF8
    }

    $zip = Join-Path $dist 'NCodeProcessReportViewer-Windows7-Portable.zip'
    if (Test-Path -LiteralPath $zip) {
        try { Remove-Item -LiteralPath $zip -Force -ErrorAction Stop }
        catch {
            $zip = Join-Path $dist 'NCodeProcessReportViewer-Windows7-Portable-Compressed.zip'
            if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
        }
    }
    Compress-Archive -LiteralPath $portable -DestinationPath $zip
    Write-Host 'Hardened executable: dist\NCodeProcessReportViewer.exe'
    Write-Host "Portable archive: $zip"
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
