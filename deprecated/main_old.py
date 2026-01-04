import time
import json
import cv2
import mediapipe as mp
from pythonosc import udp_client


# Function to get the bounds of pose landmarks with their values
# This function calculates the maximum and minimum x, y, z coordinates of the pose landmarks
# and returns a dictionary with the details of the landmarks at those bounds.
def get_pose_bounds_with_values(landmarks):
    max_x = max_y = max_z = float('-inf')
    min_x = min_y = min_z = float('inf')
    max_x_idx = max_y_idx = min_x_idx = min_y_idx = max_z_idx = min_z_idx = -1

    for idx, landmark in enumerate(landmarks):
        if landmark.x > max_x:
            max_x = landmark.x
            max_x_idx = idx
        if landmark.x < min_x:
            min_x = landmark.x
            min_x_idx = idx
        if landmark.y > max_y:
            max_y = landmark.y
            max_y_idx = idx
        if landmark.y < min_y:
            min_y = landmark.y
            min_y_idx = idx
        if landmark.z > max_z:
            max_z = landmark.z
            max_z_idx = idx
        if landmark.z < min_z:
            min_z = landmark.z
            min_z_idx = idx

    return {
        "max_x": landmark_dict(landmarks, max_x_idx),
        "min_x": landmark_dict(landmarks, min_x_idx),
        "max_y": landmark_dict(landmarks, max_y_idx),
        "min_y": landmark_dict(landmarks, min_y_idx),
        "max_z": landmark_dict(landmarks, max_z_idx),
        "min_z": landmark_dict(landmarks, min_z_idx)
    }

# Function to convert a landmark to a dictionary with its details
# This function extracts the x, y, z coordinates and visibility of a landmark
# and returns a dictionary with its id and values.
def landmark_dict(landmarks, idx):
    lm = landmarks[idx]
    d = {
        "id": idx,
        "x": round(lm.x, 3),
        "y": round(lm.y, 3),
        "z": round(lm.z, 3)
    }
    if hasattr(lm, "visibility"):
        d["visibility"] = round(lm.visibility, 3)
    return d

# Create an OSC client to send messages to a specified IP and port
# Change the IP and port as needed for your OSC server
osc_client = udp_client.SimpleUDPClient("192.168.1.28", 1234)
# osc_client = udp_client.SimpleUDPClient("127.0.0.1", 1234)

# Initialize Mediapipe drawing utilities and holistic model
mp_drawing = mp.solutions.drawing_utils
mp_holistic = mp.solutions.holistic

# Open a video capture stream
# Change the camera index if needed (0 is usually the default camera)
cap = cv2.VideoCapture(0)

# Set camera properties for better performance
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce buffer to avoid lag

# FPS tracking variables
fps_counter = 0
fps_start_time = time.time()

