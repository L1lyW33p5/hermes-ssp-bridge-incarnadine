[CmdletBinding()]
param(
    [switch]$CheckOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:SspRoot = $script:ProjectRoot
$script:HermesOk = $false
$script:SspOk = $false
$script:HermesExe = $null
$script:HermesRoot = $null
$script:ProfileDir = $null
$script:PythonExe = $null
$script:PythonWExe = $null
$script:HermesApiKey = $null
$script:HermesMessage = ''
$script:SspMessage = ''
$script:HermesVersion = ''
$script:BridgeTaskName = 'Hermes Bridge'
$script:GatewayTaskName = 'Hermes_Gateway_kikka'
$script:ControlTaskName = 'Hermes_SSP_Bridge_Control'
$script:RestartSspAfterDeployment = $false

function Wait-ForEnter {
    param([string]$Message = '按 Enter 键继续')
    [void](Read-Host $Message)
}

function Test-IsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Get-ElevationScriptPath {
    $path = [IO.Path]::GetFullPath($PSCommandPath)
    try {
        $root = [IO.Path]::GetPathRoot($path)
        $driveName = $root.TrimEnd('\').TrimEnd(':')
        $drive = Get-PSDrive -Name $driveName -PSProvider FileSystem -ErrorAction Stop
        if (-not [string]::IsNullOrWhiteSpace($drive.DisplayRoot)) {
            return Join-Path $drive.DisplayRoot $path.Substring($root.Length)
        }
    } catch {
        # Local/fixed drives do not need conversion. Keep the original path.
    }
    return $path
}

function Request-ScriptElevation {
    if (Test-IsAdministrator) {
        return $true
    }

    Write-Host ''
    Write-Host '自动部署需要管理员权限。即将弹出 Windows UAC，请选择“是”；确认后将在管理员 CMD 中重新运行完整脚本。' -ForegroundColor Yellow
    try {
        $scriptPath = (Get-ElevationScriptPath).Replace('"', '""')
        $projectRoot = Split-Path -Parent (Split-Path -Parent $scriptPath)
        $batchPath = (Join-Path $projectRoot '自动部署脚本.bat').Replace('"', '""')
        if (-not (Test-Path -LiteralPath $batchPath -PathType Leaf)) {
            throw "未找到 CMD 部署入口：$batchPath"
        }
        $arguments = '/d /c ""{0}""' -f $batchPath
        Start-Process -FilePath $env:ComSpec -ArgumentList $arguments -Verb RunAs -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Host '未能获得管理员权限：UAC 已取消，或管理员启动失败。' -ForegroundColor Red
        return $false
    }
}

function Get-HermesEnvironmentValue {
    foreach ($scope in @('Process', 'User', 'Machine')) {
        $value = [Environment]::GetEnvironmentVariable('hermes', $scope)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return [Environment]::ExpandEnvironmentVariables($value.Trim().Trim('"'))
        }
    }

    # A normal Hermes installation commonly adds hermes.exe to PATH without
    # creating a separate environment variable literally named "hermes".
    $command = Get-Command 'hermes.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        return $command.Source
    }

    $commonInstall = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\hermes.exe'
    if (Test-Path -LiteralPath $commonInstall -PathType Leaf) {
        return $commonInstall
    }

    return $null
}

function Resolve-HermesRoot {
    param([Parameter(Mandatory = $true)][string]$Executable)

    $cursor = (Get-Item -LiteralPath $Executable).Directory
    while ($null -ne $cursor) {
        if (Test-Path -LiteralPath (Join-Path $cursor.FullName 'profiles') -PathType Container) {
            return $cursor.FullName
        }
        if ($cursor.Name -ieq 'hermes') {
            return $cursor.FullName
        }
        $cursor = $cursor.Parent
    }
    return (Join-Path $env:LOCALAPPDATA 'hermes')
}

function Resolve-BridgePython {
    $configured = [Environment]::GetEnvironmentVariable('HERMES_BRIDGE_PYTHON', 'Process')
    if ([string]::IsNullOrWhiteSpace($configured)) {
        $configured = [Environment]::GetEnvironmentVariable('HERMES_BRIDGE_PYTHON', 'User')
    }
    if (-not [string]::IsNullOrWhiteSpace($configured) -and (Test-Path -LiteralPath $configured -PathType Leaf)) {
        return [IO.Path]::GetFullPath($configured)
    }

    $hermesPython = Join-Path (Split-Path -Parent $script:HermesExe) 'python.exe'
    if (Test-Path -LiteralPath $hermesPython -PathType Leaf) {
        return $hermesPython
    }

    $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return $python.Source
    }
    return $null
}

