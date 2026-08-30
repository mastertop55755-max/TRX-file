import telebot
import json
import os
import time
import threading
import uuid
import secrets
from urllib.parse import urlparse

# =========================================================
# CONFIG
# Version: TRX_bot_v13
# =========================================================
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN", "8596881560:AAHWDUwOOE7tnwCi9zWe356p1QTdaKlfWVE")
ASSISTANT_BOT_TOKEN = os.getenv("ASSISTANT_BOT_TOKEN", "8501706191:AAGbmXOenwq4_jaFTJWzWAIpGLaG83JBVDE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6739154221"))
ASSISTANT_USERNAME = os.getenv("ASSISTANT_USERNAME", "hdhdidhydudbot")
DATA_FILE = os.getenv("DATA_FILE", "data.json")

main_bot = telebot.TeleBot(MAIN_BOT_TOKEN, parse_mode="HTML")
assistant_bot = telebot.TeleBot(ASSISTANT_BOT_TOKEN, parse_mode="HTML")
data_lock = threading.RLock()

# =========================================================
# DATABASE
# =========================================================
def default_data():
    return {
        "users": {}, "channels": {}, "orders": {}, "waiting": {}, "codes": {}, "products": {}, "purchases": {}, "categories": {},
        "settings": {
            "join_points": 5, "ref_points": 10, "daily_gift": 50,
            "min_fund": 10, "max_fund": 5000, "points_per_member": 5,
            "entry_notifications": True, "maintenance": False,
            "mandatory_channels": [],
            "transfer_min": 1,
            "transfer_max": 1000000,
            "code_min": 1,
            "leaver_check_interval": 300
        }
    }

def save_data():
    with data_lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

def load_data():
    base = default_data()
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        return base
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, v in base.items():
            if k not in d: d[k] = v.copy() if isinstance(v, dict) else v
        d.setdefault("waiting", {})
        d.setdefault("channels", {})
        d.setdefault("orders", {})
        d.setdefault("users", {})
        d.setdefault("categories", {})
        # migrate old products to a default category
        if not d["categories"]:
            d["categories"]["general"] = {"id":"general","name":"عام","active":True,"created_at":time.time()}
        for _p in d.get("products", {}).values():
            _p.setdefault("category_id", "general")
        d.setdefault("codes", {})
        d.setdefault("products", {})
        d.setdefault("purchases", {})
        d.setdefault("settings", {})
        for k, v in base["settings"].items(): d["settings"].setdefault(k, v)
        for o in d.get("orders", {}).values():
            o.setdefault("rewarded_members", list(o.get("members", [])))
        return d
    except Exception as e:
        print("Database error:", repr(e))
        return base

data = load_data()

# =========================================================
# HELPERS
# =========================================================
def new_user():
    return {"points": 0, "joined": [], "orders": [], "ref_by": None,
            "refs": 0, "last_gift": 0, "channels": [], "transfers_sent": 0,
            "transfers_received": 0, "redeemed_codes": [], "purchases": []}

def get_user(uid):
    uid = str(uid)
    with data_lock:
        if uid not in data["users"]:
            data["users"][uid] = new_user()
            save_data()
        u = data["users"][uid]
        u.setdefault("points", 0); u.setdefault("joined", []); u.setdefault("orders", [])
        u.setdefault("ref_by", None); u.setdefault("refs", 0); u.setdefault("last_gift", 0)
        u.setdefault("channels", [])
        u.setdefault("transfers_sent", 0)
        u.setdefault("transfers_received", 0)
        u.setdefault("redeemed_codes", [])
        u.setdefault("purchases", [])
        return u

def is_admin(uid):
    try: return int(uid) == ADMIN_ID
    except: return False

def maintenance(): return bool(data["settings"].get("maintenance", False))
def entry_notify(): return bool(data["settings"].get("entry_notifications", True))
def mandatory_channels():
    x = data["settings"].get("mandatory_channels", [])
    return x if isinstance(x, list) else []

def clean_channel(text):
    if not text: return None
    text = text.strip()
    if text.startswith("@"):
        u = text[1:].split()[0]
        return "@" + u if u else None
    if "t.me/" in text:
        try:
            p = urlparse(text).path.strip("/")
            if not p or p.startswith("+"): return None
            return "@" + p.split("/")[0]
        except: return None
    return None

def create_channel_invite_link(channel_id):
    """Create a reusable invite link for public or private channels."""
    try:
        # Bot must be an administrator with invite-user permission.
        link_obj = assistant_bot.create_chat_invite_link(
            channel_id,
            name="Funding",
            creates_join_request=False
        )
        return getattr(link_obj, "invite_link", None)
    except Exception as e:
        print("create_channel_invite_link:", repr(e))
        return None

def get_channel_info(username):
    try:
        c = assistant_bot.get_chat(username)
        return {"id": str(c.id), "title": c.title or "بدون اسم",
                "username": c.username or "", "link": f"https://t.me/{c.username}" if c.username else None}
    except Exception as e:
        print("get_channel_info:", repr(e)); return None

def assistant_permissions(channel_id):
    try:
        me = assistant_bot.get_me()
        m = assistant_bot.get_chat_member(channel_id, me.id)
        if m.status == "creator": return True, "OK"
        if m.status != "administrator": return False, "❌ البوت المساعد ليس مشرفًا."
        if not getattr(m, "can_invite_users", False):
            return False, "❌ فعّل للبوت المساعد صلاحية إضافة المشتركين."
        return True, "OK"
    except Exception as e:
        print("assistant_permissions:", repr(e)); return False, "❌ تعذر فحص صلاحيات البوت المساعد."

def is_channel_owner(channel_id, uid):
    try:
        m = assistant_bot.get_chat_member(channel_id, uid)
        return m.status in ("administrator", "creator")
    except: return False

def check_subscription(channel_id, uid):
    try:
        m = assistant_bot.get_chat_member(channel_id, uid)
        return m.status in ("member", "administrator", "creator")
    except: return False

def mandatory_check(uid):
    missing = [c for c in mandatory_channels() if not check_subscription(c.get("id"), uid)]
    return not missing, missing

def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")



def safe_edit_message(text, chat_id, message_id, reply_markup=None, **kwargs):
    """Edit a message without crashing on Telegram's 'message is not modified'."""
    try:
        return main_bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, **kwargs)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return None
        try:
            return main_bot.send_message(chat_id, text, reply_markup=reply_markup, **kwargs)
        except Exception as e2:
            print("safe_edit_message:", repr(e2))
            return None

def transfer_kb(back=True):
    kb = telebot.types.InlineKeyboardMarkup()
    if back:
        kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية", callback_data="home"))
    return kb

def generate_recharge_code(length=10):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "TRX-" + "".join(secrets.choice(alphabet) for _ in range(length))

def create_recharge_code(points, created_by, max_uses=1):
    code = generate_recharge_code()
    while code in data["codes"]:
        code = generate_recharge_code()
    data["codes"][code] = {
        "code": code, "points": int(points), "max_uses": int(max_uses),
        "uses": 0, "used_by": [], "created_by": int(created_by), "created_at": time.time(),
        "active": True
    }
    return code

def funded_orders_for_user(uid):
    return [o for o in data["orders"].values() if int(o.get("owner_id", -1)) == int(uid)]

def get_valid_owned_channel(user_id):
    """إرجاع آخر قناة ما زال البوت المساعد موجودًا فيها والمستخدم مالكها."""
    user = get_user(user_id)
    channels = user.get("channels", [])
    valid = []
    for ch in channels:
        cid = str(ch.get("id", ""))
        if not cid:
            continue
        try:
            me = assistant_bot.get_me()
            bot_member = assistant_bot.get_chat_member(cid, me.id)
            if bot_member.status not in ("administrator", "creator"):
                continue
            owner = assistant_bot.get_chat_member(cid, user_id)
            if owner.status == "creator":
                valid.append(ch)
        except Exception:
            continue
    return valid[-1] if valid else None

def is_channel_creator(channel_id, user_id):
    """Return True only when the user is the actual channel owner (creator)."""
    try:
        member = assistant_bot.get_chat_member(str(channel_id), int(user_id))
        return member.status == "creator"
    except Exception:
        return False

def verified_owner_channels(uid):
    """القنوات/المجموعات المسجلة التي يكون المستخدم Creator فيها والبوت المساعد موجودًا.
    نعتمد على الدردشات التي سبق أن أرسلها Telegram للبوت، ثم نتحقق من المالك لحظيًا.
    """
    result = []
    seen = set()
    candidates = list(data.get("channels", {}).values())
    # أضف أيضًا القنوات الموجودة في حساب المستخدم القديم لضمان توافق البيانات.
    candidates += list(get_user(uid).get("channels", []))
    for ch in candidates:
        cid = str(ch.get("id", ""))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        try:
            bot_me = assistant_bot.get_me()
            bot_member = assistant_bot.get_chat_member(cid, bot_me.id)
            if bot_member.status not in ("administrator", "creator"):
                continue
            owner = assistant_bot.get_chat_member(cid, int(uid))
            if owner.status != "creator":
                continue
            info = assistant_bot.get_chat(cid)
            item = dict(ch)
            item.update({
                "id": cid,
                "title": getattr(info, "title", None) or item.get("title", "بدون اسم"),
                "username": getattr(info, "username", None) or item.get("username", ""),
                "type": getattr(info, "type", None) or item.get("type", "channel")
            })
            if item.get("username"):
                item["link"] = f"https://t.me/{item['username']}"
            elif not item.get("invite_link"):
                item["invite_link"] = create_channel_invite_link(cid)
            result.append(item)
        except Exception as e:
            # لا تطبع 403 بشكل مزعج لكل دورة؛ فقط تجاهل الدردشة غير المتاحة.
            continue
    return result

def user_channels_for(uid):
    return get_user(uid).get("channels", [])

def format_duration(ts):
    if not ts:
        return "غير معروف"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))

def transfer_screen(chat_id, message_id=None):
    u = get_user(chat_id)
    text = (
        "🔄 <b>تحويل نقاط</b>\n\n"
        f"💰 رصيدك الحالي: <b>{u['points']}</b> نقطة\n\n"
        f"الحد الأدنى: <b>{data['settings'].get('transfer_min', 1)}</b> نقطة\n"
        f"الحد الأقصى: <b>{data['settings'].get('transfer_max', 1000000)}</b> نقطة\n\n"
        "أرسل <b>ID المستخدم</b> الذي تريد التحويل إليه."
    )
    kb = transfer_kb()
    if message_id:
        try:
            main_bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
            return
        except Exception:
            pass
    main_bot.send_message(chat_id, text, reply_markup=kb)

def recharge_screen(chat_id, message_id=None):
    text = (
        "🎟️ <b>كود شحن</b>\n\n"
        "إذا كان لديك كود شحن، أرسله هنا كما هو.\n"
        "سيتم التحقق منه وإضافة النقاط إلى رصيدك تلقائيًا."
    )
    kb = transfer_kb()
    if message_id:
        try:
            main_bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
            return
        except Exception:
            pass
    main_bot.send_message(chat_id, text, reply_markup=kb)

