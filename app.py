import io
import json
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import requests
import streamlit as st
from PIL import Image
from ultralytics import YOLO

# ==========================================================
# KONFIGURASI MQTT & LINK WOKWI (HARUS SAMA DENGAN ESP32-S3)
# ==========================================================
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
TOPIC_TELEMETRY = "cacaoguard/lamtim/blok_b/telemetry"
TOPIC_EVENT = "cacaoguard/lamtim/blok_b/event"
TOPIC_CMD = "cacaoguard/lamtim/blok_b/cmd"
WOKWI_PROJECT_URL = "https://wokwi.com/projects/481229105019680769"

# Kelas COCO untuk YOLOv8n
PERSON_CLASS = 0
ANIMAL_CLASSES = {
    14: "Burung",
    15: "Kucing/Musang",
    16: "Anjing/Babi Hutan",
    17: "Kuda",
    18: "Kambing/Domba",
    19: "Sapi",
    20: "Gajah",
    21: "Beruang/Hama Besar",
    22: "Zebra",
    23: "Jerapah",
}


# ==========================================================
# MANAGER MQTT PERSISTEN (THREAD-SAFE)
# ==========================================================
class MqttManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.connected = False
        self.last_telemetry = {
            "node_id": "BLOK_B_LAMTIM",
            "ripeness_pct": 0,
            "risk_score": 30,
            "zone": "HIJAU",
            "temp_c": 28.0,
            "hum_pct": 75.0,
            "ble_owner": False,
            "anomaly_count": 0,
            "status": "MENUNGGU_WOKWI",
            "updated_at": "-",
        }
        self.events = []
        self.last_telegram_ts = 0.0

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"CacaoGuard-Dash-{int(time.time()) % 10000}",
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        try:
            self.client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            self.add_log("SYSTEM", f"Gagal konek MQTT: {e}", "INFO")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            self.connected = reason_code == 0
        if reason_code == 0:
            client.subscribe(TOPIC_TELEMETRY)
            client.subscribe(TOPIC_EVENT)
            self.add_log("MQTT", f"Terhubung ke {MQTT_BROKER}", "OK")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        with self.lock:
            self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        if msg.topic == TOPIC_TELEMETRY:
            with self.lock:
                self.last_telemetry.update(data)
                self.last_telemetry["updated_at"] = now_str
        elif msg.topic == TOPIC_EVENT:
            trigger = data.get("trigger", "UNKNOWN")
            action = data.get("action", "EVENT")
            zone = data.get("zone", "-")
            self.add_log(trigger, action, zone)

    def send_command(self, cmd: str):
        cmd_clean = cmd.strip().upper()
        if self.connected:
            self.client.publish(TOPIC_CMD, cmd_clean)
            self.add_log("DASHBOARD_AI", f"Kirim CMD -> Wokwi: {cmd_clean}", self.get_telemetry().get("zone", "-"))
            return True
        return False

    def add_log(self, source: str, message: str, zone: str = "-"):
        with self.lock:
            self.events.insert(
                0,
                {
                    "Waktu": datetime.now().strftime("%H:%M:%S"),
                    "Sumber": source,
                    "Keterangan": message,
                    "Zona": zone,
                },
            )
            self.events = self.events[:30]

    def get_telemetry(self):
        with self.lock:
            return dict(self.last_telemetry)

    def get_events(self):
        with self.lock:
            return list(self.events)


@st.cache_resource
def get_mqtt_manager():
    return MqttManager()


@st.cache_resource
def load_yolo_model():
    # Otomatis mengunduh yolov8n.pt (~6 MB) saat pertama kali dijalankan
    return YOLO("yolov8n.pt")