function Test-HermesEnvironment {
    $script:HermesExe = $null
    $script:HermesRoot = $null
    $script:ProfileDir = $null
    $script:PythonExe = $null
    $script:PythonWExe = $null
    $script:HermesVersion = ''

    $value = Get-HermesEnvironmentValue
    if ([string]::IsNullOrWhiteSpace($value)) {
        $script:HermesMessage = '未检测到环境变量 hermes，PATH 中也未找到 hermes.exe。'
        return $false
    }

    $candidate = $value
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        $candidate = Join-Path $candidate 'hermes.exe'
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $script:HermesMessage = "环境变量 hermes 指向的文件不存在：$value"
        return $false
    }
    if ([IO.Path]::GetFileName($candidate) -ine 'hermes.exe') {
        $script:HermesMessage = "环境变量 hermes 未指向 hermes.exe：$value"
        return $false
    }

    $script:HermesExe = [IO.Path]::GetFullPath($candidate)
    try {
        $versionOutput = (& $script:HermesExe --version 2>&1 | Out-String)
        if ($versionOutput -match '(?i)Hermes Agent v(\d+\.\d+\.\d+)') {
            $script:HermesVersion = $Matches[1]
            if ([version]$script:HermesVersion -lt [version]'0.18.0') {
                $script:HermesMessage = "Hermes 版本过旧：$($script:HermesVersion)，本项目最低验证版本为 0.18.0。"
                return $false
            }
        }
    } catch {
        $script:HermesVersion = ''
    }
    $script:HermesRoot = Resolve-HermesRoot -Executable $script:HermesExe
    $script:ProfileDir = Join-Path $script:HermesRoot 'profiles\kikka'
    $script:PythonExe = Resolve-BridgePython
    if ([string]::IsNullOrWhiteSpace($script:PythonExe)) {
        $script:HermesMessage = '已检测到 Hermes，但未找到可用于 Bridge 的 Python。'
        return $false
    }
    $pythonw = Join-Path (Split-Path -Parent $script:PythonExe) 'pythonw.exe'
    $script:PythonWExe = if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $pythonw } else { $script:PythonExe }
    $versionLabel = if ($script:HermesVersion) { " v$($script:HermesVersion)" } else { '' }
    $script:HermesMessage = "Hermes$versionLabel：$($script:HermesExe)"
    return $true
}

function Get-SspValidation {
    param([Parameter(Mandatory = $true)][string]$Root)

    if ([string]::IsNullOrWhiteSpace($Root)) {
        return [pscustomobject]@{ Ok = $false; Code = 'Empty'; Message = 'SSP 路径不能为空。' }
    }

    try {
        $fullRoot = [IO.Path]::GetFullPath($Root.Trim().Trim('"'))
    } catch {
        return [pscustomobject]@{ Ok = $false; Code = 'InvalidPath'; Message = 'SSP 路径格式无效。' }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $fullRoot 'ssp.exe') -PathType Leaf)) {
        return [pscustomobject]@{ Ok = $false; Code = 'NoSsp'; Message = "未检测到 SSP：$fullRoot 下不存在 ssp.exe。" }
    }

    $taromati = Join-Path $fullRoot 'ghost\Taromati2'
    if (-not (Test-Path -LiteralPath $taromati -PathType Container)) {
        return [pscustomobject]@{ Ok = $false; Code = 'NoGhost'; Message = 'ghost "Taromati2" 不存在。' }
    }

    $firstItem = Get-ChildItem -LiteralPath $taromati -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $firstItem) {
        return [pscustomobject]@{ Ok = $false; Code = 'EmptyGhost'; Message = 'ghost "Taromati2" 目录为空。' }
    }

    return [pscustomobject]@{ Ok = $true; Code = 'Ok'; Message = "SSP/Taromati2：$fullRoot"; Root = $fullRoot }
}

function Refresh-Environment {
    $script:HermesOk = Test-HermesEnvironment
    $sspResult = Get-SspValidation -Root $script:SspRoot
    $script:SspOk = [bool]$sspResult.Ok
    $script:SspMessage = $sspResult.Message
    if ($script:SspOk) {
        $script:SspRoot = $sspResult.Root
    }
}

function Show-EnvironmentHeader {
    Clear-Host
    $hermesMark = if ($script:HermesOk) { '✅' } else { '❌' }
    $sspMark = if ($script:SspOk) { '✅' } else { '❌' }
    Write-Host '当前环境：'
    Write-Host ('┌' + ('─' * 35) + '┐')
    Write-Host "│  Hermes  $hermesMark    SSP/Taromati2  $sspMark  │"
    Write-Host ('└' + ('─' * 35) + '┘')
}

function Select-CustomSspPath {
    while ($true) {
        Show-EnvironmentHeader
        $path = Read-Host '请输入 SSP 所在目录（输入 0 返回）'
        if ($path -eq '0') {
            return
        }
        $result = Get-SspValidation -Root $path
        if ($result.Ok) {
            $script:SspRoot = $result.Root
            $script:SspOk = $true
            $script:SspMessage = $result.Message
            Write-Host "检测成功：$($result.Message)" -ForegroundColor Green
            Start-Sleep -Milliseconds 800
            return
        }
        Write-Host $result.Message -ForegroundColor Red
        Wait-ForEnter
    }
}