def my_funds_screen(chat_id, message_id=None):
    orders = funded_orders_for_user(chat_id)
    channels = user_channels_for(chat_id)
    u = get_user(chat_id)

    lines = ["📂 <b>تمويلاتي وحساباتي</b>", ""]
    lines.append(f"💰 الرصيد: <b>{u['points']}</b> نقطة")
    lines.append(f"📢 القنوات المسجلة: <b>{len(channels)}</b>")
    lines.append(f"📦 حملات التمويل: <b>{len(orders)}</b>")
    lines.append("")

    active_orders = [o for o in orders if o.get("status") == "active"]
    if orders:
        lines.append("<b>تمويلاتك:</b>")
        for o in orders[-10:][::-1]:
            status = "🟢 نشط" if o.get("status") == "active" else (
                "✅ مكتمل" if o.get("status") == "completed" else "🛑 متوقف"
            )
            lines.append(
                f"• {esc(o.get('channel_title','قناة'))} — "
                f"{o.get('completed',0)}/{o.get('target',0)} — {status}"
            )
    else:
        lines.append("لا توجد تمويلات حتى الآن.")

    kb = telebot.types.InlineKeyboardMarkup(row_width=1)

    # Individual management buttons
    for o in active_orders[-10:][::-1]:
        title = o.get("channel_title", "القناة")
        oid = o.get("id")
        kb.add(
            telebot.types.InlineKeyboardButton(
                f"🛑 إيقاف: {title[:28]}",
                callback_data=f"fund_stop:{oid}"
            )
        )

    if active_orders:
        kb.add(
            telebot.types.InlineKeyboardButton(
                "🛑 إيقاف كل التمويلات",
                callback_data="fund_stop_all_confirm"
            )
        )

    if channels:
        lines.append("\n<b>قنواتك:</b>")
        for ch in channels[-10:][::-1]:
            title = esc(ch.get("title", "بدون اسم"))
            username = ch.get("username", "")
            lines.append(f"• {title} {esc('@'+username if username else '🔒 خاصة')}")

    kb.add(telebot.types.InlineKeyboardButton("📜 سجل العمليات", callback_data="history"))
    kb.add(telebot.types.InlineKeyboardButton("🔄 تحديث", callback_data="my_funds"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية", callback_data="home"))

    if message_id:
        try:
            main_bot.edit_message_text(
                "\n".join(lines), chat_id, message_id, reply_markup=kb
            )
            return
        except Exception:
            pass
    main_bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)

def refund_remaining_funding(order):
    """Refund only the unused part of a funding campaign."""
    if order.get("status") != "active":
        return 0

    target = max(0, int(order.get("target", 0)))
    completed = max(0, int(order.get("completed", 0)))
    remaining = max(0, target - completed)

    if "cost_per_member" in order:
        per_member = int(order.get("cost_per_member", 0))
    else:
        original_cost = int(order.get("cost", 0))
        per_member = (original_cost // target) if target else 0

    refund = remaining * per_member
    owner = get_user(order.get("owner_id"))
    owner["points"] = int(owner.get("points", 0)) + refund
    order["remaining"] = remaining
    order["status"] = "stopped"
    order["stopped_at"] = time.time()
    order["refunded_points"] = refund
    return refund

def leaver_scan():
    """Check recorded campaign members and remove users who are no longer subscribed.
    The removed user's earned points are deducted, but never below zero.
    """
    changed = False
    removed_total = 0
    for o in list(data["orders"].values()):
        # لا نفحص إلا الحملات التي ما زالت فعالة؛ الحملات الموقوفة لا تخصم نقاطًا بأثر رجعي.
        if o.get("status") not in ("active", "completed"):
            continue
        members = list(o.get("members", []))
        if not members:
            continue
        active_members = []
        for uid in members:
            try:
                subscribed = check_subscription(o["channel_id"], int(uid))
            except Exception:
                subscribed = True
            if subscribed:
                active_members.append(str(uid))
                continue
            u = get_user(uid)
            reward = int(data["settings"].get("join_points", 0))
            if reward > 0:
                u["points"] = max(0, int(u.get("points", 0)) - reward)
            if o.get("channel_id") in u.get("joined", []):
                u["joined"].remove(o["channel_id"])
            removed_total += 1
            changed = True
        if len(active_members) != len(members):
            o["members"] = active_members
            o["completed"] = len(active_members)
            o["remaining"] = max(0, int(o.get("target", 0)) - len(active_members))
            if o["remaining"] > 0 and o.get("status") == "completed":
                o["status"] = "active"
            changed = True
    if changed:
        save_data()
    return removed_total

def leaver_worker():
    while True:
        try:
            interval = max(60, int(data["settings"].get("leaver_check_interval", 300)))
            time.sleep(interval)
            removed = leaver_scan()
            if removed:
                try:
                    main_bot.send_message(ADMIN_ID, f"⚠️ فحص المغادرين: تم اكتشاف {removed} مغادر وتم خصم نقاط المكافأة الخاصة بهم.")
                except Exception:
                    pass
        except Exception as e:
            print("leaver_worker:", repr(e))
            time.sleep(60)

def main_kb(uid=None):
    # واجهة رئيسية مرتبة ومزينة — ألوان الأزرار نفسها تتحكم بها Telegram.
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("🚀 تمويل قناتي", callback_data="fund"),
        telebot.types.InlineKeyboardButton("💎 تجميع نقاط", callback_data="collect")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily"),
        telebot.types.InlineKeyboardButton("👤 حسابي", callback_data="account")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📦 تمويلاتي", callback_data="my_funds"),
        telebot.types.InlineKeyboardButton("🔗 دعوة أصدقاء", callback_data="ref")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🔄 تحويل نقاط", callback_data="transfer"),
        telebot.types.InlineKeyboardButton("🎟️ كود شحن", callback_data="recharge")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"),
        telebot.types.InlineKeyboardButton("🏆 المتصدرين", callback_data="leaderboard")
    )
    kb.add(telebot.types.InlineKeyboardButton("🛒 المتجر", callback_data="store"))
    if uid is not None and is_admin(uid):
        kb.add(telebot.types.InlineKeyboardButton("👑 لوحة الإدارة", callback_data="a_home"))
    return kb

def send_home(chat_id, message_id=None):
    u = get_user(chat_id)
    text = ("╔══════════════════╗\n"
            "   🤖 <b>بوت تبادل الاشتراكات</b>   \n"
            "╚══════════════════╝\n\n"
            f"👤 <b>حسابك:</b> <code>{chat_id}</code>\n"
            f"💰 <b>رصيدك:</b> {u['points']} نقطة\n\n"
            "✨ <b>اختر الخدمة التي تريدها:</b>\n"
            "📢 موّل قناتك أو مجموعتك بالنقاط\n"
            "💎 اجمع النقاط من القنوات والمجموعات\n"
            "🎁 احصل على مكافأتك اليومية")
    kb = main_kb(chat_id)
    if message_id:
        try:
            main_bot.edit_message_text(text, chat_id, message_id, reply_markup=kb); return
        except: pass
    main_bot.send_message(chat_id, text, reply_markup=kb)

def gate_user(uid):
    if is_admin(uid): return True
    if maintenance(): return False
    ok, _ = mandatory_check(uid)
    return ok

def mandatory_keyboard(missing):
    kb = telebot.types.InlineKeyboardMarkup()
    for c in missing:
        if c.get("link"):
            kb.add(telebot.types.InlineKeyboardButton("📢 " + c.get("title", "القناة"), url=c["link"]))
    kb.add(telebot.types.InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_mandatory"))
    return kb

def send_mandatory(uid, missing=None):
    if missing is None: _, missing = mandatory_check(uid)
    text = "🔒 <b>اشتراك إجباري</b>\n\nاشترك في القنوات التالية أولًا ثم اضغط تحقق:\n\n"
    text += "\n".join(f"{i}. <b>{c.get('title','القناة')}</b>" for i,c in enumerate(missing,1))
    main_bot.send_message(uid, text, reply_markup=mandatory_keyboard(missing))

def add_wait(uid, payload):
    with data_lock:
        data["waiting"][str(uid)] = payload; save_data()

def pop_wait(uid):
    with data_lock:
        x = data["waiting"].pop(str(uid), None); save_data(); return x

def assistant_link():
    return f"https://t.me/{ASSISTANT_USERNAME}?startchannel&admin=invite_users"

def assistant_group_link():
    return f"https://t.me/{ASSISTANT_USERNAME}?startgroup&admin=invite_users"

# =========================================================
# START / USER
# =========================================================
@main_bot.message_handler(commands=["start"])
def start(m):
    uid = m.from_user.id; new = str(uid) not in data["users"]
    u = get_user(uid); cmd = m.text or ""
    if maintenance() and not is_admin(uid):
        main_bot.send_message(uid, "🛠️ <b>البوت تحت الصيانة حاليًا.</b>"); return
    if not is_admin(uid) and mandatory_channels():
        ok, missing = mandatory_check(uid)
        if not ok: send_mandatory(uid, missing); return
    if new and entry_notify():
        try:
            name = f"@{m.from_user.username}" if m.from_user.username else (m.from_user.first_name or "بدون اسم")
            main_bot.send_message(ADMIN_ID, f"👤 <b>مستخدم جديد دخل البوت</b>\n👤 {name}\n🆔 <code>{uid}</code>")
        except: pass
    if cmd.startswith("/start ref_"):
        try:
            ref = int(cmd.split("ref_",1)[1])
            if ref != uid and u["ref_by"] is None and str(ref) in data["users"]:
                u["ref_by"] = ref; ru = get_user(ref); p = int(data["settings"]["ref_points"])
                ru["points"] += p; ru["refs"] += 1; save_data()
                try: main_bot.send_message(ref, f"🎉 دخل شخص جديد عبر رابطك!\n💰 +{p} نقطة")
                except: pass
        except: pass
    send_home(uid)

@main_bot.message_handler(commands=["admin"])
def admin_cmd(m):
    if is_admin(m.from_user.id): admin_panel(m.chat.id)
    else: main_bot.reply_to(m, "❌ ليس لديك صلاحية.")

# =========================================================
# MANDATORY GATE / COMMON BUTTONS
# =========================================================
@main_bot.callback_query_handler(func=lambda c: maintenance() and not is_admin(c.from_user.id))
def maintenance_gate(c):
    try: main_bot.answer_callback_query(c.id, "🛠️ البوت تحت الصيانة حاليًا.", show_alert=True)
    except: pass

@main_bot.callback_query_handler(func=lambda c: (not is_admin(c.from_user.id) and mandatory_channels() and c.data != "check_mandatory" and not mandatory_check(c.from_user.id)[0]))
def mandatory_gate(c):
    try:
        _, missing = mandatory_check(c.from_user.id)
        main_bot.answer_callback_query(c.id, "🔒 أكمل الاشتراك الإجباري أولًا.", show_alert=True)
        main_bot.edit_message_text("🔒 <b>اشتراك إجباري</b>\n\nأكمل الاشتراك ثم اضغط تحقق.", c.message.chat.id, c.message.message_id, reply_markup=mandatory_keyboard(missing))
    except: pass

@main_bot.callback_query_handler(func=lambda c: c.data == "check_mandatory")
def check_mandatory(c):
    uid=c.from_user.id
    if is_admin(uid) or not mandatory_channels():
        main_bot.answer_callback_query(c.id,"✅ يمكنك استخدام البوت.",show_alert=True); send_home(uid,c.message.message_id); return
    ok, missing=mandatory_check(uid)
    if not ok:
        main_bot.answer_callback_query(c.id,"❌ ما زالت هناك قناة لم تشترك بها.",show_alert=True); return
    main_bot.answer_callback_query(c.id,"✅ تم التحقق!",show_alert=True); send_home(uid,c.message.message_id)

@main_bot.callback_query_handler(func=lambda c: c.data == "home")
def home(c): pop_wait(c.from_user.id); send_home(c.message.chat.id,c.message.message_id)

@main_bot.callback_query_handler(func=lambda c: c.data == "account")
def account(c):
    u=get_user(c.from_user.id)
    text=("👤 <b>حسابك</b>\n\n"
          f"🆔 <code>{c.from_user.id}</code>\n💰 النقاط: <b>{u['points']}</b>\n"
          f"👥 الدعوات: <b>{u['refs']}</b>\n📢 الاشتراكات المحتسبة: <b>{len(u['joined'])}</b>\n"
          f"📦 التمويلات: <b>{len(u['orders'])}</b>\n📢 قنواتك المسجلة: <b>{len(u['channels'])}</b>")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية",callback_data="home"))
    c.message and main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "stats")
def stats(c):
    orders=list(data["orders"].values()); completed=sum(int(o.get("completed",0)) for o in orders)
    active=sum(1 for o in orders if o.get("status")=="active"); points=sum(int(u.get("points",0)) for u in data["users"].values())
    text=("📊 <b>إحصائيات البوت</b>\n\n"
          f"👥 المستخدمون: {len(data['users'])}\n📦 التمويلات: {len(orders)}\n🔥 النشطة: {active}\n"
          f"✅ الاشتراكات المحتسبة: {completed}\n💰 مجموع النقاط الحالية: {points}")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية",callback_data="home"))
    main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def leaderboard(c):
    users = []
    for uid, u in data.get("users", {}).items():
        users.append((int(u.get("points", 0)), int(u.get("refs", 0)), str(uid)))
    users.sort(key=lambda x: (x[0], x[1]), reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>المتصدرين</b>", "", "أفضل 10 مستخدمين حسب النقاط:", ""]
    if not users:
        lines.append("لا يوجد مستخدمون حتى الآن.")
    else:
        for i, (points, refs, uid) in enumerate(users[:10], 1):
            medal = medals[i-1] if i <= 3 else f"{i}️⃣"
            lines.append(f"{medal} <code>{uid}</code> — 💰 <b>{points}</b> نقطة — 👥 {refs} دعوة")
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔄 تحديث", callback_data="leaderboard"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية", callback_data="home"))
    safe_edit_message("\n".join(lines), c.message.chat.id, c.message.message_id, reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data == "ref")
