from gpiozero import DigitalOutputDevice
from time import sleep

# 릴레이 모듈이 active low인 경우: on() 호출 시 LOW 출력 → 릴레이 활성화
# 초기값은 True (HIGH, 즉 릴레이 OFF 상태)
air_pump = DigitalOutputDevice(17, active_high=False, initial_value=True)
solenoid = DigitalOutputDevice(27, active_high=False, initial_value=True)
def ungrip():
    air_pump.on()
    
def grip():
    air_pump.off()
    
def sol_on():
    solenoid.off()

def sol_off():
    solenoid.on()
