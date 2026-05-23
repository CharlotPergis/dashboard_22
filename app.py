from flask import Flask, request, jsonify, render_template
import joblib
import os
import time
from flask_cors import CORS
from flask_mail import Mail, Message
from datetime import datetime
from supabase import create_client
import random
import threading

# =========================================================
# FEATURE ENGINE MODULE (simplified)
# =========================================================
temp_buffer = []
current_buffer = []

def build_basic_features(temp, current):
    """Build basic features for ML prediction"""
    import pandas as pd
    
    # Add to buffers
    temp_buffer.append(temp)
    current_buffer.append(current)
    
    # Keep last 10 samples
    if len(temp_buffer) > 10:
        temp_buffer.pop(0)
    if len(current_buffer) > 10:
        current_buffer.pop(0)
    
    # Calculate features
    temp_mean = sum(temp_buffer) / len(temp_buffer) if temp_buffer else temp
    current_mean = sum(current_buffer) / len(current_buffer) if current_buffer else current
    
    temp_trend = temp_buffer[-1] - temp_buffer[0] if len(temp_buffer) >= 2 else 0
    current_trend = current_buffer[-1] - current_buffer[0] if len(current_buffer) >= 2 else 0
    
    # Create DataFrame with features
    df = pd.DataFrame({
        'temperature': [temp],
        'current': [current],
        'temp_mean_10': [temp_mean],
        'current_mean_10': [current_mean],
        'temp_trend': [temp_trend],
        'current_trend': [current_trend],
        'temp_current_ratio': [temp / current if current > 0 else 0],
        'power': [temp * current]
    })
    
    return df

# =========================================================
# FLASK APP INIT
# =========================================================
app = Flask(__name__,
            template_folder='templates',
            static_folder='static')
CORS(app)

latest_data_store = {}

print("🔥 INITIALIZING SYSTEM...")

# Email config
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = 'breaker.monitor.system@gmail.com'
app.config['MAIL_PASSWORD'] = 'kzng lhzr elww gyyu'
app.config['MAIL_DEFAULT_SENDER'] = 'breaker.monitor.system@gmail.com'
app.config['MAIL_DEBUG'] = True

try:
    mail = Mail(app)
    print("✓ Email service initialized")
except Exception as e:
    print(f"✗ Email initialization error: {e}")
    mail = None

# =========================================================
# LOAD MODELS
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Create dummy models if they don't exist
if not os.path.exists(os.path.join(BASE_DIR, "ml/hotspot_model.pkl")):
    print("⚠️ Models not found, creating dummy models...")
    os.makedirs(os.path.join(BASE_DIR, "ml"), exist_ok=True)
    
    from sklearn.ensemble import RandomForestClassifier
    import pandas as pd
    import numpy as np
    
    # Create dummy training data
    X_train = pd.DataFrame({
        'temperature': np.random.rand(100),
        'current': np.random.rand(100),
        'temp_mean_10': np.random.rand(100),
        'current_mean_10': np.random.rand(100),
        'temp_trend': np.random.rand(100),
        'current_trend': np.random.rand(100),
        'temp_current_ratio': np.random.rand(100),
        'power': np.random.rand(100)
    })
    y_train = np.random.randint(0, 2, 100)
    
    dummy_model = RandomForestClassifier()
    dummy_model.fit(X_train, y_train)
    dummy_model.feature_names_in_ = X_train.columns
    
    joblib.dump(dummy_model, os.path.join(BASE_DIR, "ml/hotspot_model.pkl"))
    joblib.dump(dummy_model, os.path.join(BASE_DIR, "ml/overload_model.pkl"))
    print("✓ Dummy models created")

hotspot_model = joblib.load(
    os.path.join(BASE_DIR, "ml/hotspot_model.pkl")
)

overload_model = joblib.load(
    os.path.join(BASE_DIR, "ml/overload_model.pkl")
)

print("✓ Models loaded successfully")

# =========================================================
# FEATURE LOCK
# =========================================================
FEATURE_COLUMNS = hotspot_model.feature_names_in_.tolist()

print("✓ Feature lock loaded")
print("Total features:", len(FEATURE_COLUMNS))

