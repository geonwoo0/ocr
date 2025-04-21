from flask import Flask, request, render_template_string, jsonify # jsonify 추가
import json
import os
import move_module  # 실제 환경에서는 주석 해제
import kit_init_module # 실제 환경에서는 주석 해제
import time # 테스트용 임시 모듈

# --- 설정 ---
POSES_FILE = "poses.json"
MOTOR_CHANNELS = [10, 11, 13, 14, 15] # 제어할 모터 번호 (정수형)
DEFAULT_INITIAL_POSE = {10: 0, 11: 135, 13: 135, 14: 135, 15: 90} # 앱 시작 시 기본 포즈

# --- 유틸리티 함수 ---
def load_poses(filename=POSES_FILE):
    """JSON 파일에서 포즈 데이터를 로드하고 모터 키를 정수로 변환"""
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 모터 채널 키(문자열)를 정수로 변환
        processed_data = {}
        for name, pose_data in data.items():
            angles_int_keys = {}
            desc = pose_data.get("desc", "") # 설명 가져오기
            for key, value in pose_data.items():
                if key.isdigit(): # 키가 숫자로만 구성되어 있는지 확인
                    angles_int_keys[int(key)] = value
            processed_data[name] = {"angles": angles_int_keys, "desc": desc}
        print(f"{filename}에서 포즈 로드 완료.")
        return processed_data
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        print(f"Error loading poses from {filename}: {e}")
        return {}

def save_poses(data, filename=POSES_FILE):
    """포즈 데이터를 JSON 파일에 저장하고 모터 키를 문자열로 변환"""
    try:
        # 저장 전 모터 채널 키(정수)를 문자열로 변환
        processed_data = {}
        for name, pose_data in data.items():
            angles_str_keys = {str(key): value for key, value in pose_data.get("angles", {}).items()}
            angles_str_keys["desc"] = pose_data.get("desc", "") # 설명 추가
            processed_data[name] = angles_str_keys

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(processed_data, f, ensure_ascii=False, indent=4)
        print(f"포즈를 {filename}에 저장 완료.")
        return True
    except Exception as e:
        print(f"Error saving poses to {filename}: {e}")
        return False

# --- Flask 앱 설정 ---
app = Flask(__name__)

# 앱 시작 시 포즈 로드
loaded_poses = load_poses()
# 현재 UI에 표시되고 있는 각도 (앱 시작 시 기본값 또는 로드된 포즈)
# 주의: current_angles는 모터 제어에 직접 사용되기보다는 UI 상태를 반영
current_angles = DEFAULT_INITIAL_POSE.copy()
if "move_pose" in loaded_poses: # 예시로 'move_pose'가 있다면 그걸 초기값으로
    current_angles = loaded_poses["move_pose"]["angles"].copy()


# 서보 키트 초기화
print("서보 키트 초기화 중...")
tca = kit_init_module.initialize_multiplexer()
kit = kit_init_module.initialize_servo_kit(tca, 0)


