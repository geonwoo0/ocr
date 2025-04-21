import move_module
import kit_init_module
import time
import json
import datetime
import grip
import socket

# 패킷 통신 설정 (보내는 대상 IP와 포트를 설정)
UDP_IP = "0.0.0.0"   # 수신측 IP 주소 (환경에 맞게 수정)
UDP_PORT = 5005            # 수신측 포트 번호 (환경에 맞게 수정)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(0.01)   # 논블록킹 타임아웃

pose = {10: 151, 11: 217, 13: 195, 14: 103, 15: 180}
g_pose = {10: 151, 11: 220, 13: 193, 14: 100, 15: 180}
pose1 = {10: 151, 11: 195, 13: 215, 14: 103, 15: 180}
move_pose = {10: 150, 11: 135, 13: 135, 14: 135, 15: 90}
move_pose1 = {10: 40, 11: 180, 13: 90, 14: 110, 15: 90}
tca = kit_init_module.initialize_multiplexer()
kit = kit_init_module.initialize_servo_kit(tca,0)
def main():
    
    move_module.move_motors(kit,move_pose1,0.5)
    move_module.move_motors(kit, move_pose ,0.5)
    while True:
        try:
            move_module.move_motors(kit, pose ,0.5)
            grip.grip()
            time.sleep(0.5)
            move_module.move_motors(kit, g_pose ,0.2)
            time.sleep(0.5)
            
            move_module.move_motors(kit, pose1, 0.5)
            time.sleep(0.5)
            grip.ungrip()
            grip.sol_on()            
            time.sleep(0.5)
            grip.sol_off()
        except socket.timeout:
            pass        

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        move_module.move_motors(kit,move_pose,1.0)
        time.sleep(0.5)
        move_module.move_motors(kit, move_pose1,0.5)
        time.sleep(0.5)
        print("종료합니다.")
