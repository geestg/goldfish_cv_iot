import os
import uuid
import math
from datetime import datetime

import cv2
import numpy as np
import pandas as pd
from flask import Flask, render_template, request, jsonify, Response, send_file
from ultralytics import YOLO

# ================= MQTT =================
import json
import paho.mqtt.publish as publish

# -------------------------------------------------
# (Opsional) Tracking ID dengan Norfair
# -------------------------------------------------
try:
    from norfair import Detection, Tracker
    USE_TRACKING = True
    print("[INFO] Norfair terdeteksi, tracking ID diaktifkan.")
except ImportError:
    USE_TRACKING = False
    print("[WARN] Norfair tidak ditemukan. Jalankan: pip install norfair")
    print("[WARN] Video akan dianalisis tanpa ID tracking.")


# ============================================================
# KONFIGURASI DASAR
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
WEB_OUTPUT_IMAGE = os.path.join(BASE_DIR, "analisa_gambar")
WEB_OUTPUT_VIDEO = os.path.join(BASE_DIR, "analisa_video")
MODEL_PATH = os.path.join(BASE_DIR, "models", "best.pt")

STREAM_SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshot")
STREAM_VIDEO_DIR = os.path.join(BASE_DIR, "video_stream")

RTSP_URL = "http://172.27.70.16:4747/video"  # sesuaikan

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(WEB_OUTPUT_IMAGE, exist_ok=True)
os.makedirs(WEB_OUTPUT_VIDEO, exist_ok=True)
os.makedirs(STREAM_SNAPSHOT_DIR, exist_ok=True)
os.makedirs(STREAM_VIDEO_DIR, exist_ok=True)

# ================== PARAMETER KALIBRASI =====================
PX_PER_CM = 12.7353  # ganti sesuai kalibrasi Anda

# ================== PARAMETER FILTER DETEKSI =================
CONF_THRESHOLD = 0.60
MIN_LENGTH_PX = 40.0
BORDER_MARGIN = 0.08

# ================= MQTT CONFIG =================
MQTT_BROKER = "172.27.27.133"
MQTT_PORT = 1883
MQTT_TOPIC_FEED = "goldfish/feeder/cmd"
MQTT_TOPIC_STATUS = "goldfish/feeder/status"

# ================= LOGIKA SERVO MULTI-PUTARAN =================
# durasi buka servo tiap putaran (ms) -> kalibrasikan sesuai jumlah pakan keluar
BASE_MS_PER_TURN = 700
# jeda antar putaran (ms) -> agar pakan turun dan servo tidak “ngegas”
GAP_MS_BETWEEN_TURNS = 600
# safety: batasi putaran maksimum supaya tidak overdosing
MAX_TURNS = 12


# ============================================================
# INISIALISASI FLASK + MODEL
# ============================================================

app = Flask(__name__, static_folder="static", template_folder="templates")

print(f"[INFO] Model Loaded: {MODEL_PATH}")
model = YOLO(MODEL_PATH)

LAST_SUMMARY = None  # simpan analisis terakhir untuk tombol feed manual


def run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:5]


# ============================================================
# LOGIKA MULTI-PUTARAN + ESTIMASI PANEN
# ============================================================

def fish_to_turns(n: int) -> int:
    """
    Pemetaan final jumlah ikan → putaran servo
    1–2  ikan → 1x
    3–4  ikan → 2x
    5–6  ikan → 3x
    7–8  ikan → 4x
    >8   ikan → 4x (safety cap)
    """
    if n <= 0:
        return 0
    elif n <= 2:
        return 1
    elif n <= 4:
        return 2
    elif n <= 6:
        return 3
    else:
        return 4


def estimate_harvest(avg_length_cm: float) -> str:
    if avg_length_cm >= 25.0:
        return "Siap Panen"
    if avg_length_cm >= 20.0:
        return "Mendekati Panen"
    return "Belum Panen"