# ==========================================================
# FUNGSI TELEGRAM BOT API
# ==========================================================
def send_telegram_alert(bot_token: str, chat_id: str, caption: str, image_bgr=None):
    if not bot_token or not chat_id:
        return False, "Token atau Chat ID belum diisi."
    try:
        if image_bgr is not None:
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("bukti_cacaoguard.jpg", buf, "image/jpeg")},
                timeout=10,
            )
        else:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "text": caption},
                timeout=10,
            )
        if resp.status_code == 200:
            return True, "Notifikasi terkirim ke Telegram!"
        return False, f"Gagal ({resp.status_code}): {resp.text[:120]}"
    except Exception as e:
        return False, f"Error Telegram: {e}"


# ==========================================================
# AI #1: DETEKSI OBJEK KEAMANAN (MANUSIA VS HEWAN)
# ==========================================================
def run_security_ai(image_bgr, model, conf_threshold: float, ble_owner: bool, mqtt_mgr: MqttManager, tg_token: str, tg_chat: str):
    results = model(image_bgr, conf=conf_threshold, verbose=False)
    annotated_bgr = results[0].plot()

    detected_persons = []
    detected_animals = []

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        if cls_id == PERSON_CLASS:
            detected_persons.append(conf)
        elif cls_id in ANIMAL_CLASSES:
            detected_animals.append((ANIMAL_CLASSES[cls_id], conf))

    # Algoritma Keputusan Sesuai Flowchart CacaoGuard AI
    if detected_persons:
        max_conf = max(detected_persons)
        if ble_owner:
            mqtt_mgr.send_command("CLEAR")
            return (
                annotated_bgr,
                "WHITELIST",
                f"Manusia terdeteksi ({max_conf*100:.1f}%), TETAPI BLE Whitelist Petani AKTIF -> Alarm dibatalkan.",
                None,
            )
        else:
            mqtt_mgr.send_command("THIEF")
            now = time.time()
            tg_msg = None
            if tg_token and tg_chat and (now - mqtt_mgr.last_telegram_ts >= 10):
                mqtt_mgr.last_telegram_ts = now
                telem = mqtt_mgr.get_telemetry()
                caption = (
                    f"🚨 PERINGATAN PENCURIAN — CacaoGuard AI\n"
                    f"📍 Lokasi: {telem.get('node_id', 'BLOK_B_LAMTIM')} (Zona {telem.get('zone', '-')})\n"
                    f"👤 Objek: MANUSIA Tanpa Tag BLE ({max_conf*100:.1f}%)\n"
                    f"🍫 Kematangan Blok: {telem.get('ripeness_pct', 0)}%\n"
                    f"⚡ Aksi: Lampu Sorot LED + Sirine 12V AKTIF!"
                )
                ok, info = send_telegram_alert(tg_token, tg_chat, caption, annotated_bgr)
                tg_msg = f"Telegram: {info}"
            return (
                annotated_bgr,
                "THIEF",
                f"BAHAYA LEVEL 2: MANUSIA tanpa izin ({max_conf*100:.1f}%)! Perintah THIEF dikirim ke Wokwi (Sirine + Lampu Sorot ON).",
                tg_msg,
            )

    if detected_animals:
        animal_name, max_conf = max(detected_animals, key=lambda x: x[1])
        mqtt_mgr.send_command("ANIMAL")
        return (
            annotated_bgr,
            "ANIMAL",
            f"PERINGATAN LEVEL 1: Hewan Hama ({animal_name}, {max_conf*100:.1f}%). Perintah ANIMAL dikirim ke Wokwi (Ultrasonik ON, tanpa alarm HP petani).",
            None,
        )

    return (
        annotated_bgr,
        "SAFE",
        "Tidak ada manusia atau hewan hama terdeteksi pada frame.",
        None,
    )


