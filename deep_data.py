import os
import re
import time
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, random_split
from torchvision import transforms, models
from PIL import Image, ImageDraw, ImageFilter
import matplotlib.pyplot as plt
import matplotlib
import random
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
import torch.cuda.amp as amp


from tqdm import tqdm  # tqdm 라이브러리 임포트 (진행바)
from torch.utils.tensorboard import SummaryWriter  # TensorBoard 기록을 위한 SummaryWriter 임포트

torch.backends.cudnn.benchmark = True
DEBUG = True  # 디버깅 메시지 출력 여부

# ------------------------ 1. 데이터셋 정의 ------------------------
class OCRDataset(Dataset):
    def __init__(self, root_dir, transform=None):
        """
        root_dir: 데이터셋의 루트 디렉토리 (예: 'roi_dataset copy')
        각 서브디렉토리의 이름(라벨)이 이미지의 정답으로 사용됩니다.
        """
        self.samples = []
        self.transform = transform
        self.alphabet = [
                        'A', 'B', 'C', 'D', 'E', 'F', 'G',
                        'H', 'I', 'J', 'K', 'L', 'M', 'N',
                        'O', 'P', 'Q', 'R', 'S', 'T', 'U',
                        'V', 'W', 'X', 'Y', 'Z','empty'
                    ]
        self.label2idx = {char: idx for idx, char in enumerate(self.alphabet)}
        for label in sorted(os.listdir(root_dir)):
            label_dir = os.path.join(root_dir, label)
            if os.path.isdir(label_dir):
                for file_name in os.listdir(label_dir):
                    if file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                        img_path = os.path.join(label_dir, file_name)
                        self.samples.append((img_path, label))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, label = self.samples[idx]
        image = Image.open(img_path).convert('L')
        if self.transform:
            image = self.transform(image)
        
        # 라벨이 단일 문자가 아닌 경우도 고려하여 전체 라벨로 매핑
        if label in self.label2idx:
            label_number = self.label2idx[label]
        else:
            raise ValueError(f"알 수 없는 라벨: {label}")
        
        return image, label_number




# ------------------------ 2. 커스텀 데이터 증강 ------------------------
class AddGaussianNoise(object):
    def __init__(self, mean=0.0, std=0.05):
        self.mean = mean
        self.std = std
        
    def __call__(self, tensor):
        noise = torch.randn(tensor.size()) * self.std + self.mean
        return tensor + noise

    def __repr__(self):
        return self.__class__.__name__ + f'(mean={self.mean}, std={self.std})'

class AddGlareEffect(object):
    """
    이미지에 랜덤으로 빛 반사(Glare) 효과를 추가하는 증강 클래스.
    """
    def __init__(self, probability=0.5, max_radius=50):
        self.probability = probability
        self.max_radius = max_radius

    def __call__(self, img):
        if random.random() > self.probability:
            return img

        if img.mode != "RGB":
            img = img.convert("RGB")
        width, height = img.size
        
        overlay = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        center_x = random.randint(0, width)
        center_y = random.randint(0, height)
        radius = random.randint(10, self.max_radius)
        
        bbox = [center_x - radius, center_y - radius, center_x + radius, center_y + radius]
        draw.ellipse(bbox, fill=(255, 255, 255))
        
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=radius/2))
        blended = Image.blend(img, overlay, alpha=0.3)
        blended = blended.convert("L")
        return blended

    def __repr__(self):
        return self.__class__.__name__ + f'(probability={self.probability}, max_radius={self.max_radius})'