def referral(c):
    me=main_bot.get_me(); link=f"https://t.me/{me.username}?start=ref_{c.from_user.id}"
    text=("🔗 <b>رابط دعوتك</b>\n\n" f"كل شخص يدخل من رابطك تحصل على <b>{data['settings']['ref_points']}</b> نقطة.\n\n<code>{link}</code>")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 الرئيسية",callback_data="home"))
    main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "daily")
def daily(c):
    u=get_user(c.from_user.id); now=time.time(); diff=now-u["last_gift"]
    if diff < 86400:
        rem=int(86400-diff); main_bot.answer_callback_query(c.id,f"❌ متاح بعد {rem//3600} ساعة و{(rem%3600)//60} دقيقة.",show_alert=True); return
    p=int(data["settings"]["daily_gift"])
    with data_lock: u["points"]+=p; u["last_gift"]=now; save_data()
    main_bot.answer_callback_query(c.id,f"🎁 +{p} نقطة",show_alert=True); send_home(c.message.chat.id,c.message.message_id)

# =========================================================
# TRANSFER / RECHARGE / MY FUNDS / LEAVER CHECK
# =========================================================
@main_bot.callback_query_handler(func=lambda c: c.data == "transfer")
def transfer_start(c):
    if not gate_user(c.from_user.id):
        if maintenance() and not is_admin(c.from_user.id):
            main_bot.answer_callback_query(c.id, "🛠️ البوت تحت الصيانة حاليًا.", show_alert=True)
        return
    add_wait(c.from_user.id, {"type": "transfer_uid"})
    transfer_screen(c.message.chat.id, c.message.message_id)

@main_bot.message_handler(func=lambda m: isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "transfer_uid")
def transfer_get_uid(m):
    target = (m.text or "").strip()
    if not target.isdigit():
        main_bot.reply_to(m, "❌ أرسل ID رقمي صحيح.")
        return
    if target == str(m.from_user.id):
        main_bot.reply_to(m, "❌ لا يمكنك تحويل النقاط لنفسك.")
        return
    if target not in data["users"]:
        main_bot.reply_to(m, "❌ المستخدم غير مسجل في البوت.")
        return
    add_wait(m.from_user.id, {"type": "transfer_amount", "target": target})
    main_bot.reply_to(m, "💰 أرسل عدد النقاط التي تريد تحويلها:")

@main_bot.message_handler(func=lambda m: isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "transfer_amount")
def transfer_apply(m):
    uid = str(m.from_user.id)
    w = data["waiting"].get(uid, {})
    try:
        amount = int((m.text or "").strip())
    except Exception:
        main_bot.reply_to(m, "❌ أرسل رقمًا صحيحًا.")
        return
    s = data["settings"]
    if amount < int(s.get("transfer_min", 1)) or amount > int(s.get("transfer_max", 1000000)):
        main_bot.reply_to(m, f"❌ المبلغ يجب أن يكون بين {s.get('transfer_min',1)} و{s.get('transfer_max',1000000)} نقطة.")
        return
    sender = get_user(m.from_user.id)
    target = get_user(w["target"])
    if int(sender["points"]) < amount:
        main_bot.reply_to(m, f"❌ رصيدك غير كافٍ. معك {sender['points']} نقطة.")
        return
    with data_lock:
        sender["points"] -= amount
        target["points"] += amount
        sender["transfers_sent"] = int(sender.get("transfers_sent", 0)) + amount
        target["transfers_received"] = int(target.get("transfers_received", 0)) + amount
        data["waiting"].pop(uid, None)
        save_data()
    main_bot.reply_to(m, f"✅ تم تحويل <b>{amount}</b> نقطة إلى المستخدم <code>{w['target']}</code>.\n💰 رصيدك الآن: <b>{sender['points']}</b>")
    try:
        main_bot.send_message(int(w["target"]), f"🎉 تم تحويل <b>{amount}</b> نقطة إلى حسابك من المستخدم <code>{m.from_user.id}</code>.\n💰 رصيدك الآن: <b>{target['points']}</b>")
    except Exception:
        pass
    send_home(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data == "recharge")
def recharge_start(c):
    if not gate_user(c.from_user.id): return
    add_wait(c.from_user.id, {"type": "recharge_code"})
    recharge_screen(c.message.chat.id, c.message.message_id)

@main_bot.message_handler(func=lambda m: isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "recharge_code")
def recharge_apply(m):
    uid = str(m.from_user.id)
    code = (m.text or "").strip().upper()
    item = data["codes"].get(code)
    if not item or not item.get("active", True):
        main_bot.reply_to(m, "❌ كود الشحن غير صحيح أو غير فعال.")
        return
    if uid in [str(x) for x in item.get("used_by", [])]:
        main_bot.reply_to(m, "❌ استخدمت هذا الكود من قبل.")
        return
    if int(item.get("uses", 0)) >= int(item.get("max_uses", 1)):
        main_bot.reply_to(m, "❌ انتهت استخدامات هذا الكود.")
        return
    u = get_user(m.from_user.id)
    points = int(item.get("points", 0))
    with data_lock:
        u["points"] += points
        item["uses"] = int(item.get("uses", 0)) + 1
        item.setdefault("used_by", []).append(uid)
        if item["uses"] >= int(item.get("max_uses", 1)):
            item["active"] = False
        u.setdefault("redeemed_codes", []).append(code)
        data["waiting"].pop(uid, None)
        save_data()
    main_bot.reply_to(m, f"✅ تم شحن حسابك بـ <b>{points}</b> نقطة.\n💰 رصيدك الجديد: <b>{u['points']}</b>")
    send_home(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data == "my_funds")
def my_funds(c):
    if not gate_user(c.from_user.id): return
    my_funds_screen(c.message.chat.id, c.message.message_id)

@main_bot.callback_query_handler(func=lambda c: c.data == "history")
def history(c):
    uid = c.from_user.id
    u = get_user(uid)
    lines = ["📜 <b>سجل العمليات</b>", ""]

    # التمويلات الخاصة بالمستخدم
    orders = funded_orders_for_user(uid)
    for o in orders[-8:][::-1]:
        status = o.get("status", "unknown")
        lines.append(
            f"🚀 تمويل: {esc(o.get('channel_title', 'قناة'))} — "
            f"{o.get('completed', 0)}/{o.get('target', 0)} — {status}"
        )

    if u.get("transfers_sent", 0):
        lines.append(f"📤 إجمالي المحوّل: <b>{u.get('transfers_sent', 0)}</b> نقطة")
    if u.get("transfers_received", 0):
        lines.append(f"📥 إجمالي المستلم: <b>{u.get('transfers_received', 0)}</b> نقطة")
    if u.get("redeemed_codes"):
        lines.append(f"🎟️ أكواد شحن مستخدمة: <b>{len(u.get('redeemed_codes', []))}</b>")
    if u.get("refs", 0):
        lines.append(f"🔗 الدعوات الناجحة: <b>{u.get('refs', 0)}</b>")

    if len(lines) == 2:
        lines.append("لا توجد عمليات مسجلة حتى الآن.")

    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 تمويلاتي", callback_data="my_funds"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية", callback_data="home"))
    safe_edit_message("\n".join(lines), c.message.chat.id, c.message.message_id, reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("fund_stop:"))
def fund_stop(c):
    uid = c.from_user.id
    oid = c.data.split(":", 1)[1]
    order = data["orders"].get(oid)

    if not order or int(order.get("owner_id", -1)) != int(uid):
        main_bot.answer_callback_query(c.id, "❌ هذا التمويل غير موجود في حسابك.", show_alert=True)
        return

    if order.get("status") != "active":
        main_bot.answer_callback_query(c.id, "⚠️ هذا التمويل متوقف بالفعل أو مكتمل.", show_alert=True)
        my_funds_screen(c.message.chat.id, c.message.message_id)
        return

    with data_lock:
        refund = refund_remaining_funding(order)
        save_data()

    main_bot.answer_callback_query(
        c.id, f"🛑 تم إيقاف التمويل وإرجاع {refund} نقطة.", show_alert=True
    )
    try:
        main_bot.send_message(
            uid,
            f"🛑 <b>تم إيقاف التمويل</b>\n\n"
            f"📢 {esc(order.get('channel_title', 'القناة'))}\n"
            f"👥 تم احتساب: {order.get('completed', 0)}\n"
            f"⏳ كان متبقيًا: {order.get('remaining', 0)}\n"
            f"💰 تم إرجاع: <b>{refund}</b> نقطة"
        )
    except Exception:
        pass
    my_funds_screen(c.message.chat.id, c.message.message_id)


@main_bot.callback_query_handler(func=lambda c: c.data == "fund_stop_all_confirm")
def fund_stop_all_confirm(c):
    if not gate_user(c.from_user.id):
        return
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("✅ نعم، أوقف الكل", callback_data="fund_stop_all"),
        telebot.types.InlineKeyboardButton("❌ إلغاء", callback_data="my_funds")
    )
    safe_edit_message(
        "⚠️ <b>تأكيد إيقاف جميع التمويلات</b>\n\nسيتم إيقاف كل الحملات النشطة وإرجاع نقاط الأعضاء المتبقية فقط.\n\nهل أنت متأكد؟",
        c.message.chat.id, c.message.message_id, reply_markup=kb
    )


@main_bot.callback_query_handler(func=lambda c: c.data == "fund_stop_all")
def fund_stop_all(c):
    uid = c.from_user.id
    if not gate_user(uid):
        return

    with data_lock:
        active = [
            o for o in data["orders"].values()
            if int(o.get("owner_id", -1)) == int(uid)
            and o.get("status") == "active"
        ]

        if not active:
            main_bot.answer_callback_query(c.id, "لا توجد تمويلات نشطة.", show_alert=True)
            my_funds_screen(c.message.chat.id, c.message.message_id)
            return

        total_refund = 0
        stopped = 0
        for order in active:
            total_refund += refund_remaining_funding(order)
            stopped += 1

        save_data()

    main_bot.answer_callback_query(
        c.id,
        f"🛑 تم إيقاف {stopped} تمويل وإرجاع {total_refund} نقطة.",
        show_alert=True
    )
    my_funds_screen(c.message.chat.id, c.message.message_id)


@main_bot.callback_query_handler(func=lambda c: c.data == "manual_leaver_check")
def manual_leaver_check(c):
    if not is_admin(c.from_user.id):
        main_bot.answer_callback_query(c.id, "⚠️ هذه الميزة متاحة للأدمن فقط.", show_alert=True)
        return
    removed = leaver_scan()
    main_bot.answer_callback_query(c.id, f"تم الفحص: {removed} مغادر.", show_alert=True)
    a_home(c)

