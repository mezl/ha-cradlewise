"""Cradlewise YAMNet cry detector — runs inside the `cradlewise_yamnet` HAOS add-on.

Lives in the component dir (not baked into the image) so it's edited via a simple
rsync + add-on restart. Reuses the deployed pure classifier + model (yamnet.py,
yamnet.tflite, yamnet_class_map.csv) sitting next to this file.

Reads the crib's live WAV audio from Home Assistant, runs YAMNet, and publishes
state to HA over MQTT with discovery. Runs on a Debian/glibc base so
ai-edge-litert installs (HA Core's Alpine/musl base cannot).
"""
import json
import os
import signal
import subprocess
import sys
import time

import numpy as np
import requests
import paho.mqtt.client as mqtt

COMPONENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, COMPONENT_DIR)
import yamnet  # noqa: E402

SUPERVISOR = "http://supervisor"
SUP_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""

DISC = "homeassistant"
BASE = "cradlewise_yamnet"
T_STATUS = f"{BASE}/status"
T_CRYING = f"{BASE}/crying/state"
T_PROB = f"{BASE}/prob/state"
DEVICE = {
    "identifiers": ["cradlewise_yamnet"],
    "name": "Cradlewise YAMNet",
    "manufacturer": "crywatch / YAMNet",
    "model": "AudioSet cry classifier",
}


def log(*a):
    print(*a, flush=True)


def options() -> dict:
    try:
        with open("/data/options.json") as f:
            return json.load(f)
    except Exception:
        return {}


def mqtt_config(opts: dict) -> dict:
    """Manual MQTT creds from options if given, else auto from the Supervisor service."""
    if opts.get("mqtt_host"):
        log("MQTT: using creds from add-on options")
        return {
            "host": opts["mqtt_host"], "port": int(opts.get("mqtt_port") or 1883),
            "username": opts.get("mqtt_username") or None,
            "password": opts.get("mqtt_password") or None, "ssl": False,
        }
    log(f"MQTT: querying Supervisor service (token present={bool(SUP_TOKEN)})")
    r = requests.get(f"{SUPERVISOR}/services/mqtt",
                     headers={"Authorization": f"Bearer {SUP_TOKEN}"}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]


def publish_discovery(client: mqtt.Client) -> None:
    avail = {"availability_topic": T_STATUS,
             "payload_available": "online", "payload_not_available": "offline"}
    client.publish(f"{DISC}/binary_sensor/{BASE}/crying/config", json.dumps({
        "name": "Baby Crying (YAMNet)",
        "object_id": "cradlewise_avo_baby_crying_yamnet",
        "unique_id": "cradlewise_yamnet_crying",
        "device_class": "sound", "state_topic": T_CRYING,
        "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:brain",
        "device": DEVICE, **avail,
    }), retain=True)
    client.publish(f"{DISC}/sensor/{BASE}/prob/config", json.dumps({
        "name": "Cry Probability (YAMNet)",
        "object_id": "cradlewise_avo_cry_probability_yamnet",
        "unique_id": "cradlewise_yamnet_prob",
        "unit_of_measurement": "%", "state_class": "measurement",
        "state_topic": T_PROB, "icon": "mdi:emoticon-cry-outline",
        "device": DEVICE, **avail,
    }), retain=True)


def build_classifier(opts: dict) -> yamnet.CryClassifier:
    model = os.path.join(COMPONENT_DIR, "yamnet.tflite")
    class_map = os.path.join(COMPONENT_DIR, "yamnet_class_map.csv")
    if not (os.path.exists(model) and os.path.exists(class_map)):
        raise SystemExit(f"Model files missing under {COMPONENT_DIR}")
    scorer = yamnet.YamnetScorer(model)
    indices = yamnet.load_class_indices(class_map)
    if not indices:
        raise SystemExit("No target cry classes matched in yamnet_class_map.csv")
    return yamnet.CryClassifier(
        scorer, indices,
        threshold=float(opts.get("threshold", 0.4)),
        sustain_on=int(opts.get("sustain_on", 2)),
        sustain_off=int(opts.get("sustain_off", 4)),
    )


def audio_url(opts: dict) -> tuple[str, str]:
    base = (opts.get("ha_base_url") or f"{SUPERVISOR}/core").rstrip("/")
    token = opts.get("ha_token") or SUP_TOKEN
    return base + "/api/cradlewise_avo/audio", token


