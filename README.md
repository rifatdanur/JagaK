# 🍫 Jagak (Jaga Kakao) — Sistem Keamanan Kebun Kakao & Deteksi Kematangan Buah Berbasis Edge AIoT

**IDTC 2.0 — AIoT Future Competition**  
**Judul Lengkap:** *Jagak (Jaga Kakao): Sistem Keamanan Kebun Kakao dan Deteksi Kematangan Buah Berbasis Edge AIoT untuk Mencegah Pencurian serta Panen Dini di Kabupaten Lampung Timur*

[![Buka Web Dashboard AI](https://img.shields.io/badge/🚀_Live_Dashboard_AI-Streamlit_App-FF4B4B?style=for-the-badge)](https://jagak-kakao.streamlit.app/)
[![Buka Simulasi Wokwi](https://img.shields.io/badge/🔌_Simulasi_Hardware_ESP32--S3-Wokwi-1D4ED8?style=for-the-badge)](https://wokwi.com/projects/481229105019680769)

---

## 🚀 Cara Mencoba Simulasi End-to-End (Tanpa Install Apa-Apa!)

Buka kedua link berikut secara **Split-Screen (kiri & kanan)** di browser:

1. **Buka Live Web Dashboard:** 👉 **[https://jagak-kakao.streamlit.app/](https://jagak-kakao.streamlit.app/)**  
   *(Jika muncul status sleep, klik **"Yes, get this app back up!"** dan tunggu ~30 detik. Awalnya status alat tampil `🔴 Offline` karena Wokwi belum dinyalakan.)*
2. **Buka Simulasi Hardware ESP32-S3:** Klik tombol **`🔌 Buka Simulasi Wokwi`** di sidebar kiri Dashboard (atau klik 👉 **[Link Wokwi](https://wokwi.com/projects/481229105019680769)**), lalu tekan tombol hijau **▶ Start Simulation**.
3. **Tunggu ~5–10 detik** sampai OLED Wokwi menampilkan **`Status: STANDBY [NET]`** → otomatis dalam 1–2 detik Dashboard berubah menjadi **`Alat Wokwi: 🟢 Online`** secara *real-time*!
4. **4 Skenario Uji untuk Juri:**
   - **Sensor → Web (*Real-Time*):** Putar **Potensiometer** (≥ 60% otomatis jadi **ZONA MERAH** + LED Kuning nyala) atau klik **DHT22** lalu geser slider Suhu & Kelembapan → angka di Dashboard otomatis bergerak tiap 1 detik.
   - **Keamanan (PIR) → Web:** Klik sensor **PIR** (*Simulate motion*) → LED Merah + Sirine menyala dan event langsung masuk ke Tab **Log MQTT**.
   - **Anti-False Alarm (BLE Whitelist Petani):** Tekan **Tombol Hijau (BLE Owner)** sampai `BLE Petani: AKTIF`, lalu picu PIR lagi → alarm otomatis dibatalkan (*Owner Verified*).
   - **AI Web → Hardware Wokwi:** Di Tab 1 (**AI Keamanan**), ambil foto lewat **Webcam / Upload Foto** orang atau hewan (atau tekan tombol cepat **`MALING` / `HEWAN` / `AMANKAN`** di sidebar) → LED & Buzzer di Wokwi langsung merespons perintah AI!

---

## Ringkasan Inovasi
**Jagak (Jaga Kakao)** dirancang sebagai perangkat **All-in-One Standalone Edge AIoT** bertenaga surya untuk mengatasi tingginya risiko pencurian buah kakao matang dan fenomena *panen dini* di Kabupaten Lampung Timur. Sistem memadukan **ESP32-S3-CAM + Sensor PIR HC-SR501 + DHT22 + BLE Whitelist + Panel Surya 6V 5W + Modem/Modul 4G** yang terhubung secara *real-time* via **MQTT (`broker.hivemq.com`)** ke **Server AI Python (YOLOv8n, Segmentasi Warna HSV, & Dynamic Risk Scoring)** serta **Notifikasi Telegram Bot**.

## Fitur Utama Prototipe
1. **Hardware Virtual ESP32-S3 (Wokwi):**
   - Sensor gerak **PIR HC-SR501** (siaga 24 jam), tombol **BLE Whitelist Petani** (*Anti-False Alarm*), potensiometer kematangan buah, **DHT22** (driver *1-wire native* ESP32-S3 tanpa library eksternal), OLED SSD1306 (sinkron jam NTP WIB), LED Sorot, LED Zona Merah, dan Buzzer 2-mode (Sirine Pencuri vs. Ultrasonik Hewan).
   - Komunikasi **MQTT dua arah *real-time*** pada topik `jagak/lamtim/#`.
2. **AI #1 — Deteksi Keamanan (`YOLOv8n`):**
   - Membedakan **Manusia** (Alarm Level 2 + Foto Bukti ke Telegram + Sirine & Lampu Sorot) dengan **Hewan Hama** (Mode Ultrasonik senyap tanpa mengganggu HP petani).
3. **AI #2 & #3 — Deteksi Kematangan Buah Kakao (HSV) & *Dynamic Risk Scoring*:**
   - Menghitung persentase buah kakao matang (kuning) vs. mentah (hijau), dipadukan dengan jam rawan malam (WIB) dan riwayat anomali untuk memetakan **Zona Hijau / Kuning / Merah** serta memberi rekomendasi jadwal panen optimal.

## Struktur Proyek
```text
JagaK/
├── app.py              # Dashboard Web Streamlit Real-Time + YOLOv8n + HSV + MQTT + Telegram Bot
├── requirements.txt    # Daftar library Python (PyTorch CPU + Ultralytics + OpenCV + Streamlit)
├── packages.txt        # Dependensi sistem Linux untuk Streamlit Cloud
├── sketch.ino          # Firmware ESP32-S3 (Driver Native DHT22, NTP WIB, MQTT 2-Arah)
├── diagram.json        # Skematik rangkaian simulasi Wokwi ESP32-S3
├── libraries.txt       # Daftar library Wokwi
└── README.md           # Dokumentasi proyek
```

## Cara Menjalankan Secara Lokal (Windows)
1. Pastikan Python 3.10+ terinstall, lalu jalankan `pip install -r requirements.txt`.
2. Jalankan perintah `python -m streamlit run app.py` di terminal (atau klik 2x `run_demo.bat`).
3. Jalankan simulasi ESP32-S3 di **[Wokwi](https://wokwi.com/projects/481229105019680769)**.