# --- HTML 템플릿 (수정됨) ---
HTML_PAGE = """
<!doctype html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>서보 제어 웹 UI</title>
    <style>
      body { font-family: sans-serif; padding: 20px; display: flex; }
      .control-panel { flex: 2; padding-right: 20px; }
      .pose-panel { flex: 1; border-left: 1px solid #ccc; padding-left: 20px; }
      label { display: inline-block; width: 80px; vertical-align: middle; }
      input[type=range] { width: 250px; vertical-align: middle; }
      input[type=number] { width: 60px; vertical-align: middle; text-align: right; }
      output { display: inline-block; width: 35px; vertical-align: middle; text-align: right; margin-left: 5px;}
      .slider-block { margin-bottom: 15px; display: flex; align-items: center; }
      button { padding: 8px 15px; margin: 5px; }
      select { padding: 5px; margin-right: 10px; min-width: 150px; }
      input[type=text] { padding: 5px; margin-right: 10px; }
      .pose-actions button { display: block; margin-bottom: 10px; }
       #saveStatus { margin-top: 10px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="control-panel">
        <h2>서보 제어</h2>
        <form method="post" id="controlForm">
            {% for motor in motor_channels %}
            <div class="slider-block">
                <label for="range_{{motor}}">모터 {{motor}}</label>
                <input type="range" id="range_{{motor}}" min="0" max="270" value="{{current_angles.get(motor, 0)}}"
                       oninput="syncValues({{motor}}, this.value)">
                <input type="number" id="number_{{motor}}" min="0" max="270" value="{{current_angles.get(motor, 0)}}"
                       oninput="syncValues({{motor}}, this.value)">
                <output id="output_{{motor}}">{{current_angles.get(motor, 0)}}</output>
                <input type="hidden" name="m{{motor}}" id="value_{{motor}}" value="{{current_angles.get(motor, 0)}}">
            </div>
            {% endfor %}
            <button type="submit">모터 이동 (전송)</button>
        </form>
    </div>

    <div class="pose-panel">
        <h2>포즈 관리</h2>
        <div>
            <label for="poseList">포즈 선택:</label>
            <select id="poseList">
                <option value="">-- 포즈 선택 --</option>
                {% for name, data in poses.items() %}
                    <option value="{{ name }}" title="{{ data.desc }}">{{ name }} ({{ data.desc }})</option>
                {% endfor %}
            </select>
            <button type="button" onclick="loadSelectedPose()">선택 포즈 로드</button>
            <button type="button" onclick="moveToSelectedPose()">선택 포즈로 이동</button>
        </div>
        <hr>
        <div>
            <h4>현재 포즈 저장</h4>
            <label for="poseName">포즈 이름:</label>
            <input type="text" id="poseName" placeholder="예: pose_X"><br>
            <label for="poseDesc">설명:</label>
            <input type="text" id="poseDesc" placeholder="예: A 위치 준비 자세"><br>
            <button type="button" onclick="saveCurrentPose()">현재 포즈 저장</button>
            <div id="saveStatus"></div>
        </div>
         <hr>
         <h4>포즈 삭제 (주의!)</h4>
         <div>
            <label for="poseToDelete">삭제할 포즈:</label>
             <select id="poseToDelete">
                 <option value="">-- 삭제할 포즈 선택 --</option>
                 {% for name in poses.keys() %}
                     <option value="{{ name }}">{{ name }}</option>
                 {% endfor %}
             </select>
            <button type="button" onclick="deleteSelectedPose()">선택 포즈 삭제</button>
             <div id="deleteStatus"></div>
         </div>
    </div>

    <script>
      // 페이지 로드 시 Flask에서 전달한 전체 포즈 데이터 저장
      const allPoses = {{ all_poses_json | safe }};
      const motorChannels = {{ motor_channels | tojson }};

      function syncValues(motorId, value) {
        const slider = document.getElementById('range_' + motorId);
        const numberInput = document.getElementById('number_' + motorId);
        const output = document.getElementById('output_' + motorId);
        const hiddenInput = document.getElementById('value_' + motorId);

        let numValue = parseFloat(value);
        const min = parseFloat(slider.min);
        const max = parseFloat(slider.max);

        if (isNaN(numValue)) numValue = parseFloat(hiddenInput.value) || min;
        numValue = Math.max(min, Math.min(max, numValue));

        slider.value = numValue;
        numberInput.value = numValue;
        output.textContent = Math.round(numValue);
        hiddenInput.value = numValue;

        if (document.activeElement === numberInput && parseFloat(value) !== numValue) {
            numberInput.value = numValue;
        }
      }

      function loadPoseToUI(poseAngles) {
          // console.log("Loading angles to UI:", poseAngles);
          motorChannels.forEach(motorId => {
              // 포즈 데이터에 해당 모터 각도가 있으면 사용, 없으면 0 또는 현재 값 유지? -> 0으로 설정
              const angle = poseAngles.hasOwnProperty(String(motorId)) ? poseAngles[String(motorId)] : poseAngles.hasOwnProperty(motorId) ? poseAngles[motorId] : 0;
              syncValues(motorId, angle);
          });
      }

      function loadSelectedPose() {
          const poseName = document.getElementById('poseList').value;
          if (!poseName || !allPoses[poseName]) {
              alert('로드할 포즈를 선택하세요.');
              return;
          }
          const poseData = allPoses[poseName];
          loadPoseToUI(poseData.angles); // angles 키 안의 각도 데이터 사용
          // 선택된 포즈의 이름과 설명을 저장 필드에 채워넣기 (수정 용도)
          document.getElementById('poseName').value = poseName;
          document.getElementById('poseDesc').value = poseData.desc || '';

      }

      function moveToSelectedPose() {
          loadSelectedPose(); // 먼저 UI에 로드
          // 잠시 후 폼 제출 (UI 업데이트 시간을 주기 위해)
          setTimeout(() => {
              document.getElementById('controlForm').submit();
          }, 100); // 0.1초 지연
      }

      function saveCurrentPose() {
          const poseName = document.getElementById('poseName').value.trim();
          const poseDesc = document.getElementById('poseDesc').value.trim();
          const statusDiv = document.getElementById('saveStatus');

          if (!poseName) {
              statusDiv.textContent = '오류: 포즈 이름을 입력하세요.';
              statusDiv.style.color = 'red';
              return;
          }
          // 현재 UI의 각도 값 읽기 (숨겨진 필드에서)
          const currentPoseAngles = {};
          motorChannels.forEach(motorId => {
              currentPoseAngles[motorId] = parseFloat(document.getElementById('value_' + motorId).value);
          });

          // 서버로 전송할 데이터 준비
          const dataToSend = {
              name: poseName,
              desc: poseDesc,
              angles: currentPoseAngles // 정수 키 사용
          };

          statusDiv.textContent = '저장 중...';
          statusDiv.style.color = 'orange';

          fetch('/save_pose', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(dataToSend)
          })
          .then(response => response.json())
          .then(data => {
              if (data.success) {
                  statusDiv.textContent = `성공: 포즈 '${poseName}' 저장됨.`;
                  statusDiv.style.color = 'green';
                  // 포즈 목록 동적 업데이트 (페이지 새로고침 없이)
                  updatePoseLists(poseName, poseDesc, true); // 새 포즈 추가
                  // 전역 allPoses 데이터도 업데이트
                  allPoses[poseName] = { angles: currentPoseAngles, desc: poseDesc };
                  document.getElementById('poseName').value = ''; // 입력 필드 초기화
                  document.getElementById('poseDesc').value = '';
              } else {
                  statusDiv.textContent = '오류: ' + data.message;
                  statusDiv.style.color = 'red';
              }
          })
          .catch(error => {
              console.error('Save Error:', error);
              statusDiv.textContent = '오류: 저장 실패 (네트워크 또는 서버 오류).';
              statusDiv.style.color = 'red';
          });
      }

        function deleteSelectedPose() {
            const poseName = document.getElementById('poseToDelete').value;
            const statusDiv = document.getElementById('deleteStatus');

            if (!poseName) {
                statusDiv.textContent = '오류: 삭제할 포즈를 선택하세요.';
                statusDiv.style.color = 'red';
                return;
            }

            if (!confirm(`정말로 포즈 '${poseName}'을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) {
                return;
            }

            statusDiv.textContent = '삭제 중...';
            statusDiv.style.color = 'orange';

            fetch('/delete_pose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: poseName })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    statusDiv.textContent = `성공: 포즈 '${poseName}' 삭제됨.`;
                    statusDiv.style.color = 'green';
                    updatePoseLists(poseName, '', false); // 포즈 삭제 반영
                    delete allPoses[poseName]; // 전역 데이터에서도 삭제
                    document.getElementById('poseToDelete').value = ''; // 선택 초기화
                } else {
                    statusDiv.textContent = '오류: ' + data.message;
                    statusDiv.style.color = 'red';
                }
            })
            .catch(error => {
                console.error('Delete Error:', error);
                statusDiv.textContent = '오류: 삭제 실패 (네트워크 또는 서버 오류).';
                statusDiv.style.color = 'red';
            });
        }


      // 포즈 목록 드롭다운 업데이트 헬퍼 함수
        function updatePoseLists(poseName, poseDesc, isAdding) {
            const poseList = document.getElementById('poseList');
            const poseToDeleteList = document.getElementById('poseToDelete');

            if (isAdding) {
                // 기존에 같은 이름의 옵션이 있으면 업데이트, 없으면 추가
                let existingOption = poseList.querySelector(`option[value="${poseName}"]`);
                if (existingOption) {
                    existingOption.textContent = `${poseName} (${poseDesc})`;
                    existingOption.title = poseDesc;
                } else {
                    const newOption = document.createElement('option');
                    newOption.value = poseName;
                    newOption.textContent = `${poseName} (${poseDesc})`;
                    newOption.title = poseDesc;
                    poseList.appendChild(newOption);
                 }

                 existingOption = poseToDeleteList.querySelector(`option[value="${poseName}"]`);
                 if(existingOption) {
                    existingOption.textContent = poseName;
                 } else {
                    const newDeleteOption = document.createElement('option');
                    newDeleteOption.value = poseName;
                    newDeleteOption.textContent = poseName;
                    poseToDeleteList.appendChild(newDeleteOption);
                 }

            } else { // 삭제하는 경우
                const optionToRemove = poseList.querySelector(`option[value="${poseName}"]`);
                if (optionToRemove) poseList.removeChild(optionToRemove);

                const deleteOptionToRemove = poseToDeleteList.querySelector(`option[value="${poseName}"]`);
                if (deleteOptionToRemove) poseToDeleteList.removeChild(deleteOptionToRemove);
            }
        }

    </script>
</body>
</html>
"""

