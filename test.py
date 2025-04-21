# -*- coding: utf-8 -*- # 파일 인코딩 명시

# --- 표준 라이브러리 임포트 ---
import time
import json
import socket
import sys # 프로그램 종료를 위해 사용
import traceback # 상세 오류 출력을 위해 추가
import random

# --- 사용자 정의 모듈 임포트 ---
# 필요한 모듈이 없으면 오류 메시지 출력 후 종료
try:
    import move_module
    import kit_init_module
    import grip
    import light_module
    import mos_photo # 센서 확인 로직이 담긴 모듈
except ImportError as e:
    print(f"[치명적 오류] 필요한 모듈을 찾을 수 없습니다: {e}")
    print("설치 상태를 확인하거나 PYTHONPATH 또는 모듈 파일의 위치를 확인해주세요.")
    sys.exit(1) # 필수 모듈 없으면 종료

# ==============================================================================
# --- 설정 상수 정의 ---
# ==============================================================================

# 파일 경로
POSES_FILE = 'poses.json'       # 로봇 포즈 데이터 파일

# UDP 통신 설정
UDP_IP = "0.0.0.0"              # 수신 대기 IP (모든 인터페이스에서 수신)
UDP_LISTEN_PORT = 5005          # 명령 수신 대기 포트
CLIENT_REPLY_IP = "192.168.50.51"
CLIENT_REPLY_PORT = 5006        # <<<--- 클라이언트가 응답을 기다리는 고정 포트
UDP_BUFFER_SIZE = 1024          # 수신 버퍼 크기
UDP_TIMEOUT = 0.5              # 수신 소켓 타임아웃 (초). 논블로킹 효과

# 로봇 포즈 이름 (poses.json 파일의 키와 일치해야 함)
INITIAL_POSE = "move_pose1"     # 프로그램 시작 및 작업 완료 후 복귀 포즈
PICK_MOVE_INTERIM = "move_pose" # 집기 동작 시 중간 이동 포즈
PICK_APPROACH = "h_pose"        # 집기 시 대상에 접근하는 포즈
PICK_GRAB_MOVE = "g_pose"       # 구슬을 잡은 후 이동하는 포즈
PLACE_A_POSE = "pose_A"         # A 위치에 놓는 포즈
PLACE_B_POSE = "pose_B"         # B 위치에 놓는 포즈
PLACE_C_POSE = "pose_C"         # C 위치에 놓는 포즈

# UDP 명령어 (Bytes 형식)
CMD_FAIL = b"fail"               # 실패 신호
CMD_EMPTY = b"empty"             # 빈 신호 (무시)

# 조명 밝기 (%)
LIGHT_WARN_LEVEL1 = 50          # 실패 1회 시 밝기
LIGHT_WARN_LEVEL2 = 100         # 실패 2회 이상 시 밝기

# 센서 확인 설정
MAX_SENSOR_RETRIES = 3          # 센서 확인 최대 시도 횟수

# 동작 관련 대기 시간 (초)
GRIP_STABILIZE_DELAY = 1.0      # 그리퍼 동작 후 안정화 대기 시간
MOVE_COMPLETION_DELAY = 0.1     # 이동 명령 후 짧은 대기 시간
SOLENOID_ACTION_DELAY = 1.0     # 솔레노이드 작동 시간
SENSOR_RETRY_DELAY = 0.5        # 센서 재시도 사이의 대기 시간

# 작업 완료 및 다음 단계 신호
SIGNAL_NEXT_TASK = b"START" # 로봇 -> 클라이언트 '다음 작업 시작' 신호

# ==============================================================================
# --- 핵심 기능 함수 정의 ---
# ==============================================================================

