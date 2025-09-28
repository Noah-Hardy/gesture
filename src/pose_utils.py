"""
Utility functions for pose landmark processing.
"""


def get_pose_bounds_with_values(landmarks):
    """Get the bounds of pose landmarks with their values"""
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


def landmark_dict(landmarks, idx):
    """Create a dictionary from a landmark at the given index"""
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


def process_landmarks_to_dict(landmarks, landmark_type="pose"):
    """Convert landmarks to dictionary format for OSC transmission"""
    landmark_list = []
    for idx, landmark in enumerate(landmarks):
        landmark_list.append({
            "type": landmark_type,
            "id": idx,
            "x": round(landmark.x, 3),
            "y": round(landmark.y, 3),
            "z": round(landmark.z, 3),
            "visibility": round(landmark.visibility, 3) if hasattr(landmark, "visibility") else None
        })
    return landmark_list