# =========================================================
# FUNDING - اختيار القناة أولًا ثم العدد
# =========================================================
def show_funding_channels(chat_id, message_id=None):
    uid = chat_id
    channels = verified_owner_channels(uid)
    text = ["📢 <b>اختيار قناة/مجموعة التمويل</b>", ""]
    if not channels:
        text += [
            "❌ لم أجد قناة أو مجموعة تملكها والبوت المساعد موجود فيها.",
            "",
            "أضف البوت المساعد كـ <b>مشرف</b> في القناة أو المجموعة، ثم اضغط تحقق مرة أخرى."
        ]
    else:
        text += ["اختر القناة أو المجموعة التي تريد تمويلها:", ""]
        for ch in channels:
            title = esc(ch.get("title", "بدون اسم"))
            if ch.get("username"):
                access = "🌐 عامة"
            else:
                access = "🔒 خاصة"
            text.append(f"• <b>{title}</b> — {access}")

    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        cid = str(ch.get("id", ""))
        kb.add(telebot.types.InlineKeyboardButton(
            f"{'👥' if ch.get('type') in ('group', 'supergroup') else '📢'} {ch.get('title','الدردشة')[:30]}",
            callback_data=f"fund_existing:{cid}"
        ))
    # النظام القديم + دعم المجموعات.
    kb.add(telebot.types.InlineKeyboardButton("➕ إضافة البوت لقناة", url=assistant_link()))
    kb.add(telebot.types.InlineKeyboardButton("👥 إضافة البوت لمجموعة", url=assistant_group_link()))
    kb.add(telebot.types.InlineKeyboardButton("🔄 تحقق / تحديث القنوات", callback_data="fund"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية", callback_data="home"))

    if message_id:
        try:
            main_bot.edit_message_text(
                "\n".join(text), chat_id, message_id,
                reply_markup=kb, disable_web_page_preview=True
            )
            return
        except Exception:
            pass
    main_bot.send_message(
        chat_id, "\n".join(text), reply_markup=kb,
        disable_web_page_preview=True
    )

@main_bot.callback_query_handler(func=lambda c: c.data == "fund")
def fund(c):
    """زر التمويل الجديد: يعرض القنوات أولًا ولا يطلب الرقم قبل اختيار القناة."""
    if not gate_user(c.from_user.id):
        return
    pop_wait(c.from_user.id)
    show_funding_channels(c.message.chat.id, c.message.message_id)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("fund_existing:"))
def fund_existing(c):
    """بعد اختيار القناة نطلب عدد الأعضاء فقط."""
    uid = c.from_user.id
    cid = c.data.split(":", 1)[1]
    channels = verified_owner_channels(uid)
    channel = next((x for x in channels if str(x.get("id")) == cid), None)
    if not channel:
        main_bot.answer_callback_query(
            c.id,
            "❌ القناة غير متاحة أو لم تعد تملكها.",
            show_alert=True
        )
        return

    # للقناة الخاصة: أنشئ رابط دعوة الآن، قبل خصم أي نقاط.
    if not channel.get("username") and not channel.get("invite_link"):
        channel["invite_link"] = create_channel_invite_link(cid)
        if not channel.get("invite_link"):
            main_bot.answer_callback_query(
                c.id,
                "❌ لم أستطع إنشاء رابط دعوة للقناة الخاصة. تأكد أن البوت المساعد مشرف ولديه صلاحية دعوة المستخدمين.",
                show_alert=True
            )
            return

    s = data["settings"]
    u = get_user(uid)
    add_wait(uid, {
        "type": "fund_count_selected",
        "selected_channel": channel
    })

    text = (
        f"{'👥' if channel.get('type') in ('group', 'supergroup') else '📢'} <b>{esc(channel.get('title','الدردشة'))}</b>\n\n"
        f"{'🔒 خاصة — رابط الدعوة جاهز' if not channel.get('username') else '🌐 عامة'}\n"
        f"💰 رصيدك: <b>{u.get('points', 0)}</b> نقطة\n"
        f"🪙 سعر العضو: <b>{s['points_per_member']}</b> نقطة\n"
        f"⬇️ الحد الأدنى: <b>{s['min_fund']}</b>\n"
        f"⬆️ الحد الأقصى: <b>{s['max_fund']}</b>\n\n"
        "أرسل <b>عدد الأعضاء</b> المطلوبين للتمويل:"
    )
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔙 اختيار قناة أخرى", callback_data="fund"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية", callback_data="home"))
    main_bot.edit_message_text(
        text, c.message.chat.id, c.message.message_id, reply_markup=kb
    )

@main_bot.message_handler(
    func=lambda m: isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict)
    and data["waiting"][str(m.from_user.id)].get("type") == "fund_count_selected"
)
def fund_count_selected(m):
    uid = str(m.from_user.id)
    w = data["waiting"].get(uid, {})
    try:
        count = int((m.text or "").strip())
    except Exception:
        main_bot.reply_to(m, "❌ أرسل رقمًا صحيحًا فقط.")
        return

    s = data["settings"]
    if not int(s["min_fund"]) <= count <= int(s["max_fund"]):
        main_bot.reply_to(
            m,
            f"❌ العدد يجب أن يكون بين {s['min_fund']} و {s['max_fund']}."
        )
        return

    channel = w.get("selected_channel")
    if not isinstance(channel, dict) or not channel.get("id"):
        pop_wait(m.from_user.id)
        main_bot.reply_to(m, "❌ انتهت جلسة اختيار القناة. افتح تمويل قناتي مرة أخرى.")
        return

    # تأكيد أخير أن البوت المساعد ما زال داخل القناة وأن المستخدم مالكها.
    ok, reason = assistant_permissions(str(channel["id"]))
    if not ok:
        pop_wait(m.from_user.id)
        main_bot.reply_to(m, reason)
        return
    if not is_channel_creator(str(channel["id"]), m.from_user.id):
        pop_wait(m.from_user.id)
        main_bot.reply_to(m, "❌ يجب أن تكون مالك القناة (Creator) لإنشاء التمويل.")
        return

    # للقنوات الخاصة، يجب أن يكون رابط الدعوة موجودًا قبل بدء الحملة.
    if not channel.get("username") and not channel.get("invite_link"):
        channel["invite_link"] = create_channel_invite_link(str(channel["id"]))
    if not channel.get("username") and not channel.get("invite_link"):
        main_bot.reply_to(
            m,
            "❌ القناة خاصة ولم أستطع إنشاء رابط دعوة. أعطِ البوت المساعد صلاحية دعوة المستخدمين ثم حاول مرة أخرى."
        )
        return

    cost = count * int(s["points_per_member"])
    u = get_user(m.from_user.id)
    if int(u.get("points", 0)) < cost:
        main_bot.reply_to(
            m,
            f"❌ نقاطك غير كافية.\nالمطلوب: <b>{cost}</b>\nمعك: <b>{u['points']}</b>"
        )
        return

    oid = f"{int(time.time())}_{m.from_user.id}_{uuid.uuid4().hex[:6]}"
    with data_lock:
        u["points"] -= cost
        order = {
            "id": oid,
            "owner_id": m.from_user.id,
            "channel_id": str(channel["id"]),
            "channel_title": channel.get("title", "بدون اسم"),
            "channel_username": channel.get("username", ""),
            "invite_link": channel.get("invite_link"),
            "target": count,
            "completed": 0,
            "remaining": count,
            "cost": cost,
            "cost_per_member": int(s["points_per_member"]),
            "created_at": time.time(),
            "status": "active",
            "members": [],
            "rewarded_members": []
        }
        data["orders"][oid] = order
        u.setdefault("orders", []).append(oid)
        data["channels"][str(channel["id"])] = {
            **channel,
            "active": True,
            "owner_id": m.from_user.id
        }
        data["waiting"].pop(uid, None)
        save_data()

    link_note = ""
    if order.get("invite_link"):
        link_note = "\n🔗 رابط الاشتراك جاهز للمشتركين."

    main_bot.send_message(
        m.chat.id,
        "🚀 <b>تم بدء التمويل بنجاح!</b>\n\n"
        f"📢 {esc(order['channel_title'])}\n"
        f"👥 المطلوب: <b>{count}</b>\n"
        f"💰 التكلفة: <b>{cost}</b> نقطة\n"
        f"⏳ المتبقي: <b>{count}</b>"
        f"{link_note}"
    )

@assistant_bot.my_chat_member_handler()
def assistant_membership(update):
    """عند إضافة البوت لقناة/مجموعة: سجّلها ثم أعطِ المستخدم زر التحقق لاختيار الدردشة التي يملكها."""
    try:
        if update.chat.type not in ("channel", "group", "supergroup"):
            return
        nm = update.new_chat_member
        if nm.status not in ("administrator", "creator"):
            return
        me = assistant_bot.get_me()
        if nm.user.id != me.id:
            return

        chat = update.chat
        actor = update.from_user
        invite = None
        # إنشاء الرابط هنا مفيد للقنوات الخاصة؛ إذا فشل، سنحاول مرة أخرى عند الاختيار.
        try:
            invite = create_channel_invite_link(str(chat.id))
        except Exception:
            invite = None

        ch = {
            "id": str(chat.id),
            "title": chat.title or "بدون اسم",
            "username": chat.username or "",
            "type": chat.type,
            "link": f"https://t.me/{chat.username}" if chat.username else None,
            "invite_link": invite,
            "active": True,
            "owner_id": actor.id
        }

        with data_lock:
            u = get_user(actor.id)
            if not any(str(x.get("id")) == str(chat.id) for x in u.get("channels", [])):
                u.setdefault("channels", []).append(ch)
            else:
                for x in u["channels"]:
                    if str(x.get("id")) == str(chat.id):
                        x.update(ch)
            data["channels"][str(chat.id)] = ch
            save_data()

        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("🔎 تحقق واختيار قناة التمويل", callback_data="fund"))
        main_bot.send_message(
            actor.id,
            f"✅ <b>البوت المساعد انضم بنجاح!</b>\n\n{'👥' if chat.type in ('group', 'supergroup') else '📢'} {esc(ch['title'])}\n\n"
            "اضغط <b>تحقق واختيار قناة التمويل</b> لعرض القنوات والمجموعات التي أنت مالكها والبوت موجود فيها.",
            reply_markup=kb
        )
    except Exception as e:
        print("assistant_membership:", repr(e))

# =========================================================
# COLLECT / VERIFY
# =========================================================
def find_order_for(uid):
    for o in data["orders"].values():
        if o.get("status")!="active" or int(o.get("remaining",0))<=0 or int(o.get("owner_id"))==int(uid): continue
        rewarded = o.get("rewarded_members") or o.get("members", [])
        if str(uid) in [str(x) for x in rewarded]: continue
        return o
    return None

