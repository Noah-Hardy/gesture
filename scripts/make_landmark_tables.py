#!/usr/bin/env python3
"""
Landmark Table Generator

Prints Markdown tables of MediaPipe's 33 pose and 21 hand landmark indices and
names, read straight from mediapipe.solutions rather than hand-typed, so the
docs can never drift from the library's actual landmark ordering.

Usage:
    uv run python scripts/make_landmark_tables.py
"""

import mediapipe as mp


def pose_table():
    rows = ['| Index | Name |', '|---|---|']
    for lm in mp.solutions.pose.PoseLandmark:
        rows.append(f'| {lm.value} | `{lm.name}` |')
    return '\n'.join(rows)


def hand_table():
    rows = ['| Index | Name |', '|---|---|']
    for lm in mp.solutions.hands.HandLandmark:
        rows.append(f'| {lm.value} | `{lm.name}` |')
    return '\n'.join(rows)


if __name__ == '__main__':
    print("### Pose landmark indices (33)\n")
    print(pose_table())
    print("\n### Hand landmark indices (21, per hand)\n")
    print(hand_table())
