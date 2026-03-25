from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
import tempfile
from datetime import datetime
from threading import RLock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "tickets.json")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-for-production")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "niku2929")

data_lock = RLock()


# ======================
# 初期データ
# ======================
def default_data():
    return {
        "tickets": [],
        "last_called": None,
        "intentional_skips": [],
        "current_number": 0,
        "wait_time_unit": 4,
        "reload_interval": 60,
        "store_name": "味付け焼肉"
    }


# ======================
# ユーティリティ
# ======================
def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def ensure_defaults(data):
    if not isinstance(data, dict):
        data = {}

    if "tickets" not in data or not isinstance(data["tickets"], list):
        data["tickets"] = []

    normalized_tickets = []
    for t in data["tickets"]:
        if not isinstance(t, dict):
            continue

        try:
            number = int(t.get("number"))
        except (TypeError, ValueError):
            continue

        if number < 1 or number > 999:
            continue

        status = t.get("status", "受付")
        if status not in ("受付", "呼び出し"):
            status = "受付"

        try:
            scan_count = int(t.get("scan_count", 0))
        except (TypeError, ValueError):
            scan_count = 0

        if scan_count not in (0, 1):
            scan_count = 1 if status == "呼び出し" else 0

        if status == "呼び出し":
            scan_count = 1

        normalized_tickets.append({
            "number": number,
            "status": status,
            "scan_count": scan_count
        })

    unique_map = {}
    for t in normalized_tickets:
        unique_map[t["number"]] = t
    data["tickets"] = sorted(unique_map.values(), key=lambda x: x["number"])

    if "last_called" not in data or not isinstance(data["last_called"], int):
        called = [t["number"] for t in data["tickets"] if t.get("status") == "呼び出し"]
        data["last_called"] = max(called) if called else None

    if "intentional_skips" not in data or not isinstance(data["intentional_skips"], list):
        data["intentional_skips"] = []

    cleaned_skips = []
    seen = set()
    for n in data["intentional_skips"]:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 999 and n not in seen:
            seen.add(n)
            cleaned_skips.append(n)
    data["intentional_skips"] = sorted(cleaned_skips)

    try:
        current_number = int(data.get("current_number", 0))
    except (TypeError, ValueError):
        current_number = 0
    if current_number < 0:
        current_number = 0
    if current_number > 999:
        current_number = current_number % 1000
    data["current_number"] = current_number

    try:
        wait_time_unit = int(data.get("wait_time_unit", 4))
    except (TypeError, ValueError):
        wait_time_unit = 4
    if wait_time_unit < 1:
        wait_time_unit = 1
    data["wait_time_unit"] = wait_time_unit

    try:
        reload_interval = int(data.get("reload_interval", 60))
    except (TypeError, ValueError):
        reload_interval = 60
    if reload_interval < 5:
        reload_interval = 5
    if reload_interval > 600:
        reload_interval = 600
    data["reload_interval"] = reload_interval

    store_name = str(data.get("store_name", "味付け焼肉")).strip()
    data["store_name"] = store_name or "味付け焼肉"

    return data


def write_json_atomic(path, data):
    ensure_data_dir()
    fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, prefix="tickets_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_data():
    with data_lock:
        ensure_data_dir()

        if not os.path.exists(DATA_FILE):
            data = ensure_defaults(default_data())
            write_json_atomic(DATA_FILE, data)
            return data

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = ensure_defaults(default_data())
            write_json_atomic(DATA_FILE, data)
            return data

        data = ensure_defaults(data)
        return data


def save_data(data):
    with data_lock:
        data = ensure_defaults(data)
        write_json_atomic(DATA_FILE, data)


def compute_missing_for_sound(current_num, data):
    if current_num is None:
        return []

    skips = set(data.get("intentional_skips", []))
    missing = [
        t["number"]
        for t in data.get("tickets", [])
        if t.get("status") == "受付"
        and t["number"] < current_num
        and t["number"] not in skips
    ]
    return sorted(set(missing))


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
    missing = []
    for t in tickets:
        n = t["number"]
        if n >= max_called:
            continue
        if t.get("status") == "受付" and n not in skips:
            missing.append(n)

    return sorted(set(missing))


def get_latest_number():
    data = load_data()
    called = [t["number"] for t in data["tickets"] if t["status"] == "呼び出し"]
    return max(called) if called else "---"


def get_called_numbers():
    data = load_data()
    return [t["number"] for t in data["tickets"] if t["status"] == "呼び出し"]


