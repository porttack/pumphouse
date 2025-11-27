#!/usr/bin/env python3
import time
import board
import adafruit_ahtx0

i2c = board.I2C()
sensor = adafruit_ahtx0.AHTx0(i2c)

print("Reading AHT20 sensor... (Ctrl+C to exit)")
while True:
    temp_c = sensor.temperature
    temp_f = (temp_c * 9/5) + 32
    humidity = sensor.relative_humidity
    
    print(f"Temp: {temp_f:.1f}°F  Humidity: {humidity:.1f}%")
    time.sleep(2)
