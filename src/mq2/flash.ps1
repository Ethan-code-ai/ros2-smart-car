param(
    [string]$Port,
    [string]$Programmer,
    [string]$FirmwareFile = (Join-Path $PSScriptRoot 'build\mq2.hex'),
    [switch]$ProbeOnly
)
$ErrorActionPreference = 'Stop'

if (-not $Programmer) {
    $pluginRoots = @('D:\ST', 'C:\ST')
    $programmers = foreach ($root in $pluginRoots) {
        Get-ChildItem -Path "$root\STM32CubeIDE_*\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_*\tools\bin\STM32_Programmer_CLI.exe" -ErrorAction SilentlyContinue
    }
    $Programmer = ($programmers | Sort-Object FullName -Descending | Select-Object -First 1).FullName
}
if (-not $Programmer -or -not (Test-Path -LiteralPath $Programmer)) {
    throw '找不到 STM32_Programmer_CLI.exe，请通过 -Programmer 指定路径。'
}
if (-not $Port) {
    $portJson = & python -c 'import json,serial.tools.list_ports; print(json.dumps([p.device for p in serial.tools.list_ports.comports() if p.vid==0x1a86 and p.pid==0x7523]))'
    if ($LASTEXITCODE -ne 0) { throw '无法枚举 CH340，请用 -Port COM9 指定。' }
    $ports = @(ConvertFrom-Json ($portJson -join ''))
    if ($ports.Count -ne 1) { throw '需要唯一的 CH340 串口，请用 -Port 指定。' }
    $Port = $ports[0]
}
if ($Port -notmatch '^COM[0-9]+$') { throw 'Port 应是 COM9 这样的 Windows 串口名。' }
if (-not $ProbeOnly -and -not (Test-Path -LiteralPath $FirmwareFile)) {
    throw '固件不存在，请先运行 build.ps1。'
}

Write-Host "串口 ISP：$Port；需要 BOOT0=1、BOOT1=0，并按一次复位键。"
$connection = @('-c', "port=$Port", 'br=115200', 'P=EVEN', 'db=8', 'sb=1', 'fc=OFF')
if ($ProbeOnly) {
    & $Programmer @connection
    if ($LASTEXITCODE -ne 0) { throw '未能连接 STM32 串口 Bootloader；没有执行擦除或写入。' }
    return
}

# Read and preserve the complete C8 flash before any programming operation.
# Do not remove read protection: doing so would erase the original firmware.
$backupDir = Join-Path $PSScriptRoot 'build\backups'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$backup = Join-Path $backupDir ('original-flash-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.bin')
& $Programmer @connection '-u' '0x08000000' '65536' $backup
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backup)) {
    throw '原固件备份失败，已停止；不会解除读保护或烧录。'
}
if ((Get-Item -LiteralPath $backup).Length -ne 65536) {
    throw '原固件备份长度错误，已停止烧录。'
}
Write-Host "原固件已备份：$backup"
# The bootloader is already synchronized by the backup connection. Reuse its
# initialized UART state; sending a second autobaud sync would require a reset.
$programConnection = $connection + @('noinit=1')
& $Programmer @programConnection '-w' $FirmwareFile '-v'
if ($LASTEXITCODE -ne 0) { throw "烧录/校验失败。原固件备份保存在：$backup" }
Write-Host '烧录及校验完成。请将 BOOT0 改回 0，按复位键，再运行 start-monitor.cmd。'
