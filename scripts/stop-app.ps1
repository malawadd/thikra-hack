<#
.SYNOPSIS
Stops Thikra processes serving the local web and API ports.

.DESCRIPTION
Finds listeners on Thikra's standard ports, walks only through related
Thikra launcher processes, and terminates the resulting process trees.
Supports -WhatIf for a non-destructive preview.

.PARAMETER Ports
Ports to inspect. Defaults to the web, API, and isolated E2E API ports.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [ValidateNotNullOrEmpty()]
    [int[]]$Ports = @(43191, 43192, 43292)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ListeningProcessIds {
    param([int[]]$RequestedPorts)

    try {
        return @(
            Get-NetTCPConnection -State Listen -ErrorAction Stop |
                Where-Object { $_.LocalPort -in $RequestedPorts } |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    catch {
        $found = [System.Collections.Generic.HashSet[int]]::new()
        foreach ($line in (& netstat.exe -ano -p TCP)) {
            if ($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                $localPort = [int]$Matches[1]
                $ownerId = [int]$Matches[2]
                if ($localPort -in $RequestedPorts) {
                    [void]$found.Add($ownerId)
                }
            }
        }
        return @($found)
    }
}

function Test-ThikraLauncherCommand {
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $patterns = @(
        'scripts[\\/](run-api|demo-human|e2e-server)\.mjs',
        'uvicorn\s+app\.main:app',
        'multiprocessing\.spawn.*spawn_main',
        'vite(\.js)?\s+dev.*43191',
        'concurrently.*--names\s+web,api',
        'pnpm(\.c?js|\.cmd)?\s+(dev|dev:web|dev:api)',
        '@thikra/web\s+dev'
    )
    return [bool]($patterns | Where-Object { $CommandLine -match $_ } | Select-Object -First 1)
}

$listenerIds = @(Get-ListeningProcessIds -RequestedPorts $Ports)
if ($listenerIds.Count -eq 0) {
    Write-Host "Thikra is not listening on ports $($Ports -join ', '). Nothing to stop."
    exit 0
}

$processRows = @()
try {
    $processRows = @(Get-CimInstance Win32_Process -ErrorAction Stop)
}
catch {
    Write-Warning 'Process ancestry could not be inspected; only listening processes will be stopped.'
}

$byId = @{}
$childrenByParent = @{}
foreach ($row in $processRows) {
    $processId = [int]$row.ProcessId
    $parentId = [int]$row.ParentProcessId
    $byId[$processId] = $row
    if (-not $childrenByParent.ContainsKey($parentId)) {
        $childrenByParent[$parentId] = [System.Collections.Generic.List[int]]::new()
    }
    $childrenByParent[$parentId].Add($processId)
}

$targets = [System.Collections.Generic.HashSet[int]]::new()
foreach ($listenerId in $listenerIds) {
    [void]$targets.Add([int]$listenerId)
}

# Include only related launcher ancestors and runtime descendants. This reaches
# concurrently/run-api wrappers without ever walking into the caller's shell.
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($targetId in @($targets)) {
        if ($byId.ContainsKey($targetId)) {
            $parentId = [int]$byId[$targetId].ParentProcessId
            if ($byId.ContainsKey($parentId) -and
                (Test-ThikraLauncherCommand -CommandLine $byId[$parentId].CommandLine) -and
                $targets.Add($parentId)) {
                $changed = $true
            }
        }
        if ($childrenByParent.ContainsKey($targetId)) {
            foreach ($childId in $childrenByParent[$targetId]) {
                if ($byId.ContainsKey($childId) -and
                    (Test-ThikraLauncherCommand -CommandLine $byId[$childId].CommandLine) -and
                    $targets.Add($childId)) {
                    $changed = $true
                }
            }
        }
    }
}

$roots = @(
    $targets |
        Where-Object { $byId.ContainsKey($_) } |
        Where-Object {
            $parentId = [int]$byId[$_].ParentProcessId
            -not ($targets.Contains($parentId) -and $byId.ContainsKey($parentId))
        } |
        Sort-Object
)

foreach ($processId in $roots) {
    $processName = 'unknown'
    if ($byId.ContainsKey($processId)) {
        $processName = [string]$byId[$processId].Name
    }
    $description = "PID $processId ($processName) and its child processes"
    if ($PSCmdlet.ShouldProcess($description, 'Stop Thikra process tree')) {
        if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
            Write-Host "Skipped $description because it already stopped."
            continue
        }
        $previousErrorPreference = $ErrorActionPreference
        try {
            # A sibling tree may already have stopped this PID. Native stderr
            # must be inspected through the exit code rather than promoted to
            # a terminating PowerShell error.
            $ErrorActionPreference = 'SilentlyContinue'
            $taskkillOutput = & taskkill.exe /PID $processId /T /F 2>&1
            $taskkillExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        if ($taskkillExitCode -ne 0 -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
            throw "Unable to stop $description. taskkill reported: $($taskkillOutput -join ' ')"
        }
        Write-Host "Stopped $description."
    }
}

if (-not $WhatIfPreference) {
    # Reloaders can briefly replace a worker after the initial process snapshot.
    # Re-scan the same known ports and terminate only those replacement owners.
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        Start-Sleep -Milliseconds 400
        $remaining = @(Get-ListeningProcessIds -RequestedPorts $Ports)
        if ($remaining.Count -eq 0) {
            break
        }
        $replacementIds = [System.Collections.Generic.HashSet[int]]::new()
        foreach ($remainingId in $remaining) {
            [void]$replacementIds.Add([int]$remainingId)
        }
        # Uvicorn reload workers can retain a socket whose reported owner is
        # an exited parent. Resolve those children by the port-owner PPID.
        foreach ($row in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
            if ([int]$row.ParentProcessId -in $remaining -and
                $row.CommandLine -match 'multiprocessing\.spawn.*spawn_main') {
                [void]$replacementIds.Add([int]$row.ProcessId)
            }
        }
        foreach ($remainingId in $replacementIds) {
            if (-not (Get-Process -Id $remainingId -ErrorAction SilentlyContinue)) {
                continue
            }
            Write-Host "Stopping replacement listener PID $remainingId (pass $attempt)."
            $previousErrorPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'SilentlyContinue'
                & taskkill.exe /PID $remainingId /T /F 2>&1 | Out-Null
            }
            finally {
                $ErrorActionPreference = $previousErrorPreference
            }
        }
    }
    $remaining = @(Get-ListeningProcessIds -RequestedPorts $Ports)
    if ($remaining.Count -gt 0) {
        throw "Thikra ports are still occupied by PID(s): $($remaining -join ', ')."
    }
    Write-Host "Thikra shutdown complete; ports $($Ports -join ', ') are free."
}
