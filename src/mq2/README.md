# STM32F103C8T6 + MQ2 + OLED + CH340 读数

独立固件通过 PA0 读取 ADC，每 500ms 同时更新 OLED 和 USART1 串口。显示 ADC 值（0–4095）和按 3.3V 参考电压估算的 A0 电压。尚未完成浓度标定，屏幕明确显示 `PPM: NOT CAL`（未校准），不把原始值冒充 ppm。

## 接线

| 信号 | STM32 接脚 |
| --- | --- |
| MQ2 AO | PA0（板上 A0；5V 供电时必须先分压，见下文） |
| MQ2 GND | G / GND |
| CH340 TXD | PA10 / A10 |
| CH340 RXD | PA9 / A9 |
| CH340 GND | G / GND |

STM32 用自己的 USB 线供电；CH340 只接 TXD、RXD、GND，不并接它的 VCC。确认 USB 转串口模块的信号电平适合 3.3V STM32。MQ2 的 DO 本例不使用。

### OLED 接线

适用于用户照片中的四针 I2C OLED，先按常见的 SSD1306 兼容 128×64 屏准备驱动。照片可以确认接口和针脚，不能单凭外观保证内部控制器型号；实际点亮后验证兼容性。

| OLED 正面丝印 | STM32 接脚 |
| --- | --- |
| GND | G / GND |
| VCC | 3.3V |
| SCL | PB6 / B6 |
| SDA | PB7 / B7 |

断电后接线，并按每个引脚旁的文字辨认，正反面观察时左右相反。OLED 使用 3.3V；MQ2 正常加热使用 5V，两个模块的 VCC 不接到同一根供电线上。

程序使用硬件 I2C1、100kHz，依次尝试 7 位地址 `0x3C` 和 `0x3D`。屏幕背面的 `0x78`/`0x7A` 是相应的 8 位写地址。串口会报告 `OLED READY,addr=...` 或 `OLED NOT FOUND`；OLED 未接上或通信失败时继续采集并发送 MQ2 数据，每 2 秒尝试恢复。

屏幕显示：顶部 `MQ2 SENSOR`，中间大字 ADC 数值，下方 `A0` 电压及 `PPM: NOT CAL`。显示内容约每半秒更新。[SSD1306 命令参考](https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf)

### MQ2 供电与 AO 分压

目前用户的 MQ2 接在 3V 电源上。这可以用来排查 ADC/串口链路，但不能据此评估传感器正常灵敏度。常规 MQ2 的加热电压要求为 5.0V±0.1V，输出也可能超过 STM32 的模拟输入范围。模块灯亮不能证明加热条件正确。[MQ2 厂家资料](https://www.winsen-sensor.com/product/mq-2.html)

正式观察传感器响应时，MQ2 使用有足够电流余量的稳定 5V 电源，GND 共地。用户套件中已有 1kΩ 电阻，取两只按下面分压：

```text
MQ2 AO ── 1kΩ ──┬── STM32 A0
                 │
                1kΩ
                 │
                GND
```

两只相同的 10kΩ 也可分压。这样 AO 为 5V 时 A0 约为 2.5V。程序显示的是分压后的 A0 电压；原 AO 电压约为显示值的两倍。没有分压电阻时，不要把 5V 供电模块的 AO 直接接到 A0。分压电阻会改变模块的有效负载，必须在最终接线下重新采集校准基准。初次预热期间原始值可能漂移；本例没有完成气体标定。

## 编译

本机已安装 STM32CubeIDE、GCC 和 STM32CubeF1 HAL 1.8.6。此工程复用本地 SDK，不改动其他 STM32 示例，也不需要下载库。

在 PowerShell 中运行：

```powershell
Set-Location 'D:\智能小车\ros2-smart-car\src\mq2'
.\build.ps1
```

生成 `build\mq2.hex`、`mq2.bin`、`mq2.elf` 和 `mq2.map`。目标内存严格设为 STM32F103C8T6 的 64KB Flash、20KB RAM。默认使用内部 8MHz HSI，不依赖板上晶振。

## 通过 CH340 烧录

1. 关闭串口助手和正在运行的 `monitor.py`。
2. 断电，设置 **BOOT0/BT0=1，BOOT1/BT1=0**，重新供电并按一次 RESET。
3. 运行下面命令。脚本自动选择唯一的 CH340；必要时追加 `-Port COM9`，实际端口号可能随 USB 接口变化。

```powershell
.\flash.ps1
```

脚本先把原有 64KB Flash 备份至 `build\backups`，备份失败即停止，不解除读保护。然后只烧录新固件所需页并校验，不执行全片擦除或更改选项字节。如果之前单独执行过 `flash.ps1 -ProbeOnly`，正式烧录前请再按一次 RESET。

4. 烧录成功后将 **BOOT0/BT0 改回 0**，按 RESET。BOOT1/BT1 保持 0。

## 显示真实读数

双击 `start-monitor.cmd`，或运行：

```powershell
python .\monitor.py
```

波特率为 **115200、8 数据位、无校验、1 停止位**。脚本只接收，不向板子发送命令。输出格式如下（这里只说明格式，数值须由真实硬件提供）：

```text
HH:MM:SS  MQ2 ADC: <原始值> / 4095    A0 ≈ <电压> V
```

按 Ctrl+C 停止。需要限时检查时可运行 `python .\monitor.py --duration 5`；没有收到有效数据时返回退出码 2，不填充模拟数值。

固件的原始串口格式为 `MQ2,adc=<原始值>,pin_mv=<毫伏>,vref_mv=3300`。若持续显示 0 或接近 4095，需核对 AO、共地、供电和 A0 实际电压；不要用固定值直接判断是否有烟雾。

## 本次硬件验证（2026-10-14）

通过 CH340 COM9 连接到芯片 ID `0x410`、ROM Bootloader 2.2。已备份原有 65536 字节 Flash，烧录并回读校验通过；只擦除了新程序占用的第 0–6 页。BOOT0 恢复 0 并复位后，已实际收到启动信息和连续读数，例如 ADC=586、A0≈0.472V，约每半秒一条。验证时 MQ2 仍采用用户原来的 3V 供电，因此只证明采集与通信链路工作。

原程序备份：`build\backups\original-flash-20261014-152309-979.bin`。固件、构建输出和这个备份都保存在本机；本次没有修改原有 hello/LED/Beep 示例。

随后完成 OLED 版集成：固件占用 Flash 9432 字节、RAM 3304 字节，已通过 COM10 烧录并回读校验，擦除范围为第 0–9 页。更新前的完整 Flash 备份为 `build\backups\original-flash-20261014-155321-208.bin`。复位后收到 `OLED READY,addr=0x3C,scl=PB6,sda=PB7` 及连续 MQ2 读数，例如 ADC=472、A0≈0.380V；用户确认 OLED 已出现文字或数字。当前浓度显示为 `PPM: NOT CAL`，后续校准需先确认 MQ2 的最终供电及分压接线。