function Get-PatchFiles {
    $patchRoot = Join-Path $script:ProjectRoot 'patches\taromati2'
    $payloadRoot = Join-Path $patchRoot 'ghost'
    if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
        return @()
    }
    return @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
}

function Get-PatchRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullName)
    $root = (Join-Path $script:ProjectRoot 'patches\taromati2').TrimEnd('\') + '\'
    return $FullName.Substring($root.Length)
}

function Test-DeploymentComplete {
    if (-not $script:HermesOk -or -not $script:SspOk) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $script:ProfileDir -PathType Container)) {
        return $false
    }
    $soul = Join-Path $script:ProfileDir 'SOUL.md'
    if (-not (Test-Path -LiteralPath $soul -PathType Leaf)) {
        return $false
    }
    $profileEnv = Join-Path $script:ProfileDir '.env'
    $apiKey = Get-DotEnvFileValue -Path $profileEnv -Key 'API_SERVER_KEY'
    if ([string]::IsNullOrWhiteSpace($apiKey) -or $apiKey.Length -lt 32) {
        return $false
    }
    $files = Get-PatchFiles
    if ($files.Count -eq 0) {
        return $false
    }
    $targetRoot = Join-Path $script:SspRoot 'ghost\Taromati2'
    foreach ($file in $files) {
        $relative = Get-PatchRelativePath -FullName $file.FullName
        $target = Join-Path $targetRoot $relative
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            return $false
        }
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
            return $false
        }
    }
    return $true
}

function Test-ChinaMainlandIp {
    Write-Host '正在检测公网 IP 所在地区……'
    try {
        $result = Invoke-RestMethod -Uri 'https://api.country.is/' -Method Get -TimeoutSec 8
        return ([string]$result.country -ieq 'CN')
    } catch {
        try {
            $country = (Invoke-RestMethod -Uri 'https://ipapi.co/country/' -Method Get -TimeoutSec 8).ToString().Trim()
            return ($country -ieq 'CN')
        } catch {
            Write-Host '公网 IP 地区检测失败，将使用 pip 默认源。' -ForegroundColor Yellow
            return $false
        }
    }
}

function Install-Dependencies {
    $requirements = Join-Path $script:ProjectRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "未找到 requirements.txt：$requirements"
    }
    if (-not (Test-Path -LiteralPath $script:PythonExe -PathType Leaf)) {
        throw "未找到 Python：$($script:PythonExe)"
    }

    $arguments = @('-m', 'pip', 'install', '-r', $requirements)
    if (Test-ChinaMainlandIp) {
        Write-Host '检测到中国大陆公网 IP，本次安装使用清华大学 TUNA PyPI 镜像。' -ForegroundColor Cyan
        $arguments = @('-m', 'pip', 'install', '--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple', '-r', $requirements)
    } else {
        Write-Host '本次安装使用 pip 默认软件源。'
    }
    & $script:PythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python 依赖安装失败（退出码 $LASTEXITCODE）。"
    }
}

function Ensure-KikkaProfile {
    if (-not (Test-Path -LiteralPath $script:ProfileDir -PathType Container)) {
        Write-Host '未检测到 kikka profile，正在创建……'
        & $script:HermesExe profile create kikka
        if ($LASTEXITCODE -ne 0) {
            throw "hermes profile create kikka 失败（退出码 $LASTEXITCODE）。"
        }
    } else {
        Write-Host '已检测到 kikka profile。'
    }
    if (-not (Test-Path -LiteralPath $script:ProfileDir -PathType Container)) {
        throw "Hermes 命令执行后仍未找到 profile 目录：$($script:ProfileDir)"
    }

    $template = Join-Path $script:ProjectRoot 'patches\hermes\kikka\SOUL.md'
    if (-not (Test-Path -LiteralPath $template -PathType Leaf)) {
        throw "未找到 SOUL.md 模板：$template"
    }
    $target = Join-Path $script:ProfileDir 'SOUL.md'
    if ((Test-Path -LiteralPath $target -PathType Leaf) -and
        ((Get-FileHash -LiteralPath $template -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash)) {
        $backup = "$target.deploy-backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $target -Destination $backup -Force
        Write-Host "原 SOUL.md 已备份至：$backup"
    }
    Copy-Item -LiteralPath $template -Destination $target -Force
    Write-Host "已部署 SOUL.md：$target"
}

function Get-DotEnvFileValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ''
    }
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line -match ('^(?i)' + [regex]::Escape($Key) + '=(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ''
}

