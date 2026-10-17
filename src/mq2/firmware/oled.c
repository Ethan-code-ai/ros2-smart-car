/*
 * Small 128x64 SSD1306 driver, page addressing, I2C1 at 100 kHz.
 * Addresses 0x3C and 0x3D are probed; silkscreen may show 0x78/0x7A
 * (the corresponding 8-bit write addresses). No gas calibration is assumed.
 * Command reference: https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf
 */
#include "oled.h"
#include "stm32f1xx_hal.h"
#include <string.h>

#define WIDTH 128U
#define HEIGHT 64U
#define I2C_TIMEOUT_MS 30U

static I2C_HandleTypeDef hi2c1;
static uint8_t framebuffer[WIDTH * HEIGHT / 8U];
static uint8_t address;

/* Each column holds seven pixels, least significant bit at the top. */
static const uint8_t digits[10][5] = {
    {0x3E,0x51,0x49,0x45,0x3E}, {0x00,0x42,0x7F,0x40,0x00},
    {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4B,0x31},
    {0x18,0x14,0x12,0x7F,0x10}, {0x27,0x45,0x45,0x45,0x39},
    {0x3C,0x4A,0x49,0x49,0x30}, {0x01,0x71,0x09,0x05,0x03},
    {0x36,0x49,0x49,0x49,0x36}, {0x06,0x49,0x49,0x29,0x1E}
};
static const uint8_t letters[26][5] = {
    {0x7E,0x11,0x11,0x11,0x7E}, {0x7F,0x49,0x49,0x49,0x36},
    {0x3E,0x41,0x41,0x41,0x22}, {0x7F,0x41,0x41,0x22,0x1C},
    {0x7F,0x49,0x49,0x49,0x41}, {0x7F,0x09,0x09,0x09,0x01},
    {0x3E,0x41,0x49,0x49,0x7A}, {0x7F,0x08,0x08,0x08,0x7F},
    {0x00,0x41,0x7F,0x41,0x00}, {0x20,0x40,0x41,0x3F,0x01},
    {0x7F,0x08,0x14,0x22,0x41}, {0x7F,0x40,0x40,0x40,0x40},
    {0x7F,0x02,0x0C,0x02,0x7F}, {0x7F,0x04,0x08,0x10,0x7F},
    {0x3E,0x41,0x41,0x41,0x3E}, {0x7F,0x09,0x09,0x09,0x06},
    {0x3E,0x41,0x51,0x21,0x5E}, {0x7F,0x09,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31}, {0x01,0x01,0x7F,0x01,0x01},
    {0x3F,0x40,0x40,0x40,0x3F}, {0x1F,0x20,0x40,0x20,0x1F},
    {0x3F,0x40,0x38,0x40,0x3F}, {0x63,0x14,0x08,0x14,0x63},
    {0x07,0x08,0x70,0x08,0x07}, {0x61,0x51,0x49,0x45,0x43}
};

static uint8_t send_bytes(uint8_t control, const uint8_t *bytes, uint16_t count)
{
    return HAL_I2C_Mem_Write(&hi2c1, (uint16_t)address << 1, control,
                            I2C_MEMADD_SIZE_8BIT, (uint8_t *)bytes, count,
                            I2C_TIMEOUT_MS) == HAL_OK;
}

static void pixel(uint16_t x, uint16_t y)
{
    if (x < WIDTH && y < HEIGHT) {
        framebuffer[(y / 8U) * WIDTH + x] |= (uint8_t)(1U << (y % 8U));
    }
}

static void text(uint16_t x, uint16_t y, const char *value, uint8_t scale)
{
    while (*value != '\0' && x + 5U * scale <= WIDTH) {
        uint8_t glyph[5] = {0};
        char ch = *value++;
        if (ch >= '0' && ch <= '9') {
            memcpy(glyph, digits[(uint8_t)(ch - '0')], sizeof(glyph));
        } else if (ch >= 'A' && ch <= 'Z') {
            memcpy(glyph, letters[(uint8_t)(ch - 'A')], sizeof(glyph));
        } else if (ch == ':') {
            glyph[2] = 0x14;
        } else if (ch == '.') {
            glyph[2] = 0x60;
        } else if (ch == '-') {
            glyph[1] = glyph[2] = glyph[3] = 0x08;
        }
        for (uint8_t column = 0U; column < 5U; ++column) {
            for (uint8_t row = 0U; row < 7U; ++row) {
                if ((glyph[column] & (1U << row)) != 0U) {
                    for (uint8_t dx = 0U; dx < scale; ++dx) {
                        for (uint8_t dy = 0U; dy < scale; ++dy) {
                            pixel(x + column * scale + dx, y + row * scale + dy);
                        }
                    }
                }
            }
        }
        x += 6U * scale;
    }
}

