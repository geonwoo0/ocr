import RPi.GPIO as GPIO
import atexit  # 프로그램 종료 시 실행할 함수를 등록하기 위해 import

# --- 초기 설정 ---
LED_PIN = 18  # 사용할 GPIO 핀 번호

# GPIO 경고 메시지 비활성화 (선택 사항)
GPIO.setwarnings(False)

# GPIO 모드 설정 (BCM 모드)
# 이 설정은 모듈이 로드될 때 한 번만 실행되도록 합니다.
try:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_PIN, GPIO.OUT)
except RuntimeError as e:
    # 이미 설정된 경우 오류가 발생할 수 있으므로 처리
    print(f"GPIO 초기 설정 중 메시지: {e}. 아마도 이미 설정되었을 수 있습니다.")
    # 필요한 경우 여기서 cleanup 후 다시 설정할 수도 있습니다.
    # GPIO.cleanup()
    # GPIO.setmode(GPIO.BCM)
    # GPIO.setup(LED_PIN, GPIO.OUT)


# PWM 객체 변수 (처음에는 None으로 초기화)
pwm = None
current_duty_cycle = 0 # 현재 Duty Cycle 추적 (선택 사항)

# --- 함수 정의 ---

def _init_pwm():
    """
    PWM 객체를 초기화하고 시작하는 내부 함수.
    GPIO 모드 설정 상태를 확인하고 필요시 재설정 시도.
    """
    global pwm
    if pwm is None:
        try:
            # --- GPIO 모드 설정 확인 및 재설정 시도 ---
            # 현재 설정된 GPIO 모드를 가져옵니다. 설정 안됐으면 None 반환.
            current_mode = GPIO.getmode()

            if current_mode is None:
                # 모드가 설정되지 않았다면 BCM 모드로 설정합니다.
                print("경고: GPIO 모드가 설정되지 않아 BCM 모드로 설정합니다.")
                GPIO.setmode(GPIO.BCM)
            elif current_mode != GPIO.BCM:
                # 만약 다른 모드(BOARD)로 이미 설정되어 있다면 문제가 될 수 있습니다.
                # 이 경우 에러를 발생시키거나, cleanup 후 재설정하는 등의 처리가 필요할 수 있습니다.
                print(f"경고: GPIO 모드가 예상치 못한 모드({current_mode})로 설정되어 있습니다. BCM 모드로 강제 변경을 시도합니다.")
                # 필요하다면 여기서 cleanup 후 재설정
                # GPIO.cleanup() # 주석 처리된 부분은 신중하게 사용해야 합니다. 다른 GPIO 사용에 영향을 줄 수 있음.
                GPIO.setmode(GPIO.BCM) # BCM 모드로 재설정 시도

            # --- GPIO 핀 설정 (출력) ---
            # setmode가 정상적으로 설정된 후 setup 호출
            # setup 함수는 해당 핀이 이미 설정되어 있어도 보통 오류를 발생시키지 않습니다.
            GPIO.setup(LED_PIN, GPIO.OUT)

            # --- PWM 객체 생성 및 시작 ---
            pwm = GPIO.PWM(LED_PIN, 1000) # 여기서 setmode 오류가 발생하지 않아야 합니다.
            pwm.start(0)
            print("PWM 객체가 초기화되고 시작되었습니다.")

        except Exception as e:
            # 오류 발생 시 메시지를 출력하고 pwm 객체를 None으로 유지합니다.
            print(f"PWM 초기화 중 오류 발생: {e}")
            pwm = None



def light_control(per):
    """
    LED 밝기를 주어진 퍼센트(0-100)로 조절합니다.
    """
    global pwm, current_duty_cycle

    # PWM 객체가 초기화되지 않았으면 초기화 시도
    if pwm is None:
        _init_pwm()

    # PWM 객체가 성공적으로 생성 및 시작되었는지 확인
    if pwm:
        try:
            # 입력값 유효성 검사 (0 ~ 100)
            if 0 <= per <= 100:
                # Duty Cycle 변경
                pwm.ChangeDutyCycle(per)
                current_duty_cycle = per
                # print(f"LED 밝기: {per}%") # 디버깅용 출력
            else:
                print("오류: 밝기 값은 0에서 100 사이여야 합니다.")
        except Exception as e:
            print(f"Duty Cycle 변경 중 오류 발생: {e}")
            # 오류 발생 시 PWM 객체 상태를 초기화할 수 있음
            pwm = None # 다음 호출 시 재초기화 유도


def light_off():
    """
    LED를 끄고 PWM 신호 생성을 중지합니다.
    GPIO 설정은 유지됩니다.
    """
    global pwm, current_duty_cycle
    if pwm is not None:
        try:
            # PWM 중지
            pwm.stop()
            pwm = None # PWM 객체 참조를 제거하여 다음번 light_control 시 재초기화 유도
            current_duty_cycle = 0
            print("PWM이 중지되었습니다.")
        except Exception as e:
            print(f"PWM 중지 중 오류 발생: {e}")
    # GPIO.cleanup() # 여기서 호출하지 않습니다!


def _cleanup_gpio():
    """
    프로그램 종료 시 호출되어 GPIO 설정을 정리하는 내부 함수.
    """
    global pwm
    print("프로그램 종료 전 GPIO 정리 작업 수행...")
    if pwm is not None:
        pwm.stop() # 혹시 PWM이 실행 중이면 중지
    GPIO.cleanup()
    print("GPIO 정리가 완료되었습니다.")

# 프로그램 종료 시 _cleanup_gpio 함수가 자동으로 호출되도록 등록
atexit.register(_cleanup_gpio)

# --- 모듈 테스트 코드 (이 파일을 직접 실행할 때만 동작) ---
if __name__ == '__main__':
    import time
    print("--- LED 제어 모듈 테스트 시작 ---")
    try:
        print("밝기 50% 설정")
        light_control(50)
        time.sleep(2)

        print("밝기 100% 설정")
        light_control(100)
        time.sleep(2)

        print("밝기 0% 설정")
        light_control(0)
        time.sleep(2)

        print("light_off() 호출 (PWM 중지)")
        light_off()
        time.sleep(2)

        print("다시 밝기 30% 설정 시도")
        light_control(30) # light_off 후에도 다시 제어 가능해야 함
        time.sleep(2)

        print("테스트 종료. 밝기 0%로 설정.")
        light_control(0)
        time.sleep(1)

    except KeyboardInterrupt:
        print("\n사용자에 의해 테스트 중단됨.")
    finally:
        # atexit에 등록했으므로 여기서 cleanup을 명시적으로 호출할 필요 없음
        print("--- LED 제어 모듈 테스트 종료 ---")