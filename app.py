# =====================================================================
# Jagak (Jaga Kakao) - Dashboard Edge AIoT Keamanan & Kematangan Kakao
# Auto-Refresh Real-Time (tiap 2 detik) tanpa mengganggu Webcam!
# =====================================================================

import io
import json
import time
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import requests
import streamlit as st
from PIL import Image
from ultralytics import YOLO

# ---------------------------------------------------------------------
# 1. KONFIGURASI DASAR (Harus sama dengan kode ESP32-S3 di Wokwi)
# ---------------------------------------------------------------------
MQTT_BROKER     = "broker.hivemq.com"
MQTT_PORT       = 1883
TOPIC_TELEMETRY = "jagak/lamtim/telemetry"
TOPIC_EVENT     = "jagak/lamtim/event"
TOPIC_CMD       = "jagak/lamtim/cmd"
LINK_WOKWI      = "https://wokwi.com/projects/481229105019680769"

TIMEOUT_WOKWI_DETIK = 8  # > 8 detik tanpa paket dari Wokwi -> dianggap Offline

KELAS_MANUSIA = 0
KELAS_HEWAN   = [14, 15, 16, 17, 18, 19, 20, 21, 22, 23]


# ---------------------------------------------------------------------
# 2. KONEKSI MQTT DUA ARAH (Background)
# ---------------------------------------------------------------------
@st.cache_resource
def mulai_mqtt():
    state = {
        "mqtt_terhubung": False,
        "last_wokwi_ts": 0.0,
        "ripeness_pct": None,
        "risk_score": None,
        "zone": None,
        "temp_c": None,
        "hum_pct": None,
        "ble_owner": None,
        "anomaly_count": 0,
        "status": None,
        "log": [],
    }

    def saat_terhubung(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            state["mqtt_terhubung"] = True
            client.subscribe(TOPIC_TELEMETRY)
            client.subscribe(TOPIC_EVENT)
        else:
            state["mqtt_terhubung"] = False

    def saat_terputus(client, userdata, disconnect_flags, reason_code, properties):
        state["mqtt_terhubung"] = False

    def saat_pesan_masuk(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        state["last_wokwi_ts"] = time.time()
        waktu_laptop = datetime.now(WIB).strftime("%H:%M:%S")

        if msg.topic == TOPIC_TELEMETRY:
            for k in ["ripeness_pct", "risk_score", "zone", "temp_c", "hum_pct", "ble_owner", "anomaly_count", "status"]:
                if k in data:
                    state[k] = data[k]
        elif msg.topic == TOPIC_EVENT:
            state["log"].insert(
                0,
                {
                    "Waktu (Laptop)": waktu_laptop,
                    "Sumber": data.get("trigger", "-"),
                    "Keterangan": data.get("action", "-"),
                    "Zona": data.get("zone", "-"),
                },
            )
            state["log"] = state["log"][:20]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = saat_terhubung
    client.on_disconnect = saat_terputus
    client.on_message = saat_pesan_masuk
    try:
        client.connect_async(MQTT_BROKER, MQTT_PORT, 30)
        client.loop_start()
    except Exception:
        state["mqtt_terhubung"] = False

    return client, state


def cek_status_koneksi(client, state):
    broker_ok = bool(state["mqtt_terhubung"] and client.is_connected())
    wokwi_ok = broker_ok and ((time.time() - state["last_wokwi_ts"]) <= TIMEOUT_WOKWI_DETIK)
    return broker_ok, wokwi_ok


def kirim_perintah_wokwi(client, state, perintah):
    broker_ok, _ = cek_status_koneksi(client, state)
    waktu_laptop = datetime.now(WIB).strftime("%H:%M:%S")
    if broker_ok:
        client.publish(TOPIC_CMD, perintah)
        state["log"].insert(
            0,
            {
                "Waktu (Laptop)": waktu_laptop,
                "Sumber": "AI_DASHBOARD",
                "Keterangan": f"Kirim Perintah -> Wokwi: {perintah}",
                "Zona": state["zone"] if state["zone"] else "null",
            },
        )
        state["log"] = state["log"][:20]
        return True
    return False


# ---------------------------------------------------------------------
# 3. LOAD MODEL AI YOLOv8n & TELEGRAM BOT
# ---------------------------------------------------------------------
@st.cache_resource
def muat_yolo():
    return YOLO("yolov8n.pt")


def kirim_telegram(token, chat_id, pesan, gambar_bgr=None):
    if not token or not chat_id:
        return "Token / Chat ID Telegram belum diisi."
    try:
        if gambar_bgr is not None:
            gambar_rgb = cv2.cvtColor(gambar_bgr, cv2.COLOR_BGR2RGB)
            buf = io.BytesIO()
            Image.fromarray(gambar_rgb).save(buf, format="JPEG")
            buf.seek(0)
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            res = requests.post(url, data={"chat_id": chat_id, "caption": pesan}, files={"photo": buf}, timeout=10)
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            res = requests.post(url, data={"chat_id": chat_id, "text": pesan}, timeout=10)
        return "Berhasil terkirim ke Telegram!" if res.status_code == 200 else f"Gagal: {res.status_code}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------
# 4. FUNGSI AI #1 (KEAMANAN) & AI #2 (KEMATANGAN KAKAO)
# ---------------------------------------------------------------------
def deteksi_keamanan(gambar_bgr, model, ble_owner, mqtt_client, state, token, chat_id):
    hasil = model(gambar_bgr, conf=0.50, verbose=False)[0]
    gambar_hasil = hasil.plot()

    ada_manusia = False
    ada_hewan = False
    for box in hasil.boxes:
        id_kelas = int(box.cls[0])
        if id_kelas == KELAS_MANUSIA:
            ada_manusia = True
        elif id_kelas in KELAS_HEWAN:
            ada_hewan = True

    waktu_sekarang = datetime.now(WIB).strftime("%d/%m/%Y %H:%M:%S")
    zona_teks = state["zone"] if state["zone"] else "null"

    if ada_manusia and ble_owner:
        kirim_perintah_wokwi(mqtt_client, state, "CLEAR")
        return gambar_hasil, "INFO", "Manusia terdeteksi, tapi BLE Petani AKTIF -> Alarm dibatalkan."

    elif ada_manusia and not ble_owner:
        kirim_perintah_wokwi(mqtt_client, state, "THIEF")
        pesan = f"🚨 PERINGATAN PENCURI — Jagak (Jaga Kakao)!\n🕒 Waktu: {waktu_sekarang}\n📍 Kebun Kakao Lampung Timur (Zona {zona_teks})\n⚡ Sirine & Lampu Sorot AKTIF!"
        status_tg = kirim_telegram(token, chat_id, pesan, gambar_hasil)
        return gambar_hasil, "BAHAYA", f"MANUSIA tanpa izin terdeteksi! Perintah THIEF dikirim ke Wokwi. ({status_tg})"

    elif ada_hewan:
        kirim_perintah_wokwi(mqtt_client, state, "ANIMAL")
        return gambar_hasil, "HEWAN", "HEWAN HAMA terdeteksi! Wokwi aktifkan mode ultrasonik (tanpa alarm HP petani)."

    return gambar_hasil, "AMAN", "Tidak ada manusia atau hewan terdeteksi."


def deteksi_kematangan_kakao(gambar_bgr, malam_rawan, jumlah_anomali):
    hsv = cv2.cvtColor(gambar_bgr, cv2.COLOR_BGR2HSV)

    mask_kuning = cv2.inRange(hsv, np.array([15, 70, 70]), np.array([36, 255, 255]))
    mask_hijau  = cv2.inRange(hsv, np.array([37, 45, 45]), np.array([85, 255, 255]))

    px_kuning = int(np.count_nonzero(mask_kuning))
    px_hijau  = int(np.count_nonzero(mask_hijau))
    total_px  = px_kuning + px_hijau

    persen_matang = (px_kuning / total_px * 100) if total_px > 100 else 0.0

    skor_jam     = 100 if malam_rawan else 20
    skor_anomali = min(int(jumlah_anomali or 0) * 25, 100)
    risk_score   = (0.50 * persen_matang) + (0.30 * skor_jam) + (0.20 * skor_anomali)

    if risk_score >= 70 or persen_matang >= 60:
        zona = "MERAH"
        saran = "Buah dominan MATANG (Kuning). Risiko pencurian tinggi — siaga ronda & jadwalkan panen!"
    elif risk_score >= 40:
        zona = "KUNING"
        saran = "Sebagian buah mulai menguning. Pantau kebun 3-5 hari ke depan."
    else:
        zona = "HIJAU"
        saran = "Buah masih mentah (Hijau). Risiko rendah, mode hemat daya aktif."

    return persen_matang, risk_score, zona, saran


# ---------------------------------------------------------------------
# 5. KOMPONEN REAL-TIME AUTO-REFRESH TIAP 2 DETIK (@st.fragment)
#    Hanya panel sensor & log yang refresh otomatis; Webcam TIDAK reset!
# ---------------------------------------------------------------------
@st.fragment(run_every=1)
def panel_sensor_realtime():
    broker_ok, wokwi_ok = cek_status_koneksi(mqtt_client, state)
    waktu_str = datetime.now(WIB).strftime("%d/%m/%Y  %H:%M:%S")

    s1, s2, s3 = st.columns([1.2, 1.4, 1.4])
    s1.markdown(f"**Broker MQTT:** {'🟢 Terhubung' if broker_ok else '🔴 Terputus'}")
    s2.markdown(f"**Alat Wokwi:** {'🟢 Online' if wokwi_ok else '🔴 Offline (Menunggu Data)'}")
    s3.markdown(f"**Waktu Laptop:** 🕒 `{waktu_str}`")

    zona_val   = state["zone"] if (wokwi_ok and state["zone"] is not None) else "null"
    skor_val   = f"Skor: {state['risk_score']}" if (wokwi_ok and state["risk_score"] is not None) else None
    matang_val = f"{state['ripeness_pct']}%" if (wokwi_ok and state["ripeness_pct"] is not None) else "null"
    ble_val    = ("AKTIF" if state["ble_owner"] else "OFF") if (wokwi_ok and state["ble_owner"] is not None) else "null"
    suhu_val   = f"{state['temp_c']} °C" if (wokwi_ok and state["temp_c"] is not None) else "null"
    hum_val    = f"{state['hum_pct']} %" if (wokwi_ok and state["hum_pct"] is not None) else "null"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Zona Risiko", zona_val, skor_val)
    k2.metric("Kematangan (Pot)", matang_val)
    k3.metric("BLE Petani", ble_val)
    k4.metric("Suhu Kebun", suhu_val)
    k5.metric("Kelembapan", hum_val)


@st.fragment(run_every=1)
def panel_log_realtime():
    if state["log"]:
        st.dataframe(state["log"], use_container_width=True, hide_index=True)
    else:
        template_kosong = [
            {"Waktu (Laptop)": "-", "Sumber": "-", "Keterangan": "Belum ada data masuk...", "Zona": "-"},
            {"Waktu (Laptop)": "-", "Sumber": "-", "Keterangan": "-", "Zona": "-"},
        ]
        st.dataframe(template_kosong, use_container_width=True, hide_index=True)


# =====================================================================
# 6. TAMPILAN UTAMA DASHBOARD STREAMLIT
# =====================================================================
st.set_page_config(page_title="Jagak (Jaga Kakao)", page_icon="🍫", layout="wide")
mqtt_client, state = mulai_mqtt()

with st.sidebar:
    st.title("🍫 Jagak (Jaga Kakao)")
    st.caption("Edge AIoT Kakao Lampung Timur | IDTC 2.0")

    st.divider()
    st.subheader("Telegram Bot")
    tg_token = st.text_input("Bot Token", type="password")
    tg_chat  = st.text_input("Chat ID")

    st.divider()
    st.subheader("Tes Kirim ke Wokwi")
    c1, c2, c3 = st.columns(3)
    if c1.button("MALING"):  kirim_perintah_wokwi(mqtt_client, state, "THIEF")
    if c2.button("HEWAN"):   kirim_perintah_wokwi(mqtt_client, state, "ANIMAL")
    if c3.button("AMANKAN"): kirim_perintah_wokwi(mqtt_client, state, "CLEAR")

    st.divider()
    st.link_button("🔌 Buka Simulasi Wokwi", LINK_WOKWI, use_container_width=True)

st.title("Dashboard Jagak (Jaga Kakao) — Lampung Timur")
panel_sensor_realtime()
st.divider()

tab1, tab2, tab3 = st.tabs(["📷 AI Keamanan (YOLO)", "🍫 AI Kematangan Kakao", "📜 Log MQTT"])

with tab1:
    st.subheader("Deteksi Manusia vs Hewan Hama (YOLOv8n)")
    mode = st.radio("Sumber gambar:", ["Webcam", "Upload Foto"], horizontal=True)

    col_cam, col_hasil = st.columns([1, 1.2])
    with col_cam:
        file_gambar = (
            st.camera_input("Ambil Foto")
            if mode == "Webcam"
            else st.file_uploader("Pilih Foto", type=["jpg", "png"])
        )

    with col_hasil:
        if file_gambar is not None:
            bytes_img = np.asarray(bytearray(file_gambar.read()), dtype=np.uint8)
            img_bgr = cv2.imdecode(bytes_img, cv2.IMREAD_COLOR)

            _, wokwi_ok_now = cek_status_koneksi(mqtt_client, state)
            ble_aktif = bool(state["ble_owner"]) if (wokwi_ok_now and state["ble_owner"] is not None) else False
            hasil_bgr, tipe, pesan = deteksi_keamanan(
                img_bgr, muat_yolo(), ble_aktif, mqtt_client, state, tg_token, tg_chat
            )
            if tipe == "BAHAYA":  st.error(pesan)
            elif tipe == "HEWAN": st.warning(pesan)
            elif tipe == "INFO":  st.info(pesan)
            else:                 st.success(pesan)

            st.image(cv2.cvtColor(hasil_bgr, cv2.COLOR_BGR2RGB), width=420)
        else:
            st.info("Ambil foto lewat webcam di kiri atau upload foto untuk melihat hasil deteksi YOLOv8n.")

with tab2:
    st.subheader("Deteksi Kematangan Buah Kakao (Hijau vs Kuning)")
    now_laptop = datetime.now(WIB)
    is_malam_laptop = (now_laptop.hour >= 18 or now_laptop.hour <= 5)
    malam_rawan = st.checkbox(
        f"Mode Malam Rawan (Sinkron Jam Laptop: {now_laptop.strftime('%H:%M')} — {'Malam' if is_malam_laptop else 'Siang'})",
        value=is_malam_laptop,
    )
    foto_kakao = st.file_uploader("Upload Foto Buah Kakao", type=["jpg", "png"], key="kakao")

    if foto_kakao is not None:
        bytes_img = np.asarray(bytearray(foto_kakao.read()), dtype=np.uint8)
        img_bgr = cv2.imdecode(bytes_img, cv2.IMREAD_COLOR)

        matang, skor, zona, saran = deteksi_kematangan_kakao(img_bgr, malam_rawan, state["anomaly_count"])
        a, b, c = st.columns(3)
        a.metric("Buah Matang (Kuning)", f"{matang:.1f}%")
        b.metric("Dynamic Risk Score", f"{skor:.1f}")
        c.metric("Status Zona", f"ZONA {zona}")
        st.info(saran)
        st.image(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB), width=360)

with tab3:
    st.subheader("Log Komunikasi Real-Time (Wokwi <-> Dashboard)")
    panel_log_realtime()