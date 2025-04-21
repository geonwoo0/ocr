import move_module
import kit_init_module
import time
import socket
import json
import datetime

# json파일로 저장한 관절 값 읽어오기
with open('poses.json', 'r') as f:
    poses_json = json.load(f)
poses = {}
for pose_name, joints in poses_json.items():
    poses[pose_name] = {int(ch): value for ch, value in joints.items()}

########################################
# 좌표 저장 함수
########################################
def store_coordinates(coordinates, filename="coordinates_log.txt"):
    timestamp = datetime.datetime.now().isoformat()
    try:
        with open(filename, 'a') as f:
            f.write(f"[{timestamp}] {json.dumps(coordinates, ensure_ascii=False)}\n")
        #print(f"[Store] 좌표 저장 완료: {coordinates}")
    except Exception as e:
        print(f"[Store] 좌표 저장 실패: {e}")
        
########################################
# 클라이언트: 서버에 태그 좌표 요청 및 응답 수신
########################################
def request_relative_positions(server_ip="192.168.50.159", port=9999, timeout=3.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((server_ip, port))
        except Exception as e:
            print(f"[Client] 서버 연결 실패: {e}")
            return None
        request_message = "tag_request"
        try:
            s.send(request_message.encode("utf-8"))
        except Exception as e:
            print(f"[Client] 요청 전송 실패: {e}")
            return None
        data = b""
        try:
            while True:
                packet = s.recv(1024)
                if not packet:
                    break
                data += packet
        except socket.timeout:
            print("[Client] 데이터 수신 타임아웃")
        try:
            message = json.loads(data.decode("utf-8"))
            return message
        except Exception as e:
            print(f"[Client] 응답 파싱 실패: {e}")
            return None

def main():
    tca = kit_init_module.initialize_multiplexer()
    kit = kit_init_module.initialize_servo_kit(tca,0)
    
    while True:
        move_module.move_motors(kit, poses["moving_pose2"],0.5)
        move_module.move_motors(kit, poses["moving_pose"],0.5)
        
        move_module.move_motors(kit, poses["pose_11"],1.0)
        time.sleep(2.0)
        
        relative_positions11 = request_relative_positions(server_ip="192.168.50.159", port=9999)
        if relative_positions11 is None:
            print("11 태그 수신실패")
        else:
            store_coordinates(relative_positions11,filename="pose11_log.txt")
            
        move_module.move_motors(kit, poses["moving_pose"],0.5)
        move_module.move_motors(kit, poses["moving_pose2"],0.5)
        
        move_module.move_motors(kit, poses["pose_home"],1.0)
        time.sleep(2.0)
        relative_positions_home = request_relative_positions(server_ip="192.168.50.159", port=9999)
        if relative_positions_home is None:
            print("home pose 태그 수신실패")
        else:
            store_coordinates(relative_positions_home,filename="home_pose_log.txt")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("종료합니다.")
