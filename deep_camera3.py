# -*- coding: utf-8 -*- # 파일 인코딩 명시

# --- 표준 라이브러리 임포트 ---
import time
# import json # 현재 코드에서 json 모듈 사용 안 함
import socket
import sys # 프로그램 종료를 위해 사용
import traceback # 상세 오류 출력을 위해 추가
import threading # 스레딩 모듈 추가

# --- 외부 라이브러리 임포트 ---
import cv2
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import transforms, models
from PIL import Image
# from collections import Counter # 현재 코드에서 Counter 사용 안 함
import numpy as np

# ============================================================
# --- 설정 상수 ---
# ============================================================
# 패킷 통신 설정 (결과 전송용)
UDP_SEND_IP = "192.168.50.128"  # 수신측(로봇 제어 PC) IP 주소
UDP_SEND_PORT = 5005           # 수신측(로봇 제어 PC) 포트 번호

# 트리거 신호 수신 설정
UDP_TRIGGER_IP = "0.0.0.0"     # 모든 인터페이스에서 트리거 신호 수신
UDP_TRIGGER_PORT = 5006        # 트리거 신호 수신 포트 (전송 포트와 달라야 함)
TRIGGER_MESSAGE = b"START"     # 예측 시작을 알리는 UDP 메시지

# 모델 및 이미지 관련 설정
MODEL_PATH = "alphabet_best10_512.pth" # 로드할 모델 파일 경로
NUM_CLASSES = 27                # 분류할 클래스 개수 (알파벳 26 + empty 1)
LSTM_HIDDEN_SIZE = 512         # 모델의 LSTM 히든 사이즈
IMG_SIZE = 256                  # 모델 입력 이미지 크기
ROI_X1, ROI_Y1 = 1620, 1605     # 관심 영역(ROI) 좌상단 좌표
ROI_WIDTH, ROI_HEIGHT = 256, 256 # 관심 영역 크기
ROI_X2, ROI_Y2 = ROI_X1 + ROI_WIDTH, ROI_Y1 + ROI_HEIGHT

# 카메라 설정
CAMERA_INDICES = [0, 2, 4]      # 사용할 카메라 장치 인덱스
CAMERA_WIDTH = 4000
CAMERA_HEIGHT = 3000
CAMERA_FPS = 15

# 알파벳 매핑 (모델 출력 인덱스 -> 문자)
# 인덱스 0~25: A~Z, 인덱스 26: empty
ALPHABET = [
    'A', 'B', 'C', 'D', 'E', 'F', 'G',
    'H', 'I', 'J', 'K', 'L', 'M', 'N',
    'O', 'P', 'Q', 'R', 'S', 'T', 'U',
    'V', 'W', 'X', 'Y', 'Z', 'empty'
]
CTC_BLANK_INDEX = NUM_CLASSES # CTC Blank 심볼 인덱스 (모델 출력 차원 = num_classes + 1 이므로)

# ============================================================
# --- 전역 변수 및 객체 ---
# ============================================================
# 결과 전송용 소켓 (전역으로 선언하여 예측 함수에서 사용)
send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# 스레드 동기화를 위한 이벤트 객체
trigger_event = threading.Event()
# 스레드 종료를 위한 플래그
keep_threads_running = True

# 모델 로드 및 전처리 설정
device = torch.device("cpu") # CPU 사용 ("cuda" if torch.cuda.is_available() else "cpu")
transform = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,)) # 그레이스케일 정규화 (평균 0.5, 표준편차 0.5)
])

# ============================================================
# --- 모델 정의 (STRNet, TPSSpatialTransformer 등) ---
# ============================================================

# ------------------------ CTC 그리디 디코더 ------------------------
def ctc_greedy_decoder(ctc_output, blank_index):
    """
    ctc_output: (T, B, C) - 네트워크 출력 (로그 확률)
    blank_index: CTC blank 심볼 인덱스
    반환: 리스트 (길이 B) - 각 배치별 디코딩 결과 (인덱스 문자열)
    """
    max_probs_indices = torch.argmax(ctc_output, dim=2) # (T, B)
    max_probs_indices = max_probs_indices.cpu().numpy().transpose(1, 0) # (B, T)
    results = []
    for seq_indices in max_probs_indices:
        prev_idx = -1
        decoded_indices = []
        for current_idx in seq_indices:
            if current_idx != prev_idx and current_idx != blank_index:
                decoded_indices.append(str(current_idx))
            prev_idx = current_idx
        results.append(''.join(decoded_indices))
    return results