@main_bot.callback_query_handler(func=lambda c: c.data == "collect")
def collect(c):
    o=find_order_for(c.from_user.id)
    if not o:
        kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔄 تحديث",callback_data="collect")); kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية",callback_data="home"))
        safe_edit_message("😔 لا توجد قنوات أو مجموعات متاحة حاليًا.",c.message.chat.id,c.message.message_id,reply_markup=kb); return
    text=("💰 <b>تجميع نقاط</b>\n\n" f"📢 {o['channel_title']}\n👥 المطلوب: {o['target']}\n✅ تم: {o['completed']}\n⏳ متبقي: {o['remaining']}\n\n🪙 ستحصل على <b>{data['settings']['join_points']}</b> نقطة.\nانضم ثم اضغط تحقق.")
    kb=telebot.types.InlineKeyboardMarkup()
    join_link = o.get("invite_link") or (f"https://t.me/{o['channel_username']}" if o.get("channel_username") else None)
    if join_link:
        kb.add(telebot.types.InlineKeyboardButton("📢 اشترك في القناة",url=join_link))
    kb.add(telebot.types.InlineKeyboardButton("✅ تحقق",callback_data=f"verify:{o['id']}"),telebot.types.InlineKeyboardButton("➡️ تخطي",callback_data="collect"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية",callback_data="home")); safe_edit_message(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("verify:"))
def verify(c):
    uid = int(c.from_user.id)
    oid = c.data.split(":", 1)[1]
    o = data["orders"].get(oid)

    if not o or o.get("status") != "active":
        main_bot.answer_callback_query(c.id, "❌ التمويل غير متاح.", show_alert=True)
        return
    if int(o.get("owner_id", -1)) == uid:
        main_bot.answer_callback_query(c.id, "❌ لا يمكنك احتساب تمويل قناتك.", show_alert=True)
        return

    # تحقق حقيقي من Telegram لحظة الضغط، وليس اعتمادًا على زر الانضمام فقط.
    if not check_subscription(o.get("channel_id"), uid):
        main_bot.answer_callback_query(c.id, "❌ لم يتم العثور على عضويتك. ادخل القناة/المجموعة أولًا ثم اضغط تحقق.", show_alert=True)
        return

    with data_lock:
        o.setdefault("members", [])
        o.setdefault("rewarded_members", list(o.get("members", [])))
        # منع تكرار المكافأة حتى لو خرج العضو ثم عاد.
        if str(uid) in {str(x) for x in o.get("rewarded_members", [])}:
            main_bot.answer_callback_query(c.id, "❌ حصلت على مكافأة هذه الحملة من قبل.", show_alert=True)
            return
        # إعادة التحقق داخل القفل لتقليل سباق الضغط المزدوج.
        if not check_subscription(o.get("channel_id"), uid):
            main_bot.answer_callback_query(c.id, "❌ العضوية غير مؤكدة حاليًا.", show_alert=True)
            return

        p = int(data["settings"].get("join_points", 0))
        u = get_user(uid)
        o["members"].append(str(uid))
        o["rewarded_members"].append(str(uid))
        o["completed"] = int(o.get("completed", 0)) + 1
        o["remaining"] = max(0, int(o.get("target", 0)) - int(o["completed"]))
        if str(o.get("channel_id")) not in [str(x) for x in u.get("joined", [])]:
            u.setdefault("joined", []).append(str(o.get("channel_id")))
        u["points"] = int(u.get("points", 0)) + p
        if o["remaining"] <= 0:
            o["status"] = "completed"
        save_data()

    main_bot.answer_callback_query(c.id, f"✅ تم التأكد من عضويتك فعليًا +{p} نقطة", show_alert=True)
    try:
        main_bot.send_message(o["owner_id"], f"📢 <b>تحديث التمويل</b>\n\n📢 {esc(o.get('channel_title','القناة'))}\n✅ تم: {o['completed']}\n⏳ متبقي: {o['remaining']}")
    except Exception:
        pass
    collect(c)

# =========================================================
# STORE / USER SHOP
# =========================================================
def shop_categories():
    return [x for x in data.get("categories", {}).values() if x.get("active", True)]

def shop_products(category_id=None):
    return [x for x in data.get("products", {}).values()
            if x.get("active", True) and int(x.get("stock", -1)) != 0
            and (category_id is None or str(x.get("category_id", "general")) == str(category_id))]

def store_screen(chat_id, message_id=None):
    cats = shop_categories()
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    text = "🛒 <b>متجر البوت</b>\n\nاختر القسم الذي تريد التصفح منه:"
    if not cats:
        text += "\n\n😔 لا توجد أقسام متاحة حاليًا."
    for cat in cats:
        count = len(shop_products(cat.get("id")))
        kb.add(telebot.types.InlineKeyboardButton(
            f"📁 {cat.get('name','قسم')} — {count} سلعة", callback_data=f"shop_cat:{cat.get('id')}"
        ))
    kb.add(telebot.types.InlineKeyboardButton("📦 مشترياتي", callback_data="shop_purchases"))
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية", callback_data="home"))
    if message_id: safe_edit_message(text, chat_id, message_id, reply_markup=kb)
    else: main_bot.send_message(chat_id, text, reply_markup=kb)

def category_screen(c, category_id):
    cat = data.get("categories", {}).get(category_id)
    if not cat or not cat.get("active", True):
        main_bot.answer_callback_query(c.id, "❌ القسم غير متاح.", show_alert=True); store_screen(c.message.chat.id,c.message.message_id); return
    products = shop_products(category_id)
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    text=f"📁 <b>{esc(cat.get('name','قسم'))}</b>\n\nاختر السلعة:"
    if not products: text += "\n\n😔 لا توجد سلع متاحة في هذا القسم."
    for p in products:
        kb.add(telebot.types.InlineKeyboardButton(f"🛍️ {p.get('name','سلعة')[:28]} — {int(p.get('price',0))} نقطة",callback_data=f"shop_item:{p.get('id')}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 الأقسام",callback_data="store"))
    safe_edit_message(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "store")
def store(c):
    if not gate_user(c.from_user.id): return
    pop_wait(c.from_user.id); store_screen(c.message.chat.id,c.message.message_id)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("shop_cat:"))
def shop_cat(c):
    if not gate_user(c.from_user.id): return
    category_screen(c,c.data.split(":",1)[1])

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("shop_item:"))
def shop_item(c):
    if not gate_user(c.from_user.id): return
    pid=c.data.split(":",1)[1]; product=data.get("products",{}).get(pid)
    if not product or not product.get("active",True):
        main_bot.answer_callback_query(c.id,"❌ هذه السلعة غير متاحة.",show_alert=True); return
    stock=int(product.get("stock",-1))
    if stock==0: main_bot.answer_callback_query(c.id,"❌ نفدت الكمية.",show_alert=True); return
    u=get_user(c.from_user.id); price=int(product.get("price",0)); stock_text="متوفر دائمًا" if stock<0 else str(stock)
    text=(f"🛍️ <b>{esc(product.get('name','سلعة'))}</b>\n\n📝 {esc(product.get('description','لا يوجد وصف.'))}\n\n💰 السعر: <b>{price}</b> نقطة\n📦 المتبقي: <b>{stock_text}</b>\n💳 رصيدك: <b>{u.get('points',0)}</b> نقطة\n\nاضغط شراء لإتمام العملية.")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🛒 شراء الآن",callback_data=f"shop_buy:{pid}")); kb.add(telebot.types.InlineKeyboardButton("🔙 القسم",callback_data=f"shop_cat:{product.get('category_id','general')}")); kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية",callback_data="home"))
    safe_edit_message(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

def deliver_product(chat_id, product):
    dtype=product.get("delivery_type","text"); fid=product.get("file_id"); caption=product.get("file_caption") or f"📦 {product.get('name','السلعة')}\n💰 تم التسليم بعد الشراء."
    try:
        if dtype=="document" and fid: main_bot.send_document(chat_id,fid,caption=caption)
        elif dtype=="photo" and fid: main_bot.send_photo(chat_id,fid,caption=caption)
        elif dtype=="video" and fid: main_bot.send_video(chat_id,fid,caption=caption)
        elif dtype=="audio" and fid: main_bot.send_audio(chat_id,fid,caption=caption)
        elif dtype=="voice" and fid: main_bot.send_voice(chat_id,fid,caption=caption)
        else: main_bot.send_message(chat_id,"📦 <b>بيانات السلعة:</b>\n"+esc(product.get("delivery","")))
    except Exception as e:
        print("deliver_product:",repr(e)); main_bot.send_message(chat_id,"⚠️ تم تسجيل عملية الشراء، لكن حدث خطأ أثناء إرسال الملف. تواصل مع الإدارة.")

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("shop_buy:"))
def shop_buy(c):
    if not gate_user(c.from_user.id): return
    pid=c.data.split(":",1)[1]
    with data_lock:
        product=data.get("products",{}).get(pid)
        if not product or not product.get("active",True): main_bot.answer_callback_query(c.id,"❌ السلعة غير متاحة.",show_alert=True); return
        stock=int(product.get("stock",-1)); price=int(product.get("price",0)); u=get_user(c.from_user.id); balance=int(u.get("points",0))
        if stock==0: main_bot.answer_callback_query(c.id,"❌ نفدت الكمية.",show_alert=True); return
        if balance<price: main_bot.answer_callback_query(c.id,f"❌ رصيدك غير كافٍ. تحتاج {price} ومعك {balance}.",show_alert=True); return
        u["points"]=balance-price
        if stock>0:
            product["stock"]=stock-1
            if product["stock"]==0: product["active"]=False
        purchase_id=uuid.uuid4().hex[:10]
        purchase={"id":purchase_id,"product_id":pid,"product_name":product.get("name","سلعة"),"user_id":c.from_user.id,"price":price,"delivery":product.get("delivery",""),"delivery_type":product.get("delivery_type","text"),"file_id":product.get("file_id"),"created_at":time.time()}
        data.setdefault("purchases",{})[purchase_id]=purchase; u.setdefault("purchases",[]).append(purchase_id); save_data()
    main_bot.answer_callback_query(c.id,"✅ تمت عملية الشراء",show_alert=True)
    main_bot.send_message(c.message.chat.id,f"✅ <b>تم الشراء بنجاح!</b>\n\n🛍️ {esc(product.get('name','سلعة'))}\n💰 السعر: <b>{price}</b> نقطة\n💳 رصيدك الآن: <b>{u['points']}</b> نقطة")
    deliver_product(c.message.chat.id,product)
    category_screen(c,product.get("category_id","general"))

@main_bot.callback_query_handler(func=lambda c: c.data == "shop_purchases")
def shop_purchases(c):
    if not gate_user(c.from_user.id): return
    u=get_user(c.from_user.id); purchases=[]
    for pid in u.get("purchases",[])[-15:][::-1]:
        item=data.get("purchases",{}).get(str(pid))
        if item: purchases.append(item)
    text="📦 <b>مشترياتي</b>\n\n" + ("\n".join(f"• {esc(x.get('product_name','سلعة'))} — {x.get('price',0)} نقطة" for x in purchases) or "لا توجد عمليات شراء حتى الآن.")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🛒 المتجر",callback_data="store")); kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية",callback_data="home")); safe_edit_message(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

# =========================================================
# ADMIN
# =========================================================
def admin_kb():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("📊 الإحصائيات", callback_data="a_stats"),
        telebot.types.InlineKeyboardButton("👥 المستخدمون", callback_data="a_users")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📢 إدارة التمويلات", callback_data="a_orders"),
        telebot.types.InlineKeyboardButton("📣 قنوات المستخدمين", callback_data="a_channels")
    )
    kb.add(telebot.types.InlineKeyboardButton("🛒 إدارة المتجر", callback_data="a_store"))
    kb.add(
        telebot.types.InlineKeyboardButton("⚙️ الإعدادات والأسعار", callback_data="a_settings"),
        telebot.types.InlineKeyboardButton("💰 إدارة النقاط", callback_data="a_modify_points")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("📣 إذاعة", callback_data="a_broadcast"),
        telebot.types.InlineKeyboardButton("🎟️ أكواد الشحن", callback_data="a_codes")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🛠️ الصيانة", callback_data="a_maintenance"),
        telebot.types.InlineKeyboardButton("⚠️ فحص المغادرين", callback_data="a_leavers")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("🔔 إشعارات الدخول", callback_data="a_entry_notify"),
        telebot.types.InlineKeyboardButton("🔒 الاشتراك الإجباري", callback_data="a_mandatory")
    )
    kb.add(telebot.types.InlineKeyboardButton("🏠 الرئيسية", callback_data="home"))
    return kb

def admin_panel(cid):
    if is_admin(cid): main_bot.send_message(cid,"👑 <b>لوحة تحكم المدير</b>\n\nاختر العملية:",reply_markup=admin_kb())

@main_bot.callback_query_handler(func=lambda c: c.data == "a_home")
def a_home(c):
    if is_admin(c.from_user.id): main_bot.edit_message_text("👑 <b>لوحة تحكم المدير</b>\n\nاختر العملية:",c.message.chat.id,c.message.message_id,reply_markup=admin_kb())