function New-SecureApiKey {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Ensure-HermesApiServerConfiguration {
    $profileEnv = Join-Path $script:ProfileDir '.env'
    $rootEnv = Join-Path $script:HermesRoot '.env'
    $apiKey = Get-DotEnvFileValue -Path $profileEnv -Key 'API_SERVER_KEY'
    if ([string]::IsNullOrWhiteSpace($apiKey) -or $apiKey.Length -lt 32) {
        $rootKey = Get-DotEnvFileValue -Path $rootEnv -Key 'API_SERVER_KEY'
        if (-not [string]::IsNullOrWhiteSpace($rootKey) -and $rootKey.Length -ge 32) {
            $apiKey = $rootKey
        } else {
            $apiKey = New-SecureApiKey
            Write-Host '已为 kikka profile 生成新的本地 API Server 密钥。' -ForegroundColor Cyan
        }
    }

    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $profileEnv -PathType Leaf) {
        foreach ($line in (Get-Content -LiteralPath $profileEnv -Encoding UTF8)) {
            $lines.Add($line)
        }
    } else {
        $lines.Add('# Hermes kikka profile settings managed by Hermes SSP Bridge.')
    }
    Set-DotEnvValue -Lines $lines -Key 'API_SERVER_ENABLED' -Value 'true'
    Set-DotEnvValue -Lines $lines -Key 'API_SERVER_KEY' -Value $apiKey
    Set-DotEnvValue -Lines $lines -Key 'API_SERVER_HOST' -Value '127.0.0.1'
    Set-DotEnvValue -Lines $lines -Key 'API_SERVER_PORT' -Value '8642'

    if (Test-Path -LiteralPath $profileEnv -PathType Leaf) {
        $existingNormalized = ((Get-Content -LiteralPath $profileEnv -Encoding UTF8) -join "`n")
        $updatedNormalized = ($lines -join "`n")
        if ($existingNormalized -ne $updatedNormalized) {
            $backup = "$profileEnv.deploy-backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item -LiteralPath $profileEnv -Destination $backup -Force
            Write-Host "原 profile .env 已备份至：$backup"
        }
    }
    [IO.File]::WriteAllLines($profileEnv, $lines, (New-Object System.Text.UTF8Encoding($false)))
    $script:HermesApiKey = $apiKey
    Write-Host 'kikka API Server 已配置为 127.0.0.1:8642。'
}

function Install-KikkaGateway {
    Write-Host '正在安装 kikka gateway 服务……'
    & $script:HermesExe --profile kikka gateway install --force --start-now --start-on-login
    if ($LASTEXITCODE -ne 0) {
        throw "hermes --profile kikka gateway install 失败（退出码 $LASTEXITCODE）。"
    }
}

function Test-KikkaGatewayHealthy {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8642/health' -Method Get -TimeoutSec 2
        return ([string]$health.status -ieq 'ok')
    } catch {
        return $false
    }
}

function Wait-KikkaGatewayHealthy {
    Write-Host '正在等待 kikka gateway API Server 就绪……'
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-KikkaGatewayHealthy) {
            Write-Host 'kikka gateway API Server 已就绪。' -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 1
    }
    throw 'kikka gateway 已安装，但 127.0.0.1:8642/health 在 30 秒内未就绪。请运行 hermes --profile kikka gateway status 查看详情。'
}

function Install-TaromatiPatches {
    $files = Get-PatchFiles
    if ($files.Count -eq 0) {
        throw 'patches\taromati2 中没有可部署文件。'
    }

    $targetRoot = Join-Path $script:SspRoot 'ghost\Taromati2'
    $backupRoot = Join-Path $script:ProjectRoot ("patch-backups\{0}\taromati2" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $backupCount = 0
    foreach ($file in $files) {
        $relative = Get-PatchRelativePath -FullName $file.FullName
        $target = Join-Path $targetRoot $relative
        $targetParent = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $targetParent -PathType Container)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }

        if (Test-Path -LiteralPath $target -PathType Leaf) {
            $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            if ($sourceHash -ne $targetHash) {
                $backup = Join-Path $backupRoot $relative
                New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
                Copy-Item -LiteralPath $target -Destination $backup -Force
                $backupCount++
            }
        }
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        $copiedHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        $expectedHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        if ($copiedHash -ne $expectedHash) {
            throw "Patch 哈希校验失败：$relative"
        }
        Write-Host "  [OK] $relative"
    }
    if ($backupCount -gt 0) {
        Write-Host "已备份 $backupCount 个原文件至：$backupRoot"
    }
}

function Copy-BackupTree {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot,
        [Parameter(Mandatory = $true)][string]$SafetyRoot
    )
    $count = 0
    foreach ($file in (Get-ChildItem -LiteralPath $SourceRoot -Recurse -File)) {
        $relative = $file.FullName.Substring($SourceRoot.TrimEnd('\').Length + 1)
        $destination = Join-Path $DestinationRoot $relative
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $safety = Join-Path $SafetyRoot $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $safety) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $safety -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        $count++
    }
    return $count
}