# ------------------------ TPS 관련 함수 및 클래스 ------------------------
def U_func(r):
    """ 방사 기저 함수 U(r) = r^2 * log(r^2). r=0일 때는 0. """
    safe_r = torch.where(r == 0, torch.ones_like(r), r)
    return (safe_r**2) * torch.log(safe_r**2)

class TPSGridGen(nn.Module):
    """ TPS 변환을 위한 샘플링 그리드 생성 클래스 """
    def __init__(self, target_control_points_np, output_size):
        super(TPSGridGen, self).__init__()
        target_control_points = torch.tensor(target_control_points_np, dtype=torch.float32)
        self.register_buffer('target_control_points', target_control_points)
        self.N = target_control_points.shape[0]
        self.out_H, self.out_W = output_size

        pairwise_diff = target_control_points.unsqueeze(1) - target_control_points.unsqueeze(0)
        pairwise_dist = torch.norm(pairwise_diff, dim=2)
        K = U_func(pairwise_dist)
        ones = torch.ones(self.N, 1, device=target_control_points.device)
        P = torch.cat([ones, target_control_points], dim=1)
        zeros_3x3 = torch.zeros(3, 3, device=target_control_points.device)
        upper_L = torch.cat([K, P], dim=1)
        lower_L = torch.cat([P.t(), zeros_3x3], dim=1)
        L = torch.cat([upper_L, lower_L], dim=0)
        L_inv = torch.inverse(L)
        self.register_buffer('L_inv', L_inv)

        grid_Y, grid_X = torch.meshgrid(
            torch.linspace(-1.0, 1.0, steps=self.out_H, device=L.device),
            torch.linspace(-1.0, 1.0, steps=self.out_W, device=L.device),
            indexing='ij'
        )
        grid = torch.stack([grid_X.flatten(), grid_Y.flatten()], dim=1)
        self.register_buffer('grid', grid)

        grid_expand = self.grid.unsqueeze(1)
        target_expand = self.target_control_points.unsqueeze(0)
        dist_grid_target = torch.norm(grid_expand - target_expand, dim=2)
        # 수정: 저장된 모델과의 호환성을 위해 버퍼 이름을 U_X, P_X로 유지
        self.register_buffer('U_X', U_func(dist_grid_target)) # (H*W, N)

        ones_grid = torch.ones(self.grid.size(0), 1, device=self.grid.device)
        self.register_buffer('P_X', torch.cat([ones_grid, self.grid], dim=1)) # (H*W, 3)

    def forward(self, source_control_points):
        B = source_control_points.size(0)
        zeros_B32 = torch.zeros(B, 3, 2, device=source_control_points.device)
        Y = torch.cat([source_control_points, zeros_B32], dim=1)
        L_inv_expand = self.L_inv.unsqueeze(0).expand(B, -1, -1)
        mapping_params = torch.bmm(L_inv_expand, Y)
        W_grid = torch.cat([self.U_X, self.P_X], dim=1) # 수정: U_X, P_X 사용
        W_grid_expand = W_grid.unsqueeze(0).expand(B, -1, -1)
        grid = torch.bmm(W_grid_expand, mapping_params)
        grid = grid.view(B, self.out_H, self.out_W, 2)
        return grid