class ElasticTransform(object):
    """
    Elastic Transformation을 적용하는 증강 클래스.
    alpha: 변형 강도 (예: 34)
    sigma: 가우시안 필터의 표준편차 (예: 4)
    probability: 효과 적용 확률
    """
    def __init__(self, alpha=34, sigma=4, probability=0.5):
        self.alpha = alpha
        self.sigma = sigma
        self.probability = probability

    def __call__(self, img):
        if random.random() > self.probability:
            return img

        image = np.array(img)
        shape = image.shape[:2]

        dx = gaussian_filter((np.random.rand(*shape) * 2 - 1), self.sigma, mode="constant", cval=0) * self.alpha
        dy = gaussian_filter((np.random.rand(*shape) * 2 - 1), self.sigma, mode="constant", cval=0) * self.alpha

        x, y = np.meshgrid(np.arange(shape[1]), np.arange(shape[0]))
        indices = np.reshape(y + dy, (-1, 1)), np.reshape(x + dx, (-1, 1))
        
        if image.ndim == 3:
            distorted_image = np.stack(
                [map_coordinates(image[:,:,c], indices, order=1, mode='reflect').reshape(shape) for c in range(image.shape[2])],
                axis=-1
            )
        else:
            distorted_image = map_coordinates(image, indices, order=1, mode='reflect').reshape(shape)

        return Image.fromarray(distorted_image.astype(np.uint8))

    def __repr__(self):
        return f"{self.__class__.__name__}(alpha={self.alpha}, sigma={self.sigma}, probability={self.probability})"

# ------------------------ 3. TPS 변환 관련 모듈 ------------------------
def U_func(r):
    # U(r) = r^2 * log(r^2), r가 0일때는 0으로 정의
    # torch.where를 사용하여 0인 경우 log를 방지
    r = torch.where(r == 0, torch.ones_like(r), r)
    return (r**2) * torch.log(r**2)

class TPSGridGen(nn.Module):
    def __init__(self, target_control_points, output_size):
        super(TPSGridGen, self).__init__()
        self.register_buffer('target_control_points', torch.tensor(target_control_points, dtype=torch.float32))
        self.N = target_control_points.shape[0]
        self.out_H, self.out_W = output_size


        # 타깃 제어점을 tensor로 변환 및 (N,2) 크기 고정
        target_control_points = torch.tensor(target_control_points, dtype=torch.float32)  # (N,2)
        self.register_buffer('target_control_points', target_control_points)

        # 타깃 제어점 사이의 거리를 계산하여 K 행렬 구성
        pairwise_diff = target_control_points.unsqueeze(1) - target_control_points.unsqueeze(0)  # (N, N, 2)
        pairwise_dist = torch.norm(pairwise_diff, dim=2)  # (N, N)
        K = U_func(pairwise_dist)  # (N, N)

        # P 행렬: (N, 3) [1, x, y]
        ones = torch.ones(self.N, 1)
        P = torch.cat([ones, target_control_points], dim=1)  # (N,3)

        # L 행렬 구성: (N+3, N+3)
        upper = torch.cat([K, P], dim=1)  # (N, N+3)
        lower = torch.cat([P.t(), torch.zeros(3, 3)], dim=1)  # (3, N+3)
        L = torch.cat([upper, lower], dim=0)  # (N+3, N+3)

        # L의 역행렬 (정규화된 L_inv)
        L_inv = torch.inverse(L)
        self.register_buffer('L_inv', L_inv)

        # 출력 이미지의 grid 좌표 생성 (정규화 좌표 [-1, 1])
        grid_X, grid_Y = torch.meshgrid(torch.linspace(-1, 1, self.out_W), torch.linspace(-1, 1, self.out_H))
        # grid: (H*W, 2)
        grid = torch.stack([grid_X.t().contiguous().view(-1), grid_Y.t().contiguous().view(-1)], dim=1)
        self.register_buffer('grid', grid)  # (H*W, 2)

        # U 함수에 필요한 부분: target_control_points와 grid 간 거리 계산
        # grid_expand: (H*W, 1, 2), target: (1, N, 2)
        grid_expand = self.grid.unsqueeze(1)  # (H*W, 1, 2)
        target_expand = self.target_control_points.unsqueeze(0)  # (1, N, 2)
        # 거리: (H*W, N)
        dist = torch.norm(grid_expand - target_expand, dim=2)
        self.register_buffer('U_X', U_func(dist))  # (H*W, N)

        # P_hat: (H*W, 3) = [1, x, y] for grid points
        ones_grid = torch.ones(self.grid.size(0), 1, device=self.grid.device)
        self.register_buffer('P_X', torch.cat([ones_grid, self.grid], dim=1))  # (H*W, 3)

    def forward(self, predicted_control_points):
        """
        predicted_control_points: (B, N, 2) 네트워크가 예측한 제어점 (source control points)
        TPS 파라미터를 구하고, 각 이미지에 대해 변환 grid를 생성함.
        """
        B = predicted_control_points.size(0)
        # 확장: (B, N, 2) -> (B, N+3, 2)
        # 우선, 대상(control points)에 해당하는 파라미터를 구하기 위해
        # [predicted_control_points; 0,0,0] (B, N+3, 2)
        zeros = torch.zeros(B, 3, 2, device=predicted_control_points.device)
        Y = torch.cat([predicted_control_points, zeros], dim=1)  # (B, N+3, 2)

        # L_inv: (N+3, N+3), expand하여 (B, N+3, N+3)
        L_inv = self.L_inv.unsqueeze(0).expand(B, -1, -1)  # (B, N+3, N+3)

        # TPS 파라미터: (B, N+3, 2)
        mapping_params = torch.bmm(L_inv, Y)  # (B, N+3, 2)

        # grid를 구성하기 위해, 각 grid point에 대해 [U, P_X]를 구성
        # U_X: (H*W, N), P_X: (H*W, 3)
        # 합치면: (H*W, N+3)
        W_X = torch.cat([self.U_X, self.P_X], dim=1)  # (H*W, N+3)

        # 각 이미지에 대해 grid 계산: (B, H*W, 2) = (B, N+3, 2)과 (H*W, N+3)의 행렬곱
        grid = torch.bmm(W_X.unsqueeze(0).expand(B, -1, -1), mapping_params)  # (B, H*W, 2)
        grid = grid.view(B, self.out_H, self.out_W, 2)
        return grid