def publish_feeding_command(summary: dict, source: str = "manual"):
    """Kirim perintah feed dengan pola multi-putaran."""
    try:
        num_fish = int(summary.get("num_fish", 0))
        if num_fish <= 0:
            return

        turns = int(summary.get("feeding_turns", 0))
        duration = int(summary.get("feeding_duration_ms", BASE_MS_PER_TURN))
        gap = int(summary.get("feeding_gap_ms", GAP_MS_BETWEEN_TURNS))

        payload = {
            "action": "feed",
            "turns": turns,
            "duration": duration,
            "gap": gap,
            "num_fish": num_fish,
            "avg_length_cm": float(summary.get("avg_length_cm", 0.0)),
            "harvest_status": summary.get("harvest_status", "Belum Panen"),
            "source": source,
            "run_id": summary.get("run_id", ""),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        publish.single(
            MQTT_TOPIC_FEED,
            json.dumps(payload),
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
        )

        publish.single(
            MQTT_TOPIC_STATUS,
            f"CMD sent: feed num_fish={num_fish}, turns={turns}, duration={duration}ms",
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
        )

        print(f"[MQTT] Published to {MQTT_TOPIC_FEED}: {payload}")

    except Exception as e:
        print(f"[MQTT] ERROR publish: {e}")


# ============================================================
# FUNGSI BANTU (FILTER + ANOTASI)
# ============================================================

def inside_valid_roi(box, img_shape):
    h, w = img_shape[:2]
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0

    left = w * BORDER_MARGIN
    right = w * (1.0 - BORDER_MARGIN)
    top = h * BORDER_MARGIN
    bottom = h * (1.0 - BORDER_MARGIN)

    return (left <= cx <= right) and (top <= cy <= bottom)


def draw_annotations(img, box, head, tail, length_cm: float, fish_id=None):
    x1, y1, x2, y2 = map(int, box)
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 2)
    cv2.circle(img, (int(head[0]), int(head[1])), 6, (0, 0, 255), -1)
    cv2.circle(img, (int(tail[0]), int(tail[1])), 6, (0, 255, 0), -1)
    cv2.line(img, (int(head[0]), int(head[1])), (int(tail[0]), int(tail[1])), (0, 255, 0), 3)

    text = f"ID {fish_id} | {length_cm:.2f} cm" if fish_id is not None else f"{length_cm:.2f} cm"
    cv2.putText(img, text, (x1, max(0, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)


# ============================================================
# ANALISIS GAMBAR
# ============================================================

def analyze_image(img_path):
    global LAST_SUMMARY

    rid = run_id()
    img = cv2.imread(img_path)
    if img is None:
        raise RuntimeError(f"Gagal membaca gambar: {img_path}")

    res = model(img)[0]

    annotated = img.copy()
    records = []

    if res.keypoints is not None and len(res.keypoints) > 0:
        kpts = res.keypoints.xy.cpu().numpy()
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()

        fish_index = 1

        for i in range(len(kpts)):
            conf = float(confs[i])
            if conf < CONF_THRESHOLD:
                continue

            head = kpts[i, 0]
            tail = kpts[i, 1]

            length_px = float(np.linalg.norm(head - tail))
            if length_px < MIN_LENGTH_PX:
                continue

            box = boxes[i]
            if not inside_valid_roi(box, img.shape):
                continue

            length_cm = length_px / PX_PER_CM

            draw_annotations(annotated, box, head, tail, length_cm, fish_id=fish_index)

            records.append({
                "run_id": rid,
                "fish_id": fish_index,
                "confidence": conf,
                "length_px": length_px,
                "length_cm": length_cm,
            })

            fish_index += 1

    idx = len(os.listdir(WEB_OUTPUT_IMAGE)) + 1
    img_name = f"IMG_ANALYSIS_{idx:04d}.png"
    csv_name = f"IMG_ANALYSIS_{idx:04d}.csv"

    cv2.imwrite(os.path.join(WEB_OUTPUT_IMAGE, img_name), annotated)
    pd.DataFrame(records).to_csv(os.path.join(WEB_OUTPUT_IMAGE, csv_name), index=False)

    max_len = max([r["length_cm"] for r in records], default=0.0)
    min_len = min([r["length_cm"] for r in records], default=0.0)
    avg_len = float(np.mean([r["length_cm"] for r in records])) if records else 0.0

    num_fish = len(records)
    turns = fish_to_turns(num_fish)


    summary = {
        "run_id": rid,
        "num_fish": num_fish,
        "max_length_cm": max_len,
        "min_length_cm": min_len,
        "avg_length_cm": avg_len,
        "harvest_status": estimate_harvest(avg_len),
        "feeding_turns": turns,
        "feeding_duration_ms": BASE_MS_PER_TURN,
        "feeding_gap_ms": GAP_MS_BETWEEN_TURNS,
    }

    LAST_SUMMARY = summary
    return img_name, csv_name, summary, records


# ============================================================
# ANALISIS VIDEO (TRACKING NORFAIR OPSIONAL)
# ============================================================

def distance_fn(detection, tracked_object):
    return np.linalg.norm(detection.points - tracked_object.estimate, axis=1).mean()


if USE_TRACKING:
    tracker = Tracker(distance_function=distance_fn, distance_threshold=30)
else:
    tracker = None


def yolo_to_detections(res):
    detections = []
    if res.keypoints is None or len(res.keypoints) == 0:
        return detections

    kpts = res.keypoints.xy.cpu().numpy()
    boxes = res.boxes.xyxy.cpu().numpy()
    confs = res.boxes.conf.cpu().numpy()

    for i in range(len(kpts)):
        conf = float(confs[i])
        if conf < CONF_THRESHOLD:
            continue

        head = kpts[i, 0]
        tail = kpts[i, 1]

        length_px = float(np.linalg.norm(head - tail))
        if length_px < MIN_LENGTH_PX:
            continue

        box = boxes[i]
        detections.append(
            Detection(
                points=np.array([head, tail]),
                scores=np.array([conf, conf]),
                data={"box": box},
            )
        )
    return detections


def analyze_video(video_path):
    global LAST_SUMMARY

    rid = run_id()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Gagal membuka video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    idx = len(os.listdir(WEB_OUTPUT_VIDEO)) + 1
    out_video = f"VID_ANALYSIS_{idx:04d}.mp4"
    out_csv = f"VID_ANALYSIS_{idx:04d}.csv"

    out_vpath = os.path.join(WEB_OUTPUT_VIDEO, out_video)
    csv_path = os.path.join(WEB_OUTPUT_VIDEO, out_csv)

    fourcc = cv2.VideoWriter_fourcc(*"avc1")
    writer = cv2.VideoWriter(out_vpath, fourcc, fps, (w, h))

    logs = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        res = model(frame)[0]
        annotated = frame.copy()

        if USE_TRACKING:
            detections = yolo_to_detections(res)
            tracks = tracker.update(detections)

            for track_obj in tracks:
                if track_obj.estimate is None or len(track_obj.estimate) < 2:
                    continue

                head, tail = track_obj.estimate[0], track_obj.estimate[1]
                box = track_obj.last_detection.data.get("box")

                if box is None or not inside_valid_roi(box, frame.shape):
                    continue

                length_px = float(np.linalg.norm(head - tail))
                length_cm = length_px / PX_PER_CM
                fish_id = int(track_obj.id)

                draw_annotations(annotated, box, head, tail, length_cm, fish_id=fish_id)

                logs.append({
                    "run_id": rid,
                    "frame": frame_idx,
                    "track_id": fish_id,
                    "length_px": length_px,
                    "length_cm": length_cm,
                })
        else:
            if res.keypoints is not None and len(res.keypoints) > 0:
                kpts = res.keypoints.xy.cpu().numpy()
                boxes = res.boxes.xyxy.cpu().numpy()
                confs = res.boxes.conf.cpu().numpy()

                for i in range(len(kpts)):
                    conf = float(confs[i])
                    if conf < CONF_THRESHOLD:
                        continue

                    head = kpts[i, 0]
                    tail = kpts[i, 1]

                    length_px = float(np.linalg.norm(head - tail))
                    if length_px < MIN_LENGTH_PX:
                        continue

                    box = boxes[i]
                    if not inside_valid_roi(box, frame.shape):
                        continue

                    length_cm = length_px / PX_PER_CM
                    fish_id = i + 1

                    draw_annotations(annotated, box, head, tail, length_cm, fish_id=fish_id)

                    logs.append({
                        "run_id": rid,
                        "frame": frame_idx,
                        "track_id": fish_id,
                        "length_px": length_px,
                        "length_cm": length_cm,
                    })

        writer.write(annotated)
        frame_idx += 1

    cap.release()
    writer.release()

    pd.DataFrame(logs).to_csv(csv_path, index=False)

    if logs:
        df = pd.DataFrame(logs)
        unique_ids = df["track_id"].nunique() if "track_id" in df.columns else 0
        avg_len = float(df["length_cm"].mean()) if "length_cm" in df.columns else 0.0
        max_len = float(df["length_cm"].max()) if "length_cm" in df.columns else 0.0
        min_len = float(df["length_cm"].min()) if "length_cm" in df.columns else 0.0
    else:
        unique_ids, avg_len, max_len, min_len = 0, 0.0, 0.0, 0.0

    turns = fish_to_turns(int(unique_ids))


    video_summary = {
        "run_id": rid,
        "num_fish": int(unique_ids),
        "max_length_cm": max_len,
        "min_length_cm": min_len,
        "avg_length_cm": avg_len,
        "harvest_status": estimate_harvest(avg_len),
        "feeding_turns": turns,
        "feeding_duration_ms": BASE_MS_PER_TURN,
        "feeding_gap_ms": GAP_MS_BETWEEN_TURNS,
    }

    LAST_SUMMARY = video_summary
    return out_video, out_csv, rid, len(logs), logs, video_summary


# ============================================================
# STREAMING (RAW)
# ============================================================

recording = False
stream_writer = None
last_frame = None


def yolo_stream_generator():
    global recording, stream_writer, last_frame
    cap = cv2.VideoCapture(RTSP_URL)

    if not cap.isOpened():
        print("[WARN] Tidak dapat membuka stream.")
        while True:
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            _, buffer = cv2.imencode(".jpg", blank)
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        last_frame = frame.copy()

        if recording and stream_writer is not None:
            stream_writer.write(frame)

        _, buffer = cv2.imencode(".jpg", frame)
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")


@app.route("/stream/live")
def stream_live():
    return Response(yolo_stream_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/stream/capture", methods=["POST"])
def stream_capture():
    global last_frame
    if last_frame is None:
        return jsonify({"status": "error", "message": "Belum ada frame stream."}), 400

    filename = datetime.now().strftime("%Y%m%d-%H%M%S") + "_snapshot.jpg"
    save_path = os.path.join(STREAM_SNAPSHOT_DIR, filename)
    cv2.imwrite(save_path, last_frame)
    return jsonify({"status": "ok", "file": filename, "path": save_path})


@app.route("/stream/record-start", methods=["POST"])
def stream_record_start():
    global recording, stream_writer, last_frame
    if recording:
        return jsonify({"status": "already_recording"})

    if last_frame is None:
        return jsonify({"status": "error", "message": "Belum ada frame stream. Buka halaman streaming dulu."}), 400

    h, w = last_frame.shape[:2]
    filename = datetime.now().strftime("%Y%m%d-%H%M%S") + "_stream.mp4"
    save_path = os.path.join(STREAM_VIDEO_DIR, filename)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    stream_writer = cv2.VideoWriter(save_path, fourcc, 20.0, (w, h))
    recording = True
    return jsonify({"status": "ok", "file": filename, "path": save_path})


@app.route("/stream/record-stop", methods=["POST"])
def stream_record_stop():
    global recording, stream_writer
    if not recording:
        return jsonify({"status": "not_recording"})

    recording = False
    if stream_writer is not None:
        stream_writer.release()
        stream_writer = None
    return jsonify({"status": "ok"})


# ============================================================
# ROUTE FILE OUTPUT
# ============================================================

@app.route("/analisa_gambar/<path:filename>")
def serve_analysis_image(filename):
    return send_file(os.path.join(WEB_OUTPUT_IMAGE, filename))


@app.route("/analisa_video/<path:filename>")
def serve_analysis_video(filename):
    return send_file(os.path.join(WEB_OUTPUT_VIDEO, filename))


# ============================================================
# ROUTE HALAMAN WEB
# ============================================================

@app.route("/")
def index():
    return render_template("index.html", active="home")


@app.route("/image")
def page_image():
    return render_template("image.html", active="image")


@app.route("/video")
def page_video():
    return render_template("video.html", active="video")


@app.route("/streaming")
def page_streaming():
    return render_template("stream.html", active="stream")


# ============================================================
# API ANALISIS
# ============================================================

@app.route("/api/analyze-image", methods=["POST"])
def api_image():
    f = request.files["image"]
    saved = os.path.join(UPLOAD_DIR, f.filename)
    f.save(saved)

    img_name, csv_name, summary, records = analyze_image(saved)

    return jsonify({
        "status": "ok",
        "summary": summary,
        "records": records,
        "image_url": f"/analisa_gambar/{img_name}",
        "csv_url": f"/analisa_gambar/{csv_name}",
    })


@app.route("/api/analyze-video", methods=["POST"])
def api_video():
    f = request.files["video"]
    saved = os.path.join(UPLOAD_DIR, f.filename)
    f.save(saved)

    video_name, csv_name, rid, total_logs, logs, video_summary = analyze_video(saved)

    return jsonify({
        "status": "ok",
        "run_id": rid,
        "summary": video_summary,
        "video_url": f"/analisa_video/{video_name}",
        "csv_url": f"/analisa_video/{csv_name}",
        "total_logs": total_logs,
        "records": logs,
    })


# ============================================================
# API FEED MANUAL (TOMBOL BERIKAN PAKAN)
# ============================================================

@app.route("/api/feed-now", methods=["POST"])
def api_feed_now():
    global LAST_SUMMARY

    if LAST_SUMMARY is None:
        return jsonify({"status": "error", "message": "Belum ada hasil analisis."}), 400

    publish_feeding_command(LAST_SUMMARY, source="manual")

    return jsonify({
        "status": "ok",
        "message": "Perintah pakan dikirim.",
        "turns": LAST_SUMMARY.get("feeding_turns", 0),
        "duration_ms": LAST_SUMMARY.get("feeding_duration_ms", BASE_MS_PER_TURN),
        "gap_ms": LAST_SUMMARY.get("feeding_gap_ms", GAP_MS_BETWEEN_TURNS)
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