class TPSSpatialTransformer(nn.Module):
    """ TPS 기반 Spatial Transformer Network 모듈 """
    def __init__(self, F=16, I_size=(256, 256), I_r_size=(256, 256), I_channel_num=1):
        super(TPSSpatialTransformer, self).__init__()
        self.F = F
        self.I_size = I_size
        self.I_r_size = I_r_size
        self.I_channel_num = I_channel_num

        self.localization = nn.Sequential(
            nn.Conv2d(I_channel_num, 8, kernel_size=7, padding=3), nn.MaxPool2d(2, stride=2), nn.ReLU(True),
            nn.Conv2d(8, 10, kernel_size=5, padding=2), nn.MaxPool2d(2, stride=2), nn.ReLU(True),
        )
        loc_output_size = 10 * (I_size[0] // 4) * (I_size[1] // 4)
        self.fc_loc = nn.Sequential(
            nn.Linear(loc_output_size, 32), nn.ReLU(True),
            nn.Linear(32, F * 2)
        )

        initial_bias = self._build_initial_target_control_points(F)
        self.fc_loc[2].weight.data.zero_()
        self.fc_loc[2].bias.data.copy_(initial_bias.view(-1))

        target_control_points_np = initial_bias.cpu().numpy()
        self.tps_grid_gen = TPSGridGen(target_control_points_np, output_size=I_r_size)

    def _build_initial_target_control_points(self, F):
        sqrt_F = int(np.sqrt(F))
        if sqrt_F * sqrt_F != F: raise ValueError("F must be a perfect square.")
        ctrl_pts_x = torch.linspace(-1.0, 1.0, steps=sqrt_F)
        ctrl_pts_y = torch.linspace(-1.0, 1.0, steps=sqrt_F)
        grid_Y, grid_X = torch.meshgrid(ctrl_pts_y, ctrl_pts_x, indexing='ij')
        target_control_points = torch.stack([grid_X.flatten(), grid_Y.flatten()], dim=1)
        return target_control_points

    def forward(self, x):
        features = self.localization(x)
        B = features.size(0)
        features_flat = features.view(B, -1)
        predicted_ctrl_pts = self.fc_loc(features_flat)
        predicted_ctrl_pts = predicted_ctrl_pts.view(B, self.F, 2)
        grid = self.tps_grid_gen(predicted_ctrl_pts)
        x_transformed = F.grid_sample(x, grid, mode='bilinear', padding_mode='border', align_corners=True)
        return x_transformed

# ------------------------ STRNet 모델 (수정됨) ------------------------
class STRNet(nn.Module):
    """ Scene Text Recognition Network """
    def __init__(self, num_classes, nh):
        super(STRNet, self).__init__()
        self.tps = TPSSpatialTransformer(F=16, I_size=(IMG_SIZE, IMG_SIZE), I_r_size=(IMG_SIZE, IMG_SIZE), I_channel_num=1)
        resnet_base = models.resnet34(weights=None)
        resnet_base.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        # <<<--- 수정: 변수 이름 'resnet'로 변경 ---
        self.resnet = nn.Sequential(*list(resnet_base.children())[:-2])
        # --------------------------------------
        self.conv_reduce = nn.Conv2d(512, 256, kernel_size=1)
        self.bilstm = nn.LSTM(input_size=256, hidden_size=nh, num_layers=2,
                              bidirectional=True, batch_first=True, dropout=0.1)
        # <<<--- 수정: 변수 이름 'fc'로 변경 ---
        self.fc = nn.Linear(nh * 2, num_classes + 1) # CTC Blank 포함
        # -----------------------------------

    def forward(self, x):
        x_tps = self.tps(x)
        # <<<--- 수정: 'self.resnet' 사용 ---
        features = self.resnet(x_tps)
        # -------------------------------
        features_reduced = self.conv_reduce(features)
        sequence_features = features_reduced.mean(dim=2)
        sequence_features = sequence_features.permute(0, 2, 1)
        lstm_output, _ = self.bilstm(sequence_features)
        # <<<--- 수정: 'self.fc' 사용 ---
        ctc_output = self.fc(lstm_output)
        # --------------------------
        log_probs = F.log_softmax(ctc_output, dim=2)
        log_probs = log_probs.permute(1, 0, 2)
        return log_probs

# ============================================================
# --- 도우미 함수 ---
# ============================================================

def load_model(model_path, num_classes, nh, device):
    """모델 구조를 생성하고 학습된 가중치를 로드합니다."""
    try:
        # 수정된 STRNet 클래스로 모델 생성
        model = STRNet(num_classes=num_classes, nh=nh).to(device)
        # 저장된 state_dict 로드
        state_dict = torch.load(model_path, map_location=device)
        # 모델에 state_dict 로드 (strict=True가 기본값)
        model.load_state_dict(state_dict)
        model.eval() # 추론 모드로 설정
        print(f"성공: 모델 '{model_path}' 로드 완료.")
        return model
    except FileNotFoundError:
        print(f"[오류] 모델 파일 '{model_path}'를 찾을 수 없습니다.")
        return None
    except RuntimeError as e:
        # state_dict 로딩 오류 시 상세 메시지 포함하여 출력
        print(f"[오류] 모델 state_dict 로드 중 오류 발생: {e}")
        print("모델 정의와 저장된 가중치 파일 간의 레이어 이름 또는 구조 불일치 가능성이 높습니다.")
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"[오류] 모델 로드 중 예상치 못한 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 출력
        return None

def setup_cameras(indices, width, height, fps):
    """지정된 인덱스의 카메라들을 열고 설정합니다."""
    caps = {}
    fourcc = cv2.VideoWriter_fourcc(*'MJPG') # 코덱 설정
    for i in indices:
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if not cap.isOpened():
            print(f"[경고] 카메라 {i}를 열 수 없습니다. 건너<0xEB><0x9B><0x84>니다.")
            continue

        prop_success = True
        prop_success &= cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        prop_success &= cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        prop_success &= cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        prop_success &= cap.set(cv2.CAP_PROP_FPS, fps)

        if not prop_success:
            print(f"[경고] 카메라 {i}의 일부 속성 설정에 실패했습니다.")

        actual_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"카메라 {i} 설정 완료 (요청: {width}x{height} @{fps}fps, 실제: {int(actual_width)}x{int(actual_height)} @{actual_fps}fps)")

        caps[i] = cap

    if not caps:
        print("[오류] 사용 가능한 카메라가 없습니다.")
        return None

    print("카메라 안정화를 위해 잠시 대기...")
    time.sleep(2) # 카메라 초기화 및 안정화 시간
    return caps

