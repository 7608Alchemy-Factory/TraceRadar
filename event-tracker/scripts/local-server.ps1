param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start',
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)]
    [int]$Port = 5173,
    [string]$DatabasePath
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runnerPath = Join-Path $PSScriptRoot 'local_workspace.py'
$pythonPath = Join-Path $workspaceRoot 'backend\.venv\Scripts\python.exe'
$instanceName = if ($Port -eq 5173) { 'local-workspace' } else { "local-workspace-$Port" }
$dataFolder = Join-Path $workspaceRoot 'data'
$processFile = Join-Path $dataFolder "$instanceName.process.json"
$entryUrl = "http://127.0.0.1:$Port"
$requestedDatabase = if ($DatabasePath) {
    if ([IO.Path]::IsPathRooted($DatabasePath)) { [IO.Path]::GetFullPath($DatabasePath) }
    else { [IO.Path]::GetFullPath((Join-Path $workspaceRoot $DatabasePath)) }
}

function Get-LocalWorkspace {
    $response = $null
    $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create("$entryUrl/api/local-workspace")
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    }
    catch { return $null }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Test-WorkspaceOwner($status) {
    return $status -and $status.application -eq 'TraceRadar' -and $status.workspace_root -eq $workspaceRoot
}

function Get-OwnedProcess($serverProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$serverProcessId)" -ErrorAction SilentlyContinue
    if ($process -and $process.Name -in @('python.exe', 'pythonw.exe') -and $process.CommandLine -and $process.CommandLine.IndexOf($runnerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $process
    }
    return $null
}

try {
    $status = Get-LocalWorkspace
    if ($Action -eq 'status') {
        if (Test-WorkspaceOwner $status) {
            Write-Host "TraceRadar is running: $entryUrl (PID $($status.process_id))"
            exit 0
        }
        Write-Host 'TraceRadar is not running.'
        exit 1
    }

    if ($Action -eq 'stop') {
        $ownedProcess = $null
        if (Test-WorkspaceOwner $status) {
            $ownedProcess = Get-OwnedProcess $status.process_id
        }
        elseif (Test-Path -LiteralPath $processFile) {
            $record = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
            $ownedProcess = Get-OwnedProcess $record.process_id
        }
        if ($ownedProcess) {
            Stop-Process -Id $ownedProcess.ProcessId -ErrorAction Stop
            Write-Host 'TraceRadar stopped. Your database and configuration are preserved.'
        }
        else { Write-Host 'No running TraceRadar process was found.' }
        if (Test-Path -LiteralPath $processFile) { Remove-Item -LiteralPath $processFile }
        exit 0
    }

    if ((Test-WorkspaceOwner $status) -and $requestedDatabase -and $status.database_path -ne $requestedDatabase) {
        throw "TraceRadar on port $Port is serving a different database. Stop that instance before switching databases."
    }
    if (!(Test-WorkspaceOwner $status)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
            throw "Port $Port is occupied by another service. No process was stopped."
        }
        if (!(Test-Path -LiteralPath $pythonPath)) {
            $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if (!$python) { throw 'Python was not found. Follow the backend setup in README.md.' }
            $pythonPath = $python.Source
        }
        & $pythonPath -c 'import fastapi, httpx, uvicorn, dotenv'
        if ($LASTEXITCODE -ne 0) { throw "Backend dependencies are missing for $pythonPath. Follow README.md." }
        if (!(Test-Path -LiteralPath (Join-Path $workspaceRoot 'frontend\dist\index.html'))) {
            throw 'The frontend build is missing. Run npm ci and npm run build in frontend first.'
        }

        # WMI owns this hidden process, so it is independent of the chat's temporary terminal/job.
        # This creates no scheduled task, login entry, or system service.
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow = [uint16]0; CreateFlags = [uint32]8}
        $commandLine = "`"$pythonPath`" `"$runnerPath`" --port $Port"
        if ($requestedDatabase) { $commandLine += " --database-path `"$requestedDatabase`"" }
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $commandLine
            CurrentDirectory = $workspaceRoot
            ProcessStartupInformation = $startup
        }
        if ($result.ReturnValue -ne 0) {
            throw "Windows could not start TraceRadar (code $($result.ReturnValue))."
        }
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 250
            $status = Get-LocalWorkspace
            if (Test-WorkspaceOwner $status) { break }
        }
        if (!(Test-WorkspaceOwner $status)) {
            throw "TraceRadar did not become ready. See $(Join-Path $dataFolder "$instanceName.log")."
        }
    }

    New-Item -ItemType Directory -Path $dataFolder -Force | Out-Null
    @{process_id = $status.process_id; runner = $runnerPath} | ConvertTo-Json | Set-Content -LiteralPath $processFile -Encoding UTF8
    Write-Host "TraceRadar is ready: $entryUrl"
    Write-Host "API documentation: $entryUrl/docs"
    Write-Host 'This window may be closed; the workspace keeps running until stopped or Windows shuts down.'
    if (!$NoBrowser) { Start-Process $entryUrl }
}
catch {
    Write-Error $_
    exit 1
}
