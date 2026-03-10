# STRNet OCR 기반 자동 알파벳 비즈 분류 시스템

작은 알파벳이 적힌 비즈를 카메라로 촬영한 뒤, OCR 모델로 문자를 인식하고 로봇 암을 이용해 각 알파벳별로 자동 분류하는 시스템입니다.

본 프로젝트는 **PyTorch 기반 STRNet OCR 모델**과 **Python 기반 하드웨어 제어 로직**을 통합하여, 특정 키트(알파벳 비즈)를 자동으로 인식하고 분류하도록 구현한 프로젝트입니다.  
다중 카메라 영상에서 관심 영역(ROI)을 추출하고, OCR 모델을 통해 비즈 표면의 알파벳을 판별한 뒤, 결과를 UDP 통신으로 로봇 제어 시스템에 전달하여 Pick & Place 동작까지 수행합니다.

또한 서보 모터 제어, 멀티플렉서 기반 PWM 제어, 솔레노이드/공압 그리퍼 제어, LED 조명 제어, 포토 센서 피드백, Flask 기반 GUI를 포함하여 실제 자동화 장비 형태로 동작하도록 구성했습니다.

---

## 프로젝트 개요

이 시스템은 다음과 같은 흐름으로 동작합니다.

1. 다중 카메라에서 비즈 이미지를 입력받음
2. 지정된 ROI를 추출하여 OCR 모델에 입력
3. 비즈에 적힌 알파벳을 예측
4. 예측 결과를 UDP로 로봇 제어부에 전달
5. 로봇 암이 해당 알파벳 분류 위치로 비즈를 이동
6. 센서와 조명을 통해 작업 상태를 확인하고 다음 작업 진행

즉, 단순 문자 인식이 아니라 **비전 인식 + 로봇 제어 + 장비 제어 + 사용자 인터페이스**까지 포함한 End-to-End 자동 분류 시스템입니다.

---

## 주요 기능 
작동 영상 링크 : https://youtu.be/P2T75jyCVEo?si=xbj1cv9V9POdzd9b

<img width="646" height="366" alt="image" src="https://github.com/user-attachments/assets/e9010dbe-446e-4e40-a5c6-fee3cd4734ff" />

### 1. 알파벳 비즈 OCR 인식
- 작은 알파벳이 적힌 비즈를 카메라로 촬영
- ROI(관심 영역)만 추출하여 OCR 정확도 향상
- STRNet 기반 OCR 모델로 문자 분류
- A~Z 및 `empty` 클래스를 예측

### 2. 다중 카메라 기반 판별
- 여러 대의 카메라에서 동시에 영상을 수집
- 각 카메라의 예측 결과를 비교하여 최종 문자 판정
- 단일 카메라 오검출 가능성을 줄이도록 구성

### 3. UDP 기반 시스템 연동
- OCR 결과를 UDP로 로봇 제어부에 전송
- 제어부에서 문자(A/B/C 등), fail, empty 상태를 수신하여 동작 분기
- 작업 완료 후 다음 작업 신호 송신 가능

<img width="685" height="480" alt="image" src="https://github.com/user-attachments/assets/c084bf45-d50c-441c-94ff-6c0ccbc7c866" />

### 4. 로봇 Pick & Place 제어
- 미리 정의된 pose 데이터를 기반으로 집기/이동/놓기 동작 수행
- S-curve 프로파일링을 적용한 부드러운 모션 제어
- 분류 결과에 따라 지정된 위치에 비즈 배치

### 5. 하드웨어 인터페이스 통합
- 서보 모터 제어
- 공압 그리퍼 및 솔레노이드 제어
- 릴레이 제어
- LED 조명 PWM 제어
- 포토 센서 기반 결과 확인

### 6. 웹 GUI 지원
- Flask 기반 웹 UI 제공
- 실시간 모터 각도 제어
- 포즈 저장 / 로드 / 삭제
- 장비 세팅 및 테스트 편의성 향상

---

## 시스템 워크플로우

### 1) 카메라 입력 및 ROI 추출
다중 카메라에서 영상을 입력받고, 비즈가 위치한 영역만 ROI로 추출합니다.

### 2) OCR 모델 예측
추출된 ROI 이미지를 STRNet 모델에 입력하여 알파벳을 예측합니다.

### 3) 결과 통합 및 전송
여러 카메라의 예측값을 비교하고 최종 판정 결과를 UDP로 전송합니다.

### 4) 명령 수신 및 동작 분기
로봇 제어부에서 문자 결과 또는 fail/empty 신호를 받아 동작을 결정합니다.

### 5) Pick & Place 실행
로봇 암이 비즈를 집고, 해당 문자 분류 위치로 이동한 뒤 내려놓습니다.

### 6) 센서 확인 및 상태 피드백
포토 센서를 통해 배출 여부를 확인하고, 조명 및 상태 신호를 통해 다음 작업으로 넘어갑니다.

### 7) GUI 기반 수동 제어
필요 시 웹 GUI를 통해 모터 위치를 조정하거나 포즈를 관리할 수 있습니다.

---

## 기술 스택

### AI / OCR
- Python
- PyTorch
- torchvision
- PIL
- NumPy
- SciPy

### OCR 모델 구조
- STRNet
  - TPS
  - ResNet34
  - BiLSTM
  - CTC

### 영상 처리
- OpenCV
- 다중 카메라 제어
- ROI 추출

### 시스템 통합
- Python socket
- Python threading
- UDP 통신

### 하드웨어 제어
- Adafruit CircuitPython ServoKit
- PCA9548A 멀티플렉서
- RPi.GPIO / gpiozero
- 릴레이 / 솔레노이드 / 포토센서 / LED PWM

### GUI
- Flask
- HTML / CSS / JavaScript
- Fetch API

---

## 프로젝트 구조

```text
ocr/
├─ README.md
├─ deep_camera3.py       # OCR 추론 메인 코드
├─ deep_data.py          # OCR 데이터셋 / 학습 / 증강 관련 코드
├─ gui_motor.py          # Flask 기반 웹 GUI
├─ move_module.py        # 서보 모터 제어 및 S-curve 모션
├─ kit_init_module.py    # ServoKit / 멀티플렉서 초기화
├─ light_module.py       # LED 조명 PWM 제어
├─ mos_photo.py          # 릴레이 + 포토센서 확인 로직
├─ move.py               # 이동 관련 실행 파일
├─ grip.py               # 공압 그리퍼 / 솔레노이드 제어
├─ auto_pump.py          # 공압/보조 액추에이터 관련 파일
├─ pi_move.py            # 라즈베리파이 이동 제어 관련 파일
├─ poses.json            # 저장된 포즈 데이터
├─ test.py               # 전체 시스템 메인 제어 로직
└─ alphabet_best10_512.pth  # 학습된 OCR 모델 가중치