function Restore-BackupSet {
    $backupRoot = Join-Path $script:ProjectRoot 'patch-backups'
    $sets = @(
        Get-ChildItem -LiteralPath $backupRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d{8}-\d{6}$' } |
            Sort-Object Name -Descending
    )
    if ($sets.Count -eq 0) {
        Write-Host '未找到 patch/runtime 备份。' -ForegroundColor Yellow
        Wait-ForEnter
        return
    }

    Clear-Host
    Write-Host '可恢复的 patch/runtime 备份：'
    for ($index = 0; $index -lt $sets.Count; $index++) {
        Write-Host ("[{0}] {1}" -f ($index + 1), $sets[$index].Name)
    }
    Write-Host '[0] 返回'
    $choice = Read-Host 'Select'
    if ($choice -notmatch '^\d+$' -or [int]$choice -eq 0) { return }
    $selectedIndex = [int]$choice - 1
    if ($selectedIndex -lt 0 -or $selectedIndex -ge $sets.Count) { return }

    $selected = $sets[$selectedIndex]
    $confirm = Read-Host "确认恢复 $($selected.Name)？当前目标文件会先另存备份。(y/n)"
    if ($confirm -notmatch '^(?i)y$') { return }
    $safetyRoot = Join-Path $backupRoot ("{0}\pre-restore" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $restored = 0
    $taromatiSource = Join-Path $selected.FullName 'taromati2'
    if (Test-Path -LiteralPath $taromatiSource -PathType Container) {
        $restored += Copy-BackupTree -SourceRoot $taromatiSource -DestinationRoot (Join-Path $script:SspRoot 'ghost\Taromati2') -SafetyRoot (Join-Path $safetyRoot 'taromati2')
    }
    $runtimeSource = Join-Path $selected.FullName 'runtime'
    if (Test-Path -LiteralPath $runtimeSource -PathType Container) {
        $restored += Copy-BackupTree -SourceRoot $runtimeSource -DestinationRoot $script:SspRoot -SafetyRoot (Join-Path $safetyRoot 'runtime')
    }
    Write-Host "已恢复 $restored 个文件；恢复前状态保存在：$safetyRoot" -ForegroundColor Green
    Wait-ForEnter
}

function Restore-SoulBackup {
    $backups = @(Get-ChildItem -LiteralPath $script:ProfileDir -File -Filter 'SOUL.md.deploy-backup.*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
    if ($backups.Count -eq 0) {
        Write-Host '未找到 SOUL.md 部署备份。' -ForegroundColor Yellow
        Wait-ForEnter
        return
    }
    $source = $backups[0]
    $target = Join-Path $script:ProfileDir 'SOUL.md'
    $confirm = Read-Host "确认恢复最新 SOUL 备份 $($source.Name)？(y/n)"
    if ($confirm -notmatch '^(?i)y$') { return }
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        Copy-Item -LiteralPath $target -Destination "$target.pre-restore.$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    }
    Copy-Item -LiteralPath $source.FullName -Destination $target -Force
    Write-Host 'SOUL.md 已恢复。' -ForegroundColor Green
    Wait-ForEnter
}

function Show-RestoreMenu {
    while ($true) {
        Clear-Host
        Write-Host '[1] 恢复 patch/runtime 备份'
        Write-Host '[2] 恢复最新 SOUL.md 备份'
        Write-Host '[0] 返回主菜单'
        $choice = Read-Host 'Select'
        switch ($choice) {
            '1' { Restore-BackupSet }
            '2' { Restore-SoulBackup }
            '0' { return }
        }
    }
}

function Set-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )
    $found = $false
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        if ($Lines[$index] -match ('^(?i)' + [regex]::Escape($Key) + '=')) {
            $Lines[$index] = "$Key=$Value"
            $found = $true
        }
    }
    if (-not $found) {
        $Lines.Add("$Key=$Value")
    }
}

function Update-LocalConfiguration {
    $path = Join-Path $script:ProjectRoot '.env'
    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        foreach ($line in (Get-Content -LiteralPath $path -Encoding UTF8)) {
            $lines.Add($line)
        }
    } else {
        $lines.Add('# Generated local paths for Hermes SSP Bridge. Keep this file private.')
    }
    Set-DotEnvValue -Lines $lines -Key 'HERMES_SSP_ROOT' -Value $script:SspRoot
    Set-DotEnvValue -Lines $lines -Key 'HERMES_BRIDGE_PYTHON' -Value $script:PythonWExe
    Set-DotEnvValue -Lines $lines -Key 'HERMES_BRIDGE_SCRIPT' -Value (Join-Path $script:ProjectRoot 'bridge_wrapper.py')
    Set-DotEnvValue -Lines $lines -Key 'HERMES_GATEWAY_PYTHON' -Value $script:PythonExe
    Set-DotEnvValue -Lines $lines -Key 'HERMES_GATEWAY_MODULE' -Value 'hermes_cli.main'
    Set-DotEnvValue -Lines $lines -Key 'HERMES_GATEWAY_PROFILE' -Value 'kikka'
    Set-DotEnvValue -Lines $lines -Key 'HERMES_GATEWAY_HOME' -Value $script:ProfileDir
    Set-DotEnvValue -Lines $lines -Key 'HERMES_API_URL' -Value 'http://127.0.0.1:8642/v1/chat/completions'
    [IO.File]::WriteAllLines($path, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "已更新本机配置：$path"
}

