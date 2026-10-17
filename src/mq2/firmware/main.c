/*
 * STM32F103C8T6: PA0 ADC1_IN0 -> USART1 PA9 TX / PA10 RX, 115200 8N1.
 * Use only a 0..VDDA analog signal on PA0. A 5 V MQ2 AO needs a divider.
 * pin_mv estimates the voltage at PA0 using an assumed 3300 mV VDDA.
 * It is not the MQ2 AO voltage before a divider, and is not gas ppm.
 */
#include "stm32f1xx_hal.h"
#include "oled.h"

#define SAMPLE_COUNT 16U
#define REPORT_INTERVAL_MS 500U
#define ASSUMED_VREF_MV 3300U

static ADC_HandleTypeDef hadc1;
static UART_HandleTypeDef huart1;
static uint8_t uart_ready;
static uint8_t oled_ready;

static HAL_StatusTypeDef uart_text(const char *text)
{
    uint16_t length = 0U;
    while (text[length] != '\0') {
        ++length;
    }
    return HAL_UART_Transmit(&huart1, (const uint8_t *)text, length, 200U);
}

static char *append_text(char *out, const char *text)
{
    while (*text != '\0') {
        *out++ = *text++;
    }
    return out;
}

static char *append_uint(char *out, uint32_t value)
{
    char digits[10];
    uint32_t count = 0U;
    do {
        digits[count++] = (char)('0' + value % 10U);
        value /= 10U;
    } while (value != 0U);
    while (count != 0U) {
        *out++ = digits[--count];
    }
    return out;
}

static void fatal_error(const char *stage, HAL_StatusTypeDef status)
{
    /* Initialization before USART1 is available cannot report over USART1. */
    for (;;) {
        if (uart_ready != 0U) {
            char line[96];
            char *out = append_text(line, "MQ2 ERROR,stage=");
            out = append_text(out, stage);
            out = append_text(out, ",hal=");
            out = append_uint(out, (uint32_t)status);
            out = append_text(out, "\r\n");
            *out = '\0';
            (void)uart_text(line);
        }
        HAL_Delay(1000U);
    }
}

static void require_ok(const char *stage, HAL_StatusTypeDef status)
{
    if (status != HAL_OK) {
        fatal_error(stage, status);
    }
}

static void clock_init(void)
{
    RCC_OscInitTypeDef oscillator = {0};
    RCC_ClkInitTypeDef clocks = {0};
    RCC_PeriphCLKInitTypeDef peripherals = {0};

    /* No external crystal and no PLL are needed. HSI directly supplies 8 MHz. */
    oscillator.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    oscillator.HSIState = RCC_HSI_ON;
    oscillator.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    oscillator.PLL.PLLState = RCC_PLL_NONE;
    require_ok("HSI", HAL_RCC_OscConfig(&oscillator));

    clocks.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                       RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clocks.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
    clocks.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clocks.APB1CLKDivider = RCC_HCLK_DIV1;
    clocks.APB2CLKDivider = RCC_HCLK_DIV1;
    require_ok("SYSCLK", HAL_RCC_ClockConfig(&clocks, FLASH_LATENCY_0));

    peripherals.PeriphClockSelection = RCC_PERIPHCLK_ADC;
    peripherals.AdcClockSelection = RCC_ADCPCLK2_DIV2; /* ADC = 4 MHz. */
    require_ok("ADC_CLOCK", HAL_RCCEx_PeriphCLKConfig(&peripherals));
}

static void uart_init(void)
{
    GPIO_InitTypeDef gpio = {0};
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();

    gpio.Pin = GPIO_PIN_9;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOA, &gpio);

    gpio.Pin = GPIO_PIN_10;
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &gpio);

    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200U;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    require_ok("UART_INIT", HAL_UART_Init(&huart1));
    uart_ready = 1U;
}

