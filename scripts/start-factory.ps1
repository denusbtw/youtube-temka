param(
    [ValidateSet('Controller','Worker')][string]$Role='Controller',
    [switch]$CpuOnly,[switch]$Rebuild,[switch]$Status,[switch]$WithTTS,
    [switch]$TestInference,[int]$TimeoutSeconds=300
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$oldTTS=$env:WITH_TTS
Push-Location $root
function DockerRun([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE): $($Arguments[0])" }
}
function Test-DockerEngine {
    try { & docker info *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
function WaitHttp([string]$Url) {
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { Start-Sleep 2 }
    } while ((Get-Date) -lt $deadline)
    throw "Readiness timeout: $Url"
}
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop with WSL2 support.' }
    $engineReady=Test-DockerEngine
    if (-not $engineReady -and -not $Status) {
        $desktop=@((Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),(Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $desktop) { throw 'Docker engine unavailable; install/start Docker Desktop.' }
        Start-Process -FilePath $desktop -WindowStyle Hidden
        $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
        do { Start-Sleep 3; $engineReady=Test-DockerEngine } while (-not $engineReady -and (Get-Date) -lt $deadline)
    }
    if (-not $engineReady) { throw 'Docker engine is unavailable.' }
    if ((& docker info --format '{{.OSType}}') -ne 'linux') { throw 'Select Linux containers in Docker Desktop.' }
    if (-not $Status) {
        & wsl --status *> $null
        if ($LASTEXITCODE -ne 0) { throw 'WSL unavailable. Enable WSL2 and Docker Desktop WSL integration.' }
    }
    if (-not (Test-Path -LiteralPath '.env')) {
        if ($Status) { throw 'No .env; run startup first.' }
        if ($Role -eq 'Worker') { throw 'Configure .env with the existing controller token, private CONTROLLER_URL and unique WORKER_ID first.' }
        $bytes=New-Object byte[] 48
        $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $text=(Get-Content .env.example -Raw).Replace('FACTORY_TOKEN=',('FACTORY_TOKEN='+[Convert]::ToBase64String($bytes)))
        [IO.File]::WriteAllText((Join-Path $root '.env'),$text,(New-Object Text.UTF8Encoding($false)))
    }
    $settings=@{}
    foreach ($line in Get-Content -LiteralPath '.env') {
        if ($line -match '^\s*([A-Z_0-9]+)=(.*)$') { $settings[$matches[1]]=$matches[2].Trim().Trim('"').Trim("'") }
    }
    if (-not $settings.FACTORY_TOKEN -or $settings.FACTORY_TOKEN.Length -lt 32) { throw 'Existing .env requires FACTORY_TOKEN of at least 32 characters; file was preserved.' }
    if ($Role -eq 'Worker') {
        $uri=$null
        if (-not [Uri]::TryCreate($settings.CONTROLLER_URL,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -notin @('http','https') -or $uri.Host -match '100\.x|localhost|127\.0\.0\.1' -or -not $settings.WORKER_ID) { throw 'Worker requires a private controller URL and unique WORKER_ID in .env.' }
        $addresses=[Net.Dns]::GetHostAddresses($uri.DnsSafeHost)
        foreach ($address in $addresses) {
            $bytes=$address.GetAddressBytes()
            $private=if ($bytes.Length -eq 4) {
                $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or ($bytes[0] -eq 100 -and $bytes[1] -ge 64 -and $bytes[1] -le 127)
            } else { ($bytes[0] -band 254) -eq 252 }
            if (-not $private) { throw 'Worker controller must resolve only to private LAN/Tailscale addresses.' }
        }
    }
    $compose=@('compose','-f','docker-compose.yml')
    $state=Join-Path $root '.factory-runtime.json'
    $gpu=$false
    if ($Status -and (Test-Path $state)) { $gpu=(Get-Content $state -Raw | ConvertFrom-Json).gpu }
    if (-not $Status -and -not $CpuOnly -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        & docker compose -f docker-compose.yml -f compose.gpu.yml run --rm --no-deps --entrypoint nvidia-smi ollama -L
        $gpu=$LASTEXITCODE -eq 0
        if (-not $gpu) { Write-Host 'GPU runtime probe failed: CPU FALLBACK' }
    }
    if ($gpu) { $compose+=@('-f','compose.gpu.yml') } else { $compose+=@('-f','compose.cpu.yml') }
    $compose+=@('--profile',$Role.ToLower())
    $services=if ($Role -eq 'Controller') { @('ollama','controller','web') } else { @('ollama','worker') }
    $service=if ($Role -eq 'Controller') { 'controller' } else { 'worker' }
    if ($WithTTS) { $env:WITH_TTS='1' }
    $resolvedText=& docker @compose config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
    $resolved=$resolvedText | ConvertFrom-Json
    $effective=$resolved.services.$service.environment
    if ($effective.FACTORY_TOKEN -ne $settings.FACTORY_TOKEN) { throw 'Shell FACTORY_TOKEN conflicts with .env; remove the conflicting shell override.' }
    if ($Role -eq 'Worker' -and ($effective.CONTROLLER_URL -ne $settings.CONTROLLER_URL -or $effective.WORKER_ID -ne $settings.WORKER_ID)) { throw 'Shell worker settings conflict with the validated private configuration in .env.' }
    if ($Role -eq 'Controller') {
        $apiPort=$resolved.services.controller.ports[0]
        $webPort=$resolved.services.web.ports[0]
        $apiHost=if ($apiPort.host_ip -and $apiPort.host_ip -ne '0.0.0.0') { $apiPort.host_ip } else { '127.0.0.1' }
        $webHost=if ($webPort.host_ip -and $webPort.host_ip -ne '0.0.0.0') { $webPort.host_ip } else { '127.0.0.1' }
        $apiBase='http://'+$apiHost+':'+$apiPort.published
        $webBase='http://'+$webHost+':'+$webPort.published
    }
    if (-not $Status) {
        New-Item -ItemType Directory -Force -Path 'secrets','secrets/legacy-drive','scratch/controller','scratch/worker' | Out-Null
        if ($Role -eq 'Worker') {
            $controllerSettings=Invoke-RestMethod ($settings.CONTROLLER_URL.TrimEnd('/')+'/api/settings') -Headers @{Authorization='Bearer '+$settings.FACTORY_TOKEN} -TimeoutSec 15
            $localWorker=& docker @compose ps -q worker
            if ($LASTEXITCODE -ne 0) { throw 'Local worker inspection failed.' }
            $conflict=@($controllerSettings.workers | Where-Object { $_.id -eq $settings.WORKER_ID -and [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$_.seen -lt 60 })
            if ($conflict.Count -and -not $localWorker) { throw 'WORKER_ID is already active on the controller. Configure a unique ID for this machine.' }
        }
        if ($Role -eq 'Controller') {
            $existingJobs=$null
            try { $existingJobs=Invoke-RestMethod ($apiBase+'/api/jobs') -Headers @{Authorization='Bearer '+$settings.FACTORY_TOKEN} -TimeoutSec 5 } catch {
                $existingContainer=& docker @compose ps -q controller
                if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the existing controller; deployment stopped without restarting it.' }
                if ($existingContainer) {
                    WaitHttp ($apiBase+'/health') | Out-Null
                    $existingJobs=Invoke-RestMethod ($apiBase+'/api/jobs') -Headers @{Authorization='Bearer '+$settings.FACTORY_TOKEN} -TimeoutSec 15
                }
            }
            if (@($existingJobs | Where-Object { $_ -and $_.state -notin @('READY','PUBLISHED','FAILED','BLOCKED','CANCELLED') }).Count) {
                throw 'Active jobs detected. Use -Status; rerun startup after processing finishes to avoid interrupting work.'
            }
        }
        if ($WithTTS) { $env:WITH_TTS='1' }
        DockerRun ($compose+@('config','--quiet'))
        $inputs=@(& git ls-files --cached --others --exclude-standard | Sort-Object -Unique)
        $hashes=foreach ($inputPath in $inputs) {
            if ($inputPath -match '^(factory/|deploy/|dashboard/|requirements.*lock$|.*\.py$)' -and (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
                $inputPath+':'+(Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash
            }
        }
        $signatureText=($hashes -join '|')+"|$gpu|$($resolved.services.$service.build.args.WITH_TTS)"
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $signature=[Convert]::ToBase64String($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($signatureText))) } finally { $sha.Dispose() }
        $previous=if (Test-Path $state) { Get-Content $state -Raw | ConvertFrom-Json } else { $null }
        $images=if ($Role -eq 'Controller') { @(($resolved.name+'-controller'),($resolved.name+'-web')) } else { @($resolved.name+'-worker') }
        & docker image inspect @images *> $null
        if ($Rebuild -or $LASTEXITCODE -ne 0 -or -not $previous -or $previous.signature -ne $signature -or $previous.role -ne $Role) {
            DockerRun ($compose+@('build')+$(if ($Rebuild) { @('--pull') } else { @() })+@($service))
            if ($Role -eq 'Controller') { DockerRun ($compose+@('build')+$(if ($Rebuild) { @('--pull') } else { @() })+@('web')) }
        } else { Write-Host 'Unchanged images: reused without rebuilding.' }
        if ($Role -eq 'Controller') { DockerRun ($compose+@('run','--rm','--no-deps',$service,'python','-m','factory.deployment','backup')) }
        if ($Role -eq 'Controller') { DockerRun ($compose+@('run','--rm','--no-deps',$service,'python','-m','factory.library')) }
        DockerRun ($compose+@('up','-d','--wait','--wait-timeout',"$TimeoutSeconds",'ollama'))
        $model=& docker @compose run --rm --no-deps $service python -c 'from factory import config; print(config.MODEL)'
        if ($LASTEXITCODE -ne 0 -or -not $model) { throw 'Configured model could not be resolved.' }
        $model=$model.Trim()
        $models=& docker @compose exec -T ollama ollama list
        if ($LASTEXITCODE -ne 0) { throw 'Ollama model listing failed.' }
        if (-not ($models -match ('^'+[regex]::Escape($model)+'\s'))) {
            DockerRun ($compose+@('exec','-T','ollama','ollama','pull',$model))
        }
        & docker @compose run --rm --no-deps $service python -m factory.deployment whisper
        if ($LASTEXITCODE -ne 0) {
            if (-not $gpu) { throw 'CPU Whisper model initialization/inference failed.' }
            Write-Host 'CUDA Whisper verification failed: explicit CPU FALLBACK. GPU build remains available.'
            $gpu=$false
            $compose=@('compose','-f','docker-compose.yml','-f','compose.cpu.yml','--profile',$Role.ToLower())
            DockerRun ($compose+@('run','--rm','--no-deps',$service,'python','-m','factory.deployment','whisper'))
        }
        DockerRun ($compose+@('up','-d','--wait','--wait-timeout',"$TimeoutSeconds")+$services)
        @{gpu=$gpu;role=$Role;signature=$signature} | ConvertTo-Json -Compress | Set-Content $state
    }
    $containerId=& docker @compose ps -q $service
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw 'Application container is unavailable.' }
    $mountText=& docker inspect --format '{{json .Mounts}}' $containerId
    if ($LASTEXITCODE -ne 0) { throw 'Persistent mount inspection failed.' }
    $mounts=$mountText | ConvertFrom-Json
    foreach ($target in @('/data','/scratch','/home/factory/.cache')) {
        if (-not @($mounts | Where-Object { $_.Destination -eq $target -and $_.RW }).Count) { throw "Required persistent writable mount missing: $target" }
    }
    if (-not @($mounts | Where-Object { $_.Destination -eq '/run/secrets' -and -not $_.RW }).Count) { throw 'Read-only OAuth mount is missing.' }
    if ($Role -eq 'Controller') {
        $health=WaitHttp ($apiBase+'/health')
        if (-not $health.configured -or -not $health.worker_alive) { throw 'Controller authentication/local worker unhealthy.' }
        $headers=@{Authorization='Bearer '+$settings.FACTORY_TOKEN}
        $config=Invoke-RestMethod ($webBase+'/api/settings') -Headers $headers -TimeoutSec 15
        $workerId=$resolved.services.controller.environment.WORKER_ID
        $heartbeat=@($config.workers | Where-Object { $_.id -eq $workerId -and [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$_.seen -lt 60 })
        if (-not $heartbeat) { throw 'Controller local-worker heartbeat is missing or stale.' }
        $page=Invoke-WebRequest ($webBase+'/factory.html') -UseBasicParsing -TimeoutSec 15
        if ($page.StatusCode -ne 200) { throw 'Web frontend failed.' }
        Write-Host 'Controller: HEALTHY; Web UI and authenticated proxy: HEALTHY; Local Worker: READY'
        $readiness=Invoke-RestMethod ($webBase+'/api/readiness') -Headers $headers -TimeoutSec 120
        foreach ($loop in @('local_worker','publisher','analytics')) {
            if ($readiness.services.$loop -ne 'RUNNING') { throw "Required background service unavailable: $loop" }
        }
        Write-Host 'Publisher: RUNNING; Analytics background loop: RUNNING'
    } else {
        $headers=@{Authorization='Bearer '+$settings.FACTORY_TOKEN}
        $remote=Invoke-RestMethod ($settings.CONTROLLER_URL.TrimEnd('/')+'/api/settings') -Headers $headers -TimeoutSec 15
        $registered=@($remote.workers | Where-Object { $_.id -eq $settings.WORKER_ID -and [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$_.seen -lt 60 })
        if (-not $registered) { throw 'Remote worker heartbeat not verified by controller.' }
        Write-Host 'Remote worker heartbeat: READY'
    }
    DockerRun ($compose+@('exec','-T','ollama','ollama','list'))
    $model=& docker @compose exec -T $service python -c 'from factory import config; print(config.MODEL)'
    if ($LASTEXITCODE -ne 0) { throw 'Configured model lookup failed.' }
    $model=$model.Trim()
    $models=& docker @compose exec -T ollama ollama list
    if ($LASTEXITCODE -ne 0 -or -not ($models -match ('^'+[regex]::Escape($model)+'\s'))) { throw 'Configured Ollama model is unavailable.' }
    if ($TestInference) { DockerRun ($compose+@('exec','-T','ollama','ollama','run',$model,'Reply with OK.')) }
    $report=& docker @compose exec -T $service python -m factory.deployment readiness
    if ($LASTEXITCODE -ne 0) { throw 'Storage/integration readiness check failed.' }
    $report | Write-Host
    Write-Host 'AI CONTENT FACTORY 7.2; Repository: denusbtw/youtube-temka; Docker Engine: READY'
    Write-Host ('Commit: '+(& git rev-parse HEAD))
    Write-Host "GPU runtime: $gpu (see actual CUDA Whisper probe above; full video acceptance separate)"
    if ($Status) { Write-Host 'Whisper inference: not repeated in read-only status mode.' } else { Write-Host 'Whisper: READY (model loaded and inference completed in an exited process)' }
    $checks=$report | ConvertFrom-Json
    $overall=if (@($checks.PSObject.Properties | Where-Object { $_.Value -notin @('READY','CONNECTED','REMOTE_CONTROLLER') }).Count) { 'DEGRADED' } else { 'READY' }
    Write-Host "OVERALL: $overall (service readiness; full video acceptance is separate)"
    if ($Role -eq 'Controller') { Write-Host ('Application: '+$webBase+'/factory.html') }
} catch {
    Write-Error $_ -ErrorAction Continue
    Write-Host 'OVERALL: FAILED. Inspect scripts/logs-factory.ps1; credentials are not printed.'
    exit 1
} finally { $env:WITH_TTS=$oldTTS; Pop-Location }
