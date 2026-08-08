param([string]$CondaEnvironment = 'python38')
$ErrorActionPreference = 'Stop'
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Invoke-TestSuite {
    param([string]$Name)
    Write-Host "== $Name tests =="
    $suiteTimer = [System.Diagnostics.Stopwatch]::StartNew()
    # cmd /c 做文本级 2>&1：PowerShell 的 $ErrorActionPreference='Stop' 会把
    # conda 包装的 stderr 输出当作 NativeCommandError 抛异常，无法直接捕获。
    $output = cmd /c "conda run -n $CondaEnvironment python -m unittest discover -s tests -v 2>&1"
    $exitCode = $LASTEXITCODE
    $suiteTimer.Stop()
    $output | Write-Host
    Write-Host ("[{0}] 耗时 {1:N1}s，退出码 {2}" -f $Name, $suiteTimer.Elapsed.TotalSeconds, $exitCode)
    if ($exitCode -ne 0) {
        Write-Host '失败/错误用例：'
        $output | Select-String '^(FAIL|ERROR):' | ForEach-Object { Write-Host ("  " + $_.Line) }
        return $exitCode
    }
    return 0
}

$mainCode = Invoke-TestSuite -Name '主工具'
if ($mainCode -ne 0) { exit $mainCode }

Push-Location ..\NCodeProcessReportViewer
try {
    $viewerCode = Invoke-TestSuite -Name '查看器'
} finally {
    Pop-Location
}

$stopwatch.Stop()
Write-Host ("总耗时 {0:N1}s" -f $stopwatch.Elapsed.TotalSeconds)
exit $viewerCode