# Start the Mediapipe holistic model for pose and hand detection
# Adjust the detection and tracking confidence as needed
with mp_holistic.Holistic(min_detection_confidence=0.5, 
                          min_tracking_confidence=0.5) as holistic:
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # Convert the frame to RGB for Mediapipe processing
        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Process the image using Mediapipe
        results = holistic.process(image)

        # Convert the image back to BGR for regular projection
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # Gather all landmarks
        pose_landmarks = []
        pose_world_landmarks = []
        right_hand_landmarks = []
        left_hand_landmarks = []
        
        timestamp = time.time()

        # Pose landmarks
        if results.pose_landmarks:
            for idx, landmark in enumerate(results.pose_landmarks.landmark):
                pose_landmarks.append({
                    "type": "pose",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3),
                    "visibility": round(landmark.visibility, 3) if hasattr(landmark, "visibility") else None
                })
                # Draw the landmark ID on the image (commented out for better performance)
                # h, w, _ = frame.shape
                # cx, cy = int(landmark.x * w), int(landmark.y * h)
                # cv2.putText(frame, str(idx), (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 1)
        # Pose world landmarks
        
        if results.pose_world_landmarks:
            for idx, landmark in enumerate(results.pose_world_landmarks.landmark):
                pose_world_landmarks.append({
                    "type": "pose_world",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3),
                    "visibility": round(landmark.visibility, 3) if hasattr(landmark, "visibility") else None
                })
            

        # Right hand landmarks
        if results.right_hand_landmarks:
            for idx, landmark in enumerate(results.right_hand_landmarks.landmark):
                right_hand_landmarks.append({
                    "type": "right_hand",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3)
                })
                h, w, _ = frame.shape
                cx, cy = int(landmark.x * w), int(landmark.y * h)
                # cv2.putText(frame, f"RH-{idx}", (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 1)

        # Left hand landmarks
        if results.left_hand_landmarks:
            for idx, landmark in enumerate(results.left_hand_landmarks.landmark):
                left_hand_landmarks.append({
                    "type": "left_hand",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3)
                })
                h, w, _ = frame.shape
                cx, cy = int(landmark.x * w), int(landmark.y * h)
                # cv2.putText(frame, f"LH-{idx}", (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 1)


        # Send all 8 channels
        # /pose/raw
        if pose_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": pose_landmarks
            }
            osc_client.send_message("/pose/raw", json.dumps(osc_payload))
            bounds = get_pose_bounds_with_values(results.pose_landmarks.landmark)
            osc_client.send_message("/pose/raw_bounds", json.dumps(bounds))
            osc_client.send_message("/mp/status", json.dumps({"status": 1}))
        else:
            osc_client.send_message("/mp/status", json.dumps({"status": 0}))

        # /pose/world
        if pose_world_landmarks:
            world_payload = {
                "timestamp": timestamp,
                "landmarks": pose_world_landmarks
            }
            osc_client.send_message("/pose/world", json.dumps(world_payload))
            world_bounds = get_pose_bounds_with_values(results.pose_world_landmarks.landmark)
            osc_client.send_message("/pose/world_bounds", json.dumps(world_bounds))

        # /right_hand/raw
        if right_hand_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": right_hand_landmarks
            }
            osc_client.send_message("/right_hand/raw", json.dumps(osc_payload))

        # /left_hand/raw
        if left_hand_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": left_hand_landmarks
            }
            osc_client.send_message("/left_hand/raw", json.dumps(osc_payload))

        # /right_hand/world and /right_hand/world_bounds
        if hasattr(results, 'right_hand_world_landmarks') and results.right_hand_world_landmarks:
            right_hand_world_landmarks = []
            for idx, landmark in enumerate(results.right_hand_world_landmarks.landmark):
                right_hand_world_landmarks.append({
                    "type": "right_hand_world",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3)
                })
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": right_hand_world_landmarks
            }
            osc_client.send_message("/right_hand/world", json.dumps(osc_payload))

        # /left_hand/world and /left_hand/world_bounds
        if hasattr(results, 'left_hand_world_landmarks') and results.left_hand_world_landmarks:
            left_hand_world_landmarks = []
            for idx, landmark in enumerate(results.left_hand_world_landmarks.landmark):
                left_hand_world_landmarks.append({
                    "type": "left_hand_world",
                    "id": idx,
                    "x": round(landmark.x, 3),
                    "y": round(landmark.y, 3),
                    "z": round(landmark.z, 3)
                })
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": left_hand_world_landmarks
            }
            osc_client.send_message("/left_hand/world", json.dumps(osc_payload))

        # Display the image with pose and hand landmarks drawn
        mp.solutions.drawing_utils.draw_landmarks(
            frame, 
            results.pose_landmarks, 
            mp_holistic.POSE_CONNECTIONS,
            mp.solutions.drawing_utils.DrawingSpec(color=(245, 117, 66), thickness=2, circle_radius=4),
            mp.solutions.drawing_utils.DrawingSpec(color=(245, 66, 230), thickness=2, circle_radius=2)
        )

        # mp.solutions.drawing_utils.draw_landmarks(
        #     frame, 
        #     results.right_hand_landmarks, 
        #     mp_holistic.HAND_CONNECTIONS,
        #     mp.solutions.drawing_utils.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=4),
        #     mp.solutions.drawing_utils.DrawingSpec(color=(0, 0, 255), thickness=2, circle_radius=2)
        # )

        # mp.solutions.drawing_utils.draw_landmarks(
        #     frame, 
        #     results.left_hand_landmarks, 
        #     mp_holistic.HAND_CONNECTIONS,
        #     mp.solutions.drawing_utils.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=4),
        #     mp.solutions.drawing_utils.DrawingSpec(color=(255, 0, 0), thickness=2, circle_radius=2)
        # )
        try:
            cv2.imshow('Pose and Hand Detection', frame)
        except Exception as e:
            print(f"Error displaying frame: {e}")
            continue

        # FPS monitoring
        fps_counter += 1
        if fps_counter % 30 == 0:  # Print FPS every 30 frames
            fps_end_time = time.time()
            actual_fps = 30 / (fps_end_time - fps_start_time)
            print(f"Actual FPS: {actual_fps:.2f}")
            fps_start_time = fps_end_time

        if cv2.waitKey(1) & 0xFF == ord('q'):  # Reduced from 10ms to 1ms for better responsiveness
            break

cap.release()
cv2.destroyAllWindows()