# --- Flask 라우트 ---

@app.route("/", methods=["GET", "POST"])
def control():
    global current_angles, loaded_poses # 전역 변수 사용

    if request.method == "POST":
        # '모터 이동 (전송)' 버튼 클릭 시 (폼 제출)
        pose_to_move = {}
        new_ui_angles = {} # UI 상태 업데이트용
        for motor in MOTOR_CHANNELS:
            val_str = request.form.get(f"m{motor}")
            try:
                val = float(val_str)
                val = max(0, min(270, val)) # 범위 제한
                pose_to_move[motor] = val # 이동할 포즈 (정수 키)
                new_ui_angles[motor] = val
            except (ValueError, TypeError):
                # 오류 발생 시 현재 값 유지
                current_val = current_angles.get(motor, 0)
                pose_to_move[motor] = current_val
                new_ui_angles[motor] = current_val

        if pose_to_move:
            print("[모터 이동 요청]", pose_to_move)
            # 주의: move_motors가 정수 키를 받을 것으로 예상됨
            move_module.move_motors(kit, pose_to_move, 1.0)
            # 이동 후 UI 상태 업데이트
            current_angles = new_ui_angles.copy()
        # POST 후에도 동일한 페이지를 현재 각도로 렌더링
        # all_poses_json을 JavaScript에서 사용할 수 있도록 전달
        all_poses_json = json.dumps(loaded_poses)
        return render_template_string(HTML_PAGE,
                                      motor_channels=MOTOR_CHANNELS,
                                      current_angles=current_angles,
                                      poses=loaded_poses,
                                      all_poses_json=all_poses_json)

    # GET 요청 시
    # all_poses_json을 JavaScript에서 사용할 수 있도록 전달
    all_poses_json = json.dumps(loaded_poses)
    return render_template_string(HTML_PAGE,
                                  motor_channels=MOTOR_CHANNELS,
                                  current_angles=current_angles,
                                  poses=loaded_poses,
                                  all_poses_json=all_poses_json)


