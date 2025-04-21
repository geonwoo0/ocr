import grip
import time
import socket
import light_module
import random

# 패킷 통신 설정 (보내는 대상 IP와 포트를 설정)
UDP_IP = "192.168.50.51"   # 수신측 IP 주소 (환경에 맞게 수정)
UDP_PORT = 5005            # 수신측 포트 번호 (환경에 맞게 수정)

# UDP 소켓 생성
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

for i in range(201):
    light = random.randint(0,100)
    light_module.light_control(light)
    grip.grip()          # 그립 동작
    time.sleep(0.3)        # 1초 대기
    grip.ungrip()        # 언그립 동작
    time.sleep(1.2)
    # 언그립 후, 완료 신호를 UDP 패킷으로 전송
    sock.sendto(b"COMPLETE", (UDP_IP, UDP_PORT))
    time.sleep(0.3)

light_module.light_off()