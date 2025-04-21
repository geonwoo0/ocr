import time

#서보 오프셋
SERVO_OFFSETS = { 11: 0, 13: 0, 14: 0, 15: 0 }
# 반전 적용할 채널
INVERTED_CHANNELS = [13]
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

def clamp(value, min_value, max_value):
    """모터 각도 제한 내로 설정"""
    return max(min(value, max_value), min_value)

def s_curve_profile(t, T, start_angle, target_angle):
    """s커브"""
    if t > T:
        t = T
    ratio = t / T
    s_val = 10 * ratio**3 - 15 * ratio**4 + 6 * ratio**5
    return start_angle + (target_angle - start_angle) * s_val

def invert_angle(angle, min_angle=0, max_angle=270):
    """뒤집어져있는 모터 각도 반전"""
    return max_angle - angle + min_angle

def move_motors(kit, target_angles,TOTAL_TIME):
    """
    모터 제어 대상: MOTOR_CHANNELS에 지정된 채널들.
    target_angles: 각 채널별 목표 각도를 딕셔너리로 전달 (예: {11: 120, 13: 200, ...})
    S-커브 보간을 적용하여 부드럽게 목표 각도에 도달합니다.
    """
    start_angles = {ch: kit.servo[ch].angle for ch in MOTOR_CHANNELS}
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > TOTAL_TIME:
            elapsed = TOTAL_TIME
        for ch in MOTOR_CHANNELS:
            ch_target = target_angles.get(ch, 135)
            if ch in INVERTED_CHANNELS:
                target_angle_inv = invert_angle(ch_target, *CHANNEL_ANGLES.get(ch, (0, 270))) + SERVO_OFFSETS.get(ch, 0)
                new_angle = s_curve_profile(elapsed, TOTAL_TIME, start_angles[ch], target_angle_inv)
            else:
                new_angle = s_curve_profile(elapsed, TOTAL_TIME, start_angles[ch], ch_target + SERVO_OFFSETS.get(ch, 0))
            min_angle, max_angle = CHANNEL_ANGLES.get(ch, (0, 270))
            new_angle = clamp(new_angle, min_angle, max_angle)
            kit.servo[ch].angle = new_angle
        if elapsed >= TOTAL_TIME:
            break
        time.sleep(0.04)