# ----------------------
# Email Alert Function
# ----------------------
def send_breaker_alert(reading, risk, alert_type, time_to_trip=None):
    if mail is None:
        return False, "Email service not configured"

    recipients = ['gwenlykapergis@gmail.com',
                  'mariamonicaragunjanvillaflor@gmail.com',
                  'mercymicadespabiladeras@gmail.com']

    time_to_trip_text = ""
    if time_to_trip and alert_type in ["overheating", "prevention"]:
        time_to_trip_text = f"\nEstimated Time to Trip: {time_to_trip['formatted']}\nUrgency: {time_to_trip['urgency']}"

    if alert_type == "overheating":
        subject = "🔥 CRITICAL: Breaker Overheating Alert!"
        body = f"""IMMEDIATE ACTION REQUIRED

BREAKER OVERHEATING DETECTED!

Temperature: {reading.temperature_c:.1f}°C
Current: {reading.current_a:.1f}A
Hotspot Probability: {risk['hotspot_prob']*100:.1f}%
Overload Probability: {risk['overload_prob']*100:.1f}%
{time_to_trip_text}

Action: Isolate circuit immediately!

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

    elif alert_type == "prevention":
        subject = "⚠️ PREVENTION: Potential Overload Detected!"
        body = f"""PREVENTIVE ACTION RECOMMENDED

POTENTIAL OVERLOAD DEVELOPING!

Temperature: {reading.temperature_c:.1f}°C
Current: {reading.current_a:.1f}A
Hotspot Probability: {risk['hotspot_prob']*100:.1f}%
Overload Probability: {risk['overload_prob']*100:.1f}%
{time_to_trip_text}

Action: Reduce load by 15-20%

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    else:
        return False, "Unknown alert type"

    try:
        msg = Message(
            subject=subject,
            sender=app.config['MAIL_USERNAME'],
            recipients=recipients
        )
        msg.body = body
        mail.send(msg)
        print(f"✓ Email sent: {subject}")
        return True, "Alert sent"
    except Exception as e:
        print(f"✗ Email error: {e}")
        return False, str(e)

# ----------------------
# Alert Tracking
# ----------------------
last_alert_time = {}
ALERT_COOLDOWN_SECONDS = 300

def should_send_alert(alert_type):
    current_time = time.time()
    if alert_type in last_alert_time:
        if current_time - last_alert_time[alert_type] < ALERT_COOLDOWN_SECONDS:
            return False
    last_alert_time[alert_type] = current_time
    return True

# =========================================================
# SYSTEM CONFIG
# =========================================================
WARMUP_SAMPLES = 10
WARNING_THRESHOLD = 0.60
CRITICAL_THRESHOLD = 0.85

# =========================================================
# API
# =========================================================
@app.route("/api/update", methods=["POST"])
def update_data():

    global latest_data_store

    try:
        # RECEIVE DATA
        data = request.json
        temp = float(data["temperature"])
        current = float(data["current"])

        # FEATURE ENGINE
        X = build_basic_features(temp, current)
        X = X.reindex(columns=FEATURE_COLUMNS, fill_value=0)

        # ML PREDICTION
        hot_prob = hotspot_model.predict_proba(X)[0][1]
        ovl_prob = overload_model.predict_proba(X)[0][1]

        composite_risk = (hot_prob + ovl_prob) / 2

        # =================================================
        # ENGINEERING STATE LOGIC
        # =================================================

        # WARMUP
        if len(temp_buffer) < WARMUP_SAMPLES:

            state = "WarmingUp"
            status = "COLLECTING DATA"

        # CRITICAL
        elif hot_prob >= CRITICAL_THRESHOLD:

            state = "Critical"
            status = "HOTSPOT CRITICAL"

            if should_send_alert("critical_hotspot"):
                send_breaker_alert(
                    reading=type("obj", (object,), {
                        "temperature_c": temp,
                        "current_a": current
                    }),
                    risk={
                        "hotspot_prob": hot_prob,
                        "overload_prob": ovl_prob
                    },
                    alert_type="overheating"
                )

        elif ovl_prob >= CRITICAL_THRESHOLD:

            state = "Critical"
            status = "OVERLOAD CRITICAL"

            if should_send_alert("critical_overload"):
                send_breaker_alert(
                    reading=type("obj", (object,), {
                        "temperature_c": temp,
                        "current_a": current
                    }),
                    risk={
                        "hotspot_prob": hot_prob,
                        "overload_prob": ovl_prob
                    },
                    alert_type="overheating"
                )

        # WARNING
        elif hot_prob >= WARNING_THRESHOLD:

            state = "Warning"
            status = "HOTSPOT WARNING"

            if should_send_alert("warning_hotspot"):
                send_breaker_alert(
                    reading=type("obj", (object,), {
                        "temperature_c": temp,
                        "current_a": current
                    }),
                    risk={
                        "hotspot_prob": hot_prob,
                        "overload_prob": ovl_prob
                    },
                    alert_type="prevention"
                )

        elif ovl_prob >= WARNING_THRESHOLD:

            state = "Warning"
            status = "OVERLOAD WARNING"

            if should_send_alert("warning_overload"):
                send_breaker_alert(
                    reading=type("obj", (object,), {
                        "temperature_c": temp,
                        "current_a": current
                    }),
                    risk={
                        "hotspot_prob": hot_prob,
                        "overload_prob": ovl_prob
                    },
                    alert_type="prevention"
                )

        # NORMAL
        else:
            state = "Normal"
            status = "SYSTEM NORMAL"

        # STORE DATA
        latest_data_store = {
            "temperature": round(temp, 2),
            "current": round(current, 2),
            "breakerState": state,
            "status": status,
            "ml": {
                "hotspot_prob": round(float(hot_prob), 4),
                "overload_prob": round(float(ovl_prob), 4),
                "composite_risk": round(float(composite_risk), 4)
            },
            "buffer_size": len(temp_buffer),
            "time": datetime.now().strftime("%H:%M:%S")
        }

        print(
            f"[{state}] T={temp:.2f}C | I={current:.2f}A | "
            f"HP={hot_prob:.3f} | OP={ovl_prob:.3f}"
        )

        return jsonify({
            "success": True,
            "state": state,
            "status": status,
            "ml": {
                "hotspot_prob": round(float(hot_prob), 4),
                "overload_prob": round(float(ovl_prob), 4),
                "composite_risk": round(float(composite_risk), 4)
            }
        })

    except Exception as e:
        print("API ERROR:", e)
        return jsonify({"success": False, "error": str(e)})