def stream_forever(clf: yamnet.CryClassifier, client: mqtt.Client, opts: dict) -> None:
    url, token = audio_url(opts)
    last_pub = None
    while True:
        try:
            log(f"Connecting to crib audio: {url}")
            with requests.get(url, headers={"Authorization": f"Bearer {token}"},
                              stream=True, timeout=(10, 60)) as r:
                r.raise_for_status()
                log(f"audio stream open (HTTP {r.status_code})")
                dropped_header = False
                carry = b""
                nchunks = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if not dropped_header:
                        chunk = chunk[44:]
                        dropped_header = True
                    buf = carry + chunk
                    n = (len(buf) // 4) * 4
                    carry = buf[n:]
                    if n == 0:
                        continue
                    a = np.frombuffer(buf[:n], dtype="<i2").astype(np.float32) / 32768.0
                    mono = (a[0::2] + a[1::2]) * 0.5
                    changed, prob = clf.feed(mono, 48000)
                    pct = round(prob * 100.0, 1)
                    if pct != last_pub:
                        client.publish(T_PROB, pct, retain=True)
                        last_pub = pct
                    if changed is not None:
                        client.publish(T_CRYING, "ON" if changed else "OFF", retain=True)
                        log(f"crying -> {changed} (p={pct}%)")
                    nchunks += 1
                    if nchunks % 200 == 0:
                        log(f"audio alive: {nchunks} chunks, prob={pct}%")
        except Exception as e:
            log(f"audio stream error ({e}); retrying in 3s")
        time.sleep(3)


def stream_ffmpeg(clf: yamnet.CryClassifier, client: mqtt.Client, source: str) -> None:
    """Read audio through ffmpeg instead of the Cradlewise WAV endpoint.

    Why: the Cradlewise audio endpoint still answers 200 with a valid RIFF header but ships ALL-ZERO PCM (its AWS
    IoT link is refused), so YAMNet scored pure silence -- 961k chunks at exactly 0.0%. The G6 over the crib has
    real audio, and its native rate is already 16 kHz = YAMNet's input rate, so ffmpeg hands us exactly what the
    model wants and NO resampling happens at all. That also drops the old linear-interp downsample, which had no
    anti-alias filter (48k->16k folded everything above 8 kHz back into the band).
    """
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    last_pub = None
    while True:
        proc = None
        try:
            log(f"Starting ffmpeg for: {source}")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            nchunks = 0
            while True:
                raw = proc.stdout.read(4096)          # 2048 samples @16 kHz = 128 ms
                if not raw:
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
                    raise RuntimeError(f"ffmpeg ended{': ' + err[:200] if err else ''}")
                mono = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                changed, prob = clf.feed(mono, 16000)   # already 16 kHz -> feed() is a no-op resample
                pct = round(prob * 100.0, 1)
                if pct != last_pub:
                    client.publish(T_PROB, pct, retain=True)
                    last_pub = pct
                if changed is not None:
                    client.publish(T_CRYING, "ON" if changed else "OFF", retain=True)
                    log(f"crying -> {changed} (p={pct}%)")
                nchunks += 1
                if nchunks % 500 == 0:
                    rms = float(np.sqrt((mono ** 2).mean()))
                    # rms is logged because an all-zero stream is exactly how the previous source failed silently
                    log(f"audio alive: {nchunks} chunks, prob={pct}%, rms={rms:.5f}")
        except Exception as e:
            log(f"ffmpeg audio error ({e}); retrying in 5s")
        finally:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        time.sleep(5)


def main() -> None:
    opts = options()
    log("Options: " + json.dumps({**opts, "ha_token": "***" if opts.get("ha_token") else ""}))
    clf = build_classifier(opts)
    log(f"YAMNet loaded; target class indices {clf._class_indices}")

    cfg = mqtt_config(opts)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cradlewise_yamnet")
    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password"))
    if cfg.get("ssl"):
        client.tls_set()
    client.will_set(T_STATUS, "offline", retain=True)

    def _shutdown(*_):
        try:
            client.publish(T_STATUS, "offline", retain=True)
            client.loop_stop()
        finally:
            sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log(f"Connecting MQTT {cfg['host']}:{cfg['port']}")
    client.connect(cfg["host"], int(cfg["port"]), keepalive=60)
    client.loop_start()
    publish_discovery(client)
    client.publish(T_STATUS, "online", retain=True)
    client.publish(T_CRYING, "OFF", retain=True)   # initial state so the sensor isn't "unknown"
    client.publish(T_PROB, 0.0, retain=True)
    src = (opts.get("audio_source") or "").strip()
    if src:
        stream_ffmpeg(clf, client, src)
    else:
        stream_forever(clf, client, opts)


if __name__ == "__main__":
    main()