def predict_single_roi(roi_img, model, transform, device):
    """ 단일 ROI 이미지에 대한 예측(인덱스 문자열) 및 신뢰도를 반환합니다. """
    input_img = transform(roi_img)
    input_img = input_img.unsqueeze(0).to(device)
    with torch.no_grad():
        output = model(input_img) # 모델 예측 (T, B=1, num_classes+1)
        sequence_length = output.shape[0] # T 값 (오류 수정 반영)

        pred_seq_list = ctc_greedy_decoder(output, blank_index=CTC_BLANK_INDEX)
        pred_str = pred_seq_list[0] if pred_seq_list else ""

        confidence = 0.0
        if sequence_length > 0:
            probs = F.softmax(output, dim=2) # 확률값으로 변환
            max_probs_per_step, _ = probs.max(dim=2) # 각 스텝 최고 확률 (T, 1)
            confidence = max_probs_per_step.mean().item() # 평균 신뢰도

    return pred_str, confidence

def parse_prediction(pred_str, alphabet_map):
    """ 예측된 인덱스 문자열을 실제 문자로 변환하고, 유효성 검사 결과를 반환합니다. """
    pred_int = None # 정수 인덱스
    display_pred = "N/A" # 화면 표시용 문자열
    is_valid = False # 유효한 예측인지 여부

    if pred_str: # 예측된 문자열이 비어있지 않다면
        try:
            pred_int = int(pred_str) # 정수 인덱스로 변환
            if 0 <= pred_int < len(alphabet_map):
                display_pred = alphabet_map[pred_int] # 유효한 인덱스면 문자로 변환
                is_valid = True # 유효한 예측
            else:
                display_pred = "InvalidIdx" # 인덱스 범위 초과
        except ValueError:
            display_pred = "NotInt" # 정수 변환 실패

    return pred_int, display_pred, is_valid

# ============================================================
# --- 스레드 함수 (디버깅 로그 추가) ---
# ============================================================

