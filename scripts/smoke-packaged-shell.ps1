$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$application = Join-Path $root 'apps\desktop\src-tauri\target\release\thikra-studio.exe'
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Packaged Thikra Studio executable is missing: $application"
}

$existingApiIds = @(Get-Process thikra-api -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$desktop = $null
$second = $null
try {
    $desktop = Start-Process -FilePath $application -WindowStyle Hidden -PassThru
    $engine = $null
    for ($attempt = 0; $attempt -lt 120; $attempt += 1) {
        $engine = Get-CimInstance Win32_Process -Filter "Name = 'thikra-api.exe'" |
            Where-Object { $_.ParentProcessId -eq $desktop.Id } |
            Select-Object -First 1
        if ($engine) { break }
        if ($desktop.HasExited) { throw "Packaged desktop exited before starting its engine" }
        Start-Sleep -Milliseconds 250
    }
    if (-not $engine) { throw "Packaged desktop did not start the embedded engine" }
    if ($engine.CommandLine -notmatch '--port\s+(\d+)') { throw "Could not read the engine's dynamic port" }
    $port = [int]$Matches[1]
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt += 1) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health/ready" -TimeoutSec 1
            if ($response.status -eq 'ready') { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "Embedded engine did not become ready on port $port" }

    $second = Start-Process -FilePath $application -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $ownedEngines = @(Get-CimInstance Win32_Process -Filter "Name = 'thikra-api.exe'" |
        Where-Object { $_.ProcessId -notin $existingApiIds })
    if ($ownedEngines.Count -ne 1) { throw "Single-instance launch created $($ownedEngines.Count) engines" }

    [void]$desktop.CloseMainWindow()
    if (-not $desktop.WaitForExit(15000)) { throw "Desktop did not close after its main window was requested to close" }
    for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
        if (-not (Get-Process -Id $engine.ProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $engine.ProcessId -ErrorAction SilentlyContinue) {
        throw "Windows Job Object did not terminate the embedded engine"
    }
    Write-Output "Packaged Tauri lifecycle smoke passed on dynamic port $port."
} finally {
    if ($second -and -not $second.HasExited) { Stop-Process -Id $second.Id -Force -ErrorAction SilentlyContinue }
    if ($desktop -and -not $desktop.HasExited) { Stop-Process -Id $desktop.Id -Force -ErrorAction SilentlyContinue }
}