# ==========================================================
# AI #2: DETEKSI KEMATANGAN BUAH KAKAO (HIJAU VS KUNING)
# ==========================================================
def analyze_cocoa_ripeness(image_bgr, jam_rawan: bool = True, anomaly_count: int = 0):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

    # Mask buah matang (Kuning / Oranye)
    lower_yellow = np.array([15, 70, 70])
    upper_yellow = np.array([36, 255, 255])
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)

    # Mask buah mentah (Hijau)
    lower_green = np.array([37, 45, 45])
    upper_green = np.array([85, 255, 255])
    mask_green = cv2.inRange(hsv, lower_green, upper_green)

    yellow_px = int(np.count_nonzero(mask_yellow))
    green_px = int(np.count_nonzero(mask_green))
    total_cocoa_px = yellow_px + green_px

    if total_cocoa_px < 100:
        ripeness_pct = 0.0
    else:
        ripeness_pct = (yellow_px / total_cocoa_px) * 100.0

    # Buat visualisasi overlay (Kuning untuk matang, Hijau untuk mentah)
    overlay = image_bgr.copy()
    overlay[mask_yellow > 0] = [0, 215, 255]  # BGR Kuning-Emas
    overlay[mask_green > 0] = [0, 180, 0]     # BGR Hijau
    blended = cv2.addWeighted(image_bgr, 0.55, overlay, 0.45, 0)

    # Dynamic Risk Scoring (exact formula sesuai Blueprint)
    skor_jam = 100 if jam_rawan else 20
    skor_anomali = min(anomaly_count * 25, 100)
    risk_score = (0.50 * ripeness_pct) + (0.30 * skor_jam) + (0.20 * skor_anomali)

    if risk_score >= 70 or ripeness_pct >= 60:
        zone = "MERAH"
        rekomendasi = "Buah kakao dominan MATANG (Kuning). Risiko pencurian TINGGI malam ini — naikkan sensitivitas ronda & jadwalkan panen besok pagi!"
    elif risk_score >= 40:
        zone = "KUNING"
        rekomendasi = "Sebagian buah mulai menguning. Pantau berkala dalam 3–5 hari ke depan."
    else:
        zone = "HIJAU"
        rekomendasi = "Buah masih dominan hijau (mentah). Risiko pencurian rendah, node berjalan dalam mode hemat daya."

    return blended, ripeness_pct, risk_score, zone, rekomendasi


# ==========================================================
# UI DASHBOARD STREAMLIT
# ==========================================================
st.set_page_config(
    page_title="CacaoGuard AI — Dashboard AIoT Lampung Timur",
    page_icon="🍫",
    layout="wide",
)

mqtt_mgr = get_mqtt_manager()
telem = mqtt_mgr.get_telemetry()

# --- SIDEBAR ---
with st.sidebar:
    st.title("🍫 CacaoGuard AI")
    st.caption("IDTC 2.0 — AIoT Future Competition\nStudi Kasus: Kakao Lampung Timur")

    if mqtt_mgr.connected:
        st.success(f"MQTT Terhubung ({MQTT_BROKER})")
    else:
        st.warning("MQTT Menghubungkan...")

    if st.button("🔄 Refresh Data Wokwi", use_container_width=True):
        st.rerun()

    st.divider()
    st.subheader("📲 Konfigurasi Telegram Bot")
    tg_token = st.text_input("Bot Token (@BotFather)", type="password", placeholder="123456:ABC-DEF...")
    tg_chat = st.text_input("Chat ID Petani / Poktan", placeholder="Contoh: 987654321")
    if st.button("Tes Kirim Pesan Telegram", use_container_width=True):
        ok, info = send_telegram_alert(
            tg_token,
            tg_chat,
            "✅ Tes Koneksi CacaoGuard AI (Lampung Timur) berhasil terhubung ke Dashboard!",
        )
        if ok:
            st.success(info)
        else:
            st.error(info)

    st.divider()
    st.subheader("🎛️ Kontrol Manual ke Wokwi")
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        if st.button("🚨 THIEF", use_container_width=True):
            mqtt_mgr.send_command("THIEF")
    with col_c2:
        if st.button("🐿️ ANIMAL", use_container_width=True):
            mqtt_mgr.send_command("ANIMAL")
    with col_c3:
        if st.button("✅ CLEAR", use_container_width=True):
            mqtt_mgr.send_command("CLEAR")

    st.caption("Klik tombol di atas untuk langsung menguji respon LED & Buzzer di simulasi Wokwi!")

    st.divider()
    st.link_button("🔌 Buka Simulasi Wokwi ESP32-S3", WOKWI_PROJECT_URL, use_container_width=True)

