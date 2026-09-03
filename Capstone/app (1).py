
from flask import Flask, render_template, jsonify, request
import serial
import threading
import time
import math
import re

app = Flask(__name__)

SERIAL_PORT = "COM17"
BAUD_RATE = 115200
SAFE_RADIUS_KM = 20.0

data = {
    "connected": False,
    "gps_fix": False,
    "latitude": None,
    "longitude": None,
    "satellites": 0,
    "hdop": None,
    "gps_chars": 0,
    "lora": "Waiting",
    "distance_km": None,
    "safety": "WAITING",
    "raw": [],
    "error": ""
}

center = {"lat": None, "lon": None}
lock = threading.Lock()

def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1)
    dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def update_safety():
    if data["latitude"] is None or data["longitude"] is None:
        data["distance_km"] = None
        data["safety"] = "WAITING"
        return
    if center["lat"] is None or center["lon"] is None:
        data["distance_km"] = 0.0
        data["safety"] = "SET CENTER"
        return
    d = distance_km(data["latitude"], data["longitude"], center["lat"], center["lon"])
    data["distance_km"] = round(d, 3)
    data["safety"] = "NORMAL" if d <= SAFE_RADIUS_KM else "ALERT"

def parse_line(line):
    line = line.strip()
    if not line:
        return

    with lock:
        data["raw"].append(line)
        data["raw"] = data["raw"][-40:]

        m = re.search(r"Latitude\s*:\s*(-?\d+(?:\.\d+)?)", line, re.I)
        if m:
            data["latitude"] = float(m.group(1))
        m = re.search(r"Longitude\s*:\s*(-?\d+(?:\.\d+)?)", line, re.I)
        if m:
            data["longitude"] = float(m.group(1))
        m = re.search(r"Satellites\s*:\s*(\d+)", line, re.I)
        if m:
            data["satellites"] = int(m.group(1))
        m = re.search(r"HDOP\s*:\s*([0-9.]+)", line, re.I)
        if m:
            data["hdop"] = float(m.group(1))
        m = re.search(r"GPS characters\s*:\s*(\d+)", line, re.I)
        if m:
            data["gps_chars"] = int(m.group(1))

        if "GPS FIX: YES" in line.upper() or "GPS FIX FOUND" in line.upper():
            data["gps_fix"] = True
        elif "GPS FIX: NO" in line.upper():
            data["gps_fix"] = False

        if "LORA: SENT" in line.upper():
            data["lora"] = "SENT"
        elif "LORA FAILED" in line.upper():
            data["lora"] = "FAILED"
        elif "GPS NOT LOCKED" in line.upper():
            data["lora"] = "Skipped - GPS no fix"
        elif "LORA READY" in line.upper():
            data["lora"] = "READY"

        update_safety()

def serial_worker():
    while True:
        try:
            with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
                with lock:
                    data["connected"] = True
                    data["error"] = ""
                while True:
                    raw = ser.readline()
                    if raw:
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            line = str(raw)
                        parse_line(line)
                    else:
                        time.sleep(0.05)
        except Exception as e:
            with lock:
                data["connected"] = False
                data["error"] = str(e)
            time.sleep(2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def status():
    with lock:
        result = dict(data)
        result["raw"] = list(data["raw"][-20:])
        result["center"] = dict(center)
        return jsonify(result)

@app.route("/api/center", methods=["POST"])
def set_center():
    body = request.get_json(silent=True) or {}
    lat, lon = body.get("lat"), body.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "Latitude and longitude required"}), 400
    try:
        center["lat"] = float(lat)
        center["lon"] = float(lon)
        with lock:
            update_safety()
        return jsonify({"ok": True, "center": center})
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid coordinates"}), 400

@app.route("/api/center/current", methods=["POST"])
def set_current_center():
    with lock:
        lat, lon = data["latitude"], data["longitude"]
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "No live GPS coordinates available yet"}), 400
    center["lat"], center["lon"] = lat, lon
    with lock:
        update_safety()
    return jsonify({"ok": True, "center": center})

@app.route("/api/center/clear", methods=["POST"])
def clear_center():
    center["lat"], center["lon"] = None, None
    with lock:
        update_safety()
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=serial_worker, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