function Get-TargetSspProcesses {
    $sspExe = [IO.Path]::GetFullPath((Join-Path $script:SspRoot 'ssp.exe'))
    return @(Get-CimInstance Win32_Process -Filter "Name = 'ssp.exe'" -ErrorAction SilentlyContinue | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        [IO.Path]::GetFullPath($_.ExecutablePath).Equals($sspExe, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Wait-ForSspExitBeforePatch {
    if ((Get-TargetSspProcesses).Count -eq 0) {
        return
    }
    $script:RestartSspAfterDeployment = $true
    while ((Get-TargetSspProcesses).Count -gt 0) {
        Write-Host ''
        Write-Host '检测到 SSP 正在运行。运行中覆盖 YAYA 辞书可能触发 last_work_able_dic 回滚。' -ForegroundColor Yellow
        Write-Host '请先从 SSP 菜单正常退出；脚本不会强制终止 SSP。'
        $choice = Read-Host '退出 SSP 后按 Enter 重新检测，输入 0 取消部署'
        if ($choice -eq '0') {
            throw '用户取消：SSP 尚未退出。'
        }
    }
    Write-Host 'SSP 已退出，可以安全部署 patch。' -ForegroundColor Green
}

function Restart-SspIfNeeded {
    if (-not $script:RestartSspAfterDeployment -or (Get-TargetSspProcesses).Count -gt 0) {
        return
    }
    $taskName = "Hermes_SSP_Restart_$PID"
    try {
        $sspExe = Join-Path $script:SspRoot 'ssp.exe'
        $action = New-ScheduledTaskAction -Execute $sspExe -WorkingDirectory $script:SspRoot
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        for ($attempt = 0; $attempt -lt 10 -and (Get-TargetSspProcesses).Count -eq 0; $attempt++) {
            Start-Sleep -Milliseconds 500
        }
        if ((Get-TargetSspProcesses).Count -eq 0) {
            throw '计划任务已启动，但 5 秒内未检测到 ssp.exe。'
        }
        Write-Host '已在普通用户会话中重新启动 SSP。' -ForegroundColor Green
    } catch {
        Write-Host "未能自动重新启动 SSP，请手动运行 ssp.exe：$($_.Exception.Message)" -ForegroundColor Yellow
    } finally {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        $script:RestartSspAfterDeployment = $false
    }
}

function Invoke-Deployment {
    Show-EnvironmentHeader
    Write-Host '开始部署 Hermes SSP Bridge……' -ForegroundColor Cyan
    try {
        Wait-ForSspExitBeforePatch
        Update-LocalConfiguration
        Install-Dependencies
        Ensure-KikkaProfile
        Ensure-HermesApiServerConfiguration
        Install-KikkaGateway
        Wait-KikkaGatewayHealthy
        Install-TaromatiPatches
        Refresh-RegisteredAutostartTasks
        if (-not (Test-DeploymentComplete)) {
            throw '部署结束后的 profile/patch 哈希校验未通过。'
        }
        Write-Host ''
        Write-Host '部署完成，所有 Patch 的 SHA-256 校验均已通过。' -ForegroundColor Green
    } catch {
        Write-Host ''
        Write-Host "部署失败：$($_.Exception.Message)" -ForegroundColor Red
    } finally {
        Restart-SspIfNeeded
    }
    Wait-ForEnter
}

function Test-TaskRegistered {
    param([Parameter(Mandatory = $true)][string]$TaskName)
    try {
        $null = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Test-GatewayAutostartRegistered {
    if (Test-TaskRegistered -TaskName $script:GatewayTaskName) {
        return $true
    }
    $startup = [Environment]::GetFolderPath('Startup')
    if ([string]::IsNullOrWhiteSpace($startup)) {
        return $false
    }
    foreach ($extension in @('vbs', 'cmd')) {
        $entry = Join-Path $startup ("{0}.{1}" -f $script:GatewayTaskName, $extension)
        if (Test-Path -LiteralPath $entry -PathType Leaf) {
            return $true
        }
    }
    return $false
}

function New-LogonTaskSettings {
    return New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
}

function Register-BridgeTask {
    $launcher = Join-Path $script:ProjectRoot 'scripts\start_bridge.vbs'
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B //Nologo "{0}"' -f $launcher) -WorkingDirectory $script:ProjectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $script:BridgeTaskName -Action $action -Trigger $trigger -Settings (New-LogonTaskSettings) -Description 'Hermes SSP Bridge (kikka)' -Force | Out-Null
}

function Register-ControlTask {
    $launcher = Join-Path $script:ProjectRoot 'scripts\start_bridge_control.vbs'
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B //Nologo "{0}"' -f $launcher) -WorkingDirectory $script:ProjectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $script:ControlTaskName -Action $action -Trigger $trigger -Settings (New-LogonTaskSettings) -Description 'Hermes SSP Bridge web control panel' -Force | Out-Null
}

function Refresh-RegisteredAutostartTasks {
    if (Test-TaskRegistered -TaskName $script:BridgeTaskName) {
        Register-BridgeTask
        Write-Host "已刷新隐藏启动任务：$($script:BridgeTaskName)"
    }
    if (Test-TaskRegistered -TaskName $script:ControlTaskName) {
        Register-ControlTask
        Write-Host "已刷新隐藏启动任务：$($script:ControlTaskName)"
    }
}

function Toggle-ScheduledTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][scriptblock]$RegisterAction
    )
    if (Test-TaskRegistered -TaskName $TaskName) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已注销：$TaskName" -ForegroundColor Yellow
    } else {
        & $RegisterAction
        Write-Host "已注册：$TaskName" -ForegroundColor Green
    }
}

function Toggle-GatewayTask {
    if (Test-GatewayAutostartRegistered) {
        & $script:HermesExe --profile kikka gateway uninstall
    } else {
        & $script:HermesExe --profile kikka gateway install --force --start-now --start-on-login
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes gateway 自启动切换失败（退出码 $LASTEXITCODE）。"
    }
}

function Get-RegistrationLabel {
    param([string]$TaskName)
    if (Test-TaskRegistered -TaskName $TaskName) { return '已注册' }
    return '未注册'
}

function Get-GatewayRegistrationLabel {
    if (Test-GatewayAutostartRegistered) { return '已注册' }
    return '未注册'
}

function Invoke-AutostartChoice {
    param([Parameter(Mandatory = $true)][ValidateSet('1', '2', '3')][string]$Choice)
    switch ($Choice) {
        '1' { Toggle-ScheduledTask -TaskName $script:BridgeTaskName -RegisterAction { Register-BridgeTask } }
        '2' { Toggle-GatewayTask }
        '3' { Toggle-ScheduledTask -TaskName $script:ControlTaskName -RegisterAction { Register-ControlTask } }
    }
}

function Write-AutostartError {
    param([Parameter(Mandatory = $true)][string]$Detail)
    if ($Detail -match '(?i)access.*denied|unauthorized|拒绝访问|权限') {
        Write-Host "自启动设置失败：权限不足（Access is denied）。详情：$Detail" -ForegroundColor Red
    } else {
        Write-Host "自启动设置失败：$Detail" -ForegroundColor Red
    }
}

function Show-AutostartMenu {
    while ($true) {
        Clear-Host
        Write-Host "[1] 注册/注销 Bridge 自启动 (当前状态：$(Get-RegistrationLabel $script:BridgeTaskName))"
        Write-Host "[2] 注册/注销 gateway(kikka) 自启动 (当前状态：$(Get-GatewayRegistrationLabel))"
        Write-Host "[3] 注册/注销 web控制面板 自启动 (当前状态：$(Get-RegistrationLabel $script:ControlTaskName))"
        Write-Host '[0] 返回主菜单'
        Write-Host ''
        $choice = Read-Host 'Select'
        try {
            switch ($choice) {
                '1' { Invoke-AutostartChoice -Choice $choice; Wait-ForEnter }
                '2' { Invoke-AutostartChoice -Choice $choice; Wait-ForEnter }
                '3' { Invoke-AutostartChoice -Choice $choice; Wait-ForEnter }
                '0' { return }
            }
        } catch {
            Write-AutostartError -Detail $_.Exception.Message
            Wait-ForEnter
        }
    }
}

function Get-CurrentModelSummary {
    $config = Join-Path $script:ProfileDir 'config.yaml'
    $summary = [ordered]@{ Provider = ''; Model = '' }
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
        return [pscustomobject]$summary
    }
    $insideModel = $false
    foreach ($line in (Get-Content -LiteralPath $config -Encoding UTF8)) {
        if ($line -match '^model:\s*$') {
            $insideModel = $true
            continue
        }
        if ($insideModel -and $line -match '^\S') {
            break
        }
        if ($insideModel -and $line -match '^\s+provider:\s*["'']?([^#"'']+?)["'']?\s*$') {
            $summary.Provider = $Matches[1].Trim()
        }
        if ($insideModel -and $line -match '^\s+default:\s*["'']?([^#"'']+?)["'']?\s*$') {
            $summary.Model = $Matches[1].Trim()
        }
    }
    return [pscustomobject]$summary
}