# --- HEADER METRICS ---
st.title("Dashboard Monitoring & AI Keamanan Kebun Kakao")

m1, m2, m3, m4, m5 = st.columns(5)
zone_now = telem.get("zone", "HIJAU")
zone_emoji = "🔴" if zone_now == "MERAH" else ("🟡" if zone_now == "KUNING" else "🟢")

m1.metric("Status Zona (Blok B)", f"{zone_emoji} {zone_now}", f"Risk Score: {telem.get('risk_score', 0)}")
m2.metric("Kematangan Buah", f"{telem.get('ripeness_pct', 0)}%", "Target Panen >= 60%")
m3.metric("BLE Whitelist Petani", "AKTIF (OK)" if telem.get("ble_owner") else "OFF", "Anti-False Alarm")
m4.metric("Cuaca Kebun (DHT22)", f"{telem.get('temp_c', 28)} °C", f"Kelembapan {telem.get('hum_pct', 75)}%")
m5.metric("Status Node Wokwi", telem.get("status", "-"), f"Update: {telem.get('updated_at', '-')}")

st.divider()

# --- TABS UTAMA ---
tab1, tab2, tab3, tab4 = st.tabs(
    [
        "📷 AI #1: Keamanan (Manusia vs Hewan)",
        "🍫 AI #2: Deteksi Kematangan Kakao",
        "🗺️ Monitoring Poktan & Log MQTT",
        "🔌 Simulasi Hardware (Wokwi) & Kode",
    ]
)

# ==========================================================
# TAB 1: AI KEAMANAN (YOLOv8n)
# ==========================================================
with tab1:
    st.subheader("Computer Vision (YOLOv8n) — Klasifikasi Manusia vs Hewan Hama")
    st.write(
        "Deteksi otomatis membedakan **Manusia** (Alarm Level 2 + Foto ke Telegram + Sirine Wokwi) "
        "dengan **Hewan Hama** (Level 1 Ultrasonik Wokwi tanpa mengganggu HP petani). "
        "Jika **BLE Owner** di Wokwi aktif, alarm manusia otomatis dibatalkan."
    )

    c_opt1, c_opt2 = st.columns([1, 1])
    with c_opt1:
        conf_th = st.slider("Confidence Threshold AI", 0.30, 0.90, 0.50, 0.05)
    with c_opt2:
        override_ble = st.checkbox(
            "Sinkron BLE Owner dari Wokwi (centang = ikuti tombol hijau Wokwi)",
            value=True,
        )
        ble_active = bool(telem.get("ble_owner", False)) if override_ble else False

    input_mode = st.radio(
        "Pilih Sumber Kamera / Gambar:",
        ["📸 Ambil Foto Webcam (Paling Stabil)", "📁 Upload Foto Uji (Manusia / Hewan)"],
        horizontal=True,
    )

    img_bgr = None
    if input_mode.startswith("📸"):
        cam_file = st.camera_input("Arahkan webcam ke orang atau gambar hewan (tupai/kucing/burung/anjing)")
        if cam_file is not None:
            file_bytes = np.asarray(bytearray(cam_file.read()), dtype=np.uint8)
            img_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    else:
        up_file = st.file_uploader("Upload gambar (.jpg / .png)", type=["jpg", "jpeg", "png"], key="sec_upload")
        if up_file is not None:
            file_bytes = np.asarray(bytearray(up_file.read()), dtype=np.uint8)
            img_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

    if img_bgr is not None:
        with st.spinner("Menjalankan inferensi YOLOv8n..."):
            model = load_yolo_model()
            res_bgr, status_code, msg, tg_info = run_security_ai(
                img_bgr, model, conf_th, ble_active, mqtt_mgr, tg_token, tg_chat
            )

        if status_code == "THIEF":
            st.error(f"🚨 {msg}")
        elif status_code == "ANIMAL":
            st.warning(f"🐿️ {msg}")
        elif status_code == "WHITELIST":
            st.info(f"🛡️ {msg}")
        else:
            st.success(f"✅ {msg}")

        if tg_info:
            st.caption(tg_info)

        st.image(cv2.cvtColor(res_bgr, cv2.COLOR_BGR2RGB), caption="Hasil Deteksi YOLOv8n", use_container_width=True)