@main_bot.callback_query_handler(func=lambda c: c.data == "a_stats")
def a_stats(c):
    if not is_admin(c.from_user.id): return
    os_=list(data["orders"].values()); active=sum(o.get("status")=="active" for o in os_); comp=sum(int(o.get("completed",0)) for o in os_); pts=sum(int(u.get("points",0)) for u in data["users"].values())
    text=("📊 <b>إحصائيات المدير</b>\n\n" f"👥 المستخدمون: {len(data['users'])}\n📦 التمويلات: {len(os_)}\n🔥 النشطة: {active}\n✅ الاشتراكات المحتسبة: {comp}\n💰 النقاط الحالية: {pts}")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_users")
def a_users(c):
    if not is_admin(c.from_user.id): return
    users=sorted(data["users"].items(),key=lambda x:int(x[1].get("points",0)),reverse=True)
    text="👥 <b>المستخدمون</b>\n\n" + ("\n".join(f"<code>{uid}</code> — 💰 {u.get('points',0)} — 👥 {u.get('refs',0)}" for uid,u in users[:50]) or "لا يوجد مستخدمون.")
    if len(users)>50: text += f"\n\n… وأكثر {len(users)-50} مستخدم"
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_orders")
def a_orders(c):
    if not is_admin(c.from_user.id): return
    os_=list(data["orders"].values())
    text="📢 <b>التمويلات</b>\n\n"+("\n".join(f"<code>{o['id']}</code> | {o['channel_title']} | {o['completed']}/{o['target']} | {o['status']}" for o in os_[-30:]) or "لا توجد تمويلات.")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_channels")
def a_channels(c):
    if not is_admin(c.from_user.id): return
    chs=list(data["channels"].values())
    text="📢 <b>قنوات المستخدمين</b>\n\n"+("\n".join(f"{i}. {x.get('title','بدون اسم')} | <code>{x.get('id')}</code> | المالك <code>{x.get('owner_id')}</code>" for i,x in enumerate(chs[-50:],1)) or "لا توجد قنوات مسجلة.")
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

# =========================================================
# ADMIN STORE MANAGEMENT
# =========================================================
def admin_store_menu_text():
    products = list(data.get("products", {}).values())
    active = sum(1 for x in products if x.get("active", True))
    return (
        "🛒 <b>إدارة المتجر</b>\n\n"
        f"📦 إجمالي السلع: <b>{len(products)}</b>\n"
        f"🟢 المتاحة: <b>{active}</b>\n\n"
        "من هنا يمكنك إضافة السلع وتعديلها أو حذفها."
    )


@main_bot.callback_query_handler(func=lambda c: c.data == "a_store")
def a_store(c):
    if not is_admin(c.from_user.id):
        return
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("➕ إضافة سلعة", callback_data="a_product_add"))
    kb.add(telebot.types.InlineKeyboardButton("📋 السلع", callback_data="a_product_list"))
    kb.add(telebot.types.InlineKeyboardButton("📁 أقسام المتجر", callback_data="a_store_categories"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن", callback_data="a_home"))
    main_bot.edit_message_text(admin_store_menu_text(), c.message.chat.id, c.message.message_id, reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data == "a_product_add")
def a_product_add(c):
    if not is_admin(c.from_user.id):
        return
    add_wait(c.from_user.id, {"type": "product_name"})
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("❌ إلغاء", callback_data="a_store"))
    main_bot.edit_message_text(
        "🛍️ <b>إضافة سلعة جديدة</b>\n\nأرسل <b>اسم السلعة</b>:",
        c.message.chat.id, c.message.message_id, reply_markup=kb
    )


@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "product_name")
def product_name(m):
    name = (m.text or "").strip()
    if not name or len(name) > 80:
        main_bot.reply_to(m, "❌ اسم السلعة يجب أن يكون من 1 إلى 80 حرفًا.")
        return
    add_wait(m.from_user.id, {"type": "product_price", "name": name})
    main_bot.reply_to(m, "💰 أرسل <b>سعر السلعة بالنقاط</b>:")


@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "product_price")
def product_price(m):
    w = data["waiting"].get(str(m.from_user.id), {})
    try:
        price = int((m.text or "").strip())
        if price < 0:
            raise ValueError
    except Exception:
        main_bot.reply_to(m, "❌ أرسل سعرًا صحيحًا أكبر من أو يساوي 0.")
        return
    add_wait(m.from_user.id, {"type": "product_description", "name": w["name"], "price": price})
    main_bot.reply_to(m, "📝 أرسل <b>وصف السلعة</b> (أو اكتب - بدون وصف):")


@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "product_description")
def product_description(m):
    w = data["waiting"].get(str(m.from_user.id), {})
    desc = (m.text or "").strip()
    if desc == "-":
        desc = "لا يوجد وصف."
    if len(desc) > 500:
        main_bot.reply_to(m, "❌ الوصف طويل جدًا. الحد الأقصى 500 حرف.")
        return
    add_wait(m.from_user.id, {"type":"product_category","name":w["name"],"price":w["price"],"description":desc})
    cats=list(data.get("categories",{}).values())
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    for cat in cats:
        kb.add(telebot.types.InlineKeyboardButton(f"📁 {cat.get('name','قسم')}",callback_data=f"admin_pick_cat:{cat.get('id')}"))
    main_bot.reply_to(m,"📁 اختر <b>قسم السلعة</b>:",reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("admin_pick_cat:"))
def admin_pick_cat(c):
    if not is_admin(c.from_user.id): return
    w=data.get("waiting",{}).get(str(c.from_user.id),{})
    if w.get("type")!="product_category": return
    cid=c.data.split(":",1)[1]
    if cid not in data.get("categories",{}): return
    add_wait(c.from_user.id,{"type":"product_stock","name":w["name"],"price":w["price"],"description":w["description"],"category_id":cid})
    main_bot.answer_callback_query(c.id,"✅ تم اختيار القسم")
    main_bot.send_message(c.message.chat.id,"📦 أرسل <b>الكمية المتاحة</b>.\n\nأرسل <code>-1</code> إذا كانت الكمية غير محدودة:")

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "product_stock")
def product_stock(m):
    w = data["waiting"].get(str(m.from_user.id), {})
    try:
        stock = int((m.text or "").strip())
        if stock == 0 or stock < -1:
            raise ValueError
    except Exception:
        main_bot.reply_to(m, "❌ أرسل كمية صحيحة: 1 أو أكثر، أو -1 لغير المحدود.")
        return
    add_wait(m.from_user.id, {
        "type": "product_delivery", "name": w["name"], "price": w["price"],
        "description": w["description"], "stock": stock, "category_id": w.get("category_id","general")
    })
    main_bot.reply_to(
        m,
        "📦 أرسل <b>الملف أو المحتوى الذي سيصل للمشتري</b>.\n\n"
        "📎 يمكنك إرسال PDF / ZIP / RAR / TXT أو أي ملف كـ Document.\n"
        "📝 ويمكنك أيضًا إرسال نص أو صورة أو فيديو أو صوت."
    )


@main_bot.message_handler(
    content_types=["text", "document", "photo", "video", "audio", "voice"],
    func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "product_delivery"
)
def product_delivery(m):
    admin_uid=str(m.from_user.id); w=data["waiting"].get(admin_uid,{})
    dtype=None; fid=None; delivery=""
    if m.document: dtype="document"; fid=m.document.file_id
    elif m.photo: dtype="photo"; fid=m.photo[-1].file_id
    elif m.video: dtype="video"; fid=m.video.file_id
    elif m.audio: dtype="audio"; fid=m.audio.file_id
    elif m.voice: dtype="voice"; fid=m.voice.file_id
    elif m.text and m.text.strip(): dtype="text"; delivery=m.text.strip()
    else:
        main_bot.reply_to(m,"❌ أرسل ملفًا (PDF/ZIP/أي ملف) أو صورة/فيديو/صوت، أو أرسل نصًا."); return
    if dtype=="text" and len(delivery)>4000:
        main_bot.reply_to(m,"❌ النص طويل جدًا. الحد الأقصى 4000 حرف."); return
    pid=uuid.uuid4().hex[:12]
    product={"id":pid,"name":w["name"],"price":int(w["price"]),"description":w["description"],"stock":int(w["stock"]),"category_id":w.get("category_id","general"),"delivery":delivery,"delivery_type":dtype,"file_id":fid,"file_caption":(m.caption or ""),"active":True,"created_by":m.from_user.id,"created_at":time.time()}
    with data_lock:
        data.setdefault("products",{})[pid]=product; data["waiting"].pop(admin_uid,None); save_data()
    stock_text="غير محدود" if product["stock"]<0 else str(product["stock"]); cat=data.get("categories",{}).get(product["category_id"],{}).get("name","عام")
    main_bot.reply_to(m,f"✅ <b>تمت إضافة السلعة!</b>\n\n🛍️ {esc(product['name'])}\n📁 القسم: <b>{esc(cat)}</b>\n💰 السعر: <b>{product['price']}</b> نقطة\n📦 الكمية: <b>{stock_text}</b>\n📎 نوع التسليم: <b>{dtype}</b>")
    admin_panel(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_store_categories")
def a_store_categories(c):
    if not is_admin(c.from_user.id): return
    cats=list(data.get("categories",{}).values())
    text="📁 <b>أقسام المتجر</b>\n\n" + ("\n".join(f"• {esc(x.get('name','قسم'))} — {'🟢' if x.get('active',True) else '🔴'}" for x in cats) or "لا توجد أقسام.")
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("➕ إضافة قسم",callback_data="a_category_add"))
    for x in cats: kb.add(telebot.types.InlineKeyboardButton(f"⚙️ {x.get('name','قسم')[:30]}",callback_data=f"a_category:{x.get('id')}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 إدارة المتجر",callback_data="a_store"))
    main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_category_add")