function Show-ModelConfiguration {
    Clear-Host
    $config = Join-Path $script:ProfileDir 'config.yaml'
    $configExists = Test-Path -LiteralPath $config -PathType Leaf
    if ($configExists) {
        $current = Get-CurrentModelSummary
        Write-Host 'kikka gateway 当前模型配置：'
        Write-Host "  Provider：$(if ($current.Provider) { $current.Provider } else { '未设置' })"
        Write-Host "  Model：$(if ($current.Model) { $current.Model } else { '未设置' })"
        Write-Host "  配置文件：$config"
        $answer = Read-Host '当前已有配置，是否需要修改？(y/n)'
    } else {
        Write-Host 'kikka gateway 尚未生成 config.yaml。' -ForegroundColor Yellow
        Write-Host "  将由 Hermes 官方模型向导创建：$config"
        $answer = Read-Host '是否现在创建并配置？(y/n)'
    }
    if ($answer -notmatch '^(?i)y$') {
        return
    }

    Write-Host ''
    Write-Host '即将进入 Hermes 官方模型向导，请按提示依次选择 Provider、认证方式和模型。' -ForegroundColor Cyan
    & $script:HermesExe --profile kikka model
    if ($LASTEXITCODE -ne 0) {
        Write-Host "模型配置未完成（退出码 $LASTEXITCODE）。" -ForegroundColor Red
        Wait-ForEnter
        return
    }
    if ((Test-GatewayAutostartRegistered) -or (Test-KikkaGatewayHealthy)) {
        Write-Host '正在重启 kikka gateway 以应用新配置……'
        & $script:HermesExe --profile kikka gateway restart
        if ($LASTEXITCODE -eq 0) {
            Wait-KikkaGatewayHealthy
        }
    }
    Write-Host 'kikka gateway 模型配置已更新。' -ForegroundColor Green
    Wait-ForEnter
}

