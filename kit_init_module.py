from adafruit_servokit import ServoKit
import board
import busio
import time

########################################
# 설정
########################################
SERVO_OFFSETS = { 11: 0, 13: 0, 14: 0, 15: 0 }
# 모터 제어 대상 채널
MOTOR_CHANNELS = [10, 11, 13, 14, 15]
# 각 채널별 각도 제한 (도)
CHANNEL_ANGLES = {
    10: (0, 270),
    11: (0, 270),
    13: (0, 270),
    14: (0, 270),
    15: (0, 180),
}
########################################
# 서보 초기화 (I2C 및 ServoKit)
########################################
i2c = busio.I2C(board.SCL, board.SDA)

def initialize_multiplexer():
    from adafruit_tca9548a import TCA9548A  # type: ignore
    try:
        print("[initialize_multiplexer] 멀티플렉서 초기화 시도 중...")
        tca = TCA9548A(i2c)
        print("[initialize_multiplexer] TCA9548A Multiplexer detected.")
        return tca
    except Exception as e:
        print("[initialize_multiplexer] 초기화 실패:", e)
        raise

def initialize_servo_kit(tca, channel):
    try:
        print(f"[initialize_servo_kit] ServoKit 초기화 시도 (채널 {channel})...")
        kit = ServoKit(channels=16, i2c=tca[channel])
        print(f"[initialize_servo_kit] PCA9685 detected on channel {channel}.")
        # 초기 각도 설정
        
        for ch in MOTOR_CHANNELS:
            if ch == 10:
                kit.servo[ch].set_pulse_width_range(500, 2500)
                kit.servo[ch].actuation_range = 270
                kit.servo[ch].angle = 0
            elif ch==15:
                kit.servo[ch].set_pulse_width_range(500, 2500)
                kit.servo[ch].actuation_range = 180
                kit.servo[ch].angle = (CHANNEL_ANGLES[ch][0]+ CHANNEL_ANGLES[ch][1])/2 + SERVO_OFFSETS.get(ch, 0)
            else:
                kit.servo[ch].set_pulse_width_range(500, 2500)
                kit.servo[ch].actuation_range = 270
                kit.servo[ch].angle = (CHANNEL_ANGLES[ch][0]+ CHANNEL_ANGLES[ch][1])/2 + SERVO_OFFSETS.get(ch, 0)
        time.sleep(1.0)
        return kit
    except Exception as e:
        print("[initialize_servo_kit] 초기화 실패:", e)
        raise
