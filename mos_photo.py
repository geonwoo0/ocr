import RPi.GPIO as GPIO
import time
import threading

# ... (상수 및 setup_gpio, control_relay, monitor_sensor 함수는 이전 답변의 '방법 1'처럼 결과 리스트 사용) ...
RELAY_PIN = 21
SENSOR_PIN = 23
SENSOR_DURATION = 1.5
DEFAULT_RELAY_ON_TIME = 0.2
MAX_RETRIES = 3 # 최대 재시도 횟수

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RELAY_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(SENSOR_PIN, GPIO.IN)
    print("GPIO 설정 완료.")

def control_relay(on_duration=DEFAULT_RELAY_ON_TIME):
    try:
        print(f"GPIO {RELAY_PIN} 핀을 {on_duration}초 동안 켭니다...")
        GPIO.output(RELAY_PIN, GPIO.HIGH)
        time.sleep(on_duration)
    finally:
        print(f"GPIO {RELAY_PIN} 핀을 끕니다...")
        GPIO.output(RELAY_PIN, GPIO.LOW)
        print("릴레이 제어 완료.")

def monitor_sensor(duration, result_list): # 결과를 저장할 리스트 인자 추가
    start_time = time.time()
    print(f"포토 센서(핀 {SENSOR_PIN}) 감지를 {duration}초 동안 시작합니다...")
    detection_count = 0

    while time.time() - start_time < duration:
        current_state = GPIO.input(SENSOR_PIN)
        if current_state == GPIO.HIGH: # 감지 상태 (HIGH 가정)
            # print(f"시간: {time.time() - start_time:.2f}초 - 감지됨!") # 필요시 주석 해제
            detection_count += 1
            time.sleep(0.1)
        else:
            pass
        #time.sleep(0.02)

    print(f"포토 센서 감지를 {duration}초 후에 종료합니다. (총 {detection_count}번 감지)")
    result_list[0] = detection_count # 리스트에 결과 저장

# --- 메인 실행 함수 (감지 횟수 반환) ---
def run_relay_and_sensor_task(sensor_duration=SENSOR_DURATION, relay_on_time=DEFAULT_RELAY_ON_TIME):
    sensor_thread = None
    gpio_setup_done = False
    detection_result = [0] # 결과를 받을 리스트
    final_detection_count = 0 # 최종 반환할 값

    try:
        setup_gpio()
        gpio_setup_done = True

        sensor_thread = threading.Thread(target=monitor_sensor, args=(sensor_duration, detection_result))
        sensor_thread.start()
        time.sleep(0.1)
        control_relay(on_duration=relay_on_time)
        sensor_thread.join()

        final_detection_count = detection_result[0] # 결과 리스트에서 값 가져오기
        print(f"이번 실행에서 최종 감지 횟수: {final_detection_count}")
        print("모든 작업 완료.")

    except KeyboardInterrupt:
        print("\n사용자에 의해 작업 중지됨.")
        raise # KeyboardInterrupt를 다시 발생시켜 루프 종료
    except Exception as e:
        print(f"\n작업 중 오류 발생: {e}")
    finally:
        if sensor_thread and sensor_thread.is_alive():
             sensor_thread.join(timeout=1.0)
        if gpio_setup_done:
            print("GPIO 설정을 초기화합니다.")
            GPIO.cleanup()
        print("run_relay_and_sensor_task 함수 종료.")

    return final_detection_count # 감지 횟수 반환

# --- 스크립트 직접 실행 시 (재시도 로직 포함) ---
if __name__ == "__main__":
    retry_count = 0
    while retry_count < MAX_RETRIES:
        print(f"\n{'='*10} 작업 시도 {retry_count + 1} / {MAX_RETRIES} {'='*10}")
        try:
            detections = run_relay_and_sensor_task()
            if detections > 0:
                print(f"\n*** 감지 성공! (총 {detections}번 감지) ***")
                break # 감지 성공 시 루프 탈출
            else:
                print("\n--- 감지 실패, 재시도합니다. ---")
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    time.sleep(1) # 재시도 전 잠시 대기 (선택 사항)
        except KeyboardInterrupt:
            print("\n재시도 중 사용자에 의해 중지됨.")
            break # 사용자가 중지하면 루프 탈출

    if retry_count == MAX_RETRIES:
        print(f"\n*** 최대 재시도 횟수({MAX_RETRIES}) 초과. 최종 감지 실패. ***")

    print("\n전체 프로그램 종료.")