def udp_trigger_listener(event, stop_flag_func):
    """
    백그라운드에서 UDP 트리거 신호를 수신 대기하고,
    지정된 메시지 수신 시 이벤트를 설정합니다. (디버깅 로그 추가)
    """
    udp_sock = None
    print("[Trigger 스레드] 초기화 시작...") # 스레드 시작 확인
    try:
        # UDP 소켓 생성 및 바인딩
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind((UDP_TRIGGER_IP, UDP_TRIGGER_PORT))
        #udp_sock.settimeout(0.5) # 0.5초마다 타임아웃
        print(f"[Trigger 스레드] 소켓 생성 및 바인딩 완료 (IP: {UDP_TRIGGER_IP}, Port: {UDP_TRIGGER_PORT}).")
        print(f"[Trigger 스레드] '{TRIGGER_MESSAGE.decode()}' 메시지 대기 시작...")

        while stop_flag_func(): # 외부 플래그 확인하며 반복
            # print("[Trigger 스레드] 루프 실행 중, 데이터 수신 시도...") # 필요시 주석 해제
            try:
                data, addr = udp_sock.recvfrom(1024) # 데이터 수신 시도
                print(f"[Trigger 스레드] UDP 데이터 수신 성공: {data} from {addr}") # 수신 성공 시 로그
                if data.strip() == TRIGGER_MESSAGE: # 메시지 비교
                    print("[Trigger 스레드] 유효한 트리거 메시지 확인! 이벤트 설정.")
                    event.set() # 이벤트 설정
                else:
                    print("[Trigger 스레드] 수신된 메시지가 트리거 메시지와 다름.")
            except socket.timeout:
                # 타임아웃은 정상, 루프 계속
                continue
            except socket.error as e:
                print(f"[Trigger 스레드] UDP 소켓 오류 발생: {e}. 잠시 후 재시도...")
                time.sleep(1)
            except Exception as e:
                print(f"[Trigger 스레드] 알 수 없는 오류 발생: {e}")
                traceback.print_exc()
                time.sleep(1)
    except Exception as e:
        print(f"[Trigger 스레드] 초기화 또는 루프 진입 전 오류 발생: {e}") # 초기화 오류 확인
        traceback.print_exc()
    finally:
        # 스레드 종료 시 소켓 정리
        if udp_sock:
            udp_sock.close()
            print("[Trigger 스레드] UDP 소켓 닫힘.")
        else:
            print("[Trigger 스레드] UDP 소켓이 생성되지 않았거나 이미 닫힘.")
        print("[Trigger 스레드] 종료됨.")


# ============================================================
# --- 핵심 예측 및 전송 함수 ---
# ============================================================

