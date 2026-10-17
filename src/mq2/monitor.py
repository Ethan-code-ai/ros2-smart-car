"""Receive real MQ2 readings from the STM32; never transmit serial commands."""

from __future__ import annotations

import argparse
from datetime import datetime
import re
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    raise SystemExit("缺少 pyserial，请运行：python -m pip install pyserial")


READING = re.compile(r"MQ2,adc=(\d+),pin_mv=(\d+),vref_mv=(\d+)")


def parse_reading(line: str) -> tuple[int, int, int] | None:
    match = READING.fullmatch(line.strip())
    if match is None:
        return None
    adc, pin_mv, vref_mv = map(int, match.groups())
    if not (0 <= adc <= 4095 and 0 < vref_mv <= 3600 and 0 <= pin_mv <= vref_mv):
        return None
    return adc, pin_mv, vref_mv


def select_port(requested: str | None) -> str:
    if requested:
        return requested
    ports = list(list_ports.comports())
    candidates = [p.device for p in ports if p.vid == 0x1A86 and p.pid == 0x7523]
    if len(candidates) == 1:
        return candidates[0]
    available = ", ".join(f"{p.device} ({p.description})" for p in ports) or "无"
    if not candidates:
        raise ValueError(f"没有发现 CH340。当前串口：{available}")
    raise ValueError(f"发现多个 CH340：{', '.join(candidates)}。请用 --port 指定。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显示 STM32 通过 CH340 发送的真实 MQ2 ADC 读数")
    parser.add_argument("--port", help="串口，例如 COM9；省略时自动选择唯一的 CH340")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=0, help="读取秒数；0 表示持续读取，Ctrl+C 退出")
    args = parser.parse_args(argv)
    if args.duration < 0 or args.baud <= 0:
        parser.error("duration 必须 >= 0，baud 必须 > 0")
    try:
        port_name = select_port(args.port)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    connection = serial.Serial(port=None, baudrate=args.baud, timeout=0.25)
    connection.dtr = False
    connection.rts = False
    connection.port = port_name
    count = 0
    pending = bytearray()
    discarding = False
    started = time.monotonic()
    notice_at = started + 5
    try:
        connection.open()
        print(f"已连接 {port_name}，{args.baud}，8N1；只接收，不发送。", flush=True)
        print("ADC 范围 0–4095；A0 电压按 3.3V 参考值估算，不是 ppm。Ctrl+C 退出。", flush=True)
        while args.duration == 0 or time.monotonic() - started < args.duration:
            incoming = connection.read(min(max(connection.in_waiting, 1), 512))
            for byte in incoming:
                if byte == 10:
                    if not discarding:
                        line = pending.decode("ascii", errors="backslashreplace").strip()
                        reading = parse_reading(line)
                        if reading is not None:
                            adc, pin_mv, _ = reading
                            stamp = datetime.now().strftime("%H:%M:%S")
                            print(f"{stamp}  MQ2 ADC: {adc:4d} / 4095    A0 ≈ {pin_mv / 1000:.3f} V", flush=True)
                            count += 1
                        elif line:
                            print(f"[串口] {line}", flush=True)
                    pending.clear()
                    discarding = False
                elif not discarding:
                    pending.append(byte)
                    if len(pending) > 512:
                        pending.clear()
                        discarding = True
            if count == 0 and time.monotonic() >= notice_at:
                print("尚未收到 MQ2 数值：请确认已烧录采集固件、BOOT0=0 后复位，且 TX/RX 接线正确。", flush=True)
                notice_at = float("inf")
    except KeyboardInterrupt:
        print("\n已停止读取。")
    except (serial.SerialException, OSError) as exc:
        print(f"串口错误：{exc}。请关闭占用串口的烧录工具/串口助手后重试。", file=sys.stderr)
        return 1
    finally:
        connection.close()
    if count == 0:
        print("没有收到有效 MQ2 读数；未生成模拟数值。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