# =========================================================
# WEB ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/latest-data")
def latest():
    return jsonify(latest_data_store)

@app.route("/full_history.html")
def full_history():
    return render_template("full_history.html")

# =========================================================
# HEALTH CHECK
# =========================================================
@app.route("/api/health")
def health():
    return jsonify({
        "status": "online",
        "models_loaded": True,
        "buffer_size": len(temp_buffer)
    })

# =========================================================
# SUPABASE SIMULATOR THREAD
# =========================================================
def supabase_simulator():
    """Simulate sending data to Supabase"""
    
    SUPABASE_URL = "https://qkniqwgcwvxkgjciccad.supabase.co"
    SUPABASE_KEY = "sb_publishable_pzHW1LlymSCVL876qchBKw_pPY0xN-2"
    
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✓ Supabase client initialized")
    except Exception as e:
        print(f"✗ Supabase initialization error: {e}")
        return
    
    def read_temperature():
        return 25 + random.uniform(-5, 15)
    
    def read_current():
        return 15 + random.uniform(-5, 25)
    
    print("="*50)
    print("🚀 Breaker Monitor - Sending to Supabase (FAST MODE)")
    print(f"📡 URL: {SUPABASE_URL}")
    print("="*50)
    
    success = 0
    errors = 0
    
    while True:
        try:
            temp = read_temperature()
            current = read_current()
            
            # Determine state
            if temp > 75 or current > 45:
                state = "Overheating"
                hot_prob = 0.92
                ovl_prob = 0.88
            elif temp > 60 or current > 35:
                state = "Overload"
                hot_prob = 0.78
                ovl_prob = 0.72
            elif temp > 50 or current > 28:
                state = "Potential Overload"
                hot_prob = 0.58
                ovl_prob = 0.52
            else:
                state = "Normal"
                hot_prob = 0.12
                ovl_prob = 0.10
            
            composite = (hot_prob + ovl_prob) / 2
            
            data = {
                "created_at": datetime.now().isoformat(),
                "temperature_c": round(temp, 2),
                "current_a": round(current, 2),
                "breaker_state": state,
                "hotspot_probability": round(hot_prob, 3),
                "overload_probability": round(ovl_prob, 3),
                "composite_risk": round(composite, 3)
            }
            
            response = supabase.table("breaker_readings").insert(data).execute()
            
            success += 1
            print(f"✅ [{success}] Sent to Supabase: {temp:.1f}°C, {current:.1f}A, {state}")
            
        except Exception as e:
            errors += 1
            print(f"❌ Supabase Error: {e}")
        
        time.sleep(1)  # Sends every 1 second

# =========================================================
# RUN SERVER
# =========================================================
if __name__ == "__main__":

    print("===================================")
    print("⚡ SMART PANEL MONITORING SYSTEM")
    print("🔥 Predictive ML Protection Enabled")
    print("===================================")
    
    # Start Supabase simulator in a separate thread
    supabase_thread = threading.Thread(target=supabase_simulator, daemon=True)
    supabase_thread.start()
    print("✓ Supabase simulator thread started")
    
    # Run Flask app
    app.run(host="0.0.0.0", port=5000, debug=False)