def a_category_add(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"category_name"}); main_bot.edit_message_text("📁 أرسل اسم القسم الجديد:",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="category_name")
def category_name(m):
    name=(m.text or "").strip()
    if not name or len(name)>50: main_bot.reply_to(m,"❌ اسم القسم يجب أن يكون من 1 إلى 50 حرفًا."); return
    cid=uuid.uuid4().hex[:10]
    with data_lock: data.setdefault("categories",{})[cid]={"id":cid,"name":name,"active":True,"created_at":time.time()}; data["waiting"].pop(str(m.from_user.id),None); save_data()
    main_bot.reply_to(m,f"✅ تم إنشاء القسم: <b>{esc(name)}</b>"); admin_panel(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_category:"))
def a_category(c):
    if not is_admin(c.from_user.id): return
    cid=c.data.split(":",1)[1]; cat=data.get("categories",{}).get(cid)
    if not cat: return
    count=len([p for p in data.get("products",{}).values() if p.get("category_id")==cid])
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("🔴 إيقاف القسم" if cat.get("active",True) else "🟢 تشغيل القسم",callback_data=f"a_category_toggle:{cid}"))
    if cid!="general": kb.add(telebot.types.InlineKeyboardButton("🗑️ حذف القسم",callback_data=f"a_category_delete:{cid}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 الأقسام",callback_data="a_store_categories"))
    main_bot.edit_message_text(f"📁 <b>{esc(cat.get('name','قسم'))}</b>\n\n📦 السلع: <b>{count}</b>\n📊 الحالة: {'🟢 متاح' if cat.get('active',True) else '🔴 متوقف'}",c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_category_toggle:"))
def a_category_toggle(c):
    if not is_admin(c.from_user.id): return
    cid=c.data.split(":",1)[1]; cat=data.get("categories",{}).get(cid)
    if cat: cat["active"]=not cat.get("active",True); save_data(); main_bot.answer_callback_query(c.id,"✅ تم تغيير الحالة",show_alert=True); a_category(c)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_category_delete:"))
def a_category_delete(c):
    if not is_admin(c.from_user.id): return
    cid=c.data.split(":",1)[1]
    if cid=="general": return
    with data_lock:
        for p in data.get("products",{}).values():
            if p.get("category_id")==cid: p["category_id"]="general"
        data.get("categories",{}).pop(cid,None); save_data()
    main_bot.answer_callback_query(c.id,"🗑️ تم حذف القسم ونقل سلعه إلى عام",show_alert=True); a_store_categories(c)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_product_list")
def a_product_list(c):
    if not is_admin(c.from_user.id):
        return
    products = list(data.get("products", {}).values())
    text = "📋 <b>سلع المتجر</b>\n\n"
    if not products:
        text += "لا توجد سلع."
    else:
        for i, p in enumerate(products[-30:][::-1], 1):
            stock = "∞" if int(p.get("stock", -1)) < 0 else str(p.get("stock", 0))
            status = "🟢" if p.get("active", True) else "🔴"
            text += f"{i}. {status} <b>{esc(p.get('name','سلعة'))}</b> — {p.get('price',0)} نقطة — مخزون: {stock}\n"
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    for p in products[-20:][::-1]:
        kb.add(telebot.types.InlineKeyboardButton(
            f"⚙️ {p.get('name','سلعة')[:30]}", callback_data=f"a_product:{p.get('id')}"
        ))
    kb.add(telebot.types.InlineKeyboardButton("➕ إضافة سلعة", callback_data="a_product_add"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 إدارة المتجر", callback_data="a_store"))
    main_bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_product:"))
def a_product(c):
    if not is_admin(c.from_user.id):
        return
    pid = c.data.split(":", 1)[1]
    p = data.get("products", {}).get(pid)
    if not p:
        main_bot.answer_callback_query(c.id, "❌ السلعة غير موجودة.", show_alert=True)
        a_product_list(c)
        return
    stock = "غير محدود" if int(p.get("stock", -1)) < 0 else str(p.get("stock", 0))
    status = "متاحة 🟢" if p.get("active", True) else "متوقفة 🔴"
    text = (
        "🛍️ <b>إدارة السلعة</b>\n\n"
        f"📌 الاسم: <b>{esc(p.get('name','سلعة'))}</b>\n"
        f"💰 السعر: <b>{p.get('price',0)}</b> نقطة\n"
        f"📦 المخزون: <b>{stock}</b>\n"
        f"📊 الحالة: <b>{status}</b>\n\n"
        f"📝 {esc(p.get('description','لا يوجد وصف.'))}"
    )
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    if p.get("active", True):
        kb.add(telebot.types.InlineKeyboardButton("🔴 إيقاف السلعة", callback_data=f"a_product_toggle:{pid}"))
    else:
        kb.add(telebot.types.InlineKeyboardButton("🟢 تشغيل السلعة", callback_data=f"a_product_toggle:{pid}"))
    kb.add(telebot.types.InlineKeyboardButton("🗑️ حذف السلعة", callback_data=f"a_product_delete:{pid}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 السلع", callback_data="a_product_list"))
    main_bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=kb)


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_product_toggle:"))
def a_product_toggle(c):
    if not is_admin(c.from_user.id):
        return
    pid = c.data.split(":", 1)[1]
    p = data.get("products", {}).get(pid)
    if not p:
        main_bot.answer_callback_query(c.id, "❌ السلعة غير موجودة.", show_alert=True)
        return
    p["active"] = not p.get("active", True)
    save_data()
    main_bot.answer_callback_query(c.id, "✅ تم تغيير حالة السلعة.", show_alert=True)
    a_product(c)


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_product_delete:"))
def a_product_delete(c):
    if not is_admin(c.from_user.id):
        return
    pid = c.data.split(":", 1)[1]
    p = data.get("products", {}).get(pid)
    if not p:
        main_bot.answer_callback_query(c.id, "❌ السلعة غير موجودة.", show_alert=True)
        return
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"a_product_delete_yes:{pid}"),
        telebot.types.InlineKeyboardButton("❌ إلغاء", callback_data=f"a_product:{pid}")
    )
    main_bot.edit_message_text(
        f"⚠️ هل تريد حذف السلعة <b>{esc(p.get('name','سلعة'))}</b> نهائيًا؟",
        c.message.chat.id, c.message.message_id, reply_markup=kb
    )


@main_bot.callback_query_handler(func=lambda c: c.data.startswith("a_product_delete_yes:"))
def a_product_delete_yes(c):
    if not is_admin(c.from_user.id):
        return
    pid = c.data.split(":", 1)[1]
    with data_lock:
        p = data.get("products", {}).pop(pid, None)
        save_data()
    if p:
        main_bot.answer_callback_query(c.id, "🗑️ تم حذف السلعة.", show_alert=True)
    a_product_list(c)


# settings
SETTING_LABELS={"join_points":"نقاط الانضمام","ref_points":"نقاط الدعوة","daily_gift":"الهدية اليومية","points_per_member":"سعر العضو","min_fund":"الحد الأدنى للتمويل","max_fund":"الحد الأقصى للتمويل","transfer_min":"الحد الأدنى للتحويل","transfer_max":"الحد الأقصى للتحويل","leaver_check_interval":"فترة فحص المغادرين بالثواني"}
@main_bot.callback_query_handler(func=lambda c: c.data == "a_settings")
def a_settings(c):
    if not is_admin(c.from_user.id): return
    s=data["settings"]; text=("⚙️ <b>الإعدادات والأسعار</b>\n\n" + "\n".join(f"{k}: <b>{s[k]}</b>" for k in SETTING_LABELS))
    kb=telebot.types.InlineKeyboardMarkup(row_width=2)
    for key,label in SETTING_LABELS.items(): kb.add(telebot.types.InlineKeyboardButton(label,callback_data=f"set_{key}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def set_prompt(c):
    if not is_admin(c.from_user.id): return
    key=c.data[4:]
    if key not in SETTING_LABELS: return
    add_wait(c.from_user.id,{"type":"change_setting","key":key})
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 إلغاء",callback_data="a_settings"))
    main_bot.edit_message_text(f"📝 أرسل القيمة الجديدة لـ <b>{SETTING_LABELS[key]}</b> (رقم صحيح):",c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="change_setting")
def set_value(m):
    uid=str(m.from_user.id); w=data["waiting"][uid]; key=w["key"]
    try: val=int((m.text or "").strip()); assert val>=0
    except: main_bot.reply_to(m,"❌ أرسل رقمًا صحيحًا أكبر من أو يساوي 0."); return
    if key=="max_fund" and val<int(data["settings"]["min_fund"]): main_bot.reply_to(m,"❌ الحد الأقصى لا يمكن أن يكون أقل من الحد الأدنى."); return
    if key=="min_fund" and val>int(data["settings"]["max_fund"]): main_bot.reply_to(m,"❌ الحد الأدنى لا يمكن أن يكون أكبر من الحد الأقصى."); return
    with data_lock: data["settings"][key]=val; data["waiting"].pop(uid,None); save_data()
    main_bot.reply_to(m,f"✅ تم تحديث <b>{SETTING_LABELS[key]}</b> إلى <b>{val}</b>."); admin_panel(m.chat.id)

# modify points
@main_bot.callback_query_handler(func=lambda c: c.data == "a_modify_points")
def mod_points_menu(c):
    if not is_admin(c.from_user.id): return
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("➖ إزالة نقاط من شخص",callback_data="a_remove_points"))
    kb.add(telebot.types.InlineKeyboardButton("🗑️ إزالة كل نقاط شخص",callback_data="a_remove_all_points"))
    kb.add(telebot.types.InlineKeyboardButton("➕ إضافة/تعديل نقاط",callback_data="a_add_points"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home"))
    main_bot.edit_message_text("💰 <b>إدارة نقاط المستخدمين</b>\n\nاختر العملية:",c.message.chat.id,c.message.message_id,reply_markup=kb)

# ---------------- إزالة عدد محدد من النقاط ----------------
@main_bot.callback_query_handler(func=lambda c: c.data == "a_remove_points")
def remove_points_start(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"remove_points_uid"})
    main_bot.edit_message_text("👤 أرسل <b>ID المستخدم</b> الذي تريد إزالة النقاط منه:",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="remove_points_uid")
def remove_points_uid(m):
    target=(m.text or "").strip()
    if target not in data["users"]:
        main_bot.reply_to(m,"❌ هذا المستخدم غير مسجل.")
        return
    add_wait(m.from_user.id,{"type":"remove_points_val","target":target})
    current=int(data["users"][target].get("points",0))
    main_bot.reply_to(m,f"💰 رصيد المستخدم الحالي: <b>{current}</b> نقطة\n\nأرسل عدد النقاط التي تريد إزالتها:")

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="remove_points_val")
def remove_points_value(m):
    admin_uid=str(m.from_user.id); w=data["waiting"][admin_uid]
    try:
        amount=int((m.text or "").strip())
        if amount <= 0: raise ValueError
    except Exception:
        main_bot.reply_to(m,"❌ أرسل عددًا صحيحًا أكبر من 0.")
        return
    target=w["target"]
    u=get_user(target)
    old=int(u.get("points",0))
    removed=min(amount,old)
    with data_lock:
        u["points"]=old-removed
        data["waiting"].pop(admin_uid,None)
        save_data()
    main_bot.reply_to(m,f"✅ تم إزالة <b>{removed}</b> نقطة من المستخدم <code>{target}</code>.\n💰 الرصيد السابق: <b>{old}</b>\n💰 الرصيد الجديد: <b>{u['points']}</b>")
    admin_panel(m.chat.id)

# ---------------- إزالة كل النقاط من شخص ----------------
@main_bot.callback_query_handler(func=lambda c: c.data == "a_remove_all_points")
def remove_all_points_start(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"remove_all_points_uid"})
    main_bot.edit_message_text("🗑️ أرسل <b>ID المستخدم</b> لإزالة جميع نقاطه:",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="remove_all_points_uid")
def remove_all_points_uid(m):
    admin_uid=str(m.from_user.id)
    target=(m.text or "").strip()
    if target not in data["users"]:
        main_bot.reply_to(m,"❌ هذا المستخدم غير مسجل.")
        return
    u=get_user(target)
    old=int(u.get("points",0))
    with data_lock:
        u["points"]=0
        data["waiting"].pop(admin_uid,None)
        save_data()
    main_bot.reply_to(m,f"🗑️ تم إزالة جميع نقاط المستخدم <code>{target}</code>.\n💰 النقاط التي تمت إزالتها: <b>{old}</b>\n💰 الرصيد الجديد: <b>0</b>")
    admin_panel(m.chat.id)

# ---------------- إضافة/تعديل النقاط (الوظيفة القديمة) ----------------
@main_bot.callback_query_handler(func=lambda c: c.data == "a_add_points")
def mod_start(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"mod_points_uid"})
    main_bot.edit_message_text("👤 أرسل <b>ID المستخدم</b>:",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="mod_points_uid")
def mod_uid(m):
    target=(m.text or "").strip()
    if target not in data["users"]:
        main_bot.reply_to(m,"❌ هذا المستخدم غير مسجل.")
        return
    add_wait(m.from_user.id,{"type":"mod_points_val","target":target})
    main_bot.reply_to(m,"💰 أرسل عدد النقاط. يمكنك استخدام رقم موجب للإضافة أو سالب للخصم:")

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="mod_points_val")
def mod_value(m):
    uid=str(m.from_user.id); w=data["waiting"][uid]
    try: val=int((m.text or "").strip())
    except:
        main_bot.reply_to(m,"❌ أرسل رقمًا فقط.")
        return
    u=get_user(w["target"])
    with data_lock:
        u["points"]=max(0,int(u.get("points",0))+val)
        data["waiting"].pop(uid,None)
        save_data()
    main_bot.reply_to(m,f"✅ تم التعديل. الرصيد الجديد: <b>{u['points']}</b>")
    admin_panel(m.chat.id)

