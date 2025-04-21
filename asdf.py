import light_module
import time

light_module.light_control(100)
time.sleep(3)
light_module.light_off()
time.sleep(3)
light_module.light_control(50)