def process_and_predict(current_frames, model, transform, device, alphabet_map):
    """
    현재 프레임들에서 ROI를 추출하고, 모델 예측을 수행하며,
    결과를 비교하여 UDP로 전송합니다.
    """
    global send_sock # 전역 전송 소켓 사용

    try:
        print("\n--- 예측 작업 시작 ---")
        start_time = time.time() # 시작 시간 기록

        # --- ROI 추출 및 PIL 변환 ---
        rois_pil = {}
        valid_frames = True
        if not current_frames: # 프레임 딕셔너리가 비어있는 경우
             print("[오류] 예측할 프레임 데이터가 없습니다.")
             return

        for i in CAMERA_INDICES:
            if i not in current_frames or current_frames[i] is None:
                print(f"[오류] 카메라 {i}의 현재 프레임이 유효하지 않습니다.")
                valid_frames = False
                break
            roi = current_frames[i][ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
            rois_pil[i] = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)).convert('L')

        if not valid_frames:
            print("오류로 인해 예측 작업을 중단합니다.")
            return

        # --- 각 ROI에 대해 예측 수행 ---
        predictions = {} # 예측된 인덱스 문자열 저장
        confidences = {} # 예측 신뢰도 저장
        for i in CAMERA_INDICES:
            pred_str, conf = predict_single_roi(rois_pil[i], model, transform, device)
            predictions[i] = pred_str
            confidences[i] = conf

        # --- 예측 결과 파싱 및 유효성 검사 ---
        parsed_predictions = {} # 파싱된 정수 인덱스 저장
        display_outputs = {}    # 화면 출력용 문자열 저장
        prediction_valid = {}   # 각 예측의 유효성 저장
        for i in CAMERA_INDICES:
            pred_int, display_pred, is_valid = parse_prediction(predictions[i], alphabet_map)
            parsed_predictions[i] = pred_int
            display_outputs[i] = display_pred
            prediction_valid[i] = is_valid

        # --- 결과 콘솔 출력 ---
        output_str = " / ".join([f"Cam{i}: {display_outputs[i]} ({int(confidences[i]*100)}%)" for i in CAMERA_INDICES])
        print(f"개별 예측 결과: {output_str}")

        # --- 최종 결과 결정 로직 ---
        final_result_int = None # 최종 결정된 인덱스 (None이면 실패)
        first_valid_pred_int = None
        all_valid_and_same = True

        for i in CAMERA_INDICES:
            if not prediction_valid[i]: # 하나라도 유효하지 않으면 최종 실패
                all_valid_and_same = False
                print(f"판정 실패: 카메라 {i}의 예측이 유효하지 않음 ('{display_outputs[i]}')")
                break
            if first_valid_pred_int is None:
                first_valid_pred_int = parsed_predictions[i]
            elif parsed_predictions[i] != first_valid_pred_int:
                all_valid_and_same = False
                print(f"판정 실패: 카메라 {i}의 예측({display_outputs[i]})이 이전 예측({alphabet_map[first_valid_pred_int]})과 불일치")
                break

        # --- 최종 결과 결정 및 UDP 전송 ---
        if all_valid_and_same:
            final_result_int = first_valid_pred_int
            # 'empty' 클래스 처리: 'empty'로 판정되면 'fail' 전송 (요구사항에 따라 변경 가능)
            if alphabet_map[final_result_int] == 'empty':
                 message_to_send = b"empty"
                 display_sent = "empty (detected 'empty')"
                 print(f"결정: 'empty' 전송")
            else:
                message_to_send = alphabet_map[final_result_int].encode('utf-8')
                display_sent = alphabet_map[final_result_int]
                print(f"최종 판정: '{display_sent}' (모든 카메라 유효 및 일치)")
        else:
            # 실패 시 'fail' 메시지 전송
            message_to_send = b"fail"
            display_sent = "fail"
            print("최종 판정: 'fail'")

        # UDP 메시지 전송
        try:
            send_sock.sendto(message_to_send, (UDP_SEND_IP, UDP_SEND_PORT))
            print(f"--> UDP 전송: '{display_sent}' to {UDP_SEND_IP}:{UDP_SEND_PORT}")
        except socket.error as e:
            print(f"[오류] UDP 메시지 전송 실패: {e}")
        except Exception as e:
            print(f"[오류] UDP 전송 중 예상치 못한 오류: {e}")
            traceback.print_exc()

        # 작업 소요 시간 출력
        end_time = time.time()
        print(f"--- 예측 작업 완료 (소요 시간: {end_time - start_time:.3f}초) ---\n")

    except Exception as e:
        # 예측 함수 전체에서 예외 발생 시
        print(f"[심각한 오류] process_and_predict 함수 실행 중 오류 발생: {e}")
        traceback.print_exc() # 상세 오류 스택 출력


# ============================================================
# --- 메인 실행 함수 ---
# ============================================================