@app.route("/save_pose", methods=["POST"])
def handle_save_pose():
    global loaded_poses # 전역 변수 수정
    data = request.get_json()
    pose_name = data.get('name')
    pose_desc = data.get('desc', '')
    angles = data.get('angles') # 이 각도는 정수 키를 가짐

    if not pose_name or not angles:
        return jsonify({"success": False, "message": "포즈 이름 또는 각도 데이터가 없습니다."})

    # 새 포즈/수정된 포즈를 loaded_poses에 업데이트
    loaded_poses[pose_name] = {"angles": angles, "desc": pose_desc}

    # 파일에 저장 (save_poses 함수는 내부적으로 키를 문자열로 변환)
    if save_poses(loaded_poses):
        return jsonify({"success": True})
    else:
        # 파일 저장 실패 시 loaded_poses에서 방금 추가한 것 다시 제거 (롤백 시도)
        # 주의: 완벽한 롤백은 아닐 수 있음
        if pose_name in loaded_poses: del loaded_poses[pose_name]
        return jsonify({"success": False, "message": "파일 저장 중 오류 발생"})

@app.route("/delete_pose", methods=["POST"])
def handle_delete_pose():
    global loaded_poses # 전역 변수 수정
    data = request.get_json()
    pose_name = data.get('name')

    if not pose_name:
        return jsonify({"success": False, "message": "삭제할 포즈 이름이 없습니다."})

    if pose_name not in loaded_poses:
        return jsonify({"success": False, "message": f"'{pose_name}' 포즈를 찾을 수 없습니다."})

    # 메모리에서 삭제
    del loaded_poses[pose_name]

    # 파일에 변경 사항 저장
    if save_poses(loaded_poses):
        return jsonify({"success": True})
    else:
        # 파일 저장 실패 시 복구 시도 (어려움, 일단 실패 메시지만 전달)
        # 간단하게 다시 로드?
        # loaded_poses = load_poses() # 이 방법은 동시성 문제 가능성 있음
        return jsonify({"success": False, "message": "파일 저장 중 오류 발생 (삭제 반영 실패)"})


# --- 앱 실행 ---
if __name__ == "__main__":
    # 초기 각도로 모터 설정 (선택 사항)
    print("초기 각도로 모터 설정...")
    # current_angles가 정수 키를 가지고 있으므로 그대로 사용 가능
    move_module.move_motors(kit, current_angles, 1.0)
    print("초기화 완료. 서버 시작 중...")
    # Flask 앱 실행
    app.run(host="0.0.0.0", port=8080, debug=False) # debug=True로 하면 코드 변경 시 자동 재시작