static void uint_text(char *destination, uint32_t value)
{
    char reversed[10];
    uint8_t count = 0U;
    do {
        reversed[count++] = (char)('0' + value % 10U);
        value /= 10U;
    } while (value != 0U);
    while (count != 0U) {
        *destination++ = reversed[--count];
    }
    *destination = '\0';
}

static uint8_t flush(void)
{
    for (uint8_t page = 0U; page < 8U; ++page) {
        uint8_t position[] = {(uint8_t)(0xB0U + page), 0x00, 0x10};
        if (!send_bytes(0x00, position, sizeof(position)) ||
            !send_bytes(0x40, &framebuffer[page * WIDTH], WIDTH)) {
            return 0U;
        }
    }
    return 1U;
}

uint8_t oled_address(void)
{
    return address;
}

uint8_t oled_init(void)
{
    GPIO_InitTypeDef gpio = {0};
    address = 0U;
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_AFIO_CLK_ENABLE();
    __HAL_RCC_I2C1_CLK_ENABLE();
    __HAL_RCC_I2C1_FORCE_RESET();
    __HAL_RCC_I2C1_RELEASE_RESET();

    gpio.Pin = GPIO_PIN_6 | GPIO_PIN_7;
    gpio.Mode = GPIO_MODE_AF_OD;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOB, &gpio);

    hi2c1.Instance = I2C1;
    hi2c1.Init.ClockSpeed = 100000U;
    hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c1.Init.OwnAddress1 = 0U;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0U;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK) {
        return 0U;
    }
    HAL_Delay(100U);
    for (uint8_t candidate = 0x3CU; candidate <= 0x3DU; ++candidate) {
        if (HAL_I2C_IsDeviceReady(&hi2c1, (uint16_t)candidate << 1, 1U, 10U) == HAL_OK) {
            address = candidate;
            break;
        }
    }
    if (address == 0U) {
        return 0U;
    }
    static const uint8_t initialize[] = {
        0xAE,             /* display off */
        0xD5, 0x80,       /* display clock */
        0xA8, 0x3F,       /* 64 rows */
        0xD3, 0x00, 0x40, /* zero offset, start line 0 */
        0x8D, 0x14,       /* internal charge pump */
        0x20, 0x02,       /* page addressing */
        0xA1, 0xC8,       /* segment remap, descending COM */
        0xDA, 0x12,       /* 128x64 COM configuration */
        0x81, 0x7F,       /* medium contrast */
        0xD9, 0xF1, 0xDB, 0x40,
        0xA4, 0xA6, 0x2E  /* show RAM, normal polarity, no scroll */
    };
    if (!send_bytes(0x00, initialize, sizeof(initialize))) {
        return 0U;
    }
    memset(framebuffer, 0, sizeof(framebuffer));
    text(0U, 0U, "MQ2 SENSOR", 1U);
    text(0U, 20U, "WAIT DATA", 1U);
    text(0U, 52U, "PPM: NOT CAL", 1U);
    const uint8_t display_on = 0xAF;
    return flush() && send_bytes(0x00, &display_on, 1U);
}

uint8_t oled_show_reading(uint32_t adc, uint32_t pin_mv)
{
    if (address == 0U) {
        return 0U;
    }
    char number[11];
    char voltage[] = "A0: 0.000 V";
    /* PA0 is limited to 0..3.3 V, so one digit before the decimal is enough. */
    voltage[4] = (char)('0' + pin_mv / 1000U);
    voltage[6] = (char)('0' + (pin_mv / 100U) % 10U);
    voltage[7] = (char)('0' + (pin_mv / 10U) % 10U);
    voltage[8] = (char)('0' + pin_mv % 10U);
    uint_text(number, adc);
    memset(framebuffer, 0, sizeof(framebuffer));
    text(0U, 0U, "MQ2 SENSOR", 1U);
    text(0U, 20U, "ADC", 1U);
    text(30U, 16U, number, 2U);
    text(0U, 36U, voltage, 1U);
    text(0U, 52U, "PPM: NOT CAL", 1U);
    return flush();
}