static void adc_init(void)
{
    GPIO_InitTypeDef gpio = {0};
    ADC_ChannelConfTypeDef channel = {0};
    __HAL_RCC_ADC1_CLK_ENABLE();

    gpio.Pin = GPIO_PIN_0;
    gpio.Mode = GPIO_MODE_ANALOG;
    HAL_GPIO_Init(GPIOA, &gpio);

    hadc1.Instance = ADC1;
    hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
    hadc1.Init.ContinuousConvMode = DISABLE;
    hadc1.Init.DiscontinuousConvMode = DISABLE;
    hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
    hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
    hadc1.Init.NbrOfConversion = 1U;
    require_ok("ADC_INIT", HAL_ADC_Init(&hadc1));

    channel.Channel = ADC_CHANNEL_0;
    channel.Rank = ADC_REGULAR_RANK_1;
    channel.SamplingTime = ADC_SAMPLETIME_239CYCLES_5;
    require_ok("ADC_CHANNEL", HAL_ADC_ConfigChannel(&hadc1, &channel));
    require_ok("ADC_CALIBRATION", HAL_ADCEx_Calibration_Start(&hadc1));
}

static uint32_t adc_average(void)
{
    uint32_t sum = 0U;
    for (uint32_t index = 0U; index < SAMPLE_COUNT; ++index) {
        require_ok("ADC_START", HAL_ADC_Start(&hadc1));
        require_ok("ADC_TIMEOUT", HAL_ADC_PollForConversion(&hadc1, 10U));
        sum += HAL_ADC_GetValue(&hadc1);
        require_ok("ADC_STOP", HAL_ADC_Stop(&hadc1));
    }
    return (sum + SAMPLE_COUNT / 2U) / SAMPLE_COUNT;
}

static void report_sample(uint32_t adc)
{
    char line[80];
    uint32_t pin_mv = (adc * ASSUMED_VREF_MV + 2047U) / 4095U;
    char *out = append_text(line, "MQ2,adc=");
    out = append_uint(out, adc);
    out = append_text(out, ",pin_mv=");
    out = append_uint(out, pin_mv);
    out = append_text(out, ",vref_mv=3300\r\n");
    *out = '\0';
    require_ok("UART_TX", uart_text(line));
}

static void report_oled(void)
{
    if (oled_ready != 0U) {
        (void)uart_text(oled_address() == 0x3CU ?
            "OLED READY,addr=0x3C,scl=PB6,sda=PB7\r\n" :
            "OLED READY,addr=0x3D,scl=PB6,sda=PB7\r\n");
    } else {
        (void)uart_text("OLED NOT FOUND,scl=PB6,sda=PB7\r\n");
    }
}

void SysTick_Handler(void)
{
    /* HAL_Init installs a 1 ms SysTick. Delays and peripheral timeouts need it. */
    HAL_IncTick();
}

int main(void)
{
    require_ok("HAL_INIT", HAL_Init());
    clock_init();
    uart_init();
    adc_init();
    oled_ready = oled_init();
    report_oled();
    require_ok("UART_BANNER", uart_text(
        "MQ2 READY,chip=STM32F103C8T6,baud=115200,pin=PA0,"
        "samples=16,period_ms=500,vref_mv=3300\r\n"));

    uint32_t next_report = HAL_GetTick();
    uint32_t next_oled_probe = next_report + 2000U;
    for (;;) {
        if ((int32_t)(HAL_GetTick() - next_report) >= 0) {
            next_report += REPORT_INTERVAL_MS;
            uint32_t adc = adc_average();
            uint32_t pin_mv = (adc * ASSUMED_VREF_MV + 2047U) / 4095U;
            report_sample(adc);
            if (oled_ready != 0U) {
                if (!oled_show_reading(adc, pin_mv)) {
                    oled_ready = 0U;
                    next_oled_probe = HAL_GetTick() + 2000U;
                    (void)uart_text("OLED WRITE ERROR,retry_ms=2000\r\n");
                }
            } else if ((int32_t)(HAL_GetTick() - next_oled_probe) >= 0) {
                oled_ready = oled_init();
                next_oled_probe = HAL_GetTick() + 2000U;
                if (oled_ready != 0U) {
                    report_oled();
                }
            }
        } else {
            __WFI(); /* SysTick wakes the core every millisecond. */
        }
    }
}