# broadcast
@main_bot.callback_query_handler(func=lambda c: c.data == "a_broadcast")
def broadcast_prompt(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"broadcast"}); main_bot.edit_message_text("📣 أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين.\n\nيمكنك إرسال نص أو صورة أو ملف أو فيديو، وسيتم نسخ الرسالة.",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="broadcast")
def broadcast(m):
    pop_wait(m.from_user.id); main_bot.reply_to(m,"🚀 جاري الإرسال...")
    ok=bad=0
    for uid in list(data["users"].keys()):
        try: main_bot.copy_message(int(uid),m.chat.id,m.message_id); ok+=1
        except: bad+=1
        time.sleep(0.03)
    main_bot.send_message(m.chat.id,f"✅ اكتملت الإذاعة!\n🟢 نجاح: {ok}\n🔴 فشل: {bad}")

# entry notifications
@main_bot.callback_query_handler(func=lambda c: c.data == "a_entry_notify")
def a_entry(c):
    if not is_admin(c.from_user.id): return
    en=entry_notify(); text=f"🔔 <b>إشعارات الدخول</b>\n\nالحالة: <b>{'مفعلة ✅' if en else 'متوقفة ❌'}</b>"
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔕 إيقاف" if en else "🔔 تشغيل",callback_data="toggle_entry_notify")); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "toggle_entry_notify")
def toggle_entry(c):
    if not is_admin(c.from_user.id): return
    with data_lock: data["settings"]["entry_notifications"]=not entry_notify(); save_data()
    a_entry(c)

# maintenance
@main_bot.callback_query_handler(func=lambda c: c.data == "a_maintenance")
def a_maintenance(c):
    if not is_admin(c.from_user.id): return
    en=maintenance(); text=f"🛠️ <b>نظام الصيانة</b>\n\nالحالة: <b>{'🔴 البوت مقفول' if en else '🟢 البوت يعمل'}</b>\n\nالأدمن يظل قادرًا على استخدام اللوحة أثناء الصيانة."
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🟢 تشغيل البوت" if en else "🛑 قفل البوت",callback_data="toggle_maintenance")); kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "toggle_maintenance")
def toggle_maintenance(c):
    if not is_admin(c.from_user.id): return
    with data_lock: data["settings"]["maintenance"]=not maintenance(); save_data()
    main_bot.answer_callback_query(c.id,"تم تغيير حالة الصيانة.",show_alert=True); a_maintenance(c)

# recharge codes admin
@main_bot.callback_query_handler(func=lambda c: c.data == "a_codes")
def a_codes(c):
    if not is_admin(c.from_user.id): return
    active = sum(1 for x in data.get("codes", {}).values() if x.get("active", True))
    text = (
        "🎟️ <b>أكواد الشحن</b>\n\n"
        f"🔢 إجمالي الأكواد: <b>{len(data.get('codes', {}))}</b>\n"
        f"🟢 النشطة: <b>{active}</b>\n\n"
        "يمكنك إنشاء كود بقيمة وعدد استخدامات محدد."
    )
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(telebot.types.InlineKeyboardButton("➕ إنشاء كود", callback_data="a_code_create"))
    kb.add(telebot.types.InlineKeyboardButton("📋 آخر الأكواد", callback_data="a_code_list"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن", callback_data="a_home"))
    main_bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_code_create")
def a_code_create(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id, {"type": "code_points"})
    main_bot.edit_message_text("🎟️ أرسل عدد النقاط التي سيعطيها الكود:", c.message.chat.id, c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "code_points")
def code_points(m):
    try:
        points = int((m.text or "").strip())
        if points < int(data["settings"].get("code_min", 1)): raise ValueError
    except Exception:
        main_bot.reply_to(m, "❌ أرسل عدد نقاط صحيح.")
        return
    add_wait(m.from_user.id, {"type": "code_uses", "points": points})
    main_bot.reply_to(m, "🔢 أرسل عدد مرات استخدام الكود (مثلاً 1):")

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting", {}).get(str(m.from_user.id)), dict) and data["waiting"][str(m.from_user.id)].get("type") == "code_uses")
def code_uses(m):
    uid=str(m.from_user.id); w=data["waiting"][uid]
    try:
        uses=int((m.text or "").strip())
        if uses < 1: raise ValueError
    except Exception:
        main_bot.reply_to(m, "❌ أرسل عدد استخدامات صحيح أكبر من صفر.")
        return
    with data_lock:
        code=create_recharge_code(w["points"], m.from_user.id, uses)
        data["waiting"].pop(uid,None); save_data()
    main_bot.reply_to(m, f"✅ تم إنشاء الكود:\n\n<code>{code}</code>\n\n💰 القيمة: <b>{w['points']}</b> نقطة\n🔢 الاستخدامات: <b>{uses}</b>")
    admin_panel(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_code_list")
def a_code_list(c):
    if not is_admin(c.from_user.id): return
    items=list(data.get("codes", {}).values())[-30:][::-1]
    text="📋 <b>آخر أكواد الشحن</b>\n\n"
    if not items: text += "لا توجد أكواد."
    else:
        for x in items:
            text += f"<code>{esc(x['code'])}</code> — 💰 {x['points']} — {x['uses']}/{x['max_uses']} — {'🟢' if x.get('active',True) else '🔴'}\n"
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 رجوع",callback_data="a_codes"))
    main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "a_leavers")
def a_leavers(c):
    if not is_admin(c.from_user.id): return
    interval=int(data["settings"].get("leaver_check_interval",300))
    text=("⚠️ <b>فحص المغادرين</b>\n\n"
          "يفحص الأعضاء الذين تم احتسابهم في حملات التمويل، وإذا غادر المستخدم القناة يتم حذف احتساب اشتراكه وخصم مكافأة الانضمام من رصيده (حتى صفر).\n\n"
          f"⏱️ الفحص التلقائي كل <b>{interval}</b> ثانية.\n\n"
          "يمكنك تشغيل فحص فوري من الزر أدناه.")
    kb=telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("🔎 فحص الآن",callback_data="run_leaver_scan"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home"))
    main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "run_leaver_scan")
def run_leaver_scan(c):
    if not is_admin(c.from_user.id): return
    removed=leaver_scan()
    main_bot.answer_callback_query(c.id,f"✅ تم الفحص. المغادرون: {removed}",show_alert=True)
    a_leavers(c)

# mandatory admin
@main_bot.callback_query_handler(func=lambda c: c.data == "a_mandatory")
def a_mandatory(c):
    if not is_admin(c.from_user.id): return
    ch=mandatory_channels(); status="❌ لا توجد قنوات." if not ch else "\n".join(f"{i}. {x.get('title','القناة')} {x.get('username','')}" for i,x in enumerate(ch,1))
    text="🔒 <b>الاشتراك الإجباري</b>\n\n"+status+"\n\nالمستخدمون لن يستطيعوا استخدام البوت قبل الاشتراك."
    kb=telebot.types.InlineKeyboardMarkup(row_width=1); kb.add(telebot.types.InlineKeyboardButton("➕ إضافة قناة",callback_data="mandatory_add"))
    if ch: kb.add(telebot.types.InlineKeyboardButton("➖ حذف قناة",callback_data="mandatory_remove_menu"),telebot.types.InlineKeyboardButton("📋 عرض القنوات",callback_data="mandatory_list"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 لوحة الأدمن",callback_data="a_home")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data == "mandatory_add")
def mandatory_add(c):
    if not is_admin(c.from_user.id): return
    add_wait(c.from_user.id,{"type":"mandatory_add"}); main_bot.edit_message_text("➕ <b>إضافة قناة للاشتراك الإجباري</b>\n\nأرسل معرف قناة عامة مثل <code>@MyChannel</code>.",c.message.chat.id,c.message.message_id)

@main_bot.message_handler(func=lambda m: is_admin(m.from_user.id) and isinstance(data.get("waiting",{}).get(str(m.from_user.id)),dict) and data["waiting"][str(m.from_user.id)].get("type")=="mandatory_add")
def mandatory_add_msg(m):
    u=clean_channel(m.text)
    if not u: main_bot.reply_to(m,"❌ أرسل معرفًا عامًا صحيحًا مثل @MyChannel."); return
    info=get_channel_info(u)
    if not info: main_bot.reply_to(m,"❌ تعذر الوصول للقناة. تأكد أنها عامة وأن البوت المساعد موجود فيها."); return
    ok,reason=assistant_permissions(info["id"])
    if not ok: main_bot.reply_to(m,reason); return
    if any(str(x.get("id"))==str(info["id"]) for x in mandatory_channels()): main_bot.reply_to(m,"⚠️ القناة مضافة بالفعل."); return
    with data_lock: data["settings"]["mandatory_channels"].append(info); data["waiting"].pop(str(m.from_user.id),None); save_data()
    main_bot.reply_to(m,f"✅ تمت إضافة <b>{info['title']}</b> للاشتراك الإجباري."); admin_panel(m.chat.id)

@main_bot.callback_query_handler(func=lambda c: c.data == "mandatory_remove_menu")
def mandatory_remove_menu(c):
    if not is_admin(c.from_user.id): return
    kb=telebot.types.InlineKeyboardMarkup(row_width=1)
    for i,x in enumerate(mandatory_channels()): kb.add(telebot.types.InlineKeyboardButton(f"🗑️ {i+1}. {x.get('title','القناة')}",callback_data=f"mandatory_remove:{i}"))
    kb.add(telebot.types.InlineKeyboardButton("🔙 رجوع",callback_data="a_mandatory")); main_bot.edit_message_text("➖ اختر القناة التي تريد حذفها:",c.message.chat.id,c.message.message_id,reply_markup=kb)

@main_bot.callback_query_handler(func=lambda c: c.data.startswith("mandatory_remove:"))
def mandatory_remove(c):
    if not is_admin(c.from_user.id): return
    try:
        i=int(c.data.split(":",1)[1]); ch=mandatory_channels(); removed=ch.pop(i); data["settings"]["mandatory_channels"]=ch; save_data(); main_bot.answer_callback_query(c.id,"✅ تم حذف القناة.",show_alert=True); a_mandatory(c)
    except: main_bot.answer_callback_query(c.id,"❌ تعذر الحذف.",show_alert=True)

@main_bot.callback_query_handler(func=lambda c: c.data == "mandatory_list")
def mandatory_list(c):
    if not is_admin(c.from_user.id): return
    ch=mandatory_channels(); text="📋 <b>قنوات الاشتراك الإجباري</b>\n\n"+"\n".join(f"{i}. {x.get('title','القناة')} — <code>{x.get('username','')}</code>" for i,x in enumerate(ch,1))
    kb=telebot.types.InlineKeyboardMarkup(); kb.add(telebot.types.InlineKeyboardButton("🔙 رجوع",callback_data="a_mandatory")); main_bot.edit_message_text(text,c.message.chat.id,c.message.message_id,reply_markup=kb)

# =========================================================
# RUN
# =========================================================
def _delete_webhooks():
    """Remove stale webhooks before starting long polling.
    Telegram does not allow getUpdates while a webhook is active.
    """
    for name, bot in (("Main", main_bot), ("Assistant", assistant_bot)):
        try:
            bot.delete_webhook(drop_pending_updates=True)
            print(f"✅ {name} Bot webhook deleted")
        except Exception as e:
            print(f"⚠️ {name} webhook cleanup failed: {e!r}")


def _run_assistant():
    try:
        assistant_bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30,
            allowed_updates=None,
        )
    except Exception as e:
        print(f"❌ Assistant polling stopped: {e!r}")


if __name__ == "__main__":
    print("🤖 Main + Assistant bots are starting...")

    # IMPORTANT: delete any old webhook before getUpdates/infinity_polling.
    _delete_webhooks()

    threading.Thread(target=_run_assistant, daemon=True).start()
    threading.Thread(target=leaver_worker, daemon=True).start()

    try:
        main_bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30,
            allowed_updates=None,
        )
    except Exception as e:
        print(f"❌ Main polling stopped: {e!r}")
