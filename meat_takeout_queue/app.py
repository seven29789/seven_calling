from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
from datetime import datetime
from threading import Lock

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # セッション用の秘密キー

DATA_FILE = "data/tickets.json"
data_lock = Lock()

# ======================
# ユーティリティ
# ======================
def load_data():
    with data_lock:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

def save_data(data):
    with data_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_defaults(data):
    if "tickets" not in data:
        data["tickets"] = []
    if "last_called" not in data:
        called = [t["number"] for t in data["tickets"] if t.get("status") == "呼び出し"]
        data["last_called"] = max(called) if called else None
    if "intentional_skips" not in data:
        data["intentional_skips"] = []
    if "current_number" not in data:
        data["current_number"] = 0
    if "wait_time_unit" not in data:
        data["wait_time_unit"] = 4
    if "reload_interval" not in data:
        data["reload_interval"] = 60
    if "store_name" not in data:
        data["store_name"] = "味付け焼肉"
    return data

def compute_missing_for_sound(current_num, data):
    if current_num is None:
        return []
    skips = set(data.get("intentional_skips", []))
    miss = [t["number"] for t in data.get("tickets", [])
            if t.get("status") == "受付" and t["number"] < current_num and t["number"] not in skips]
    return sorted(set(miss))

def snapshot_missing_for_ui(data):
    tickets = data.get("tickets", [])
    if not tickets:
        return []
    max_called = data.get("last_called")
    if not isinstance(max_called, int):
        called_nums = [t["number"] for t in tickets if t.get("status") == "呼び出し"]
        if not called_nums:
            return []
        max_called = max(called_nums)
    skips = set(data.get("intentional_skips", []))
    miss = []
    for t in tickets:
        n = t["number"]
        if n >= max_called:
            continue
        if t.get("status") == "受付" and n not in skips:
            miss.append(n)
    return sorted(miss)

# ======================
# ルーティング
# ======================
@app.route("/")
def home():
    return redirect("/monitor_config")

@app.route("/set", methods=["POST"])
def set_config():
    data = load_data()
    data = ensure_defaults(data)
    data["reload_interval"] = int(request.form.get("reload_interval", data.get("reload_interval", 60)))
    data["wait_time_unit"] = int(request.form.get("wait_time_unit", data.get("wait_time_unit", 4)))
    data["store_name"] = request.form.get("store_name", data.get("store_name", "味付け焼肉"))
    save_data(data)
    return redirect(request.referrer or url_for("monitor_config"))

@app.route("/adjust", methods=["POST"])
def adjust_number():
    delta = int(request.form.get("delta"))
    data = load_data()
    data = ensure_defaults(data)
    data["current_number"] += delta
    if data["current_number"] < 1:
        data["current_number"] = 1
    elif data["current_number"] > 999:
        data["current_number"] = 1
    save_data(data)
    return redirect(url_for("monitor_config"))

@app.route("/issue", methods=["POST"])
def issue_ticket():
    """番号札の新規追加"""
    data = load_data()
    data = ensure_defaults(data)
    data["current_number"] += 1
    if data["current_number"] > 999:
        data["current_number"] = 1
    ticket_number = data["current_number"]
    data["tickets"].append({
        "number": ticket_number,
        "status": "受付",
        "scan_count": 0
    })
    save_data(data)
    return redirect(url_for("admin"))

@app.route("/管理")
def admin():
    data = load_data()
    data = ensure_defaults(data)
    save_data(data)
    play_alarm = session.get("play_alarm", False)
    missing    = session.get("missing", [])
    return render_template("admin.html", data=data, play_alarm=play_alarm, missing=missing, update_time=datetime.now().strftime("%Y/%m/%d %H:%M"))

@app.route("/monitor")
def monitor():
    """お客様用モニター画面"""
    data = load_data()
    data = ensure_defaults(data)
    reload_interval = data.get("reload_interval", 60)
    wait_time_unit = data.get("wait_time_unit", 4)
    wait_list = get_waiting_numbers()
    latest = get_latest_number()
    history = sorted(get_called_numbers())
    return render_template(
        "monitor.html",
        latest=latest,
        history=history,
        wait_count=len(wait_list),
        wait_time_unit=wait_time_unit,
        reload_interval=reload_interval,
        store_name=data.get("store_name", "味付け焼肉"),
        update_time=datetime.now().strftime("%Y/%m/%d %H:%M")
    )

