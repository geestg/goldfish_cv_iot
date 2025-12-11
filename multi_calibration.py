import os
import cv2
import numpy as np
from ultralytics import YOLO

# ===============================
# KONFIGURASI
# ===============================
MODEL_PATH = "models/best.pt"
DATASET_DIR = "kalibrasi_images"   # folder berisi beberapa foto kalibrasi
FISH_REAL_LENGTH_CM = 8.0          # panjang ikan asli dalam cm
CONF_THRESHOLD = 0.70              # confidence minimal agar ikan dianggap valid

# ===============================
# LOAD MODEL
# ===============================
model = YOLO(MODEL_PATH)

# ===============================
# FUNGSI HITUNG PANJANG IKAN
# ===============================
def measure_length_px(keypoints):
    """
    keypoints: array 2x2 (head, tail)
    """
    head = keypoints[0]
    tail = keypoints[1]
    return float(np.linalg.norm(head - tail))

# ===============================
# PROSES KALIBRASI
# ===============================
all_lengths_px = []

print("\n======================================")
print("   MULTI-FISH CALIBRATION START")
print("======================================\n")

# List semua gambar kalibrasi
files = [f for f in os.listdir(DATASET_DIR) if f.lower().endswith((".jpg", ".png", ".jpeg"))]

if len(files) == 0:
    print("Tidak ada gambar di folder kalibrasi_images!")
    exit()

for fname in files:
    path = os.path.join(DATASET_DIR, fname)
    img = cv2.imread(path)

    print(f"[INFO] Memproses: {fname}")

    results = model(img)[0]

    if results.keypoints is None:
        print("  → Tidak ada ikan terdeteksi. Skip.\n")
        continue

    kpts = results.keypoints.xy.cpu().numpy()
    confs = results.boxes.conf.cpu().numpy()

    for i in range(len(kpts)):
        if confs[i] < CONF_THRESHOLD:
            continue  # skip ikan yang tidak jelas

        length_px = measure_length_px(kpts[i])
        all_lengths_px.append(length_px)

        print(f"  Ikan {i+1}: {length_px:.2f} px (conf={confs[i]:.2f})")

print("\n======================================")
print("           HASIL KALIBRASI")
print("======================================\n")

if len(all_lengths_px) == 0:
    print("Tidak cukup ikan yang valid untuk kalibrasi.")
    exit()

mean_px = np.mean(all_lengths_px)
px_per_cm = mean_px / FISH_REAL_LENGTH_CM

print(f"Total ikan valid: {len(all_lengths_px)}")
print(f"Rata-rata panjang (px): {mean_px:.2f} px")
print(f"Panjang ikan asli: {FISH_REAL_LENGTH_CM:.2f} cm")
print(f"\n>>> PX_PER_CM BARU = {px_per_cm:.4f} px/cm\n")

print("Gunakan nilai ini di app.py:")
print(f"PX_PER_CM = {px_per_cm:.4f}\n")