function Show-CheckOnlyReport {
    Refresh-Environment
    [pscustomobject]@{
        hermes = $script:HermesOk
        hermes_version = $script:HermesVersion
        hermes_message = $script:HermesMessage
        ssp_taromati2 = $script:SspOk
        ssp_message = $script:SspMessage
        deployment_complete = (Test-DeploymentComplete)
        gateway_healthy = (Test-KikkaGatewayHealthy)
        gateway_autostart = (Test-GatewayAutostartRegistered)
        project_root = $script:ProjectRoot
    } | ConvertTo-Json
}

if (-not $CheckOnly -and -not (Test-IsAdministrator)) {
    if (Request-ScriptElevation) {
        exit 0
    }
    Wait-ForEnter
    exit 1
}

if ($CheckOnly) {
    Show-CheckOnlyReport
    exit 0
}

while ($true) {
    Refresh-Environment
    Show-EnvironmentHeader

    if ($script:HermesOk -and -not $script:SspOk) {
        Write-Host $script:SspMessage -ForegroundColor Yellow
        $custom = Read-Host '是否自定义 SSP/Taromati2 路径？(y/n)'
        if ($custom -match '^(?i)y$') {
            Select-CustomSspPath
            continue
        }
        Show-EnvironmentHeader
        Write-Host '[1] 重新检测        [0] 退出部署'
        Write-Host ''
        $choice = Read-Host 'Select'
        if ($choice -eq '0') { break }
        continue
    }

    if (-not $script:HermesOk -or -not $script:SspOk) {
        if (-not $script:HermesOk) { Write-Host $script:HermesMessage -ForegroundColor Yellow }
        if (-not $script:SspOk) { Write-Host $script:SspMessage -ForegroundColor Yellow }
        Write-Host '[1] 重新检测        [0] 退出部署'
        Write-Host ''
        $choice = Read-Host 'Select'
        if ($choice -eq '0') { break }
        continue
    }

    $deployed = Test-DeploymentComplete
    if (-not $deployed) {
        Write-Host '[1] 开始部署    [2] 备份恢复'
        Write-Host '[0] 退出部署'
        Write-Host ''
        $choice = Read-Host 'Select'
        if ($choice -eq '0') { break }
        if ($choice -eq '1') { Invoke-Deployment }
        if ($choice -eq '2') { Show-RestoreMenu }
        continue
    }

    Write-Host '[1] 重新部署    [2] 自启动管理'
    Write-Host '[3] kikka gateway 模型设置'
    Write-Host '[4] 备份恢复    [0] 退出部署'
    Write-Host ''
    $choice = Read-Host 'Select'
    switch ($choice) {
        '1' { Invoke-Deployment }
        '2' { Show-AutostartMenu }
        '3' { Show-ModelConfiguration }
        '4' { Show-RestoreMenu }
        '0' { exit 0 }
    }
}

exit 0