@app.route("/handle", methods=["POST"])
def handle_number():
    try:
        number = int(request.form.get("number"))
        if number < 1 or number > 999:
            return redirect(url_for("admin"))
    except (ValueError, TypeError):
        return redirect(url_for("admin"))

    action = request.form.get("action", "auto")
    intentional_flag = (request.form.get("intentional_skip") in ("on", "true", "1"))

    data = load_data()
    data = ensure_defaults(data)
    ticket = next((t for t in data["tickets"] if t["number"] == number), None)

    if action == "delete":
        data["tickets"] = [t for t in data["tickets"] if t["number"] != number]

    elif action == "next":
        if ticket:
            if ticket["scan_count"] == 0:
                new_missing = compute_missing_for_sound(number, data)
                if intentional_flag:
                    for m in new_missing:
                        if m not in data["intentional_skips"]:
                            data["intentional_skips"].append(m)
                else:
                    if new_missing:
                        session["play_alarm"] = True
                        session["missing"] = new_missing
                ticket["scan_count"] = 1
                ticket["status"] = "呼び出し"
                data["last_called"] = number
            else:
                data["tickets"] = [t for t in data["tickets"] if t["number"] != number]

    elif action == "back":
        if ticket and ticket["status"] == "呼び出し":
            ticket["scan_count"] = 0
            ticket["status"] = "受付"

    else:
        if ticket is None:
            data["tickets"].append({
                "number": number,
                "status": "受付",
                "scan_count": 0
            })
        elif ticket["scan_count"] == 0:
            new_missing = compute_missing_for_sound(number, data)
            if intentional_flag:
                for m in new_missing:
                    if m not in data["intentional_skips"]:
                        data["intentional_skips"].append(m)
            else:
                if new_missing:
                    session["play_alarm"] = True
                    session["missing"] = new_missing
            ticket["scan_count"] = 1
            ticket["status"] = "呼び出し"
            data["last_called"] = number
        else:
            data["tickets"] = [t for t in data["tickets"] if t["number"] != number]

    save_data(data)
    return redirect(url_for("admin"))

@app.route("/reset", methods=["POST"])
def reset_tickets():
    data = load_data()
    data = ensure_defaults(data)
    data["tickets"] = []
    data["current_number"] = 0
    data["last_called"] = None
    data["intentional_skips"] = []
    save_data(data)
    session.pop("play_alarm", None)
    session.pop("missing", None)
    return redirect(url_for("admin"))

@app.get("/gap_state")
def gap_state():
    data = load_data()
    data = ensure_defaults(data)
    missing_now = snapshot_missing_for_ui(data)
    save_data(data)
    play_alarm = bool(session.pop("play_alarm", False))
    return jsonify({"ok": True, "playAlarm": play_alarm, "missing": missing_now})

@app.route("/changelog")
def changelog():
    return render_template("changelog.html")

# ======================
# ログイン関連
# ======================
ADMIN_PASSWORD = "niku2929"

@app.route("/monitor_login", methods=["GET", "POST"])
def monitor_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["authenticated"] = True
            return redirect("/monitor_config")
        else:
            return render_template("login.html", error="パスワードが違います")
    return render_template("login.html")

@app.route("/monitor_config")
def monitor_config():
    if not session.get("authenticated"):
        return redirect("/monitor_login")
    data = load_data()
    data = ensure_defaults(data)
    save_data(data)
    return render_template("monitor_config.html", data=data)

# ======================
# 参照関数
# ======================
def get_latest_number():
    data = load_data()
    data = ensure_defaults(data)
    called = [t["number"] for t in data["tickets"] if t["status"] == "呼び出し"]
    return max(called) if called else "---"

def get_called_numbers():
    data = load_data()
    data = ensure_defaults(data)
    return [t["number"] for t in data["tickets"] if t["status"] == "呼び出し"]

def get_waiting_numbers():
    data = load_data()
    data = ensure_defaults(data)
    return [t["number"] for t in data["tickets"] if t["status"] == "受付"]

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(debug=True)
