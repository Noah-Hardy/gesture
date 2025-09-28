import time
import cv2

# Simple FPS test with just camera capture and display
cap = cv2.VideoCapture(1)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

fps_counter = 0
fps_start_time = time.time()

print("Testing camera FPS without MediaPipe...")
print("Press 'q' to quit")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break
    
    cv2.imshow('FPS Test', frame)
    
    fps_counter += 1
    if fps_counter % 30 == 0:
        fps_end_time = time.time()
        actual_fps = 30 / (fps_end_time - fps_start_time)
        print(f"Camera-only FPS: {actual_fps:.2f}")
        fps_start_time = fps_end_time
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
