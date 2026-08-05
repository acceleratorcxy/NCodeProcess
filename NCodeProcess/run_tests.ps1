param([string]$CondaEnvironment = 'python38')
$ErrorActionPreference = 'Stop'

Write-Host '== NCodeProcess main tests =='
conda run -n $CondaEnvironment python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '== NCodeProcessReportViewer tests =='
Push-Location ..\NCodeProcessReportViewer
try {
    conda run -n $CondaEnvironment python -m unittest discover -s tests -v
} finally {
    Pop-Location
}