class TPSSpatialTransformer(nn.Module):
    """
    TPS Spatial Transformer 모듈.
    localization 네트워크를 통해 제어점을 예측하고, TPSGridGen을 사용해 이미지를 변환합니다.
    """
    def __init__(self, F=16, I_size=(256, 256), I_r_size=(256, 256), I_channel_num=1):
        super(TPSSpatialTransformer, self).__init__()
        self.F = F  # 제어점 개수
        self.I_size = I_size
        self.I_r_size = I_r_size
        self.I_channel_num = I_channel_num

        # localization network
        self.localization = nn.Sequential(
            nn.Conv2d(I_channel_num, 8, kernel_size=7, stride=1, padding=3),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True),
            nn.Conv2d(8, 10, kernel_size=5, stride=1, padding=2),
            nn.MaxPool2d(2, stride=2),
            nn.ReLU(True)
        )
        self.fc_loc = nn.Sequential(
            nn.Linear(10 * (I_size[0]//4) * (I_size[1]//4), 32),
            nn.ReLU(True),
            nn.Linear(32, F * 2)
        )
        # fc_loc의 마지막 레이어 초기화를 identity에 가깝게 설정
        self.fc_loc[2].weight.data.zero_()
        initial_bias = self._build_initial_bias(F)
        self.fc_loc[2].bias.data.copy_(initial_bias)

        # 타깃 제어점: 정규 격자 (F개의 제어점, 좌표 범위 [-1, 1])
        target_control_points = self._build_initial_bias(F).view(-1, 2).cpu().numpy()
        self.tps_grid_gen = TPSGridGen(target_control_points, output_size=I_r_size)

    def _build_initial_bias(self, F):
        # F개의 제어점을 정규 격자에 배치 (예: sqrt(F) x sqrt(F))
        grid_size = int(np.sqrt(F))
        ctrl_pts_x = np.linspace(-1.0, 1.0, grid_size)
        ctrl_pts_y = np.linspace(-1.0, 1.0, grid_size)
        P_Y, P_X = np.meshgrid(ctrl_pts_y, ctrl_pts_x)
        initial_bias = np.stack([P_X, P_Y], axis=2).astype(np.float32)
        initial_bias = initial_bias.reshape(-1)
        return torch.from_numpy(initial_bias)

    def forward(self, x):
        # x: (B, C, H, W)
        xs = self.localization(x)
        xs = xs.view(xs.size(0), -1)
        # 예측된 제어점 (B, F*2)
        predicted_ctrl_pts = self.fc_loc(xs)
        predicted_ctrl_pts = predicted_ctrl_pts.view(-1, self.F, 2)  # (B, F, 2)
        # TPS grid 생성
        grid = self.tps_grid_gen(predicted_ctrl_pts)
        # grid_sample를 통해 이미지 변환 (align_corners=True 권장)
        x_transformed = F.grid_sample(x, grid, align_corners=True)
        return x_transformed

# ------------------------ 4. STRNet (TPS+ResNet+BiLSTM+CTC) 모델 ------------------------
class STRNet(nn.Module):
    def __init__(self, num_classes, nh):
        """
        num_classes: 실제 클래스 개수 (예: 9)
        nh: LSTM hidden size
        CTC를 위해 blank 심볼을 추가하므로 최종 출력 차원은 num_classes+1
        """
        super(STRNet, self).__init__()
        # 1) TPS 모듈
        self.tps = TPSSpatialTransformer(F=16, I_size=(256, 256), I_r_size=(256, 256), I_channel_num=1)
        
        # 2) ResNet 백본 (ResNet34 사용, 입력 채널 1)
        resnet = models.resnet34(pretrained=True)
        resnet.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        # 마지막 fc, avgpool 제거
        self.resnet = nn.Sequential(*list(resnet.children())[:-2])  # 출력: (B, 512, H', W')
        
        # 3) 채널 수 축소 (512 -> 256)
        self.conv_reduce = nn.Conv2d(512, 256, kernel_size=1)
        
        # 4) BiLSTM
        self.bilstm = nn.LSTM(256, nh, num_layers=2, bidirectional=True, batch_first=True, dropout=0.3)
        
        # 5) 최종 분류기 (CTC를 위해 num_classes+1, blank 심볼)
        self.fc = nn.Linear(2 * nh, num_classes + 1)
        
    def forward(self, x):
        # x: (B, 1, H, W)
        x = self.tps(x)  # TPS 변환
        x = self.resnet(x)  # (B, 512, H', W')
        x = self.conv_reduce(x)  # (B, 256, H', W')
        # H' 방향에 대해 평균 풀링하여 시퀀스 특성으로 변환 (가로 방향 시퀀스)
        x = x.mean(dim=2)  # (B, 256, W')
        x = x.permute(0, 2, 1)  # (B, W', 256)
        x, _ = self.bilstm(x)  # (B, W', 2*nh)
        x = self.fc(x)  # (B, W', num_classes+1)
        # CTC Loss를 위해 log_softmax 적용 및 (T, B, C) 형태로 변환
        x = x.log_softmax(2)
        x = x.permute(1, 0, 2)  # (T, B, num_classes+1)
        return x

# ------------------------ 5. CTC 후처리 (그리디 디코딩) ------------------------
def ctc_greedy_decoder(ctc_output, blank_index):
    """
    ctc_output: (T, B, C) - 네트워크 출력 (로그 확률)
    blank_index: CTC blank 심볼 인덱스
    반환: 리스트 (길이 B) - 각 배치별 디코딩 결과 문자열
    """
    # argmax: (T, B)
    max_probs = torch.argmax(ctc_output, dim=2)  # (T, B)
    max_probs = max_probs.cpu().numpy().transpose(1, 0)  # (B, T)
    
    results = []
    for seq in max_probs:
        prev = -1
        decoded = []
        for idx in seq:
            # 중복 제거 및 blank 무시
            if idx != prev and idx != blank_index:
                decoded.append(str(idx))
            prev = idx
        results.append(''.join(decoded))
    return results

def calculate_accuracy(ctc_output, labels, blank_index):
    """정확도 계산 함수"""
    decoded_preds = ctc_greedy_decoder(ctc_output, blank_index)
    correct = 0
    for pred, label in zip(decoded_preds, labels.cpu().numpy()):
        if pred == str(label):
            correct += 1
    return correct / len(labels)

## 학습 파일 버전 관리
def get_next_version_filename(filename, dir_path="."):
    """
    지정된 디렉토리 내에서 동일한 파일명이 존재하면 버전 번호를 올려서
    새로운 파일명으로 반환하는 함수.
    
    예) filename이 "alp_best_v1.pth" 인 경우,
    동일 파일이 있으면 "alp_best_v2.pth" 반환.
    """
    name, ext = os.path.splitext(filename)
    
    # 기존 파일이 없다면 그대로 반환
    candidate = os.path.join(dir_path, filename)
    if not os.path.exists(candidate):
        return candidate
    
    # 파일명에서 v숫자 패턴 추출 (예: alp_best_v1)
    pattern = re.compile(r"(.*)_v(\d+)$")
    match = pattern.match(name)
    if match:
        base_name = match.group(1)
        version = int(match.group(2))
    else:
        base_name = name
        version = 1
    
    # 이미 있는 파일들을 확인하여 가장 큰 버전 번호 찾기
    while True:
        version += 1
        new_filename = f"{base_name}_v{version}{ext}"
        candidate = os.path.join(dir_path, new_filename)
        if not os.path.exists(candidate):
            return candidate
# ------------------------ 6. 학습 및 검증 코드 ------------------------#

def train_ocr_model():
    ######################################################################
    ################################수정###################################
    ######################################################################
    data_folder = 'abc_dataset'  # 데이터셋 폴더
    img_size = 256               # 이미지 사이즈
    num_classes = 27             # 알파벳 26개
    epochs = 200                 # 에포크
    batch_size = 128             # 배치 사이즈
    learning_rate = 0.000001       # 학습률
    nh = 1024                     # 히든 레이어 차원
    file_name = get_next_version_filename('alphabet_v1.pth')  # 저장할 학습파일 이름 (최초 호출, 이후 저장 시 변경 X)
    ######################################################################
    ######################################################################
    ######################################################################
    train_loss_history = []
    val_loss_history = []

    # TensorBoard 기록용 SummaryWriter 생성 (로그 저장 경로 지정)
    writer = SummaryWriter(log_dir="runs/ocr_experiment")

    transform = transforms.Compose([
        transforms.RandomRotation(degrees=(-30, 30)),
        transforms.Resize((img_size, img_size)),
        transforms.ColorJitter(brightness=0.5, contrast=0.5),
        transforms.RandomHorizontalFlip(p=0.4),
        transforms.RandomVerticalFlip(p=0.4),
        AddGlareEffect(probability=0.5, max_radius=50),
        transforms.GaussianBlur(kernel_size=(5, 5), sigma=(0.1, 5)),
        transforms.ToTensor(),
        AddGaussianNoise(mean=0.0, std=0.1),
        transforms.Normalize((0.5,), (0.5,))
    ])

    dataset = OCRDataset(data_folder, transform=transform)
    total_size = len(dataset)
    train_size = int(0.8 * total_size)
    val_size = total_size - train_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=6, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=6, pin_memory=True)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = STRNet(num_classes=num_classes, nh=nh).to(device)

    criterion = nn.CTCLoss(blank=num_classes)
    optimizer = optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-5)
    
    scaler = amp.GradScaler()

    best_val_loss = float('inf')
    best_epoch = -1
    recent_window = 10
    recent_val_losses = []


    for epoch in range(epochs):
        model.train()
        running_loss = 0.0
        train_accuracy = 0.0

        # tqdm을 사용하여 학습 진행바를 표시 (train_loader 반복문 수정)
        for images, labels in tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs} [Train]"):
            images = images.to(device)
            # labels가 단일 숫자라면, tensor로 변환 (이미 __getitem__에서 단일 숫자로 반환)
            labels = torch.tensor(labels, dtype=torch.long).to(device)
            optimizer.zero_grad()

            with amp.autocast():
                outputs = model(images)
                T, B, C = outputs.size()
                input_lengths = torch.full((B,), T, dtype=torch.long).to(device)
                target_lengths = torch.ones(B, dtype=torch.long).to(device)
                targets = labels.view(-1)
                loss = criterion(outputs, targets, input_lengths, target_lengths)
                
            
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()

            running_loss += loss.item()
            train_accuracy += calculate_accuracy(outputs.detach().cpu(), labels, num_classes)
        
        avg_train_accuracy = train_accuracy / len(train_loader)
        writer.add_scalar("Accuracy/Train", avg_train_accuracy, epoch+1)  # TensorBoard에 Train accuracy 기록        
        avg_train_loss = running_loss / len(train_loader)
        train_loss_history.append(avg_train_loss)
        print(f"Epoch [{epoch+1}/{epochs}] 평균 Train Loss: {avg_train_loss:.4f}")
        writer.add_scalar("Loss/Train", avg_train_loss, epoch+1)  # TensorBoard에 Train loss 기록

        model.eval()
        val_loss = 0.0
        val_accuracy = 0.0
        # tqdm을 사용하여 검증 진행바를 표시 (val_loader 반복문 수정)
        with torch.no_grad():
            for images, labels in tqdm(val_loader, desc=f"Epoch {epoch+1}/{epochs} [Val]"):
                images = images.to(device)
                labels = torch.tensor(labels, dtype=torch.long).to(device)
                with amp.autocast():
                    outputs = model(images)
                    T, B, C = outputs.size()
                    input_lengths = torch.full((B,), T, dtype=torch.long).to(device)
                    target_lengths = torch.ones(B, dtype=torch.long).to(device)
                    targets = labels.view(-1)
                    loss = criterion(outputs, targets, input_lengths, target_lengths)
                val_loss += loss.item()
                val_accuracy += calculate_accuracy(outputs.detach().cpu(), labels, num_classes)
        avg_val_accuracy = val_accuracy / len(val_loader)
        writer.add_scalar("Accuracy/Validation", avg_val_accuracy, epoch+1)  # TensorBoard에 Validation accuracy 기록
        avg_val_loss = val_loss / len(val_loader)
        val_loss_history.append(avg_val_loss)
        recent_val_losses.append((epoch + 1, avg_val_loss))
        
        if len(recent_val_losses) > recent_window:
            recent_val_losses.pop(0)
        
        min_epoch, min_loss = min(recent_val_losses, key=lambda x: x[1])
        if (epoch + 1) == min_epoch:
            recent_file_name = "alphabet_best10.pth"
            torch.save(model.state_dict(), recent_file_name)
            print(f"💾 최근 10 에폭 기준 Best 모델 저장됨: {recent_file_name} (Epoch {min_epoch}, Loss: {min_loss:.4f})")

        print(f"Epoch [{epoch+1}/{epochs}] Validation Loss: {avg_val_loss:.4f}")
        writer.add_scalar("Loss/Validation", avg_val_loss, epoch+1)  # TensorBoard에 Validation loss 기록

        # 최적 모델 저장 시점
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_epoch = epoch + 1
            # 저장할 때마다 새로운 파일로 저장하려면 아래와 같이 get_next_version_filename 함수를 호출
            # 만약 매번 덮어쓰기 원한다면 해당 함수를 제거하면 됩니다.
            #save_path = get_next_version_filename(file_name, dir_path=save_dir)
            torch.save(model.state_dict(), file_name)
            print(f"✅ Best model 저장: {file_name} (Epoch: {best_epoch}, Val Loss: {best_val_loss:.4f})")
        #torch.save(model.state_dict(), {last_file_name})
    
    writer.close()  # TensorBoard 기록 마무리

    print(f"모델 학습 완료. 최적 epoch: {best_epoch}, 최적 Validation Loss: {best_val_loss:.4f}")

    matplotlib.use("TkAgg")
    epochs_range = range(1, epochs + 1)
    plt.figure(figsize=(8, 6))
    plt.plot(epochs_range, train_loss_history, label="Train Loss")
    plt.plot(epochs_range, val_loss_history, label="Validation Loss")
    plt.xlabel("Epoch")
    plt.ylabel("CTC Loss")
    plt.title("Train vs Validation Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig("training_curve_ctc.png")
    plt.show()

if __name__ == "__main__":
    train_ocr_model()