# ==========================================================
# TAB 2: AI KEMATANGAN BUAH KAKAO
# ==========================================================
with tab2:
    st.subheader("Deteksi Kematangan Buah Kakao (Hijau vs Kuning) & Dynamic Risk Scoring")
    st.write(
        "Upload foto buah kakao di pohon (hijau/mentah atau kuning/matang). "
        "Sistem menghitung persentase buah matang lalu memperbarui **Dynamic Risk Score** untuk mencegah panen dini."
    )

    jam_rawan_sim = st.checkbox("Simulasikan Kondisi Malam Rawan (18.00 - 05.00 WIB)", value=True)
    cocoa_file = st.file_uploader("Upload Foto Buah Kakao (.jpg / .png)", type=["jpg", "jpeg", "png"], key="cocoa_up")

    if cocoa_file is not None:
        file_bytes = np.asarray(bytearray(cocoa_file.read()), dtype=np.uint8)
        cocoa_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        blended, ripe_pct, r_score, z_stat, rekom = analyze_cocoa_ripeness(
            cocoa_bgr, jam_rawan=jam_rawan_sim, anomaly_count=int(telem.get("anomaly_count", 0))
        )

        col_r1, col_r2, col_r3 = st.columns(3)
        col_r1.metric("Persentase Matang (Kuning)", f"{ripe_pct:.1f}%")
        col_r2.metric("Dynamic Risk Score", f"{r_score:.1f} / 100")
        col_r3.metric("Klasifikasi Zona", f"{'🔴' if z_stat=='MERAH' else ('🟡' if z_stat=='KUNING' else '🟢')} ZONA {z_stat}")

        if z_stat == "MERAH":
            st.error(f"⚠️ Rekomendasi AI: {rekom}")
        elif z_stat == "KUNING":
            st.warning(f"🔔 Rekomendasi AI: {rekom}")
        else:
            st.success(f"✅ Rekomendasi AI: {rekom}")

        c_img1, c_img2 = st.columns(2)
        c_img1.image(cv2.cvtColor(cocoa_bgr, cv2.COLOR_BGR2RGB), caption="Foto Asli Buah Kakao", use_container_width=True)
        c_img2.image(cv2.cvtColor(blended, cv2.COLOR_BGR2RGB), caption="Segmentasi Warna AI (Kuning=Matang, Hijau=Mentah)", use_container_width=True)

        if st.button("📲 Kirim Rekomendasi Panen ke Telegram Petani"):
            pesan_panen = (
                f"🍫 LAPORAN KEMATANGAN — CacaoGuard AI\n"
                f"📍 Blok B Lampung Timur\n"
                f"🟡 Kematangan: {ripe_pct:.1f}% | Risk Score: {r_score:.1f}\n"
                f"📌 Status: ZONA {z_stat}\n"
                f"💡 {rekom}"
            )
            ok, info = send_telegram_alert(tg_token, tg_chat, pesan_panen, blended)
            if ok:
                st.success(info)
            else:
                st.error(info)