def main():
    global keep_threads_running # 전역 종료 플래그 사용

    # --- 초기화 ---
    print("프로그램 초기화 시작...")
    model = load_model(MODEL_PATH, NUM_CLASSES, LSTM_HIDDEN_SIZE, device)
    if not model:
        print("[치명적 오류] 모델 로딩 실패. 프로그램을 종료합니다.")
        return

    caps = setup_cameras(CAMERA_INDICES, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS)
    if not caps:
        print("[치명적 오류] 카메라 초기화 실패. 프로그램을 종료합니다.")
        return

    # --- UDP 트리거 리스너 스레드 시작 ---
    udp_thread = threading.Thread(target=udp_trigger_listener,
                                 args=(trigger_event, lambda: keep_threads_running),
                                 daemon=True) # 메인 종료시 자동 종료
    udp_thread.start()

    # --- 메인 루프 (카메라 영상 표시 및 트리거 대기) ---
    print("\n초기화 완료. 실시간 카메라 영상 표시 중...")
    print(f"예측 시작: Enter 키 입력 또는 UDP 포트 {UDP_TRIGGER_PORT}로 '{TRIGGER_MESSAGE.decode()}' 메시지 전송")
    print("프로그램 종료: 'q' 키 입력")

    active_cameras = list(caps.keys()) # 현재 활성화된 카메라 인덱스 리스트

    while keep_threads_running:
        current_frames = {}
        valid_read = True

        # --- 모든 활성 카메라에서 프레임 읽기 ---
        # start_read_time = time.perf_counter() # 성능 측정용
        for i in active_cameras:
            ret, frame = caps[i].read()
            if not ret:
                print(f"[경고] 카메라 {i}에서 프레임 읽기 실패!")
                valid_read = False
                # TODO: 실패한 카메라 재연결 로직 추가 고려
                break # 이번 루프 중단
            current_frames[i] = frame
        # read_time = time.perf_counter() - start_read_time

        if not valid_read:
            time.sleep(0.1) # 프레임 읽기 실패 시 잠시 대기 후 재시도
            continue

        # --- 실시간 영상 및 ROI 표시 ---
        # display_start_time = time.perf_counter()
        for i in active_cameras:
            if i in current_frames and current_frames[i] is not None:
                try:
                    frame_display = current_frames[i].copy() # 원본 보존
                    cv2.rectangle(frame_display, (ROI_X1, ROI_Y1), (ROI_X2, ROI_Y2), (0, 255, 0), 3) # ROI
                    display_small = cv2.resize(frame_display, (400, 300), interpolation=cv2.INTER_LINEAR) # 창 크기 조절
                    cv2.imshow(f'Camera {i}', display_small)
                except Exception as display_err:
                    print(f"[오류] 카메라 {i} 영상 표시 중 오류: {display_err}")
        # display_time = time.perf_counter() - display_start_time

        # --- 키 입력 확인 (1ms 대기) ---
        key = cv2.waitKey(1) & 0xFF

        # 'q' 입력 시 종료 플래그 설정 및 루프 탈출
        if key == ord('q'):
            print("\n'q' 키 입력 감지. 프로그램 종료 시작...")
            keep_threads_running = False # 스레드 종료 신호
            break

        # Enter 키 입력 시 트리거 이벤트 설정 (이미 설정되지 않았을 경우)
        if key in [13, 10] and not trigger_event.is_set():
            print("\n[Main 스레드] Enter 키 입력 감지! 예측 트리거 이벤트 설정.")
            trigger_event.set()

        # --- 트리거 이벤트 확인 및 예측 실행 ---
        if trigger_event.is_set():
            print("[Main 스레드] 예측 트리거 이벤트 감지! 예측 및 전송 로직 실행...")
            trigger_event.clear() # 이벤트 즉시 초기화

            # 현재 유효한 프레임들로 예측 수행
            process_and_predict(current_frames, model, transform, device, ALPHABET)

            print("\n예측 완료. 다시 트리거 대기 상태로 돌아갑니다.")

    # --- 종료 처리 ---
    print("\n메인 루프 종료. 리소스 정리 시작...")

    # 카메라 리소스 해제
    print("카메라 리소스 해제...")
    for i, cap in caps.items():
        print(f"  카메라 {i} 해제 중...")
        cap.release()

    # OpenCV 창 닫기
    print("OpenCV 창 닫기...")
    cv2.destroyAllWindows()

    # 전송용 UDP 소켓 닫기
    if send_sock:
        print("전송용 UDP 소켓 닫기...")
        send_sock.close()

    # UDP 리스너 스레드가 완전히 종료될 때까지 잠시 기다림 (선택 사항)
    print("UDP 리스너 스레드 종료 대기...")
    time.sleep(0.6) # 스레드가 timeout(0.5s) 후 종료할 시간을 줌
    if udp_thread.is_alive():
         print("[경고] UDP 트리거 스레드가 아직 실행 중입니다.")

    print("프로그램이 정상적으로 종료되었습니다.")

# ============================================================
# --- 스크립트 실행 ---
# ============================================================
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n[최상위 예외 발생] 프로그램 실행 중 심각한 오류가 발생했습니다.")
        print(f"오류: {e}")
        traceback.print_exc() # 상세 오류 스택 출력
    finally:
        # 예외 발생 시에도 최소한의 정리 시도 (이미 main 함수 내부에 포함됨)
        print("프로그램 최종 종료.")