# JagaK — Sistem Keamanan & Deteksi Kematangan Kakao Berbasis Edge AIoT

**IDTC 2.0 — AIoT Future Competition**  
**Studi Kasus:** Perkebunan Kakao Kabupaten Lampung Timur

---

## Ringkasan Inovasi
**JagaK** mengatasi masalah pencurian buah kakao matang dan fenomena *panen dini* di Lampung Timur melalui kombinasi **ESP32-S3 + Sensor Fusion (PIR, Audio AI TinyML, Kamera IR) + LoRa Mesh + Computer Vision AI (YOLOv8n & Segmentasi Kematangan) + Dynamic Risk Scoring + Notifikasi Telegram Poktan**.

## Link Simulasi Hardware (Wokwi ESP32-S3)
- **Wokwi Live Project:** https://wokwi.com/projects/481229105019680769
- web dashboard https://jagak-kakao.streamlit.app/

## Fitur Utama Prototipe
1. **Simulasi Hardware ESP32-S3 (`wokwi/`):**
   - Sensor gerak PIR, simulasi Audio TinyML, tombol BLE Whitelist Petani, potensiometer kematangan buah, DHT22, OLED SSD1306, LED Sorot, LED Zona Merah, dan Buzzer 2-mode (Sirine Pencuri vs Ultrasonik Hewan).
   - Komunikasi MQTT dua arah real-time (`broker.hivemq.com`).
2. **AI #1 — Deteksi Keamanan (`YOLOv8n`):**
   - Membedakan **Manusia** (Alarm Level 2 + Foto ke Telegram + Sirine Wokwi) dengan **Hewan Hama** (Mode Ultrasonik tanpa mengganggu HP petani).
   - Mendukung **BLE Whitelist Petani** (*Anti-False Alarm*).
3. **AI #2 — Deteksi Kematangan Buah Kakao & Dynamic Risk Scoring:**
   - Analisis persentase buah matang (kuning) vs mentah (hijau) untuk memetakan **Zona Hijau / Kuning / Merah** serta memberi rekomendasi jadwal panen optimal.

## Struktur Proyek
```text
cacaoguard_ai/
├── app.py              # Dashboard Web Streamlit + YOLOv8 + MQTT + Telegram Bot
├── requirements.txt    # Daftar library Python (siap deploy ke Streamlit Cloud)
├── packages.txt        # Dependensi sistem Linux untuk Streamlit Cloud
├── run_demo.bat        # Launcher 1-klik untuk Windows (demo lokal)
└── wokwi/
    ├── sketch.ino      # Firmware ESP32-S3
    ├── diagram.json    # Skematik rangkaian Wokwi
    └── libraries.txt   # Daftar library Arduino/Wokwi
```

## Cara Menjalankan Secara Lokal (Windows)
1. Pastikan Python 3.10+ terinstall dan jalankan `pip install -r requirements.txt`.
2. Klik 2x file **`run_demo.bat`** (atau jalankan `streamlit run app.py` di terminal).
3. Jalankan simulasi ESP32-S3 di **Wokwi** menggunakan file di folder `wokwi/`.