# ==========================================================
# TAB 3: MONITORING POKTAN & LOG REAL-TIME MQTT
# ==========================================================
with tab3:
    st.subheader("Status Jaringan Kelompok Tani (Poktan) — Lampung Timur")
    poktan_data = [
        {"Blok": "Blok A (Pak Slamet)", "Koneksi": "LoRa 433MHz (-78 dBm)", "Kematangan": "25%", "Zona": "🟢 HIJAU", "Baterai Surya": "92%"},
        {
            "Blok": "Blok B (LIVE WOKWI ESP32-S3)",
            "Koneksi": "MQTT Live" if mqtt_mgr.connected else "Offline",
            "Kematangan": f"{telem.get('ripeness_pct', 0)}%",
            "Zona": f"{zone_emoji} {zone_now}",
            "Baterai Surya": "88%",
        },
        {"Blok": "Blok C (Pak Hendra)", "Koneksi": "LoRa 433MHz (-84 dBm)", "Kematangan": "78%", "Zona": "🔴 MERAH", "Baterai Surya": "81%"},
        {"Blok": "Blok D (Bu Sari)", "Koneksi": "LoRa 433MHz (-80 dBm)", "Kematangan": "48%", "Zona": "🟡 KUNING", "Baterai Surya": "95%"},
        {"Blok": "Blok E (Pos Ronda)", "Koneksi": "Gateway 4G / Wi-Fi", "Kematangan": "-", "Zona": "🟢 ONLINE", "Baterai Surya": "UPS 100%"},
    ]
    st.dataframe(poktan_data, use_container_width=True, hide_index=True)

    st.subheader("📜 Log Kejadian & Komunikasi MQTT Real-Time")
    events = mqtt_mgr.get_events()
    if events:
        st.dataframe(events, use_container_width=True, hide_index=True)
    else:
        st.info("Belum ada event masuk. Coba klik sensor PIR atau tombol di Wokwi, lalu klik 'Refresh Data Wokwi' di sidebar.")

# ==========================================================
# TAB 4: SIMULASI HARDWARE WOKWI & INSPEKSI KODE ESP32-S3
# ==========================================================
with tab4:
    st.subheader("Simulasi Hardware ESP32-S3 (Wokwi) — Terhubung Real-Time via MQTT")
    st.write(
        "Simulasi perangkat keras **CacaoGuard AI Field Node** berjalan di Wokwi menggunakan **ESP32-S3-DevKitC-1** "
        "dan terhubung langsung dua arah dengan Dashboard ini melalui broker MQTT (`broker.hivemq.com`)."
    )

    st.link_button("🚀 Buka Simulasi ESP32-S3 di Wokwi (Tab Baru)", WOKWI_PROJECT_URL, type="primary")

    st.markdown(
        """
        **Cara Pengujian End-to-End (Split-Screen Wokwi + Dashboard):**
        1. Klik tombol **Buka Simulasi ESP32-S3 di Wokwi** di atas lalu tekan **▶ Start Simulation**.
        2. Tunggu hingga OLED menampilkan **`Status: STANDBY [NET]`**.
        3. Putar **Potentiometer** (kematangan buah) atau klik **PIR / Tombol Audio AI / Tombol Hijau BLE Owner** di Wokwi, lalu klik **🔄 Refresh Data Wokwi** di sidebar kiri Dashboard.
        4. Uji perintah balik dari AI ke Hardware dengan mengklik tombol **`🚨 THIEF`**, **`🐿️ ANIMAL`**, atau **`✅ CLEAR`** di sidebar (atau lewat deteksi kamera di Tab 1).
        """
    )

    st.divider()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ino_path = os.path.join(base_dir, "wokwi", "sketch.ino")
    diag_path = os.path.join(base_dir, "wokwi", "diagram.json")

    with st.expander("📄 Lihat Source Code Firmware ESP32-S3 (wokwi/sketch.ino)"):
        if os.path.exists(ino_path):
            with open(ino_path, "r", encoding="utf-8") as f:
                st.code(f.read(), language="cpp")
        else:
            st.warning("File wokwi/sketch.ino belum ditemukan.")

    with st.expander("🧩 Lihat Skematik Rangkaian Wokwi (wokwi/diagram.json)"):
        if os.path.exists(diag_path):
            with open(diag_path, "r", encoding="utf-8") as f:
                st.code(f.read(), language="json")
        else:
            st.warning("File wokwi/diagram.json belum ditemukan.")
