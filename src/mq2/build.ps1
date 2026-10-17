[CmdletBinding()]
param(
    [string]$SDK,
    [string]$CompilerDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Find-Sdk {
    $repository = Join-Path $env:USERPROFILE 'STM32Cube\Repository'
    if (Test-Path -LiteralPath $repository -PathType Container) {
        $candidates = @(Get-ChildItem -LiteralPath $repository -Directory -Filter 'STM32Cube_FW_F1_V*' |
            Sort-Object { [version]($_.Name -replace '^STM32Cube_FW_F1_V', '') } -Descending)
        foreach ($candidate in $candidates) {
            $header = Join-Path $candidate.FullName 'Drivers\STM32F1xx_HAL_Driver\Inc\stm32f1xx_hal.h'
            if (Test-Path -LiteralPath $header -PathType Leaf) { return $candidate.FullName }
        }
    }
    throw 'STM32Cube F1 SDK not found. Pass -SDK <STM32Cube_FW_F1_V1.8.6 directory>.'
}

function Find-Compiler {
    $installed = Get-Command 'arm-none-eabi-gcc.exe' -ErrorAction SilentlyContinue
    if ($null -ne $installed) { return (Split-Path -Parent $installed.Source) }

    $roots = @('D:\ST', 'C:\ST', 'C:\Program Files\STMicroelectronics', 'C:\Program Files (x86)\STMicroelectronics')
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $ides = @(Get-ChildItem -LiteralPath $root -Directory -Filter 'STM32CubeIDE*' |
            Sort-Object Name -Descending)
        foreach ($ide in $ides) {
            foreach ($plugins in @((Join-Path $ide.FullName 'STM32CubeIDE\plugins'), (Join-Path $ide.FullName 'plugins'))) {
                if (-not (Test-Path -LiteralPath $plugins -PathType Container)) { continue }
                $toolchains = @(Get-ChildItem -LiteralPath $plugins -Directory -Filter 'com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32*' |
                    Sort-Object Name -Descending)
                foreach ($toolchain in $toolchains) {
                    $bin = Join-Path $toolchain.FullName 'tools\bin'
                    if (Test-Path -LiteralPath (Join-Path $bin 'arm-none-eabi-gcc.exe') -PathType Leaf) { return $bin }
                }
            }
        }
    }
    throw 'ARM GCC not found. Pass -CompilerDir <directory containing arm-none-eabi-gcc.exe>.'
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($SDK)) { $SDK = Find-Sdk }
    if ([string]::IsNullOrWhiteSpace($CompilerDir)) { $CompilerDir = Find-Compiler }
    $SDK = (Resolve-Path -LiteralPath $SDK).Path
    $CompilerDir = (Resolve-Path -LiteralPath $CompilerDir).Path
    $gcc = Join-Path $CompilerDir 'arm-none-eabi-gcc.exe'
    $objcopy = Join-Path $CompilerDir 'arm-none-eabi-objcopy.exe'
    $size = Join-Path $CompilerDir 'arm-none-eabi-size.exe'
    foreach ($tool in @($gcc, $objcopy, $size)) {
        if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing tool: $tool" }
    }

    $firmware = Join-Path $PSScriptRoot 'firmware'
    $build = Join-Path $PSScriptRoot 'build'
    $objectDir = Join-Path $build 'obj'
    $hal = Join-Path $SDK 'Drivers\STM32F1xx_HAL_Driver'
    $cmsis = Join-Path $SDK 'Drivers\CMSIS'
    $device = Join-Path $cmsis 'Device\ST\STM32F1xx'
    $sources = @(
        (Join-Path $firmware 'main.c'),
        (Join-Path $firmware 'oled.c'),
        (Join-Path $firmware 'runtime.c'),
        (Join-Path $device 'Source\Templates\system_stm32f1xx.c'),
        (Join-Path $device 'Source\Templates\gcc\startup_stm32f103xb.s')
    )
    foreach ($module in @('hal', 'hal_cortex', 'hal_rcc', 'hal_rcc_ex', 'hal_gpio', 'hal_i2c', 'hal_dma', 'hal_adc', 'hal_adc_ex', 'hal_flash', 'hal_flash_ex', 'hal_pwr', 'hal_uart')) {
        $sources += Join-Path $hal "Src\stm32f1xx_$module.c"
    }
    $linkerScript = Join-Path $firmware 'stm32f103c8.ld'
    foreach ($inputFile in ($sources + @($linkerScript, (Join-Path $firmware 'stm32f1xx_hal_conf.h')))) {
        if (-not (Test-Path -LiteralPath $inputFile -PathType Leaf)) { throw "Missing input: $inputFile" }
    }

    New-Item -ItemType Directory -Force -Path $objectDir | Out-Null
    $common = @('-mcpu=cortex-m3', '-mthumb', '-mfloat-abi=soft', '-Os', '-g3', '-ffunction-sections', '-fdata-sections', '-DSTM32F103xB', '-DUSE_HAL_DRIVER')
    $includes = @("-I$firmware", "-I$(Join-Path $hal 'Inc')", "-I$(Join-Path $cmsis 'Include')", "-I$(Join-Path $device 'Include')")
    $objects = @()
    Write-Host "SDK: $SDK"
    Write-Host "Compiler: $gcc"
    foreach ($source in $sources) {
        $obj = Join-Path $objectDir (([IO.Path]::GetFileNameWithoutExtension($source)) + '.o')
        $compileArgs = $common + $includes
        if ([IO.Path]::GetExtension($source) -eq '.s') {
            $compileArgs += @('-x', 'assembler-with-cpp')
        } else {
            $compileArgs += @('-std=c11', '-Wall', '-Wextra', '-Werror')
        }
        $compileArgs += @('-c', $source, '-o', $obj)
        Write-Host "Compile $([IO.Path]::GetFileName($source))"
        Invoke-Checked $gcc $compileArgs
        $objects += $obj
    }

    $elf = Join-Path $build 'mq2.elf'
    $map = Join-Path $build 'mq2.map'
    $linkArgs = $common + @("-T$linkerScript", '-nostartfiles', '--specs=nano.specs', '--specs=nosys.specs', '-Wl,--gc-sections', "-Wl,-Map=$map", '-Wl,--print-memory-usage') + $objects + @('-o', $elf)
    Invoke-Checked $gcc $linkArgs
    Invoke-Checked $objcopy @('-O', 'ihex', $elf, (Join-Path $build 'mq2.hex'))
    Invoke-Checked $objcopy @('-O', 'binary', $elf, (Join-Path $build 'mq2.bin'))
    Invoke-Checked $size @($elf)
    Write-Host "Build complete: $build"
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