def get_waiting_numbers():
    data = load_data()
    return [t["number"] for t in data["tickets"] if t["status"] == "受付"]


def normalize_number(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if 1 <= n <= 999:
        return n
    return None


# ======================
# ルーティング
# ======================
@app.route("/")
def home():
    return redirect("/monitor_config")


@app.route("/health")
def health():
    return "ok", 200


@app.route("/set", methods=["POST"])
def set_config():
    data = load_data()

    data["reload_interval"] = normalize_int(
        request.form.get("reload_interval"),
        default=data.get("reload_interval", 60),
        minimum=5,
        maximum=600
    )
    data["wait_time_unit"] = normalize_int(
        request.form.get("wait_time_unit"),
        default=data.get("wait_time_unit", 4),
        minimum=1,
        maximum=999
    )

    store_name = request.form.get("store_name", data.get("store_name", "味付け焼肉"))
    data["store_name"] = str(store_name).strip() or "味付け焼肉"

    save_data(data)
    return redirect(request.referrer or url_for("monitor_config"))


@app.route("/adjust", methods=["POST"])
def adjust_number():
    delta = normalize_int(request.form.get("delta"), default=0, minimum=-999, maximum=999)
    data = load_data()

    current = int(data.get("current_number", 0)) + delta
    if current < 1:
        current = 1
    elif current > 999:
        current = 1

    data["current_number"] = current
    save_data(data)
    return redirect(url_for("monitor_config"))


@app.route("/issue", methods=["POST"])
def issue_ticket():
    data = load_data()

    next_number = int(data.get("current_number", 0)) + 1
    if next_number > 999:
        next_number = 1

    data["current_number"] = next_number
    ticket_number = next_number

    existing = next((t for t in data["tickets"] if t["number"] == ticket_number), None)
    if existing:
        existing["status"] = "受付"
        existing["scan_count"] = 0
    else:
        data["tickets"].append({
            "number": ticket_number,
            "status": "受付",
            "scan_count": 0
        })

    data["tickets"] = sorted(data["tickets"], key=lambda x: x["number"])
    save_data(data)
    return redirect(url_for("admin"))


@app.route("/管理")
def admin():
    data = load_data()
    play_alarm = session.get("play_alarm", False)
    missing = session.get("missing", [])
    return render_template(
        "admin.html",
        data=data,
        play_alarm=play_alarm,
        missing=missing,
        update_time=datetime.now().strftime("%Y/%m/%d %H:%M")
    )


@app.route("/monitor")
def monitor():
    data = load_data()
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
    number = normalize_number(request.form.get("number"))
    if number is None:
        return redirect(url_for("admin"))

    action = request.form.get("action", "auto")
    intentional_flag = request.form.get("intentional_skip") in ("on", "true", "1")

    data = load_data()
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

            called_nums = [t["number"] for t in data["tickets"] if t.get("status") == "呼び出し" and t["number"] != number]
            data["last_called"] = max(called_nums) if called_nums else None

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

    data["intentional_skips"] = sorted(set(data.get("intentional_skips", [])))
    data["tickets"] = sorted(data["tickets"], key=lambda x: x["number"])
    save_data(data)
    return redirect(url_for("admin"))


@app.route("/reset", methods=["POST"])
def reset_tickets():
    data = load_data()
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
    missing_now = snapshot_missing_for_ui(data)
    play_alarm = bool(session.pop("play_alarm", False))
    return jsonify({
        "ok": True,
        "playAlarm": play_alarm,
        "missing": missing_now
    })


@app.route("/changelog")
def changelog():
    return render_template("changelog.html")


# ======================
# ログイン関連
# ======================
@app.route("/monitor_login", methods=["GET", "POST"])
def monitor_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["authenticated"] = True
            return redirect("/monitor_config")
        return render_template("login.html", error="パスワードが違います")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect("/monitor_login")


@app.route("/monitor_config")
def monitor_config():
    if not session.get("authenticated"):
        return redirect("/monitor_login")

    data = load_data()
    return render_template("monitor_config.html", data=data)


# ======================
# 補助関数
# ======================
def normalize_int(value, default=0, minimum=None, maximum=None):
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = default

    if minimum is not None and num < minimum:
        num = minimum
    if maximum is not None and num > maximum:
        num = maximum
    return num


# ======================
# 起動
# ======================
ensure_data_dir()
try:
    save_data(load_data())
except Exception:
    pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