def load_poses_from_json(filename):
    """
    JSON 파일에서 로봇 포즈 데이터를 로드하고 파싱합니다.
    키가 숫자인 값만 유효한 포즈 데이터로 간주합니다.

    Args:
        filename (str): 로드할 JSON 파일 이름.

    Returns:
        dict: 성공 시 포즈 데이터 딕셔너리. 실패 시 None.
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f: # 인코딩 명시
            poses_json = json.load(f)

        # JSON 데이터를 파싱하여 유효한 포즈만 추출
        # 키는 문자열 유지, 값은 {모터번호(int): 각도(float)} 형태
        poses = {
            name: {int(k): float(v) for k, v in pose.items() if k.isdigit()}
            for name, pose in poses_json.items()
        }
        print(f"성공: '{filename}'에서 포즈 데이터 로드 완료.")
        return poses
    except FileNotFoundError:
        print(f"[오류] 포즈 파일 '{filename}'을(를) 찾을 수 없습니다.")
        return None
    except json.JSONDecodeError:
        print(f"[오류] 포즈 파일 '{filename}'의 JSON 형식이 잘못되었습니다.")
        return None
    except Exception as e:
        print(f"[오류] 포즈 파일 로드 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return None

def initialize_robot_hardware():
    """
    로봇 팔 제어를 위한 하드웨어(멀티플렉서, 서보 키트)를 초기화합니다.

    Returns:
        object: 성공 시 서보 키트 객체. 실패 시 None.
    """
    try:
        print("하드웨어 초기화 시도...")
        tca = kit_init_module.initialize_multiplexer()
        kit = kit_init_module.initialize_servo_kit(tca, 0)
        print("성공: 하드웨어 초기화 완료.")
        return kit
    except Exception as e:
        print(f"[오류] 하드웨어 초기화 중 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return None

def setup_udp_socket(ip, port, timeout):
    """ UDP 수신용 소켓을 설정하고 바인딩합니다. """
    sock = None # 초기화
    try:
        print(f"UDP 소켓 설정 시도 (수신용 IP: {ip}, Port: {port})...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 소켓 주소 재사용 옵션 설정 (선택 사항, 빠른 재시작 시 유용)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ip, port))
        sock.settimeout(timeout) # 수신용이므로 타임아웃 설정
        print(f"성공: UDP 소켓 설정 및 바인딩 완료 (수신용).")
        return sock
    except socket.error as e:
        print(f"[오류] UDP 소켓 설정 실패: {e}")
        if sock: sock.close() # 소켓 생성 중 오류 발생 시 닫기 시도
        return None
    except Exception as e:
        print(f"[오류] UDP 소켓 설정 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        if sock: sock.close()
        return None

def perform_pick_sequence(kit, poses):
    """구슬을 집는 전체 동작 시퀀스를 수행합니다."""
    try:
        print(">> 구슬 집기 동작 시작...")
        # move_motors 함수는 이동 완료까지 대기(블로킹)한다고 가정
        move_module.move_motors(kit, poses[PICK_MOVE_INTERIM], 1.0)
        move_module.move_motors(kit, poses[PICK_APPROACH], 0.5)
        grip.grip() # 그리퍼 닫기
        time.sleep(GRIP_STABILIZE_DELAY)
        move_module.move_motors(kit, poses[PICK_GRAB_MOVE], 0.5)
        time.sleep(GRIP_STABILIZE_DELAY) # 잡은 후 안정화 또는 이동 시간
        move_module.move_motors(kit, poses[PICK_MOVE_INTERIM], 1.0)
        move_module.move_motors(kit, poses[INITIAL_POSE], 0.5) # 초기 위치로 복귀
        time.sleep(MOVE_COMPLETION_DELAY)
        print(">> 구슬 집기 동작 완료.")
        return True
    except KeyError as e:
        print(f"[오류] 집기 동작 중 필요한 포즈 키({e})를 찾을 수 없습니다. poses.json 파일을 확인하세요.")
        return False
    except Exception as e:
        print(f"[오류] 구슬 집기 동작 중 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return False


def perform_place_sequence(kit, poses, place_pose_key):
    """지정된 위치에 구슬을 놓는 전체 동작 시퀀스를 수행합니다."""
    try:
        print(f">> '{place_pose_key}' 위치에 구슬 놓기 동작 시작...")
        target_pose = poses.get(place_pose_key)
        if not target_pose:
            print(f"[경고] 놓기 동작: 정의되지 않은 포즈 키 '{place_pose_key}'. poses.json 파일을 확인하세요.")
            return False # 정의되지 않은 포즈면 실패 처리

        move_module.move_motors(kit, target_pose, 1.0)
        time.sleep(MOVE_COMPLETION_DELAY)
        grip.ungrip() # 그리퍼 열기
        grip.sol_on() # 솔레노이드 작동 (구슬 밀어내기?)
        time.sleep(SOLENOID_ACTION_DELAY)
        grip.sol_off() # 솔레노이드 정지
        move_module.move_motors(kit, poses[INITIAL_POSE], 0.5) # 초기 위치로 복귀
        print(">> 구슬 놓기 동작 완료.")
        return True
    except KeyError as e:
        print(f"[오류] 놓기 동작 중 필요한 포즈 키({e})를 찾을 수 없습니다. poses.json 파일을 확인하세요.")
        return False
    except Exception as e:
        print(f"[오류] 구슬 놓기 동작 중 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return False

def check_sensor_after_place():
    """
    구슬 놓기 후 센서를 확인합니다. 최대 횟수만큼 시도합니다.

    Returns:
        int: 최종 감지 횟수 (성공 시 > 0, 실패 시 0).
    """
    detection_count = 0
    print("== 센서 확인 시작 ==")
    for attempt in range(MAX_SENSOR_RETRIES):
        print(f"  [시도 {attempt + 1}/{MAX_SENSOR_RETRIES}] 센서 확인 작업 실행...")
        try:
            # mos_photo 모듈의 함수가 감지 횟수를 반환한다고 가정
            count = mos_photo.run_relay_and_sensor_task()
        except Exception as e:
            print(f"  [오류] 센서 확인 작업 중 오류 발생: {e}")
            traceback.print_exc() # 상세 오류 출력
            count = 0 # 오류 발생 시 감지 실패로 간주

        if count > 0:
            detection_count = count
            print(f"  [성공] 센서 감지 완료! (감지 횟수: {detection_count})")
            break # 성공 시 즉시 종료
        else:
            print(f"  [실패] 센서 감지 실패.")
            if attempt < MAX_SENSOR_RETRIES - 1:
                print(f"  {SENSOR_RETRY_DELAY}초 후 재시도...")
                time.sleep(SENSOR_RETRY_DELAY)

    if detection_count == 0:
        print("[경고] 센서 최종 감지 실패! (모든 시도 실패)")

    print("== 센서 확인 종료 ==")
    return detection_count

# ==============================================================================
# --- 메인 제어 함수 ---
# ==============================================================================

def main_control_loop():
    """ 메인 제어 로직을 실행하는 함수. """

    # --- 초기화 단계 ---
    sock = None # <<<--- 소켓 변수 하나만 사용

    poses = load_poses_from_json(POSES_FILE)
    if not poses:
        print("[치명적 오류] 포즈 데이터 로딩 실패. 프로그램을 종료합니다.")
        return

    kit = initialize_robot_hardware()
    if not kit:
        print("[치명적 오류] 하드웨어 초기화 실패. 프로그램을 종료합니다.")
        return

    # <<<--- 수정: 수신용 소켓 하나만 설정 ---
    sock = setup_udp_socket(UDP_IP, UDP_LISTEN_PORT, UDP_TIMEOUT)
    if not sock:
        print("[치명적 오류] UDP 소켓 설정 실패. 프로그램을 종료합니다.")
        # kit 정리 등 필요시 추가
        return
    # ---------------------------------------

    # --- 상태 변수 및 초기 위치 이동 ---
    fail_count = 0
    try:
        print("초기 위치로 로봇 팔 이동 시도...")
        # 초기 포즈 존재 여부 확인
        if INITIAL_POSE not in poses:
             print(f"[오류] 초기 포즈 키 '{INITIAL_POSE}'가 '{POSES_FILE}'에 정의되지 않았습니다!")
             if sock: sock.close() # 소켓 정리
             return
        move_module.move_motors(kit, poses[INITIAL_POSE], 0.5)
        print("성공: 초기 위치 이동 완료.")
    except Exception as e:
         print(f"[오류] 초기 위치 이동 중 오류 발생: {e}")
         traceback.print_exc() # 상세 오류 출력
         if sock: sock.close() # 소켓 정리
         return

    # --- 메인 루프 시작 ---
    print("\n" + "="*20 + " 메인 루프 시작 " + "="*20)
    print(f"UDP 명령 수신 대기 중 (포트 {UDP_LISTEN_PORT})... 응답은 클라이언트 포트 {CLIENT_REPLY_PORT} 로 전송됩니다.") # 안내 메시지 수정
    keep_running = True
    while keep_running:
        try:
            # --- UDP 데이터 수신 (수신용 소켓 사용) ---
            try:
                # 데이터와 보낸 곳의 주소(addr)를 받음
                data, addr = sock.recvfrom(UDP_BUFFER_SIZE) # sock 사용 (원래대로)
                print(f"\n[수신] 주소: {addr}, 데이터: {data}")
            except socket.timeout:
                # 타임아웃은 정상적인 상황. 데이터 수신 없이 루프 계속.
                continue
            except socket.error as e:
                # 소켓 관련 오류 발생 시 (네트워크 문제 등)
                print(f"[오류] UDP 수신 중 소켓 오류 발생: {e}. 잠시 후 재시도...")
                time.sleep(1) # 잠시 대기 후 다음 루프에서 재시도
                continue

            # --- 수신된 명령 처리 ---
            if data == CMD_FAIL:
                fail_count += 1
                print(f"-> 실패 신호 감지. (연속 실패: {fail_count}회)")
                if fail_count == 1:
                    print(f"   조명 밝기 설정: {LIGHT_WARN_LEVEL1}%")
                    light_module.light_control(LIGHT_WARN_LEVEL1)
                elif fail_count == 2:
                    print(f"   조명 밝기 설정: {LIGHT_WARN_LEVEL2}%")
                    light_module.light_control(LIGHT_WARN_LEVEL2)
                # 실패 신호 수신 시 다른 작업 없이 다음 명령 대기
                else :
                    rand_light = random.randint(0,100)
                    print(f"   조명 밝기 설정: {rand_light}%")
                    light_module.light_control(rand_light)
                time.sleep(1.5)
                bytes_sent = sock.sendto(SIGNAL_NEXT_TASK, (CLIENT_REPLY_IP, CLIENT_REPLY_PORT))
                continue

            elif data == CMD_EMPTY:
                emt_count = check_sensor_after_place()
                print("->empty 수신.")
                if emt_count>=1:
                    time.sleep(1.5)
                    bytes_sent = sock.sendto(SIGNAL_NEXT_TASK, (CLIENT_REPLY_IP, CLIENT_REPLY_PORT))
                # 빈 신호 수신 시 다른 작업 없이 다음 명령 대기
                continue

            else:
                # --- 정상 명령 또는 알 수 없는 명령 처리 ---
                # 정상적인 명령(fail, empty 제외)이 수신되면 실패 카운트 초기화
                if fail_count > 0:
                    print("-> 정상 신호 또는 새 명령 감지. 실패 카운트 초기화 및 조명 끄기.")
                    fail_count = 0
                    light_module.light_off()

                # --- 로봇 작업 시퀀스 ---
                # 1. 구슬 집기
                if not perform_pick_sequence(kit, poses):
                    print("[경고] 구슬 집기 동작 실패. 다음 명령 대기.")
                    # 집기 실패 시에도 fail_count를 증가시킬지 여부 결정 필요
                    # fail_count += 1
                    # light_module.light_control(LIGHT_WARN_LEVEL1)
                    continue # 집기 실패 시 다음 명령 대기

                # 2. 명령 디코딩
                try:
                    # 수신된 데이터를 utf-8로 디코딩하고 양 끝 공백 제거
                    command = data.decode('utf-8').strip()
                    print(f"-> 수신된 명령 디코딩: '{command}'")
                except UnicodeDecodeError:
                    print(f"[경고] 수신된 데이터({data})를 UTF-8로 디코딩할 수 없습니다.")
                    continue # 디코딩 실패 시 다음 명령 대기

                # 3. 해당 위치에 구슬 놓기 (명령 기반)
                place_key = None
                if command == "A": place_key = PLACE_A_POSE
                elif command == "B": place_key = PLACE_B_POSE
                elif command == "C": place_key = PLACE_C_POSE
                else: place_key = PLACE_C_POSE 
                # else: place_key는 None으로 유지됨 (알 수 없는 명령)

                if place_key: # 명령이 A, B, C 중 하나인 경우
                    if not perform_place_sequence(kit, poses, place_key):
                        print("[경고] 구슬 놓기 동작 실패. 다음 명령 대기.")
                        # 놓기 실패 시에도 fail_count를 증가시킬지 여부 결정 필요
                        # fail_count += 1
                        # light_module.light_control(LIGHT_WARN_LEVEL1)
                        continue # 놓기 실패 시 다음 명령 대기

                    # ==========================================================
                    # --- 4. 놓기 완료 후 센서 확인 및 결과에 따른 신호 전송 ---
                    # ==========================================================
                    final_detection_count = check_sensor_after_place()

                    if final_detection_count == 0:
                        # 센서 최종 감지 실패 시 처리 로직
                        print("[알림] 센서 최종 감지 실패 처리. (다음 작업 신호 보내지 않음)")
                        # 필요시 여기에 실패 관련 추가 처리 (예: fail_count 증가 및 조명 설정)
                        # fail_count += 1
                        # print(f"-> 센서 감지 실패. 실패 카운트 증가: {fail_count}")
                        # if fail_count == 1: light_module.light_control(LIGHT_WARN_LEVEL1)
                        # elif fail_count >= 2: light_module.light_control(LIGHT_WARN_LEVEL2)
                    else:
                        # 센서 감지 성공 시 (final_detection_count > 0)
                        print(f"-> 센서 감지 성공({final_detection_count}회). 다음 작업 시작 신호 전송 시도...")
                        try:
                            # --- 수정: 목적지 포트를 CLIENT_REPLY_PORT(5006)로 지정 ---
                            #client_ip = addr[0] # 클라이언트 IP는 유지
                            #reply_addr = (CLIENT_REPLY_IP, CLIENT_REPLY_PORT) # 목적지 주소 생성 (고정 포트 사용)

                            # 수신용 소켓(sock)으로 sendto 사용, 목적지는 reply_addr
                            time.sleep(3.0)
                            bytes_sent = sock.sendto(SIGNAL_NEXT_TASK, (CLIENT_REPLY_IP, CLIENT_REPLY_PORT))
                            print(f"   신호 '{SIGNAL_NEXT_TASK.decode()}'를 {CLIENT_REPLY_IP, CLIENT_REPLY_PORT}로 전송 완료 ({bytes_sent} bytes).")
                            # -------------------------------------------------
                        except socket.error as e:
                            print(f"[오류] 다음 작업 시작 신호 전송 실패: {e}")
                        except Exception as e:
                            print(f"[오류] 신호 전송 중 예상치 못한 오류: {e}")
                            traceback.print_exc() # 상세 오류 출력
                    # ==========================================================

                else: # 알 수 없는 명령 처리 (place_key가 None인 경우)
                    print(f"[경고] 알 수 없는 명령('{command}') 수신. 로봇 팔 초기 위치로 복귀 시도.")
                    try:
                        # 알 수 없는 명령 시 초기 위치로 이동 (안전을 위해)
                        move_module.move_motors(kit, poses[INITIAL_POSE], 0.5)
                    except Exception as move_err:
                        print(f"[오류] 알 수 없는 명령 후 초기 위치 복귀 중 오류: {move_err}")
                        traceback.print_exc() # 상세 오류 출력
                    # 알 수 없는 명령 처리 후 다음 명령 대기
                    continue

        except KeyboardInterrupt:
            # Ctrl+C 입력 시 루프 종료
            print("\n사용자에 의해 프로그램 중지 요청됨.")
            keep_running = False # 루프 종료 플래그 설정
        except Exception as e:
            # 메인 루프 내에서 예상치 못한 오류 발생 시
            print(f"\n[치명적 오류] 메인 루프 실행 중 심각한 오류 발생: {e}")
            traceback.print_exc() # 상세 오류 스택 출력
            print("오류 복구를 위해 잠시 대기 후 루프를 계속합니다. 문제가 지속되면 점검 필요.")
            # 필요하다면 여기서 keep_running = False로 설정하여 프로그램 종료 가능
            time.sleep(2) # 오류 발생 시 잠시 대기

    # --- 루프 종료 후 자원 정리 ---
    print("\n" + "="*20 + " 프로그램 종료 처리 시작 " + "="*20)
    # <<<--- 수정: 소켓 하나만 닫음 ---
    if sock:
        print(f"UDP 소켓(포트 {UDP_LISTEN_PORT}) 닫기...")
        sock.close()
    # -----------------------------
    print("조명 끄기...")
    try:
        # light_module이 없을 경우 대비
        if 'light_module' in sys.modules:
             light_module.light_off()
    except Exception as light_err:
        print(f"[경고] 조명 끄기 중 오류 발생: {light_err}")

    # 다른 하드웨어 정리 (예: GPIO.cleanup() 등)가 필요하면 여기에 추가
    # 예: kit_init_module.cleanup_hardware() 같은 함수가 있다면 호출
    print("모든 정리 작업 완료. 프로그램 종료.")


# ==============================================================================
# --- 스크립트 실행 지점 ---
# ==============================================================================
if __name__ == "__main__":
    try:
        main_control_loop() # 메인 제어 함수 호출
    except Exception as e:
        # main_control_loop 외부의 예외 처리 (거의 발생하지 않겠지만 안전 장치)
        print("\n[최상위 예외 발생] 프로그램 초기화 또는 최종 정리 중 심각한 오류가 발생했습니다.")
        print(f"오류: {e}")
        traceback.print_exc()
    finally:
        # 예외 발생 여부와 관계없이 항상 실행
        print("프로그램 최종 종료.")