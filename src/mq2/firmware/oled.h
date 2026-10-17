#ifndef MQ2_OLED_H
#define MQ2_OLED_H

#include <stdint.h>

/* SSD1306-compatible 128x64 I2C OLED: PB6=SCL, PB7=SDA, 3.3 V supply. */
uint8_t oled_init(void);
uint8_t oled_address(void);
uint8_t oled_show_reading(uint32_t adc, uint32_t pin_mv);

#endif
