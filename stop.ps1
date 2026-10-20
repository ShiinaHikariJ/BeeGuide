$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (Test-Path -LiteralPath $pidFile) {
    $serverProcessId = [int](Get-Content -LiteralPath $pidFile)
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" -ErrorAction SilentlyContinue
    $expectedExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if ($serverProcess -and $serverProcess.ExecutablePath -eq $expectedExecutable -and $serverProcess.CommandLine -like '*app.serve*') {
        # Windows venv launchers can start a base-Python child. Stop only
        # verified descendants running this app, then the verified launcher.
        $runtimeChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $serverProcessId" -ErrorAction SilentlyContinue
        foreach ($runtimeChild in $runtimeChildren) {
            if ($runtimeChild.CommandLine -like '*app.serve*') {
                try { Stop-Process -Id $runtimeChild.ProcessId -ErrorAction Stop }
                catch { & taskkill.exe /PID $runtimeChild.ProcessId /F | Out-Null }
            }
        }
        try { Stop-Process -Id $serverProcessId -ErrorAction Stop }
        catch { & taskkill.exe /PID $serverProcessId /F | Out-Null }
    }
    Remove-Item -LiteralPath $pidFile
}
