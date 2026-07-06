"""
Telegram Digital Products Shop Bot
==================================

Railway-ready Telegram bot using:
- aiogram v3
- MongoDB Atlas via motor
- FSM admin flows
- Balance + deposit approval
- Instant stock delivery
- Support tickets
- Referral points
- Admin statistics
- Payment method management
- Security checks, rate limiting, logging

Important:
Use this only for lawful digital products that you own or are authorized to distribute.
Do not sell stolen credentials, compromised accounts, illegal access, spam tools, malware, or anything unauthorized.
"""

import asyncio
import html
import hmac
import hashlib
import json
import urllib.parse
import urllib.request
import logging
import math
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

# ==========================================================
# ENV / CONFIG
# ==========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN in environment variables.")

if not MONGODB_URI:
    raise RuntimeError("Missing MONGODB_URI in environment variables.")

ADMIN_IDS: List[int] = []
for raw_id in ADMIN_IDS_RAW.split(","):
    raw_id = raw_id.strip()
    if raw_id.isdigit():
        ADMIN_IDS.append(int(raw_id))

if not ADMIN_IDS:
    raise RuntimeError("Missing ADMIN_IDS. Example: ADMIN_IDS=123456789,987654321")

OWNER_ID = ADMIN_IDS[0]

APP_NAME = os.getenv("APP_NAME", "Telegram Digital Shop")
DATABASE_NAME = os.getenv("DATABASE_NAME", "telegram_digital_shop")
CURRENCY = os.getenv("CURRENCY", "USDT")
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Asia/Karachi")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
# Supports both names: BINANCE_API_SECRET and BINANCE_SECRET_KEY
BINANCE_API_SECRET = (os.getenv("BINANCE_API_SECRET", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip())
BINANCE_AUTO_VERIFY = os.getenv("BINANCE_AUTO_VERIFY", "false").lower().strip() in {"1", "true", "yes", "on"}
BINANCE_COIN = os.getenv("BINANCE_COIN", "USDT").strip().upper()
BINANCE_NETWORK = os.getenv("BINANCE_NETWORK", "TRC20").strip().upper()
BINANCE_WALLET_ADDRESS = os.getenv("BINANCE_WALLET_ADDRESS", "").strip()
BINANCE_MIN_DEPOSIT = float(os.getenv("BINANCE_MIN_DEPOSIT", "1") or 1)
PREORDER_ADVANCE_FEE_PERCENT = float(os.getenv("PREORDER_ADVANCE_FEE_PERCENT", "4") or 4)

# Basic limits
RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "0.65"))
BROADCAST_DELAY = float(os.getenv("BROADCAST_DELAY", "0.04"))
MAX_STOCK_PREVIEW = int(os.getenv("MAX_STOCK_PREVIEW", "50"))
MAX_BROADCAST_TEXT = int(os.getenv("MAX_BROADCAST_TEXT", "3500"))
MAX_SUPPORT_MESSAGE = int(os.getenv("MAX_SUPPORT_MESSAGE", "3500"))
MAX_PRODUCT_DESCRIPTION = int(os.getenv("MAX_PRODUCT_DESCRIPTION", "1500"))

# ==========================================================
# LOGGING
# ==========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("telegram-shop")

# ==========================================================
# BOT / DB
# ==========================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

mongo = AsyncIOMotorClient(MONGODB_URI)
db = mongo[DATABASE_NAME]

users = db.users
products = db.products
orders = db.orders
payments = db.payments
payment_methods = db.payment_methods
tickets = db.tickets
settings = db.settings
logs = db.logs
refunds = db.refunds
audit_events = db.audit_events
content_methods = db.content_methods
free_products = db.free_products
canva_requests = db.canva_requests
admin_accounts = db.admin_accounts
point_exchanges = db.point_exchanges
preorders = db.preorders
preorder_products = db.preorder_products
reseller_payouts = db.reseller_payouts
seller_profiles = db.seller_profiles
x_orders = db.x_orders
x_payments = db.x_payments

# ==========================================================
# IN-MEMORY RATE LIMITING
# ==========================================================

RATE_BUCKET: Dict[int, float] = {}
ADMIN_LAST_ACTION: Dict[int, float] = {}

# ==========================================================
# UTILS
# ==========================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def ts() -> int:
    return int(time.time())

def escape(value: Any) -> str:
    return html.escape(str(value), quote=False)

def code(value: Any) -> str:
    return f"<code>{escape(value)}</code>"

def bold(value: Any) -> str:
    return f"<b>{escape(value)}</b>"

def money(value: Any) -> str:
    try:
        return f"{float(value):.2f} {CURRENCY}"
    except Exception:
        return f"0.00 {CURRENCY}"

def preorder_advance_total(base_price_each: Any, quantity: int) -> Tuple[float, float, float]:
    """Return subtotal, fee amount, total for fixed-price preorder products."""
    try:
        subtotal = round(float(base_price_each) * int(quantity), 2)
    except Exception:
        subtotal = 0.0
    fee = round(subtotal * (PREORDER_ADVANCE_FEE_PERCENT / 100), 2)
    total = round(subtotal + fee, 2)
    return subtotal, fee, total

def short_id(oid: Any) -> str:
    s = str(oid)
    return s[-8:]

def clean_username(username: str) -> str:
    return username.strip().replace("@", "").replace("https://t.me/", "").replace("t.me/", "")

def is_valid_username(username: str) -> bool:
    username = clean_username(username)
    return bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", username))

def parse_float(text: str) -> Optional[float]:
    try:
        value = float(text.strip().replace(",", "."))
        if math.isfinite(value):
            return value
        return None
    except Exception:
        return None

def parse_int(text: str) -> Optional[int]:
    try:
        value = int(text.strip())
        return value
    except Exception:
        return None

def object_id(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

DEFAULT_BULK_DISCOUNT_RULES = [
    {"quantity": 2, "percent": 5},
    {"quantity": 5, "percent": 10},
    {"quantity": 10, "percent": 15},
]

def normalize_bulk_discount_rules(value: Any) -> List[Dict[str, int]]:
    rules: List[Dict[str, int]] = []
    if isinstance(value, str):
        # Accepted formats:
        # 2=5,5=10,10=15
        # or one rule per line: 2 5
        chunks = []
        for part in value.replace(";", ",").split(","):
            chunks.extend([x for x in part.splitlines() if x.strip()])
        for chunk in chunks:
            nums = re.findall(r"\d+", chunk)
            if len(nums) >= 2:
                rules.append({"quantity": int(nums[0]), "percent": int(nums[1])})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                q = parse_int(str(item.get("quantity", "")))
                pc = parse_int(str(item.get("percent", "")))
            else:
                nums = re.findall(r"\d+", str(item))
                q = int(nums[0]) if len(nums) >= 1 else None
                pc = int(nums[1]) if len(nums) >= 2 else None
            if q is not None and pc is not None:
                rules.append({"quantity": q, "percent": pc})
    clean: Dict[int, int] = {}
    for rule in rules:
        q = int(rule.get("quantity", 0))
        pc = int(rule.get("percent", 0))
        if q > 1 and 0 <= pc <= 100:
            clean[q] = pc
    return [{"quantity": q, "percent": clean[q]} for q in sorted(clean)]

async def get_bulk_discount_rules() -> List[Dict[str, int]]:
    value = await get_setting("bulk_discount_rules", DEFAULT_BULK_DISCOUNT_RULES)
    rules = normalize_bulk_discount_rules(value)
    return rules or DEFAULT_BULK_DISCOUNT_RULES

async def discount_for_quantity_async(quantity: int) -> float:
    best_percent = 0
    for rule in await get_bulk_discount_rules():
        if quantity >= int(rule.get("quantity", 0)):
            best_percent = max(best_percent, int(rule.get("percent", 0)))
    return best_percent / 100

def discount_for_quantity(quantity: int) -> float:
    # Fallback used only where async settings cannot be loaded.
    best_percent = 0
    for rule in DEFAULT_BULK_DISCOUNT_RULES:
        if quantity >= int(rule.get("quantity", 0)):
            best_percent = max(best_percent, int(rule.get("percent", 0)))
    return best_percent / 100

def discount_label(quantity: int) -> str:
    d = discount_for_quantity(quantity)
    if d <= 0:
        return "No discount"
    return f"{int(d * 100)}% OFF"

async def bulk_discount_text() -> str:
    rules = await get_bulk_discount_rules()
    if not rules:
        return "Bulk discounts: No bulk discounts"
    text = "Bulk discounts:\n"
    for rule in rules:
        text += f"• {int(rule['quantity'])} items = {int(rule['percent'])}% OFF\n"
    return text.rstrip()


def make_unique_deposit_amount(amount: float, user_id: int) -> float:
    """Create a small unique amount so two users paying same value can still be matched."""
    base = round(float(amount), 3)
    # 0.001001 to 0.009999 unique suffix based on user/time.
    seed = (int(user_id) + int(time.time())) % 8999 + 1001
    return round(base + (seed / 1_000_000), 6)

async def verify_binance_deposit_tx(
    txid: str,
    amount: float,
    coin: str | None = None,
    expected_network: str | None = None,
    expected_address: str | None = None,
) -> Tuple[bool, str]:
    """Verify a completed Binance spot deposit by TxID, amount, coin, optional network/address.
    Requires BINANCE_API_KEY and BINANCE_API_SECRET/BINANCE_SECRET_KEY environment variables.
    """
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        return False, "Binance API key/secret is not configured."
    txid = (txid or "").strip()
    if not txid:
        return False, "Empty transaction ID."
    coin = (coin or BINANCE_COIN).upper().strip()
    expected_network = (expected_network or "").upper().strip()
    expected_address = (expected_address or "").strip()

    # Duplicate TXID protection: never approve the same TxID twice.
    existing = await payments.find_one({"txid": {"$regex": f"^{re.escape(txid)}$", "$options": "i"}, "status": {"$in": ["approved", "pending"]}})
    if existing:
        return False, "This TxID was already used/submitted."
    existing_preorder = await preorders.find_one({"txid": {"$regex": f"^{re.escape(txid)}$", "$options": "i"}, "payment_status": {"$in": ["submitted", "auto_verified"]}})
    if existing_preorder:
        return False, "This TxID was already used/submitted for a preorder."

    def _request() -> Tuple[bool, str]:
        timestamp = int(time.time() * 1000)
        params = {
            "coin": coin,
            "status": 1,
            "timestamp": timestamp,
        }
        query = urllib.parse.urlencode(params)
        signature = hmac.new(BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{query}&signature={signature}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": BINANCE_API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return False, f"Binance API error: {e}"
        if not isinstance(payload, list):
            return False, f"Unexpected Binance response: {payload}"

        for row in payload:
            row_tx = str(row.get("txId") or row.get("txID") or row.get("tranId") or "")
            row_amount = float(row.get("amount", 0) or 0)
            row_coin = str(row.get("coin", "")).upper()
            row_network = str(row.get("network", "")).upper()
            row_address = str(row.get("address", "") or row.get("addressTag", ""))

            tx_matches = txid.lower() in row_tx.lower() or row_tx.lower() in txid.lower()
            coin_matches = row_coin == coin
            amount_matches = row_amount + 1e-9 >= float(amount)
            network_matches = True if not expected_network else row_network == expected_network
            address_matches = True if not expected_address else expected_address.lower() in row_address.lower()

            if tx_matches and coin_matches and amount_matches and network_matches and address_matches:
                details = f"Verified {row_amount} {row_coin}"
                if row_network:
                    details += f" on {row_network}"
                return True, details
        return False, "No completed matching Binance deposit found."
    return await asyncio.to_thread(_request)

async def safe_send_message(chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramForbiddenError:
        return False
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except Exception:
            return False
    except Exception as e:
        logger.warning("safe_send_message failed chat_id=%s err=%s", chat_id, e)
        return False

async def audit(action: str, actor_id: Optional[int] = None, data: Optional[Dict[str, Any]] = None) -> None:
    try:
        await audit_events.insert_one({
            "action": action,
            "actor_id": actor_id,
            "data": data or {},
            "created_at": utcnow(),
        })
    except Exception as e:
        logger.warning("Audit insert failed: %s", e)

async def get_setting(key: str, default: Any = None) -> Any:
    doc = await settings.find_one({"key": key})
    if not doc:
        return default
    return doc.get("value", default)

async def set_setting(key: str, value: Any) -> None:
    await settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value, "updated_at": utcnow()}},
        upsert=True,
    )


async def load_extra_admins() -> None:
    """Load owner-added admins from MongoDB into the in-memory ADMIN_IDS list."""
    try:
        async for adm in admin_accounts.find({"active": {"$ne": False}}):
            uid = int(adm.get("user_id", 0))
            if uid and uid not in ADMIN_IDS:
                ADMIN_IDS.append(uid)
    except Exception as e:
        logger.warning("Could not load extra admins: %s", e)

async def notify_admins(text: str, **kwargs) -> None:
    for admin_id in list(dict.fromkeys(ADMIN_IDS)):
        await safe_send_message(admin_id, text, **kwargs)

async def notify_admin_order(order: Dict[str, Any], buyer: Dict[str, Any] | None = None) -> None:
    delivered_count = len(order.get("delivered", []) or [])
    text = (
        f"🛒 {bold('New Purchase Notification')}\n\n"
        f"Order ID: {code(short_id(order.get('_id', '')))}\n"
        f"Buyer ID: {code(order.get('user_id'))}\n"
        f"Username: @{escape(order.get('username') or (buyer or {}).get('username') or 'None')}\n"
        f"Product: {bold(order.get('product_name', 'Unknown'))}\n"
        f"Quantity: {bold(order.get('quantity', 1))}\n"
        f"Subtotal: {bold(money(order.get('subtotal', 0)))}\n"
        f"Discount: {bold(str(int(float(order.get('discount', 0)) * 100)) + '%')}\n"
        f"Total Paid: {bold(money(order.get('total', 0)))}\n"
        f"Paid With: {bold(order.get('paid_with', money(order.get('total', 0))))}\n"
        f"Delivered Items: {bold(delivered_count)}\n"
        f"Date: {escape(order.get('created_at', utcnow()))}"
    )
    await notify_admins(text)

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    return await users.find_one({"user_id": user_id})

async def ensure_user(message: Message, ref_code: Optional[str] = None) -> Dict[str, Any]:
    uid = message.from_user.id
    existing = await users.find_one({"user_id": uid})
    if existing:
        await users.update_one(
            {"user_id": uid},
            {"$set": {
                "username": message.from_user.username,
                "first_name": message.from_user.first_name,
                "last_seen": utcnow(),
            }},
        )
        existing["username"] = message.from_user.username
        existing["first_name"] = message.from_user.first_name
        return existing

    referred_by = None
    if ref_code and ref_code.startswith("REF"):
        ref_num = parse_int(ref_code.replace("REF", ""))
        if ref_num and ref_num != uid:
            ref_user = await users.find_one({"user_id": ref_num})
            if ref_user:
                referred_by = ref_num

    new_user = {
        "user_id": uid,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "balance": 0.0,
        "points": 0,
        "banned": False,
        "ban_reason": None,
        "referred_by": referred_by,
        "referral_awarded": False,
        "referral_count": 0,
        "total_spent": 0.0,
        "total_orders": 0,
        "created_at": utcnow(),
        "last_seen": utcnow(),
    }
    await users.insert_one(new_user)

    if referred_by:
        reward = int(await get_setting("referral_reward_points", 1))
        await users.update_one(
            {"user_id": referred_by},
            {"$inc": {"points": reward, "referral_count": 1}},
        )
        await safe_send_message(
            referred_by,
            f"🎉 New referral joined!\nYou received {bold(reward)} point(s)."
        )
        await users.update_one({"user_id": uid}, {"$set": {"referral_awarded": True}})
        new_user["referral_awarded"] = True
        await audit("referral_join", uid, {"referred_by": referred_by, "reward": reward})

    await audit("user_registered", uid, {"username": message.from_user.username})
    return new_user

async def award_referral_if_eligible(user_id: int) -> bool:
    """Award referral points once after the referred user has passed force-join."""
    user = await users.find_one({"user_id": int(user_id)})
    if not user:
        return False
    referred_by = user.get("referred_by")
    if not referred_by or user.get("referral_awarded"):
        return False
    if await force_join_required(int(user_id), "all"):
        return False
    reward = int(await get_setting("referral_reward_points", 1))
    await users.update_one({"user_id": int(referred_by)}, {"$inc": {"points": reward, "referral_count": 1}})
    await users.update_one({"user_id": int(user_id)}, {"$set": {"referral_awarded": True, "referral_awarded_at": utcnow()}})
    await safe_send_message(int(referred_by), f"🎉 New referral verified!\nYou received {bold(reward)} point(s).")
    await audit("referral_verified", int(user_id), {"referred_by": int(referred_by), "reward": reward})
    return True

async def is_banned(user_id: int) -> bool:
    user = await users.find_one({"user_id": user_id}, {"banned": 1})
    return bool(user and user.get("banned"))

async def security_check_event(obj: Message | CallbackQuery) -> bool:
    user_id = obj.from_user.id

    now_time = time.time()
    last_time = RATE_BUCKET.get(user_id, 0)
    if now_time - last_time < RATE_LIMIT_SECONDS:
        if isinstance(obj, CallbackQuery):
            await obj.answer("Slow down.", show_alert=False)
        return False
    RATE_BUCKET[user_id] = now_time

    if await is_banned(user_id):
        if isinstance(obj, CallbackQuery):
            await obj.answer("You are banned.", show_alert=True)
        else:
            await obj.answer("⛔ You are banned.")
        return False

    return True

async def admin_only_callback(cb: CallbackQuery) -> bool:
    if not is_admin(cb.from_user.id):
        await cb.answer("Admins only.", show_alert=True)
        await audit("admin_denied", cb.from_user.id, {"callback": cb.data})
        return False
    return True

async def admin_only_message(message: Message) -> bool:
    if not is_admin(message.from_user.id):
        await message.answer("Admins only.")
        await audit("admin_denied", message.from_user.id, {"text": message.text})
        return False
    return True

async def product_by_id(pid: str) -> Optional[Dict[str, Any]]:
    oid = object_id(pid)
    if not oid:
        return None
    return await products.find_one({"_id": oid})

async def method_by_id(mid: str) -> Optional[Dict[str, Any]]:
    oid = object_id(mid)
    if not oid:
        return None
    return await payment_methods.find_one({"_id": oid})

async def next_position(collection) -> int:
    """Return the next display position for ordered admin lists."""
    doc = await collection.find_one({}, sort=[("position", DESCENDING), ("created_at", DESCENDING)])
    try:
        return int(doc.get("position", 0)) + 1 if doc else 1
    except Exception:
        return 1

async def set_item_position(collection, item_id: str, position: int) -> bool:
    """Set manual display position on any supported collection item."""
    oid = object_id(item_id)
    if not oid or position < 1:
        return False
    result = await collection.update_one({"_id": oid}, {"$set": {"position": int(position), "updated_at": utcnow()}})
    return result.modified_count > 0 or result.matched_count > 0

async def ticket_by_id(tid: str) -> Optional[Dict[str, Any]]:
    oid = object_id(tid)
    if not oid:
        return None
    return await tickets.find_one({"_id": oid})

async def payment_by_id(pid: str) -> Optional[Dict[str, Any]]:
    oid = object_id(pid)
    if not oid:
        return None
    return await payments.find_one({"_id": oid})


# ==========================================================
# USER PANEL BUTTON VISIBILITY
# ==========================================================

DEFAULT_BUTTON_VISIBILITY: Dict[str, bool] = {
    # Main reply-keyboard buttons
    "products": True,
    "resell": True,
    "free_items": True,
    "preorder": True,
    "x_premium": True,
    "referral": True,
    "methods": True,
    "deposit": True,
    "support": True,
    "balance": True,
    "exchange": True,
    # X Premium + Grok inline buttons
    "x_plan_3m": True,
    "x_plan_6m": True,
    "x_plan_1y": True,
    "x_method": True,
    "x_my_orders": True,
    "x_pay_points": True,
    "x_pay_balance": True,
    "x_pay_manual": True,
}

BUTTON_VISIBILITY_CACHE: Dict[str, bool] = DEFAULT_BUTTON_VISIBILITY.copy()

BUTTON_TITLES: Dict[str, str] = {
    "products": "🛍️ PRODUCTS",
    "resell": "♻️ RESELL",
    "free_items": "🎁 FREE ITEMS",
    "preorder": "📝 PREORDER",
    "x_premium": "🐦 X PREMIUM + GROK",
    "referral": "👥 REFERRAL",
    "methods": "💳 METHODS",
    "deposit": "💰 DEPOSIT",
    "support": "📞 SUPPORT",
    "balance": "💼 BALANCE",
    "exchange": "💱 EXCHANGE",
    "x_plan_3m": "🐦 X + Grok 3 Months",
    "x_plan_6m": "🐦 X + Grok 6 Months",
    "x_plan_1y": "🐦 X + Grok 1 Year",
    "x_method": "📝 X Method Button",
    "x_my_orders": "🧾 My X + Grok Orders Button",
    "x_pay_points": "💎 X Buy with Points",
    "x_pay_balance": "💰 X Buy with USDT Balance",
    "x_pay_manual": "📤 X Manual Binance Payment",
}

MENU_TEXT_TO_BUTTON_KEY: Dict[str, str] = {
    "🛍️ PRODUCTS": "products",
    "♻️ RESELL": "resell",
    "🎁 FREE ITEMS": "free_items",
    "📝 PREORDER": "preorder",
    "🐦 X PREMIUM + GROK": "x_premium",
    "👥 REFERRAL": "referral",
    "💳 METHODS": "methods",
    "💰 DEPOSIT": "deposit",
    "📞 SUPPORT": "support",
    "💼 BALANCE": "balance",
    "💱 EXCHANGE": "exchange",
}

MENU_LAYOUT: List[List[Tuple[str, str]]] = [
    [("products", "🛍️ PRODUCTS"), ("resell", "♻️ RESELL")],
    [("free_items", "🎁 FREE ITEMS"), ("preorder", "📝 PREORDER")],
    [("x_premium", "🐦 X PREMIUM + GROK")],
    [("referral", "👥 REFERRAL")],
    [("methods", "💳 METHODS"), ("deposit", "💰 DEPOSIT")],
    [("support", "📞 SUPPORT")],
    [("balance", "💼 BALANCE"), ("exchange", "💱 EXCHANGE")],
]

def normalize_button_visibility(value: Any) -> Dict[str, bool]:
    data = DEFAULT_BUTTON_VISIBILITY.copy()
    if isinstance(value, dict):
        for key in DEFAULT_BUTTON_VISIBILITY:
            if key in value:
                data[key] = bool(value[key])
    return data

async def load_button_visibility() -> Dict[str, bool]:
    global BUTTON_VISIBILITY_CACHE
    BUTTON_VISIBILITY_CACHE = normalize_button_visibility(await get_setting("button_visibility", DEFAULT_BUTTON_VISIBILITY))
    return BUTTON_VISIBILITY_CACHE

async def save_button_visibility(data: Dict[str, bool]) -> None:
    global BUTTON_VISIBILITY_CACHE
    BUTTON_VISIBILITY_CACHE = normalize_button_visibility(data)
    await set_setting("button_visibility", BUTTON_VISIBILITY_CACHE)

def button_visible(key: str, user_id: Optional[int] = None) -> bool:
    # Hidden buttons are hidden only from normal users. Admins can still see/use all buttons.
    if user_id is not None and is_admin(user_id):
        return True
    return bool(BUTTON_VISIBILITY_CACHE.get(key, True))

async def ensure_button_allowed(message_or_callback: Message | CallbackQuery, key: str) -> bool:
    user_id = message_or_callback.from_user.id
    if button_visible(key, user_id):
        return True
    text = "⛔ This option is currently disabled by admin."
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer(text, show_alert=True)
    else:
        await message_or_callback.answer(text, reply_markup=main_menu(user_id))
    return False

def button_manager_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    main_keys = ["products", "resell", "free_items", "preorder", "x_premium", "referral", "methods", "deposit", "support", "balance", "exchange"]
    x_keys = ["x_plan_3m", "x_plan_6m", "x_plan_1y", "x_method", "x_my_orders", "x_pay_points", "x_pay_balance", "x_pay_manual"]
    for key in main_keys + x_keys:
        visible = bool(BUTTON_VISIBILITY_CACHE.get(key, True))
        status = "🟢" if visible else "🔴"
        title = BUTTON_TITLES.get(key, key)
        rows.append([InlineKeyboardButton(text=f"{status} {title}", callback_data=f"btnvis:toggle:{key}")])
    rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:button_manager")])
    rows.append([InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ==========================================================
# KEYBOARDS
# ==========================================================

FULL_WIDTH_MENU_KEYS = {"x_premium", "referral", "support"}
MENU_ORDER: List[Tuple[str, str]] = [item for row in MENU_LAYOUT for item in row]

def _append_menu_pair(rows: List[List[KeyboardButton]], pair: List[KeyboardButton]) -> None:
    if pair:
        rows.append(pair.copy())
        pair.clear()

def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Build a clean user menu dynamically.

    Hidden buttons no longer leave ugly gaps or force too many buttons into one row.
    Important buttons stay full-width, while normal buttons are paired two per row.
    """
    rows: List[List[KeyboardButton]] = []
    pair: List[KeyboardButton] = []

    for key, label in MENU_ORDER:
        if not button_visible(key, user_id):
            continue
        btn = KeyboardButton(text=label)
        if key in FULL_WIDTH_MENU_KEYS:
            _append_menu_pair(rows, pair)
            rows.append([btn])
            continue
        pair.append(btn)
        if len(pair) == 2:
            _append_menu_pair(rows, pair)

    _append_menu_pair(rows, pair)

    if is_admin(user_id):
        rows.append([KeyboardButton(text="👑 ADMIN PANEL")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add Product", callback_data="admin:add_product"),
            InlineKeyboardButton(text="📦 Products", callback_data="admin:products:1"),
        ],
        [
            InlineKeyboardButton(text="🎁 Free Items", callback_data="admin:free_products"),
            InlineKeyboardButton(text="🎬 Paid Methods", callback_data="admin:content_methods"),
        ],
        [
            InlineKeyboardButton(text="📩 Canva Requests", callback_data="admin:canva_requests"),
            InlineKeyboardButton(text="📝 Preorders", callback_data="admin:preorders"),
        ],
        [
            InlineKeyboardButton(text="➕ Preorder Product", callback_data="admin:add_preorder_product"),
            InlineKeyboardButton(text="📋 Preorder Products", callback_data="admin:preorder_products"),
        ],
        [
            InlineKeyboardButton(text="💱 Point Exchanges", callback_data="admin:point_exchanges"),
        ],
        [
            InlineKeyboardButton(text="♻️ Resell Approval", callback_data="admin:resell:pending:1"),
            InlineKeyboardButton(text="👑 VIP Sellers", callback_data="admin:sellers:1"),
        ],
        [
            InlineKeyboardButton(text="📥 Bulk Add Stock", callback_data="admin:add_stock"),
            InlineKeyboardButton(text="💳 Payment Methods", callback_data="admin:methods"),
        ],
        [
            InlineKeyboardButton(text="💰 Deposits", callback_data="admin:deposits:pending:1"),
            InlineKeyboardButton(text="🧾 Orders", callback_data="admin:orders:1"),
        ],
        [
            InlineKeyboardButton(text="🎟 Tickets", callback_data="admin:tickets:open:1"),
            InlineKeyboardButton(text="👥 Users", callback_data="admin:users:1"),
        ],
        [
            InlineKeyboardButton(text="🏆 Top Referrals", callback_data="admin:top_referrals"),
            InlineKeyboardButton(text="🛒 Top Buyers", callback_data="admin:top_buyers"),
        ],
        [
            InlineKeyboardButton(text="💰 Top Balances", callback_data="admin:top_balances"),
        ],
        [
            InlineKeyboardButton(text="🚫 Block User", callback_data="admin:block_user"),
            InlineKeyboardButton(text="✅ Unblock User", callback_data="admin:unblock_user"),
        ],
        [
            InlineKeyboardButton(text="🔎 User Search", callback_data="admin:user_search"),
            InlineKeyboardButton(text="📊 Statistics", callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton(text="➕ Add Admin", callback_data="admin:add_admin"),
            InlineKeyboardButton(text="👑 Admins", callback_data="admin:list_admins"),
        ],
        [
            InlineKeyboardButton(text="🐦 X + Grok Orders", callback_data="xadmin:orders"),
            InlineKeyboardButton(text="💰 X + Grok Payments", callback_data="xadmin:payments"),
        ],
        [
            InlineKeyboardButton(text="⚙️ X Settings", callback_data="xadmin:settings"),
            InlineKeyboardButton(text="📝 X Method", callback_data="xadmin:method"),
        ],
        [
            InlineKeyboardButton(text="👁 Button Manager", callback_data="admin:button_manager"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast"),
            InlineKeyboardButton(text="⚙️ Settings", callback_data="admin:settings"),
        ],
    ])

async def product_buy_keyboard(pid: str, stock_count: int) -> InlineKeyboardMarkup:
    # Manual quantity input only. User selects product, taps Buy, enters any quantity,
    # then confirms before balance is deducted and stock is delivered.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Now", callback_data=f"buyask:{pid}")],
        [InlineKeyboardButton(text="⬅️ Back to Products", callback_data="nav:products")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
    ])

async def contact_keyboard() -> InlineKeyboardMarkup:
    username = await get_setting("support_username", "support")
    username = clean_username(str(username or "support"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Contact Admin/Support", url=f"https://t.me/{username}")]
    ])

def admin_product_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Edit Price", callback_data=f"prod:edit_price:{pid}"),
            InlineKeyboardButton(text="📝 Edit Description", callback_data=f"prod:edit_desc:{pid}"),
        ],
        [
            InlineKeyboardButton(text="📦 View Stock", callback_data=f"prod:view_stock:{pid}"),
            InlineKeyboardButton(text="➕ Add Stock", callback_data=f"prod:add_stock:{pid}"),
        ],
        [
            InlineKeyboardButton(text="📌 Set Position", callback_data=f"prod:position:{pid}"),
            InlineKeyboardButton(text="📛 Stockout Label", callback_data=f"prod:stockout:{pid}"),
        ],
        [
            InlineKeyboardButton(text="🟢 Toggle Active", callback_data=f"prod:toggle:{pid}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"prod:delete:{pid}"),
        ],
    ])

def deposit_admin_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"deposit:approve:{payment_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"deposit:reject:{payment_id}"),
        ]
    ])

def payment_method_keyboard(mid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Set Default", callback_data=f"method:default:{mid}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"method:edit:{mid}"),
        ],
        [
            InlineKeyboardButton(text="📌 Set Position", callback_data=f"method:position:{mid}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"method:delete:{mid}"),
        ],
    ])

def ticket_admin_keyboard(tid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Reply", callback_data=f"ticket:reply:{tid}"),
            InlineKeyboardButton(text="Close", callback_data=f"ticket:close:{tid}"),
        ]
    ])

def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set Product Contact Username", callback_data="settings:set:support_username")],
        [InlineKeyboardButton(text="Set Multiple Support Contacts", callback_data="settings:set:support_contacts")],
        [InlineKeyboardButton(text="Set Referral Reward Points", callback_data="settings:set:referral_reward_points")],
        [InlineKeyboardButton(text="Set Canva Price", callback_data="settings:set:canva_price_usdt")],
        [InlineKeyboardButton(text="Set Canva Points Requirement", callback_data="settings:set:canva_points_required")],
        [InlineKeyboardButton(text="Set Canva First Account Free", callback_data="settings:set:canva_first_free_enabled")],
        [InlineKeyboardButton(text="Set Start Message", callback_data="settings:set:start_message")],
        [InlineKeyboardButton(text="Set Force Join Channels", callback_data="settings:set:force_join_channels")],
        [InlineKeyboardButton(text="Set Force Join Groups", callback_data="settings:set:force_join_groups")],
        [InlineKeyboardButton(text="Set Force Join Scope", callback_data="settings:set:force_join_scope")],
        [InlineKeyboardButton(text="Set Bulk Discounts", callback_data="settings:set:bulk_discount_rules")],
        [InlineKeyboardButton(text="Set Points → USDT Rate", callback_data="settings:set:points_per_usdt")],
        [InlineKeyboardButton(text="Set Binance Auto Verify", callback_data="settings:set:binance_auto_verify_enabled")],
        [InlineKeyboardButton(text="Set Binance Wallet", callback_data="settings:set:binance_wallet_address")],
        [InlineKeyboardButton(text="Set Binance Network", callback_data="settings:set:binance_network")],
        [InlineKeyboardButton(text="Set Binance Min Deposit", callback_data="settings:set:binance_min_deposit")],
        [InlineKeyboardButton(text="Set Canva Mode", callback_data="settings:set:canva_delivery_mode")],
        [InlineKeyboardButton(text="Set Canva Link", callback_data="settings:set:canva_invite_link")],
    ])

# ==========================================================
# STATES
# ==========================================================

class AddProductState(StatesGroup):
    name = State()
    price = State()
    description = State()

class BuyQuantityState(StatesGroup):
    product_id = State()
    quantity = State()

class EditProductPriceState(StatesGroup):
    product_id = State()
    price = State()

class EditProductDescriptionState(StatesGroup):
    product_id = State()
    description = State()

class AddStockState(StatesGroup):
    product_id = State()
    stock = State()

class DepositState(StatesGroup):
    amount = State()
    method = State()
    screenshot = State()

class AddMethodState(StatesGroup):
    name = State()
    details = State()

class EditMethodState(StatesGroup):
    method_id = State()
    details = State()

class SupportTicketState(StatesGroup):
    subject = State()
    message = State()

class AdminTicketReplyState(StatesGroup):
    ticket_id = State()
    message = State()

class BroadcastState(StatesGroup):
    message = State()

class UserSearchState(StatesGroup):
    query = State()

class BlockUserState(StatesGroup):
    mode = State()
    query = State()

class PointsState(StatesGroup):
    user_id = State()
    mode = State()
    amount = State()

class BalanceState(StatesGroup):
    user_id = State()
    mode = State()
    amount = State()

class SettingState(StatesGroup):
    key = State()
    value = State()

class RefundState(StatesGroup):
    order_id = State()
    reason = State()

class CanvaRequestState(StatesGroup):
    gmail = State()
    payment = State()

class AddContentMethodState(StatesGroup):
    name = State()
    price = State()
    description = State()
    delivery = State()

class AddFreeProductState(StatesGroup):
    name = State()
    price = State()
    points = State()
    description = State()

class AddFreeStockState(StatesGroup):
    product_id = State()
    stock = State()

class SetPositionState(StatesGroup):
    item_type = State()
    item_id = State()
    position = State()

class PointExchangeState(StatesGroup):
    points = State()

class AddAdminState(StatesGroup):
    chat_id = State()

class PreorderState(StatesGroup):
    details = State()
    payment_method = State()
    txid = State()
    payment_screenshot = State()

class ManualPreorderPaymentState(StatesGroup):
    preorder_id = State()
    payment_method = State()
    payment_screenshot = State()

class AddPreorderProductState(StatesGroup):
    name = State()
    price = State()
    description = State()

class EditPreorderProductState(StatesGroup):
    product_id = State()
    field = State()
    value = State()

class AdminPreorderAnswerState(StatesGroup):
    preorder_id = State()
    answer = State()

class AdminPreorderPriceState(StatesGroup):
    preorder_id = State()
    amount = State()

class StockoutPreorderQuantityState(StatesGroup):
    product_id = State()
    quantity = State()

class ManualPreorderQuantityState(StatesGroup):
    quantity = State()
    details = State()

class PreorderProductQuantityState(StatesGroup):
    product_id = State()
    quantity = State()

class PatchContentMethodState(StatesGroup):
    method_id = State()
    patch = State()
    success_rate = State()

class BinanceVerifyState(StatesGroup):
    amount = State()
    txid = State()

class XUsernameState(StatesGroup):
    plan_key = State()
    pay_type = State()

class XPaymentState(StatesGroup):
    plan_key = State()
    x_username = State()
    txid = State()
    screenshot = State()

class XRejectState(StatesGroup):
    order_id = State()
    reason = State()

class XPaymentRejectState(StatesGroup):
    payment_id = State()
    reason = State()

class XSettingsState(StatesGroup):
    key = State()
    value = State()

class XMethodEditState(StatesGroup):
    field = State()
    value = State()

# ==========================================================
# DATABASE INIT
# ==========================================================

async def ensure_indexes() -> None:
    await users.create_index([("user_id", ASCENDING)], unique=True)
    await users.create_index([("username", ASCENDING)])
    await users.create_index([("created_at", DESCENDING)])
    await products.create_index([("name", ASCENDING)])
    await products.create_index([("active", ASCENDING)])
    await products.create_index([("position", ASCENDING)])
    await orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await orders.create_index([("status", ASCENDING)])
    await payments.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await payments.create_index([("status", ASCENDING)])
    await payments.create_index([("txid", ASCENDING)])
    await tickets.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    await tickets.create_index([("status", ASCENDING)])
    await payment_methods.create_index([("name", ASCENDING)])
    await payment_methods.create_index([("position", ASCENDING)])
    await settings.create_index([("key", ASCENDING)], unique=True)
    await content_methods.create_index([("active", ASCENDING), ("created_at", DESCENDING)])
    await content_methods.create_index([("position", ASCENDING)])
    await content_methods.create_index([("success_rate", DESCENDING)])
    await free_products.create_index([("active", ASCENDING), ("created_at", DESCENDING)])
    await canva_requests.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    await point_exchanges.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    await preorders.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    await preorders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await preorder_products.create_index([("active", ASCENDING), ("position", ASCENDING)])
    await admin_accounts.create_index([("user_id", ASCENDING)], unique=True)
    await seller_profiles.create_index([("user_id", ASCENDING)], unique=True)
    await seller_profiles.create_index([("vip", DESCENDING), ("trusted", DESCENDING)])
    await reseller_payouts.create_index([("seller_id", ASCENDING), ("created_at", DESCENDING)])
    await reseller_payouts.create_index([("order_id", ASCENDING)], unique=True)
    await x_orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await x_orders.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    await x_payments.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await x_payments.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    await x_payments.create_index([("txid", ASCENDING)])

    defaults = {
        "support_username": "support",
        "support_contacts": [],
        "referral_reward_points": 1,
        "canva_price_usdt": 1.0,
        "canva_points_required": 10,
        "maintenance_mode": False,
        "start_message": f"✅ Welcome to {APP_NAME}\n\nChoose an option below:",
        "force_join_channels": [],
        "force_join_groups": [],
        "force_join_scope": "none",
        "bulk_discount_rules": DEFAULT_BULK_DISCOUNT_RULES,
        "points_per_usdt": 10,
        "canva_first_free_enabled": True,
        "binance_auto_verify_enabled": BINANCE_AUTO_VERIFY,
        "binance_wallet_address": BINANCE_WALLET_ADDRESS,
        "binance_network": BINANCE_NETWORK,
        "binance_coin": BINANCE_COIN,
        "binance_min_deposit": BINANCE_MIN_DEPOSIT,
        "canva_delivery_mode": "gmail",
        "canva_invite_link": "",
        "resell_enabled": True,
        "default_reseller_commission_percent": 10,
        "button_visibility": DEFAULT_BUTTON_VISIBILITY,
        "x_binance_id": "",
        "x_plans": {
            "3m": {"name": "X Premium + Grok 3 Months", "months": 3, "price_usdt": 5.0, "points": 50},
            "6m": {"name": "X Premium + Grok 6 Months", "months": 6, "price_usdt": 9.0, "points": 90},
            "1y": {"name": "X Premium + Grok 1 Year", "months": 12, "price_usdt": 15.0, "points": 150},
        },
        "x_method_title": "X Premium + Grok Method",
        "x_method_price_usdt": 10.0,
        "x_method_points": 100,
        "x_method_content": "Admin has not added the method yet.",
    }
    for key, value in defaults.items():
        existing = await settings.find_one({"key": key})
        if not existing:
            await set_setting(key, value)
    await admin_accounts.update_one({"user_id": OWNER_ID}, {"$set": {"user_id": OWNER_ID, "role": "owner", "active": True, "updated_at": utcnow()}, "$setOnInsert": {"created_at": utcnow()}}, upsert=True)
    await load_button_visibility()
    await load_extra_admins()

# ==========================================================
# TEXT FORMATTERS
# ==========================================================

def format_product(product: Dict[str, Any]) -> str:
    stock_count = len(product.get("stock", []))
    active = "Active" if product.get("active", True) else "Hidden"
    return (
        f"🛍️ {bold(product.get('name', 'Product'))}\n\n"
        f"💵 Price: {bold(money(product.get('price', 0)))}\n"
        f"📦 Stock: {bold(stock_count)}\n"
        f"📌 Status: {bold(active)}\n"
        f"🔥 Sales: {bold(product.get('sales', 0))}\n"
        f"🏷️ Seller: {bold(product.get('seller_badge_label', 'Admin Store') if product.get('is_resell') else 'Admin Store')}\n"
        f"✅ Approval: {bold(product.get('approval_status', 'approved'))}\n\n"
        f"{escape(product.get('description', ''))}\n\n"
        f"Bulk discounts:\n"
        f"• Admin configurable"
    )

async def format_product_display(product: Dict[str, Any]) -> str:
    base = format_product(product)
    base = base.replace("Bulk discounts:\n• Admin configurable", await bulk_discount_text())
    return base

def format_user(user: Dict[str, Any]) -> str:
    return (
        f"👤 {bold('User')}\n\n"
        f"ID: {code(user.get('user_id'))}\n"
        f"Username: @{escape(user.get('username') or 'None')}\n"
        f"Name: {escape(user.get('first_name') or '')}\n"
        f"Balance: {bold(money(user.get('balance', 0)))}\n"
        f"Points: {bold(user.get('points', 0))}\n"
        f"Orders: {bold(user.get('total_orders', 0))}\n"
        f"Spent: {bold(money(user.get('total_spent', 0)))}\n"
        f"Referrals: {bold(user.get('referral_count', 0))}\n"
        f"Banned: {bold('Yes' if user.get('banned') else 'No')}"
    )

def format_order(order: Dict[str, Any], include_data: bool = False) -> str:
    text = (
        f"🧾 Order {code(short_id(order.get('_id')))}\n\n"
        f"Product: {bold(order.get('product_name', 'Unknown'))}\n"
        f"Quantity: {bold(order.get('quantity', order.get('qty', 0)))}\n"
        f"Subtotal: {bold(money(order.get('subtotal', 0)))}\n"
        f"Discount: {bold(str(int(float(order.get('discount', 0)) * 100)) + '%')}\n"
        f"Total: {bold(money(order.get('total', 0)))}\n"
        f"Status: {bold(order.get('status', 'unknown'))}\n"
        f"Date: {escape(order.get('created_at', ''))}\n"
    )
    if include_data and order.get("delivered"):
        text += "\nDelivered data:\n"
        text += "\n".join(code(x) for x in order.get("delivered", []))
    return text

def format_payment(payment: Dict[str, Any]) -> str:
    return (
        f"💰 Deposit {code(short_id(payment.get('_id')))}\n\n"
        f"User: {code(payment.get('user_id'))}\n"
        f"Amount: {bold(money(payment.get('amount', 0)))}\n"
        f"Method: {bold(payment.get('method', 'Unknown'))}\n"
        f"Status: {bold(payment.get('status', 'unknown'))}\n"
        f"Date: {escape(payment.get('created_at', ''))}"
    )

def format_ticket(ticket: Dict[str, Any]) -> str:
    last_text = ""
    if ticket.get("messages"):
        last_text = ticket["messages"][-1].get("text", "")
    return (
        f"🎟 Ticket {code(short_id(ticket.get('_id')))}\n\n"
        f"Subject: {bold(ticket.get('subject', 'No subject'))}\n"
        f"User: {code(ticket.get('user_id'))}\n"
        f"Status: {bold(ticket.get('status', 'unknown'))}\n\n"
        f"Last message:\n{escape(truncate(last_text, 700))}"
    )


# ==========================================================
# CANCEL / FORCE JOIN HELPERS
# ==========================================================

USER_MENU_TEXTS = {
    "🛍️ PRODUCTS", "👥 REFERRAL", "💳 METHODS",
    "💰 DEPOSIT", "📞 SUPPORT", "💼 BALANCE",
    "🎁 FREE ITEMS", "📝 PREORDER", "🐦 X PREMIUM + GROK", "👑 ADMIN PANEL", "💱 EXCHANGE", "♻️ RESELL",
}

def cancel_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data="global:cancel")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
    ])

def nav_inline_keyboard(back_data: str = "global:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data=back_data)],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
    ])

def _normalize_force_join_entries(raw: Any) -> List[str]:
    if isinstance(raw, str):
        parts: List[str] = []
        for chunk in raw.replace(";", ",").split(","):
            parts.extend([x.strip() for x in chunk.splitlines() if x.strip()])
        raw = parts
    entries: List[str] = []
    for item in (raw or []):
        text = str(item or "").strip()
        if not text:
            continue
        if text.startswith("https://t.me/") or text.startswith("http://t.me/") or text.startswith("t.me/"):
            last = text.rstrip("/").split("/")[-1]
            # Public t.me/group links can be verified. Private invite links like +abc cannot be verified by username.
            if last and not last.startswith("+"):
                text = last
        text = clean_username(text)
        if text and text not in entries:
            entries.append(text)
    return entries

def force_join_keyboard(channels: List[str], groups: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        clean = str(ch).strip().replace("@", "")
        if clean:
            rows.append([InlineKeyboardButton(text=f"📢 Join @{clean}", url=f"https://t.me/{clean}")])
    for grp in (groups or []):
        clean = str(grp).strip().replace("@", "")
        if clean:
            rows.append([InlineKeyboardButton(text=f"👥 Join @{clean}", url=f"https://t.me/{clean}")])
    rows.append([InlineKeyboardButton(text="✅ I Joined", callback_data="forcejoin:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def get_force_join_channels() -> List[str]:
    return _normalize_force_join_entries(await get_setting("force_join_channels", []))

async def get_force_join_groups() -> List[str]:
    return _normalize_force_join_entries(await get_setting("force_join_groups", []))

async def get_force_join_targets() -> List[str]:
    targets: List[str] = []
    for item in (await get_force_join_channels()) + (await get_force_join_groups()):
        if item and item not in targets:
            targets.append(item)
    return targets

async def force_join_required(user_id: int, scope: str = "all") -> bool:
    if is_admin(user_id):
        return False
    mode = str(await get_setting("force_join_scope", "none") or "none").lower().strip()
    targets = await get_force_join_targets()
    if not targets or mode in {"none", "off", "empty", "disabled"}:
        return False
    if mode == "free_canva" and scope != "free_canva":
        return False
    if mode == "all" or mode == scope or (mode == "free_canva" and scope == "free_canva"):
        for target in targets:
            try:
                member = await bot.get_chat_member(f"@{target}", user_id)
                if member.status in {"left", "kicked"}:
                    return True
            except Exception:
                # If Telegram cannot check it, keep force-join required so admin notices the username/link is wrong.
                return True
    return False

async def send_force_join_message(message_or_callback: Message | CallbackQuery, scope: str = "all") -> bool:
    user_id = message_or_callback.from_user.id
    if not await force_join_required(user_id, scope):
        return False
    channels = await get_force_join_channels()
    groups = await get_force_join_groups()
    text = "🔒 Please join required channel/group first, then click ✅ I Joined."
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(text, reply_markup=force_join_keyboard(channels, groups))
        await message_or_callback.answer("Join required channel/group.", show_alert=True)
    else:
        await message_or_callback.answer(text, reply_markup=force_join_keyboard(channels, groups))
    return True

# ==========================================================
# START / HELP
# ==========================================================

# Ignore normal user messages/callbacks inside groups.
# This keeps the shop panel private and stops the bot from opening menus in chat groups.
@router.message(F.chat.type != "private")
async def ignore_group_messages(message: Message) -> None:
    return

@router.callback_query(lambda callback: bool(callback.message) and callback.message.chat.type != "private")
async def ignore_group_callbacks(callback: CallbackQuery) -> None:
    try:
        await callback.answer("Please use the bot in private chat.", show_alert=False)
    except Exception:
        pass

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    ref_code = parts[1].strip() if len(parts) > 1 else None
    user = await ensure_user(message, ref_code=ref_code)

    if user.get("banned"):
        await message.answer("⛔ You are banned.")
        return

    if await send_force_join_message(message, "all"):
        return
    start_text = str(await get_setting("start_message", f"✅ Welcome to {APP_NAME}\n\nChoose an option below:"))
    await message.answer(start_text, reply_markup=main_menu(message.from_user.id))

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await ensure_user(message)
    await message.answer(
        "ℹ️ Commands\n\n"
        "/start — Open menu\n"
        "/balance — Check balance\n"
        "/myorders — Your orders\n"
        "/cancel — Cancel current action\n\n"
        "Use the menu buttons for products, deposit, referral, and support."
    )

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✅ Cancelled.", reply_markup=main_menu(message.from_user.id))


@router.callback_query(F.data == "global:cancel")
async def global_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    back_to = data.get("back_to")
    await state.clear()
    if back_to == "products":
        await callback.message.answer("⬅️ Back to products.")
        await show_products(callback.message)
    elif isinstance(back_to, str) and back_to.startswith("product:"):
        pid = back_to.split(":", 1)[1]
        product = await product_by_id(pid)
        if product:
            stock_count = len(product.get("stock", []))
            if stock_count <= 0:
                await callback.message.answer(
                    (await format_product_display(product)) + "\n\n📛 Stockout: This product is visible but not available right now.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=(
                        [[InlineKeyboardButton(text="📝 Preorder this product", callback_data=f"stockoutpreorder:{pid}")]]
                        + ([[InlineKeyboardButton(text="✏️ Admin Edit Price", callback_data=f"prod:edit_price:{pid}")]] if is_admin(callback.from_user.id) else [])
                        + [[InlineKeyboardButton(text="⬅️ Back to Products", callback_data="nav:products")],
                           [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")]]
                    )),
                )
            else:
                await callback.message.answer(await format_product_display(product), reply_markup=await product_buy_keyboard(pid, stock_count))
        else:
            await callback.message.answer("✅ Cancelled. Choose another option.", reply_markup=main_menu(callback.from_user.id))
    elif back_to == "preorder":
        await callback.message.answer("⬅️ Back to preorder.")
        await preorder_start(callback.message, state)
    elif isinstance(back_to, str) and back_to.startswith("preproduct:"):
        pid = back_to.split(":", 1)[1]
        await show_preorder_product_by_id(callback.message, pid)
    else:
        await callback.message.answer("✅ Cancelled. Choose another option.", reply_markup=main_menu(callback.from_user.id))
    await callback.answer("Back")

@router.callback_query(F.data == "global:main_menu")
async def global_main_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("🏠 Main menu", reply_markup=main_menu(callback.from_user.id))
    await callback.answer("Main Menu")

@router.callback_query(F.data == "nav:products")
async def nav_products_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("⬅️ Back to products.")
    await show_products(callback.message)
    await callback.answer()

@router.callback_query(F.data == "nav:preorder")
async def nav_preorder_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("⬅️ Back to preorder.")
    await preorder_start(callback.message, state)
    await callback.answer()

@router.callback_query(F.data == "forcejoin:check")
async def force_join_check_callback(callback: CallbackQuery) -> None:
    if await force_join_required(callback.from_user.id, "all"):
        await callback.answer("You still need to join all required channels.", show_alert=True)
        return
    await award_referral_if_eligible(callback.from_user.id)
    await callback.message.answer("✅ Verified. Use the menu now.", reply_markup=main_menu(callback.from_user.id))
    await callback.answer("Verified")

@router.message(StateFilter("*"), F.text.in_(USER_MENU_TEXTS))
async def menu_text_always_works(message: Message, state: FSMContext) -> None:
    button_key = MENU_TEXT_TO_BUTTON_KEY.get(message.text or "")
    if button_key and not await ensure_button_allowed(message, button_key):
        return
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    if message.text == "👑 ADMIN PANEL":
        await show_admin_panel(message)
    elif message.text == "🛍️ PRODUCTS":
        await show_products(message)
    elif message.text == "🎁 FREE CANVA":
        await free_canva_menu(message, state)
    elif message.text == "👥 REFERRAL":
        await referral_menu(message)
    elif message.text == "💳 METHODS":
        await show_content_methods(message)
    elif message.text == "💰 DEPOSIT":
        await deposit_start(message, state)
    elif message.text == "📞 SUPPORT":
        await support_direct_open(message, state)
    elif message.text == "💼 BALANCE":
        await show_balance(message)
    elif message.text == "🎁 FREE ITEMS":
        await show_free_products(message)
    elif message.text == "📝 PREORDER":
        await preorder_start(message, state)
    elif message.text == "💱 EXCHANGE":
        await exchange_points_button(message, state)
    elif message.text == "♻️ RESELL":
        await reseller_panel(message, state)
    elif message.text == "🐦 X PREMIUM + GROK":
        await x_premium_menu(message, state)





def preorder_payment_methods_keyboard(prefix: str = "prepaymethod") -> InlineKeyboardMarkup:
    # Dynamic buttons are built in async functions; this fallback only provides cancel.
    return cancel_inline_keyboard()

async def send_preorder_payment_methods(target: Message, state: FSMContext) -> bool:
    rows = []
    if bool(await get_setting("binance_auto_verify_enabled", BINANCE_AUTO_VERIFY)):
        rows.append([InlineKeyboardButton(text="🤖 Binance TXID Auto Verify", callback_data="preorderpaymethod:binance")])
    async for method in payment_methods.find({}).sort([("position", ASCENDING), ("default", DESCENDING), ("created_at", DESCENDING)]):
        mid = str(method["_id"])
        label = method.get("name", "Payment")
        rows.append([InlineKeyboardButton(text=f"💳 {label}", callback_data=f"prepaymethod:{mid}")])
    data = await state.get_data()
    if data.get("back_to"):
        rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="global:cancel")])
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")])
    if len(rows) <= 1:
        await state.clear()
        await target.answer("❌ No payment method is available right now. Contact support.", reply_markup=main_menu(target.from_user.id))
        return False
    total = float((await state.get_data()).get("estimated_total", 0) or 0)
    await target.answer(
        "💳 <b>Advance Payment Required</b>\n\n"
        f"Amount to pay: {bold(money(total))}\n\n"
        "Choose payment method. After paying, you will send:\n"
        "1) Transaction ID / TXID\n"
        "2) Amount paid\n"
        "3) Screenshot/photo\n\n"
        "If Binance auto verify is enabled, bot will check TXID first. If not found, admin can still verify manually.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(PreorderState.payment_method)
    return True

async def create_preorder_after_payment(message: Message, state: FSMContext, screenshot_file_id: str, screenshot_type: str) -> None:
    data = await state.get_data()
    method_id = data.get("payment_method_id")
    oid = object_id(method_id)
    method = await payment_methods.find_one({"_id": oid}) if oid else None
    method_name = method.get("name") if method else data.get("payment_method_name", "Unknown")

    preorder_type = data.get("preorder_type", "manual")
    qty = int(data.get("quantity", 1) or 1)
    price_each = float(data.get("price_each", 0) or 0)
    total = round(float(data.get("estimated_total", price_each * qty) or 0), 2)
    details = data.get("details", "")
    txid = str(data.get("txid") or "").strip()
    paid_amount = float(data.get("paid_amount", total) or total)
    auto_verified = bool(data.get("auto_verified", False))
    verify_info = str(data.get("verify_info") or "")
    payment_status = "auto_verified" if auto_verified else "submitted"

    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "details": details,
        "product_id": data.get("product_id"),
        "product_name": data.get("product_name"),
        "quantity": qty,
        "price_each": price_each,
        "estimated_total": total,
        "paid_amount": paid_amount,
        "payment_method_id": method_id,
        "payment_method": method_name,
        "payment_screenshot_file_id": screenshot_file_id,
        "payment_screenshot_type": screenshot_type,
        "txid": txid,
        "auto_verified": auto_verified,
        "verify_info": verify_info,
        "payment_status": payment_status,
        "type": preorder_type,
        "status": "pending",
        "answers": [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await preorders.insert_one(doc)
    preorder_id = str(result.inserted_id)
    await state.clear()

    status_line = "auto-verified by Binance" if auto_verified else "pending admin approval"
    await message.answer(
        f"✅ Preorder payment submitted.\n\n"
        f"Preorder ID: {code(short_id(preorder_id))}\n"
        f"Product: {bold(data.get('product_name') or 'Manual preorder')}\n"
        f"Accounts: {bold(qty)}\n"
        f"Required amount: {bold(money(total))}\n"
        f"Paid amount: {bold(money(paid_amount))}\n"
        f"Payment method: {bold(method_name)}\n"
        f"TXID: {code(txid or 'Not provided')}\n"
        f"Status: {bold(status_line)}\n\n"
        f"Admin will reply with your products after checking.",
        reply_markup=main_menu(message.from_user.id),
    )

    caption = (
        f"📝 {bold('New Paid Preorder')}\n\n"
        f"Preorder ID: {code(preorder_id)}\n"
        f"User: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\n"
        f"Type: {bold(preorder_type)}\n"
        f"Product: {bold(data.get('product_name') or 'Manual preorder')}\n"
        f"Accounts: {bold(qty)}\n"
        f"Price each: {bold(money(price_each))}\n"
        f"Required total: {bold(money(total))}\n"
        f"Paid amount: {bold(money(paid_amount))}\n"
        f"Payment method: {bold(method_name)}\n"
        f"TXID: {code(txid or 'Not provided')}\n"
        f"Auto verify: {bold('YES' if auto_verified else 'NO / manual check')}\n"
        f"Verify info: {escape(verify_info or 'N/A')}\n\n"
        f"Details:\n{escape(details)}"
    )
    for admin_id in list(dict.fromkeys(ADMIN_IDS)):
        try:
            if screenshot_type == "photo":
                await bot.send_photo(admin_id, photo=screenshot_file_id, caption=caption, reply_markup=preorder_admin_keyboard(preorder_id))
            else:
                await bot.send_document(admin_id, document=screenshot_file_id, caption=caption, reply_markup=preorder_admin_keyboard(preorder_id))
        except Exception:
            await safe_send_message(admin_id, caption, reply_markup=preorder_admin_keyboard(preorder_id))
    await audit("paid_preorder_created", message.from_user.id, {"preorder_id": preorder_id, "type": preorder_type, "total": total, "txid": txid, "auto_verified": auto_verified})

async def create_balance_paid_preorder(
    user_id: int,
    username: Optional[str],
    data: Dict[str, Any],
    source: str = "balance",
) -> ObjectId:
    """Create a preorder paid from the user's bot balance and notify admins."""
    total = round(float(data.get("estimated_total", 0) or 0), 2)
    qty = int(data.get("quantity", 1) or 1)
    doc = {
        "user_id": user_id,
        "username": username,
        "details": data.get("details", ""),
        "product_id": data.get("product_id"),
        "product_name": data.get("product_name"),
        "quantity": qty,
        "price_each": float(data.get("price_each", 0) or 0),
        "subtotal": float(data.get("subtotal", 0) or 0),
        "advance_fee": float(data.get("advance_fee", 0) or 0),
        "estimated_total": total,
        "payment_method_id": None,
        "payment_method": "Bot Balance",
        "payment_screenshot_file_id": None,
        "payment_screenshot_type": None,
        "payment_status": "paid_from_balance",
        "paid_with": "Bot Balance",
        "amount_paid": total,
        "type": data.get("preorder_type", "preorder"),
        "status": "pending",
        "answers": [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await preorders.insert_one(doc)
    preorder_id = str(result.inserted_id)
    await notify_admins(
        f"📝 {bold('New Balance-Paid Preorder')}{chr(10)*2}"
        f"Preorder ID: {code(preorder_id)}{chr(10)}"
        f"User: {code(user_id)} @{escape(username or 'None')}{chr(10)}"
        f"Type: {bold(doc.get('type'))}{chr(10)}"
        f"Product: {bold(doc.get('product_name') or 'Preorder')}{chr(10)}"
        f"Accounts: {bold(qty)}{chr(10)}"
        f"Amount deducted from balance: {bold(money(total))}{chr(10)}"
        f"Payment: {bold('Bot Balance')}{chr(10)*2}"
        f"Details:{chr(10)}{escape(doc.get('details', ''))}",
        reply_markup=preorder_admin_keyboard(preorder_id),
    )
    await audit("balance_paid_preorder_created", user_id, {"preorder_id": preorder_id, "amount": total, "source": source})
    return result.inserted_id

# ==========================================================
# PREORDER SYSTEM
# ==========================================================

def preorder_admin_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 Set Price", callback_data=f"preorder:setprice:{pid}"),
            InlineKeyboardButton(text="💬 Answer Client", callback_data=f"preorder:answer:{pid}"),
        ],
        [
            InlineKeyboardButton(text="✅ Mark Fulfilled", callback_data=f"preorder:done:{pid}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"preorder:reject:{pid}"),
        ],
    ])

@router.message(F.text == "📝 PREORDER")
async def preorder_start(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)
    if await send_force_join_message(message, "all"):
        return
    await state.clear()

    rows: List[List[InlineKeyboardButton]] = []
    async for pp in preorder_products.find({"active": {"$ne": False}}).sort([("position", ASCENDING), ("created_at", DESCENDING)]):
        label = f"{pp.get('name', 'Preorder')} — {money(pp.get('price', 0))}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"preproduct:view:{pp['_id']}")])

    rows.append([InlineKeyboardButton(text="✍️ Manual Preorder", callback_data="preorder:manual")])
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")])

    await message.answer(
        "📝 <b>Preorder Accounts</b>\n\nChoose a preorder product below. Payment is advance and screenshot is required. You can also use manual preorder.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )

@router.message(PreorderState.details)
async def preorder_save(message: Message, state: FSMContext) -> None:
    details = truncate((message.text or "").strip(), 2500)
    if len(details) < 3:
        await message.answer("❌ Please send preorder details.")
        return

    data = await state.get_data()
    qty = int(data.get("quantity", 1) or 1)
    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "details": details,
        "product_id": None,
        "product_name": "Manual preorder",
        "quantity": qty,
        "price_each": 0.0,
        "estimated_total": 0.0,
        "payment_method_id": None,
        "payment_method": None,
        "payment_screenshot_file_id": None,
        "payment_screenshot_type": None,
        "payment_status": "not_requested",
        "type": "manual",
        "status": "awaiting_price",
        "answers": [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await preorders.insert_one(doc)
    preorder_id = str(result.inserted_id)
    await state.clear()

    await message.answer(
        f"✅ Manual preorder sent to admin.\n\n"
        f"Preorder ID: {code(short_id(preorder_id))}\n"
        f"Status: {bold('waiting for admin price')}\n\n"
        f"Admin will review your request and send you a payment button with the final price.",
        reply_markup=main_menu(message.from_user.id),
    )

    await notify_admins(
        f"📝 {bold('New Manual Preorder')}\n\n"
        f"Preorder ID: {code(preorder_id)}\n"
        f"User: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\n"
        f"Payment: {bold('Not requested yet')}\n"
        f"Status: {bold('waiting for admin price')}\n\n"
        f"Details:\n{escape(details)}",
        reply_markup=preorder_admin_keyboard(preorder_id),
    )
    await audit("manual_preorder_created", message.from_user.id, {"preorder_id": preorder_id})

@router.callback_query(PreorderState.payment_method, F.data == "preorderpaymethod:binance")
async def preorder_binance_payment_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    data = await state.get_data()
    total = float(data.get("estimated_total", 0) or 0)
    wallet_address = str(await get_setting("binance_wallet_address", BINANCE_WALLET_ADDRESS) or "").strip()
    network = str(await get_setting("binance_network", BINANCE_NETWORK) or BINANCE_NETWORK).upper().strip()
    coin = str(await get_setting("binance_coin", BINANCE_COIN) or BINANCE_COIN).upper().strip()
    if not wallet_address:
        await callback.message.answer("❌ Binance wallet address is not configured. Ask admin to set Binance wallet in settings.", reply_markup=cancel_inline_keyboard())
        await callback.answer()
        return
    await state.update_data(
        payment_method_id=None,
        payment_method_name="Binance TXID Auto Verify",
        auto_verify_requested=True,
        binance_coin=coin,
        binance_network=network,
        binance_wallet_address=wallet_address,
    )
    await state.set_state(PreorderState.txid)
    await callback.message.answer(
        f"🤖 {bold('Binance Auto Verify')}\n\n"
        f"Amount to pay: {bold(money(total))}\n"
        f"Coin: {bold(coin)}\n"
        f"Network: {bold(network)}\n"
        f"Wallet address:\n{code(wallet_address)}\n\n"
        f"After payment, send TXID and amount paid like this:\n"
        f"{code('TXID_HERE | ' + str(total))}\n\n"
        f"Then bot will check Binance first. If not found, your screenshot will still go to admin for manual check.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.callback_query(PreorderState.payment_method, F.data.startswith("prepaymethod:"))
async def preorder_payment_method_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    mid = callback.data.split(":", 1)[1]
    oid = object_id(mid)
    method = await payment_methods.find_one({"_id": oid}) if oid else None
    if not method:
        await callback.answer("Payment method not found.", show_alert=True)
        return
    await state.update_data(payment_method_id=mid, payment_method_name=method.get("name"), auto_verify_requested=False)
    await state.set_state(PreorderState.txid)
    total = float((await state.get_data()).get("estimated_total", 0) or 0)
    await callback.message.answer(
        f"✅ Payment method selected: {bold(method.get('name'))}\n\n"
        f"Amount to pay: {bold(money(total))}\n"
        f"Payment details:\n{code(method.get('details', ''))}\n\n"
        f"After payment, send TXID and amount paid like this:\n"
        f"{code('TXID_HERE | ' + str(total))}\n\n"
        f"After that, bot will ask for screenshot/photo.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(PreorderState.txid)
async def preorder_payment_txid_received(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    text = (message.text or "").strip()
    data = await state.get_data()
    required_total = float(data.get("estimated_total", 0) or 0)
    # Accept: TXID | 3.12  OR  TXID 3.12  OR multiple lines.
    parts = [x.strip() for x in re.split(r"[|\n]", text) if x.strip()]
    if len(parts) >= 2:
        txid = parts[0]
        paid_amount = parse_float(parts[1])
    else:
        bits = text.split()
        txid = bits[0] if bits else ""
        paid_amount = parse_float(bits[-1]) if len(bits) >= 2 else None
    if len(txid) < 6 or paid_amount is None or paid_amount <= 0:
        await message.answer(
            f"❌ Invalid format. Send TXID and paid amount like:\n{code('TXID_HERE | ' + str(required_total))}",
            reply_markup=cancel_inline_keyboard(),
        )
        return
    if paid_amount + 1e-9 < required_total:
        await message.answer(
            f"❌ Paid amount is less than required.\nRequired: {bold(money(required_total))}\nYou sent: {bold(money(paid_amount))}",
            reply_markup=cancel_inline_keyboard(),
        )
        return

    auto_verified = False
    verify_info = "Manual payment method; admin will check screenshot."
    if data.get("auto_verify_requested"):
        coin = str(data.get("binance_coin") or BINANCE_COIN).upper().strip()
        network = str(data.get("binance_network") or BINANCE_NETWORK).upper().strip()
        wallet_address = str(data.get("binance_wallet_address") or BINANCE_WALLET_ADDRESS).strip()
        ok, info = await verify_binance_deposit_tx(txid, paid_amount, coin=coin, expected_network=network, expected_address=wallet_address)
        auto_verified = bool(ok)
        verify_info = info
        if ok:
            await message.answer("✅ Binance payment found. Now send screenshot/photo for admin record.", reply_markup=cancel_inline_keyboard())
        else:
            await message.answer(
                f"⚠️ Binance auto verify did not find the payment yet.\nReason: {escape(info)}\n\nSend screenshot/photo now. Admin can verify manually.",
                reply_markup=cancel_inline_keyboard(),
            )
    else:
        await message.answer("✅ TXID received. Now send payment screenshot/photo or document.", reply_markup=cancel_inline_keyboard())

    await state.update_data(txid=txid, paid_amount=float(paid_amount), auto_verified=auto_verified, verify_info=verify_info)
    await state.set_state(PreorderState.payment_screenshot)

@router.message(PreorderState.payment_screenshot, F.photo)
async def preorder_payment_photo(message: Message, state: FSMContext) -> None:
    await create_preorder_after_payment(message, state, message.photo[-1].file_id, "photo")

@router.message(PreorderState.payment_screenshot, F.document)
async def preorder_payment_document(message: Message, state: FSMContext) -> None:
    await create_preorder_after_payment(message, state, message.document.file_id, "document")

@router.message(PreorderState.payment_screenshot)
async def preorder_payment_need_screenshot(message: Message) -> None:
    await message.answer("❌ Please upload payment screenshot/photo or document.", reply_markup=cancel_inline_keyboard())

@router.callback_query(F.data.startswith("preorder:setprice:"))
async def preorder_set_price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    await state.update_data(preorder_id=pid)
    await state.set_state(AdminPreorderPriceState.amount)
    await callback.message.answer(
        f"💵 Send final price in {CURRENCY} for this preorder.\n\n"
        f"Preorder ID: {code(short_id(pre['_id']))}\n"
        f"User: {code(pre.get('user_id'))}\n\n"
        f"Details:\n{escape(pre.get('details', ''))}",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(AdminPreorderPriceState.amount)
async def preorder_set_price_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    amount = parse_float(message.text or "")
    if amount is None or amount <= 0:
        await message.answer("❌ Invalid price. Send price like 5 or 5.50", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    pid = data.get("preorder_id")
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await state.clear()
        await message.answer("❌ Preorder not found.")
        return

    await preorders.update_one(
        {"_id": pre["_id"]},
        {"$set": {
            "estimated_total": round(float(amount), 2),
            "payment_status": "requested",
            "status": "awaiting_user_payment",
            "price_set_by": message.from_user.id,
            "price_set_at": utcnow(),
            "updated_at": utcnow(),
        }},
    )

    await safe_send_message(
        pre["user_id"],
        f"💵 {bold('Preorder Price Set')}\n\n"
        f"Preorder ID: {code(short_id(pre['_id']))}\n"
        f"Amount to pay: {bold(money(amount))}\n\n"
        f"Tap the button below to choose payment method and upload screenshot.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Pay {money(amount)}", callback_data=f"preorderpay:{pid}")],
            [InlineKeyboardButton(text="📞 Support", url=f"https://t.me/{clean_username(str(await get_setting('support_username', 'support')))}")],
        ]),
    )
    await state.clear()
    await message.answer("✅ Price sent to user with payment button.")
    await audit("manual_preorder_price_set", message.from_user.id, {"preorder_id": pid, "amount": amount})

@router.callback_query(F.data.startswith("preorderpay:"))
async def manual_preorder_pay_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 1)[1]
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre or pre.get("user_id") != callback.from_user.id:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    if pre.get("payment_status") not in {"requested", "rejected"}:
        await callback.answer("Payment is not requested or already submitted.", show_alert=True)
        return

    rows = []
    async for method in payment_methods.find({}).sort([("position", ASCENDING), ("default", DESCENDING), ("created_at", DESCENDING)]):
        rows.append([InlineKeyboardButton(text=f"💳 {method.get('name', 'Payment')}", callback_data=f"manualprepay:{pid}:{method['_id']}")])
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")])
    if len(rows) <= 1:
        await callback.message.answer("❌ No payment method available. Contact support.", reply_markup=await contact_keyboard())
        await callback.answer()
        return
    await callback.message.answer(
        f"💳 {bold('Choose payment method')}\n\n"
        f"Preorder ID: {code(short_id(pre['_id']))}\n"
        f"Amount: {bold(money(pre.get('estimated_total', 0)))}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("manualprepay:"))
async def manual_preorder_payment_method_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid payment request.", show_alert=True)
        return
    pid, mid = parts[1], parts[2]
    oid = object_id(pid)
    moid = object_id(mid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    method = await payment_methods.find_one({"_id": moid}) if moid else None
    if not pre or pre.get("user_id") != callback.from_user.id:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    if not method:
        await callback.answer("Payment method not found.", show_alert=True)
        return
    await state.update_data(preorder_id=pid, payment_method_id=mid, payment_method_name=method.get("name"))
    await state.set_state(ManualPreorderPaymentState.payment_screenshot)
    await callback.message.answer(
        f"✅ Payment method selected: {bold(method.get('name'))}\n\n"
        f"Amount: {bold(money(pre.get('estimated_total', 0)))}\n"
        f"Payment details:\n{code(method.get('details', ''))}\n\n"
        f"Now send your payment screenshot/photo or document.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

async def update_manual_preorder_payment(message: Message, state: FSMContext, screenshot_file_id: str, screenshot_type: str) -> None:
    data = await state.get_data()
    pid = data.get("preorder_id")
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre or pre.get("user_id") != message.from_user.id:
        await state.clear()
        await message.answer("❌ Preorder not found.", reply_markup=main_menu(message.from_user.id))
        return

    method_id = data.get("payment_method_id")
    moid = object_id(method_id)
    method = await payment_methods.find_one({"_id": moid}) if moid else None
    method_name = method.get("name") if method else data.get("payment_method_name", "Unknown")

    await preorders.update_one(
        {"_id": pre["_id"]},
        {"$set": {
            "payment_method_id": method_id,
            "payment_method": method_name,
            "payment_screenshot_file_id": screenshot_file_id,
            "payment_screenshot_type": screenshot_type,
            "payment_status": "submitted",
            "status": "pending",
            "updated_at": utcnow(),
        }},
    )
    await state.clear()

    await message.answer(
        f"✅ Payment proof submitted to admin.\n\n"
        f"Preorder ID: {code(short_id(pre['_id']))}\n"
        f"Amount: {bold(money(pre.get('estimated_total', 0)))}\n"
        f"Payment method: {bold(method_name)}\n\n"
        f"Admin will check payment and reply with your products.",
        reply_markup=main_menu(message.from_user.id),
    )

    caption = (
        f"💳 {bold('Manual Preorder Payment Submitted')}\n\n"
        f"Preorder ID: {code(pid)}\n"
        f"User: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\n"
        f"Amount: {bold(money(pre.get('estimated_total', 0)))}\n"
        f"Payment method: {bold(method_name)}\n\n"
        f"Details:\n{escape(pre.get('details', ''))}"
    )
    for admin_id in list(dict.fromkeys(ADMIN_IDS)):
        try:
            if screenshot_type == "photo":
                await bot.send_photo(admin_id, photo=screenshot_file_id, caption=caption, reply_markup=preorder_admin_keyboard(pid))
            else:
                await bot.send_document(admin_id, document=screenshot_file_id, caption=caption, reply_markup=preorder_admin_keyboard(pid))
        except Exception:
            await safe_send_message(admin_id, caption, reply_markup=preorder_admin_keyboard(pid))
    await audit("manual_preorder_payment_submitted", message.from_user.id, {"preorder_id": pid})

@router.message(ManualPreorderPaymentState.payment_screenshot, F.photo)
async def manual_preorder_payment_photo(message: Message, state: FSMContext) -> None:
    await update_manual_preorder_payment(message, state, message.photo[-1].file_id, "photo")

@router.message(ManualPreorderPaymentState.payment_screenshot, F.document)
async def manual_preorder_payment_document(message: Message, state: FSMContext) -> None:
    await update_manual_preorder_payment(message, state, message.document.file_id, "document")

@router.message(ManualPreorderPaymentState.payment_screenshot)
async def manual_preorder_payment_need_screenshot(message: Message) -> None:
    await message.answer("❌ Please upload payment screenshot/photo or document.", reply_markup=cancel_inline_keyboard())

@router.callback_query(F.data == "admin:preorders")
async def admin_preorders(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for pre in preorders.find({"status": {"$in": ["awaiting_price", "awaiting_user_payment", "pending", "answered"]}}).sort("created_at", DESCENDING).limit(20):
        found = True
        pid = str(pre["_id"])
        await callback.message.answer(
            f"📝 {bold('Preorder')}\n\n"
            f"ID: {code(pid)}\n"
            f"User: {code(pre.get('user_id'))} @{escape(pre.get('username') or 'None')}\n"
            f"Status: {bold(pre.get('status'))}\n"
            f"Payment: {bold(pre.get('payment_status', 'not submitted'))} via {bold(pre.get('payment_method', 'N/A'))}\n"
            f"Amount: {bold(money(pre.get('estimated_total', 0)))}\n\n"
            f"Details:\n{escape(pre.get('details', ''))}",
            reply_markup=preorder_admin_keyboard(pid),
        )
    if not found:
        await callback.message.answer("No pending preorders.")
    await callback.answer()

@router.callback_query(F.data.startswith("preorder:answer:"))
async def preorder_answer_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    await state.update_data(preorder_id=pid)
    await state.set_state(AdminPreorderAnswerState.answer)
    await callback.message.answer("Send answer/notification for the client:", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(AdminPreorderAnswerState.answer)
async def preorder_answer_send(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    pid = data.get("preorder_id")
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await state.clear()
        await message.answer("❌ Preorder not found.")
        return
    answer = truncate((message.text or "").strip(), 2500)
    if len(answer) < 1:
        await message.answer("❌ Send an answer.")
        return
    await preorders.update_one(
        {"_id": pre["_id"]},
        {"$set": {"status": "answered", "updated_at": utcnow(), "last_answer_by": message.from_user.id},
         "$push": {"answers": {"from_admin": message.from_user.id, "text": answer, "at": utcnow()}}},
    )
    await safe_send_message(
        pre["user_id"],
        f"📝 {bold('Preorder Update')}\n\n"
        f"Preorder ID: {code(short_id(pre['_id']))}\n\n"
        f"Admin answer:\n{escape(answer)}\n\n"
        f"Need help? Tap the support button below.",
        reply_markup=await contact_keyboard(),
    )
    await state.clear()
    await message.answer("✅ Answer sent to client.")
    await audit("preorder_answered", message.from_user.id, {"preorder_id": pid})

@router.callback_query(F.data.startswith("preorder:done:"))
async def preorder_done(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    await preorders.update_one({"_id": pre["_id"]}, {"$set": {"status": "fulfilled", "handled_by": callback.from_user.id, "updated_at": utcnow()}})
    await safe_send_message(pre["user_id"], f"✅ Your preorder {code(short_id(pre['_id']))} has been fulfilled. Check admin message or contact support if needed.", reply_markup=await contact_keyboard())
    await callback.message.answer("✅ Preorder marked fulfilled and client notified.")
    await callback.answer()

@router.callback_query(F.data.startswith("preorder:reject:"))
async def preorder_reject(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pre = await preorders.find_one({"_id": oid}) if oid else None
    if not pre:
        await callback.answer("Preorder not found.", show_alert=True)
        return
    refund_text = ""
    update_fields = {"status": "rejected", "handled_by": callback.from_user.id, "updated_at": utcnow()}
    if pre.get("payment_status") == "paid_from_balance" and not pre.get("refund_processed"):
        refund_amount = round(float(pre.get("amount_paid", pre.get("estimated_total", 0)) or 0), 2)
        if refund_amount > 0:
            await users.update_one({"user_id": pre["user_id"]}, {"$inc": {"balance": refund_amount}})
            update_fields["refund_processed"] = True
            update_fields["refund_amount"] = refund_amount
            update_fields["refund_at"] = utcnow()
            refund_text = f"{chr(10)*2}💰 Refunded to your bot balance: {bold(money(refund_amount))}"
    await preorders.update_one({"_id": pre["_id"]}, {"$set": update_fields})
    await safe_send_message(pre["user_id"], f"❌ Your preorder {code(short_id(pre['_id']))} was rejected.{refund_text}{chr(10)*2}Contact support for details.", reply_markup=await contact_keyboard())
    await callback.message.answer("❌ Preorder rejected and client notified." + (" Balance refunded." if refund_text else ""))
    await callback.answer()

# ==========================================================
# USER BALANCE / ORDERS
# ==========================================================

@router.message(F.text.in_({"💼 BALANCE", "/balance"}))
@router.message(Command("balance"))
async def show_balance(message: Message) -> None:
    if not await security_check_event(message):
        return
    user = await ensure_user(message)
    await message.answer(
        f"💼 {bold('Your Account')}\n\n"
        f"Balance: {bold(money(user.get('balance', 0)))}\n"
        f"Points: {bold(user.get('points', 0))}\n"
        f"Total orders: {bold(user.get('total_orders', 0))}"
    )

@router.message(F.text.in_({"🧾 MY ORDERS", "/myorders"}))
@router.message(Command("myorders"))
async def my_orders(message: Message) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)

    cursor = orders.find({"user_id": message.from_user.id}).sort("created_at", DESCENDING).limit(10)
    found = False
    text = "🧾 Your recent orders:\n\n"
    async for order in cursor:
        found = True
        text += (
            f"• {escape(order.get('product_name'))} x{order.get('quantity', order.get('qty', 0))} "
            f"— {money(order.get('total', 0))} — {escape(order.get('status'))}\n"
        )

    await message.answer(text if found else "You have no orders yet.")

# ==========================================================
# PRODUCTS / BUY
# ==========================================================

@router.message(F.text == "🛍️ PRODUCTS")
async def show_products(message: Message) -> None:
    if not await security_check_event(message):
        return
    if await send_force_join_message(message, "all"):
        return
    await ensure_user(message)

    rows: List[List[InlineKeyboardButton]] = []
    product_list = []
    async for product in products.find({"active": {"$ne": False}}):
        stock_count = len(product.get("stock", []))
        product["_stock_count"] = stock_count
        product_list.append(product)

    # In-stock products first. Every product appears on its own line and price is visible.
    product_list.sort(key=lambda p: (0 if p.get("_stock_count", 0) > 0 else 1, -int(p.get("seller_reach_boost", 0) or 0), int(p.get("position", 999999)), str(p.get("name", "")).lower()))

    for product in product_list:
        # Main product list only shows product name and price. Stockout appears after opening product.
        label = f"{product.get('name')} — {money(product.get('price', 0))}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"product:view:{product['_id']}")])

    if not rows:
        await message.answer("❌ No products available now.")
        return

    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")])
    await message.answer(
        f"🛍️ {bold('Choose a service:')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )

@router.callback_query(F.data.startswith("product:view:"))
async def user_view_product(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 2)[2]
    product = await product_by_id(pid)
    if not product or product.get("active") is False:
        await callback.answer("Product not found.", show_alert=True)
        return
    stock_count = len(product.get("stock", []))
    if stock_count <= 0:
        await callback.message.answer(
            (await format_product_display(product)) + "\n\n📛 Stockout: This product is visible but not available right now.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=(
                [[InlineKeyboardButton(text="📝 Preorder this product", callback_data=f"stockoutpreorder:{pid}")],
                 [InlineKeyboardButton(text="📛 Stockout", callback_data="noop:stockout")]]
                + ([[InlineKeyboardButton(text="✏️ Admin Edit Price", callback_data=f"prod:edit_price:{pid}")]] if is_admin(callback.from_user.id) else [])
                + [[InlineKeyboardButton(text="⬅️ Back to Products", callback_data="nav:products")],
                   [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")]]
            )),
        )
    else:
        buy_kb = await product_buy_keyboard(pid, stock_count)
        product_rows = list(buy_kb.inline_keyboard)
        if is_admin(callback.from_user.id):
            product_rows.insert(1, [InlineKeyboardButton(text="✏️ Admin Edit Price", callback_data=f"prod:edit_price:{pid}")])
        await callback.message.answer(
            await format_product_display(product),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=product_rows),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("stockoutpreorder:") & (F.data != "stockoutpreorder:confirm"))
async def stockout_preorder_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 1)[1]
    product = await product_by_id(pid)
    if not product or product.get("active") is False:
        await callback.answer("Product not found.", show_alert=True)
        return
    await state.update_data(product_id=pid, back_to=f"product:{pid}")
    await state.set_state(StockoutPreorderQuantityState.quantity)
    await callback.message.answer(
        f"📝 {bold('Stockout Preorder')}\n\n"
        f"Product: {bold(product.get('name', 'Product'))}\n"
        f"Price shown: {bold(money(product.get('price', 0)))}\n\n"
        f"Send how many accounts you want to preorder. Admin will review and set final price if needed.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(StockoutPreorderQuantityState.quantity)
async def stockout_preorder_quantity(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    qty = parse_int(message.text or "")
    if not qty or qty <= 0:
        await message.answer("❌ Send a valid number of accounts.", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    product = await product_by_id(data.get("product_id"))
    if not product:
        await state.clear()
        await message.answer("❌ Product not found.", reply_markup=main_menu(message.from_user.id))
        return

    subtotal, fee, total = preorder_advance_total(product.get("price", 0), qty)
    details = (
        f"Stockout preorder: {product.get('name')} | Quantity: {qty} account(s) | "
        f"Subtotal: {money(subtotal)} | {int(PREORDER_ADVANCE_FEE_PERCENT)}% advance fee: {money(fee)} | "
        f"Advance total: {money(total)}"
    )

    await state.update_data(
        preorder_type="stockout",
        details=details,
        product_id=str(product.get("_id")),
        product_name=product.get("name"),
        quantity=qty,
        price_each=float(product.get("price", 0) or 0),
        subtotal=subtotal,
        advance_fee=fee,
        estimated_total=total,
        back_to=f"product:{product.get('_id')}",
    )

    await message.answer(
        f"📝 <b>Confirm Stockout Preorder</b>\n\n"
        f"Product: {bold(product.get('name'))}\n"
        f"Accounts: {bold(qty)}\n"
        f"Base price each: {bold(money(product.get('price', 0)))}\n"
        f"Subtotal: {bold(money(subtotal))}\n"
        f"Advance fee ({int(PREORDER_ADVANCE_FEE_PERCENT)}%): {bold(money(fee))}\n"
        f"Total to pay now: {bold(money(total))}\n\n"
        f"This stockout preorder requires advance payment before it is sent to admin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Continue to Payment", callback_data="stockoutpreorder:confirm")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="global:cancel")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
        ]),
    )

@router.callback_query(F.data == "stockoutpreorder:confirm")
async def stockout_preorder_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    data = await state.get_data()
    if data.get("preorder_type") != "stockout" or not data.get("product_id"):
        await callback.answer("Preorder data not found. Please start again.", show_alert=True)
        await state.clear()
        return

    total = round(float(data.get("estimated_total", 0) or 0), 2)
    user = await get_user(callback.from_user.id)
    user_balance = float((user or {}).get("balance", 0) or 0)

    if user_balance >= total:
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"balance": -total, "total_spent": total}})
        preorder_oid = await create_balance_paid_preorder(callback.from_user.id, callback.from_user.username, data, source="stockout_balance")
        await state.clear()
        await callback.message.answer(
            f"✅ {bold('Stockout preorder submitted successfully!')}{chr(10)*2}"
            f"Preorder ID: {code(short_id(preorder_oid))}{chr(10)}"
            f"Product: {bold(data.get('product_name'))}{chr(10)}"
            f"Accounts: {bold(data.get('quantity', 1))}{chr(10)}"
            f"Paid from your bot balance: {bold(money(total))}{chr(10)}"
            f"Remaining balance: {bold(money(user_balance - total))}{chr(10)*2}"
            f"Admin will review and reply with your products.",
            reply_markup=main_menu(callback.from_user.id),
        )
        await callback.answer("Preorder submitted")
        return

    await callback.message.answer(
        f"💰 <b>Advance Payment Required</b>{chr(10)*2}"
        f"Product: {bold(data.get('product_name'))}{chr(10)}"
        f"Accounts: {bold(data.get('quantity', 1))}{chr(10)}"
        f"Subtotal: {bold(money(data.get('subtotal', 0)))}{chr(10)}"
        f"Advance fee ({int(PREORDER_ADVANCE_FEE_PERCENT)}%): {bold(money(data.get('advance_fee', 0)))}{chr(10)}"
        f"Total to pay now: {bold(money(total))}{chr(10)}"
        f"Your bot balance: {bold(money(user_balance))}{chr(10)*2}"
        f"Your balance is not enough. Choose a payment method and send TXID, paid amount, and screenshot."
    )
    await send_preorder_payment_methods(callback.message, state)
    await callback.answer()

@router.callback_query(F.data == "noop:stockout")
async def noop_stockout(callback: CallbackQuery) -> None:
    await callback.answer("This item is out of stock.", show_alert=True)

@router.callback_query(F.data.startswith("buyask:"))
async def ask_product_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return

    pid = callback.data.split(":", 1)[1]
    product = await product_by_id(pid)
    if not product or product.get("active") is False:
        await callback.answer("Product not found.", show_alert=True)
        return

    stock_count = len(product.get("stock", []))
    if stock_count <= 0:
        await callback.answer("This product is stockout.", show_alert=True)
        return

    await state.update_data(product_id=pid, back_to=f"product:{pid}")
    await state.set_state(BuyQuantityState.quantity)
    await callback.message.answer(
        f"🛒 {bold(product.get('name', 'Product'))}\n\n"
        f"Available stock: {bold(stock_count)}\n"
        f"Price per account: {bold(money(product.get('price', 0)))}\n\n"
        f"Send how many accounts you want to buy.\n"
        f"Nearest bulk discount will be applied automatically.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(BuyQuantityState.quantity)
async def receive_product_quantity(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return

    quantity = parse_int(message.text or "")
    if not quantity or quantity <= 0:
        await message.answer("❌ Send a valid number of accounts.", reply_markup=cancel_inline_keyboard())
        return

    data = await state.get_data()
    pid = data.get("product_id")
    product = await product_by_id(pid)
    if not product or product.get("active") is False:
        await state.clear()
        await message.answer("❌ Product not found. Please choose again.", reply_markup=main_menu(message.from_user.id))
        return

    stock_count = len(product.get("stock", []))
    if stock_count < quantity:
        await message.answer(
            f"❌ Not enough stock.\n\nAvailable: {bold(stock_count)}\nRequested: {bold(quantity)}",
            reply_markup=cancel_inline_keyboard(),
        )
        return

    user = await get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Please /start first.", reply_markup=main_menu(message.from_user.id))
        return

    price = float(product.get("price", 0))
    subtotal = round(price * quantity, 2)
    discount = await discount_for_quantity_async(quantity)
    total = round(subtotal * (1 - discount), 2)

    await state.clear()
    await message.answer(
        f"✅ {bold('Confirm Purchase')}\n\n"
        f"Product: {bold(product.get('name'))}\n"
        f"Quantity: {bold(quantity)}\n"
        f"Price each: {bold(money(price))}\n"
        f"Subtotal: {bold(money(subtotal))}\n"
        f"Discount applied: {bold(int(discount * 100))}%\n"
        f"Total to pay: {bold(money(total))}\n"
        f"Your balance: {bold(money(user.get('balance', 0)))}\n\n"
        f"Click confirm to buy, or cancel to stop.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Confirm Buy", callback_data=f"buyconfirm:{pid}:{quantity}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")],
        ]),
    )

@router.callback_query(F.data.startswith("buyconfirm:"))
async def buy_product(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid request.", show_alert=True)
        return

    pid, qty_raw = parts[1], parts[2]
    quantity = parse_int(qty_raw)
    if not quantity or quantity <= 0:
        await callback.answer("Invalid quantity.", show_alert=True)
        return

    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Please /start first.", show_alert=True)
        return

    product = await product_by_id(pid)
    if not product or product.get("active") is False:
        await callback.answer("Product not found.", show_alert=True)
        return

    stock = product.get("stock", [])
    if len(stock) < quantity:
        await callback.answer("Not enough stock.", show_alert=True)
        return

    price = float(product.get("price", 0))
    subtotal = round(price * quantity, 2)
    discount = await discount_for_quantity_async(quantity)
    total = round(subtotal * (1 - discount), 2)

    if float(user.get("balance", 0)) < total:
        await callback.message.answer(
            f"❌ Insufficient balance.\n\n"
            f"Need: {bold(money(total))}\n"
            f"Your balance: {bold(money(user.get('balance', 0)))}\n\n"
            f"Use 💰 DEPOSIT to add balance.",
            reply_markup=cancel_inline_keyboard(),
        )
        await callback.answer()
        return

    delivered = stock[:quantity]
    remaining = stock[quantity:]

    # Atomic-ish update: only set remaining after checking current product.
    try:
        await products.update_one(
            {"_id": product["_id"]},
            {
                "$set": {"stock": remaining, "updated_at": utcnow()},
                "$inc": {"sales": quantity, "revenue": total},
            },
        )
        await users.update_one(
            {"user_id": callback.from_user.id},
            {
                "$inc": {
                    "balance": -total,
                    "total_spent": total,
                    "total_orders": 1,
                }
            },
        )
        is_resell_order = bool(product.get("is_resell"))
        seller_id = int(product.get("seller_id", 0) or 0) if is_resell_order else None
        commission_percent = float(product.get("seller_commission_percent", await get_setting("default_reseller_commission_percent", 10)) or 10) if is_resell_order else 0.0
        order_doc = {
            "user_id": callback.from_user.id,
            "username": callback.from_user.username,
            "product_id": str(product["_id"]),
            "product_name": product.get("name"),
            "quantity": quantity,
            "subtotal": subtotal,
            "discount": discount,
            "total": total,
            "delivered": delivered,
            "status": "awaiting_buyer_confirm" if is_resell_order else "completed",
            "is_resell": is_resell_order,
            "seller_id": seller_id,
            "seller_username": product.get("seller_username"),
            "commission_percent": commission_percent,
            "seller_payout_status": "waiting_buyer_confirmation" if is_resell_order else None,
            "created_at": utcnow(),
        }
        result = await orders.insert_one(order_doc)
        order_doc["_id"] = result.inserted_id
        await notify_admin_order(order_doc, user)
        await audit("order_completed", callback.from_user.id, {
            "order_id": str(result.inserted_id),
            "product": product.get("name"),
            "quantity": quantity,
            "total": total,
        })
    except Exception as e:
        logger.exception("Order failed")
        await callback.message.answer("❌ Order failed due to database error. Contact support.")
        return

    delivered_text = "\n".join(code(item) for item in delivered)
    if bool(product.get("is_resell")):
        await callback.message.answer(
            f"✅ {bold('Reseller order delivered!')}\n\n"
            f"Order ID: {code(short_id(result.inserted_id))}\n"
            f"Product: {bold(product.get('name'))}\n"
            f"Quantity: {bold(quantity)}\n"
            f"Paid: {bold(money(total))}\n\n"
            f"Your data:\n{delivered_text}\n\n"
            f"Please check the product. When it is correct, tap ✅ Confirm Received. Seller payout will be released after your confirmation.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Confirm Received", callback_data=f"buyerconfirm:{result.inserted_id}")],
                [InlineKeyboardButton(text="📞 Support", url=f"https://t.me/{clean_username(str(await get_setting('support_username', 'support')))}")],
            ]),
        )
        if product.get("seller_id"):
            await safe_send_message(int(product.get("seller_id")), f"🛒 New reseller sale waiting buyer confirmation.\nProduct: {bold(product.get('name'))}\nAmount: {bold(money(total))}\nOrder: {code(short_id(result.inserted_id))}")
        await callback.answer("Delivered, waiting buyer confirmation")
    else:
        await callback.message.answer(
            f"✅ {bold('Order completed!')}\n\n"
            f"Order ID: {code(short_id(result.inserted_id))}\n"
            f"Product: {bold(product.get('name'))}\n"
            f"Quantity: {bold(quantity)}\n"
            f"Discount: {bold(int(discount * 100))}%\n"
            f"Paid: {bold(money(total))}\n\n"
            f"Your data:\n{delivered_text}",
            reply_markup=await contact_keyboard(),
        )
        await callback.answer("Delivered!")

# ==========================================================
# REFERRAL
# ==========================================================

@router.message(F.text == "👥 REFERRAL")
async def referral_menu(message: Message) -> None:
    if not await security_check_event(message):
        return
    if await send_force_join_message(message, "all"):
        return
    user = await ensure_user(message)
    me = await bot.get_me()

    ref_code = f"REF{message.from_user.id}"
    ref_link = f"https://t.me/{me.username}?start={ref_code}"

    await message.answer(
        f"👥 {bold('Referral System')}\n\n"
        f"Referral code: {code(ref_code)}\n"
        f"Referral link:\n{escape(ref_link)}\n\n"
        f"Reward points: {bold(await get_setting('referral_reward_points', 1))}\n"
        f"Your points: {bold(user.get('points', 0))}\n"
        f"Referral count: {bold(user.get('referral_count', 0))}"
    )

# ==========================================================
# CANVA PRO
# ==========================================================

async def send_free_canva_request_prompt(target: Message, user: Dict[str, Any], state: FSMContext) -> None:
    mode = str(await get_setting("canva_delivery_mode", "gmail") or "gmail").lower().strip()
    if mode == "link":
        link = str(await get_setting("canva_invite_link", "") or "").strip()
        if not link:
            await target.answer("❌ Canva link is not configured yet. Contact support.", reply_markup=await contact_keyboard())
            return
        await state.clear()
        sent_msg = await target.answer(
            f"🎁 {bold('Canva Pro Invite Link')}\n\n"
            f"Open this link and join Canva Pro:\n{escape(link)}\n\n"
            f"⚠️ This message will disappear in 30 minutes.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Open Canva Link", url=link)],
                [InlineKeyboardButton(text="📞 Support", url=f"https://t.me/{clean_username(str(await get_setting('support_username', 'support')))}")],
            ])
        )

        async def delete_canva_link_later(chat_id: int, message_id: int) -> None:
            await asyncio.sleep(1800)
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception:
                pass

        asyncio.create_task(delete_canva_link_later(sent_msg.chat.id, sent_msg.message_id))
        return

    price = float(await get_setting("canva_price_usdt", 1.0))
    points_required = int(await get_setting("canva_points_required", 10))
    await state.set_state(CanvaRequestState.gmail)
    await target.answer(
        f"🎁 {bold('Canva Pro Gmail Request')}\n\n"
        "Send your Gmail address first. If first-free Canva is enabled and you have not used it, this request will be free. Otherwise bot will ask you to choose payment: USDT balance or referral points.\n\n"
        f"USDT price: {bold(money(price))}\n"
        f"Points required: {bold(points_required)}\n"
        f"Your balance: {bold(money(user.get('balance', 0)))}\n"
        f"Your points: {bold(user.get('points', 0))}\n\n"
        "Example: yourname@gmail.com",
        reply_markup=cancel_inline_keyboard(),
    )

@router.message(F.text == "🎁 FREE CANVA")
async def free_canva_menu(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    user = await ensure_user(message)
    if await send_force_join_message(message, "free_canva"):
        return
    await send_free_canva_request_prompt(message, user, state)

@router.callback_query(F.data == "freeitems:free_canva")
async def free_canva_from_free_items(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    if await send_force_join_message(callback, "free_canva"):
        return
    user = await get_user(callback.from_user.id)
    if not user:
        user = {
            "user_id": callback.from_user.id,
            "username": callback.from_user.username,
            "first_name": callback.from_user.first_name,
            "last_name": callback.from_user.last_name,
            "balance": 0.0,
            "points": 0,
            "banned": False,
            "ban_reason": None,
            "referred_by": None,
            "referral_count": 0,
            "total_spent": 0.0,
            "total_orders": 0,
            "created_at": utcnow(),
            "last_seen": utcnow(),
        }
        await users.insert_one(user)
    await send_free_canva_request_prompt(callback.message, user, state)
    await callback.answer()

@router.message(CanvaRequestState.gmail)
async def save_canva_request(message: Message, state: FSMContext) -> None:
    gmail = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", gmail):
        await message.answer("❌ Invalid Gmail/email. Send again.")
        return
    price = float(await get_setting("canva_price_usdt", 1.0))
    points_required = int(await get_setting("canva_points_required", 10))

    first_free_enabled = bool(await get_setting("canva_first_free_enabled", True))
    user = await get_user(message.from_user.id) or {}
    first_free_available = first_free_enabled and not bool(user.get("canva_first_free_used"))
    status = "pending" if first_free_available else "awaiting_payment"
    payment_type = "first_free" if first_free_available else None
    paid_text = "First Canva account free" if first_free_available else None

    result = await canva_requests.insert_one({
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "gmail": gmail,
        "status": status,
        "payment_type": payment_type,
        "paid_text": paid_text,
        "price_usdt": price,
        "points_required": points_required,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    await state.clear()
    rid = str(result.inserted_id)

    if first_free_available:
        await users.update_one({"user_id": message.from_user.id}, {"$set": {"canva_first_free_used": True}})
        await message.answer(
            f"✅ Gmail received: {code(gmail)}\n\nYour first Canva request is free and has been sent to admin.\nStatus: {bold('pending')}",
            reply_markup=main_menu(message.from_user.id),
        )
        await notify_admins(
            f"📩 {bold('New FREE First Canva Request')}\n\n"
            f"Request ID: {code(rid)}\n"
            f"User: {code(message.from_user.id)} @{escape(message.from_user.username or '')}\n"
            f"Gmail: {code(gmail)}\n"
            f"Payment: {bold('First account free')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Mark Added", callback_data=f"canvareq:done:{rid}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"canvareq:reject:{rid}"),
            ]]),
        )
        await audit("canva_first_free_request", message.from_user.id, {"request_id": rid})
        return

    await message.answer(
        f"📩 Gmail received: {code(gmail)}\n\nChoose payment. Bot will deduct only after you click one option.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Deduct {money(price)}", callback_data=f"canvareqpay:usdt:{rid}")],
            [InlineKeyboardButton(text=f"Deduct {points_required} Points", callback_data=f"canvareqpay:points:{rid}")],
        ]),
    )

@router.callback_query(F.data.startswith("canvareqpay:"))
async def canva_request_take_payment(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid request.", show_alert=True)
        return
    pay_type, rid = parts[1], parts[2]
    oid = object_id(rid)
    req = await canva_requests.find_one({"_id": oid}) if oid else None
    user = await get_user(callback.from_user.id)
    if not req or not user or req.get("user_id") != callback.from_user.id:
        await callback.answer("Request not found.", show_alert=True)
        return
    if req.get("status") != "awaiting_payment":
        await callback.answer("Already paid or handled.", show_alert=True)
        return
    price = float(req.get("price_usdt", await get_setting("canva_price_usdt", 1.0)))
    points_required = int(req.get("points_required", await get_setting("canva_points_required", 10)))
    if pay_type == "usdt":
        if float(user.get("balance", 0)) < price:
            await callback.answer("Insufficient USDT balance.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"balance": -price, "total_spent": price}})
        paid_text = money(price)
    elif pay_type == "points":
        if int(user.get("points", 0)) < points_required:
            await callback.answer("Not enough referral points.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"points": -points_required}})
        paid_text = f"{points_required} points"
    else:
        await callback.answer("Invalid payment type.", show_alert=True)
        return
    await canva_requests.update_one({"_id": req["_id"]}, {"$set": {"status": "pending", "payment_type": pay_type, "paid_text": paid_text, "updated_at": utcnow()}})
    await callback.message.answer(
        f"✅ Canva request paid and sent to admin.\n\nGmail: {code(req.get('gmail'))}\nPaid with: {bold(paid_text)}\nStatus: {bold('pending')}",
        reply_markup=main_menu(callback.from_user.id),
    )
    for admin_id in ADMIN_IDS:
        await safe_send_message(
            admin_id,
            f"📩 {bold('New Paid Canva Request')}\n\nRequest ID: {code(req['_id'])}\nUser: {code(callback.from_user.id)} @{escape(callback.from_user.username or '')}\nGmail: {code(req.get('gmail'))}\nPaid with: {bold(paid_text)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Mark Added", callback_data=f"canvareq:done:{req['_id']}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"canvareq:reject:{req['_id']}"),
            ]]),
        )
    await callback.answer("Paid")

@router.callback_query(F.data.startswith("canvareq:done:"))
async def canva_request_done(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    rid = callback.data.split(":", 2)[2]
    oid = object_id(rid)
    req = await canva_requests.find_one({"_id": oid}) if oid else None
    if not req:
        await callback.answer("Request not found.", show_alert=True)
        return
    await canva_requests.update_one({"_id": req["_id"]}, {"$set": {"status": "added", "handled_by": callback.from_user.id, "updated_at": utcnow()}})
    await safe_send_message(req["user_id"], f"✅ Your Gmail {code(req.get('gmail'))} has been added to Canva Pro.")
    await callback.message.answer("✅ Canva request marked as added.")
    await callback.answer()

@router.callback_query(F.data.startswith("canvareq:reject:"))
async def canva_request_reject(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    rid = callback.data.split(":", 2)[2]
    oid = object_id(rid)
    req = await canva_requests.find_one({"_id": oid}) if oid else None
    if not req:
        await callback.answer("Request not found.", show_alert=True)
        return
    await canva_requests.update_one({"_id": req["_id"]}, {"$set": {"status": "rejected", "handled_by": callback.from_user.id, "updated_at": utcnow()}})
    await safe_send_message(req["user_id"], "❌ Your Canva request was rejected. Contact support if this is a mistake.")
    await callback.message.answer("❌ Canva request rejected.")
    await callback.answer()

# ==========================================================
# CONTENT METHODS / FREE PRODUCTS / PAYMENT METHOD SEPARATION
# ==========================================================

@router.message(F.text == "💳 METHODS")
async def show_content_methods(message: Message) -> None:
    if not await security_check_event(message):
        return
    if await send_force_join_message(message, "all"):
        return
    await ensure_user(message)
    rows: List[List[InlineKeyboardButton]] = []
    async for method in content_methods.find({"active": {"$ne": False}}).sort([("position", ASCENDING), ("created_at", DESCENDING)]):
        rows.append([InlineKeyboardButton(text=f"{method.get('name')} — {money(method.get('price', 0))}", callback_data=f"cm:view:{method['_id']}")])
    if not rows:
        await message.answer("No paid methods available yet.")
        return
    await message.answer(f"💳 {bold('Methods')}\n\nChoose a method to buy:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("cm:view:"))
async def view_content_method(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    method = await content_methods.find_one({"_id": oid}) if oid else None
    if not method or method.get("active") is False:
        await callback.answer("Method not found.", show_alert=True)
        return
    text = (
        f"💳 {bold(method.get('name'))}\n\n"
        f"Price: {bold(money(method.get('price', 0)))}\n"
        f"Status: {bold('Available')}\n\n"
        f"{escape(method.get('description', ''))}"
    )
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Buy Method", callback_data=f"cm:buy:{mid}")]]))
    await callback.answer()

@router.callback_query(F.data.startswith("cm:buy:"))
async def buy_content_method(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    method = await content_methods.find_one({"_id": oid}) if oid else None
    user = await get_user(callback.from_user.id)
    if not method or not user:
        await callback.answer("Not found.", show_alert=True)
        return
    price = float(method.get("price", 0))
    if float(user.get("balance", 0)) < price:
        await callback.answer("Insufficient balance.", show_alert=True)
        return
    await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"balance": -price, "total_spent": price, "total_orders": 1}})
    delivery_items = method.get("delivery_items") or [{"type": method.get("delivery_type", "text"), "value": method.get("delivery", "")}]
    order_doc = {"user_id": callback.from_user.id, "username": callback.from_user.username, "product_id": mid, "product_name": f"METHOD: {method.get('name')}", "quantity": 1, "subtotal": price, "discount": 0, "total": price, "paid_with": money(price), "delivered": delivery_items, "status": "completed", "created_at": utcnow()}
    result = await orders.insert_one(order_doc)
    order_doc["_id"] = result.inserted_id
    await notify_admin_order(order_doc, user)
    await content_methods.update_one({"_id": method["_id"]}, {"$inc": {"sales": 1, "revenue": price}})
    await callback.message.answer(f"✅ Method purchased!\nOrder ID: {code(short_id(result.inserted_id))}\nPaid: {bold(money(price))}\n\nDelivering all files/videos/text now...")
    for item in delivery_items:
        item_type = item.get("type", "text") if isinstance(item, dict) else "text"
        value = item.get("value", "") if isinstance(item, dict) else str(item)
        caption = item.get("caption") if isinstance(item, dict) else None
        if item_type == "video":
            await bot.send_video(callback.from_user.id, value, caption=caption or "🎬 Method video")
        elif item_type == "document":
            await bot.send_document(callback.from_user.id, value, caption=caption or "📁 Method file")
        else:
            await callback.message.answer(f"📄 Method text:\n\n{escape(value)}")
    await callback.answer("Delivered!")

# ==========================================================
# DEPOSIT SYSTEM
# ==========================================================

@router.message(F.text == "💰 DEPOSIT")
async def deposit_start(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)
    await state.set_state(DepositState.amount)
    await message.answer("💰 Enter deposit amount in USDT:", reply_markup=cancel_inline_keyboard())

@router.message(DepositState.amount)
async def deposit_amount(message: Message, state: FSMContext) -> None:
    amount = parse_float(message.text or "")
    if amount is None or amount <= 0 or amount > 100000:
        await message.answer("❌ Enter a valid positive amount.")
        return

    count = await payment_methods.count_documents({})
    if count == 0:
        await state.clear()
        await message.answer("❌ No payment method available. Contact support.")
        return

    await state.update_data(amount=amount)
    await state.set_state(DepositState.method)

    rows = []
    async for method in payment_methods.find({}).sort([("position", ASCENDING), ("default", DESCENDING), ("created_at", ASCENDING)]):
        rows.append([InlineKeyboardButton(text=method.get("name"), callback_data=f"depositmethod:{method['_id']}")])
    if bool(await get_setting("binance_auto_verify_enabled", BINANCE_AUTO_VERIFY)):
        rows.append([InlineKeyboardButton(text="🤖 Binance Auto Verify", callback_data="deposit:binance_auto")])
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")])
    await message.answer("💳 Select payment method:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(DepositState.method, F.data == "deposit:binance_auto")
async def deposit_binance_auto_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    data = await state.get_data()
    requested_amount = float(data.get("amount", 0) or 0)
    min_deposit = float(await get_setting("binance_min_deposit", BINANCE_MIN_DEPOSIT) or BINANCE_MIN_DEPOSIT)
    wallet_address = str(await get_setting("binance_wallet_address", BINANCE_WALLET_ADDRESS) or "").strip()
    network = str(await get_setting("binance_network", BINANCE_NETWORK) or BINANCE_NETWORK).upper().strip()
    coin = str(await get_setting("binance_coin", BINANCE_COIN) or BINANCE_COIN).upper().strip()

    if requested_amount < min_deposit:
        await callback.answer(f"Minimum Binance deposit is {money(min_deposit)}", show_alert=True)
        return
    if not wallet_address:
        await callback.message.answer("❌ Binance wallet address is not configured. Admin must set BINANCE_WALLET_ADDRESS or set it from Settings.")
        await callback.answer()
        return

    unique_amount = make_unique_deposit_amount(requested_amount, callback.from_user.id)
    await state.update_data(
        amount=unique_amount,
        requested_amount=requested_amount,
        binance_coin=coin,
        binance_network=network,
        binance_wallet_address=wallet_address,
    )
    await state.set_state(BinanceVerifyState.txid)
    await callback.message.answer(
        f"🤖 {bold('Binance TXID Auto Verify')}\n\n"
        f"Send exactly: {bold(str(unique_amount) + ' ' + coin)}\n"
        f"Network: {bold(network)}\n"
        f"Wallet address:\n{code(wallet_address)}\n\n"
        f"After payment, send the Binance TxID / transaction ID here.\n\n"
        f"⚠️ Do not reuse TxID. Bot checks duplicate TxIDs automatically.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(BinanceVerifyState.txid)
async def deposit_binance_auto_verify(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    data = await state.get_data()
    amount = float(data.get("amount", 0) or 0)
    requested_amount = float(data.get("requested_amount", amount) or amount)
    txid = (message.text or "").strip()
    coin = str(data.get("binance_coin") or await get_setting("binance_coin", BINANCE_COIN) or BINANCE_COIN).upper().strip()
    network = str(data.get("binance_network") or await get_setting("binance_network", BINANCE_NETWORK) or BINANCE_NETWORK).upper().strip()
    wallet_address = str(data.get("binance_wallet_address") or await get_setting("binance_wallet_address", BINANCE_WALLET_ADDRESS) or "").strip()

    if len(txid) < 6:
        await message.answer("❌ Send a valid Binance TxID.", reply_markup=cancel_inline_keyboard())
        return

    ok, info = await verify_binance_deposit_tx(txid, amount, coin=coin, expected_network=network, expected_address=wallet_address)
    if not ok:
        doc = {"user_id": message.from_user.id, "username": message.from_user.username, "amount": amount, "requested_amount": requested_amount, "method": "Binance TXID Auto Verify", "coin": coin, "network": network, "wallet_address": wallet_address, "txid": txid, "status": "pending", "auto_verified": False, "verify_info": info, "created_at": utcnow(), "updated_at": utcnow()}
        result = await payments.insert_one(doc)
        await state.clear()
        await message.answer(f"⚠️ Auto verify could not find this payment.{chr(10)*2}Reason: {escape(info)}{chr(10)*2}Deposit sent to admin for manual checking.{chr(10)}Deposit ID: {code(short_id(result.inserted_id))}", reply_markup=main_menu(message.from_user.id))
        await notify_admins(
            f"⚠️ {bold('Binance TXID Needs Manual Check')}{chr(10)*2}"
            f"Deposit ID: {code(result.inserted_id)}{chr(10)}"
            f"User: {code(message.from_user.id)} @{escape(message.from_user.username or '')}{chr(10)}"
            f"Amount claimed: {bold(money(amount))}{chr(10)}"
            f"Coin/Network: {bold(coin + ' / ' + network)}{chr(10)}"
            f"Wallet: {code(wallet_address)}{chr(10)}"
            f"TxID: {code(txid)}{chr(10)}"
            f"Auto-check reason: {escape(info)}",
            reply_markup=deposit_admin_keyboard(str(result.inserted_id)),
        )
        await audit("binance_auto_deposit_pending_manual", message.from_user.id, {"payment_id": str(result.inserted_id), "amount": amount, "txid": txid, "reason": info})
        return

    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "amount": amount,
        "requested_amount": requested_amount,
        "method": "Binance TXID Auto Verify",
        "coin": coin,
        "network": network,
        "wallet_address": wallet_address,
        "txid": txid,
        "status": "approved",
        "auto_verified": True,
        "verify_info": info,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "handled_at": utcnow(),
    }
    result = await payments.insert_one(doc)
    await users.update_one({"user_id": message.from_user.id}, {"$inc": {"balance": amount}})
    await state.clear()
    await message.answer(
        f"✅ Binance deposit verified automatically.\n\nAmount added: {bold(money(amount))}\nDeposit ID: {code(short_id(result.inserted_id))}",
        reply_markup=main_menu(message.from_user.id),
    )
    await notify_admins(
        f"🤖 {bold('Binance TXID Auto Deposit Approved')}\n\nUser: {code(message.from_user.id)} @{escape(message.from_user.username or '')}\nAmount: {bold(money(amount))}\nCoin/Network: {bold(coin + ' / ' + network)}\nTxID: {code(txid)}\nInfo: {escape(info)}"
    )
    await audit("binance_auto_deposit_approved", message.from_user.id, {"payment_id": str(result.inserted_id), "amount": amount, "txid": txid})

@router.callback_query(DepositState.method, F.data.startswith("depositmethod:"))
async def deposit_method_button(callback: CallbackQuery, state: FSMContext) -> None:
    mid = callback.data.split(":", 1)[1]
    method = await method_by_id(mid)
    if not method:
        await callback.answer("Payment method not found.", show_alert=True)
        return
    await state.update_data(method=method.get("name"))
    await state.set_state(DepositState.screenshot)
    await callback.message.answer(
        f"✅ Method selected: {bold(method.get('name'))}\n\n"
        f"Payment details:\n{code(method.get('details'))}\n\n"
        f"Now upload payment screenshot/photo.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(DepositState.method)
async def deposit_method(message: Message, state: FSMContext) -> None:
    await message.answer("Please select a payment method using the buttons above.")

@router.message(DepositState.screenshot, F.photo)
async def deposit_screenshot(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id

    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "amount": float(data["amount"]),
        "method": data["method"],
        "screenshot_file_id": photo_file_id,
        "status": "pending",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await payments.insert_one(doc)
    pid = str(result.inserted_id)

    await state.clear()
    await message.answer(
        f"✅ Deposit submitted.\n\n"
        f"Deposit ID: {code(short_id(pid))}\n"
        f"Amount: {bold(money(data['amount']))}\n"
        f"Status: {bold('pending')}\n\n"
        f"Wait for admin approval.",
        reply_markup=main_menu(message.from_user.id),
    )

    admin_caption = (
        f"💰 {bold('Pending Deposit')}\n\n"
        f"ID: {code(short_id(pid))}\n"
        f"User: {code(message.from_user.id)} @{escape(message.from_user.username or '')}\n"
        f"Amount: {bold(money(data['amount']))}\n"
        f"Method: {bold(data['method'])}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                photo=photo_file_id,
                caption=admin_caption,
                reply_markup=deposit_admin_keyboard(pid),
            )
        except Exception as e:
            logger.warning("Could not notify admin %s: %s", admin_id, e)

    await audit("deposit_submitted", message.from_user.id, {"payment_id": pid, "amount": data["amount"]})

@router.message(DepositState.screenshot)
async def deposit_need_photo(message: Message) -> None:
    await message.answer("❌ Please upload a photo screenshot.")

@router.callback_query(F.data.startswith("deposit:approve:"))
async def approve_deposit(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return

    payment_id = callback.data.split(":")[2]
    payment = await payment_by_id(payment_id)
    if not payment:
        await callback.answer("Payment not found.", show_alert=True)
        return

    if payment.get("status") != "pending":
        await callback.answer("Already handled.", show_alert=True)
        return

    amount = float(payment.get("amount", 0))
    await payments.update_one(
        {"_id": payment["_id"]},
        {"$set": {
            "status": "approved",
            "handled_by": callback.from_user.id,
            "handled_at": utcnow(),
            "updated_at": utcnow(),
        }},
    )
    await users.update_one({"user_id": payment["user_id"]}, {"$inc": {"balance": amount}})

    await safe_send_message(
        payment["user_id"],
        f"✅ Deposit approved!\n\nAmount added: {bold(money(amount))}"
    )

    await callback.message.answer(f"✅ Deposit approved: {bold(money(amount))}")
    await callback.answer("Approved")
    await audit("deposit_approved", callback.from_user.id, {"payment_id": payment_id, "user_id": payment["user_id"], "amount": amount})

@router.callback_query(F.data.startswith("deposit:reject:"))
async def reject_deposit(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return

    payment_id = callback.data.split(":")[2]
    payment = await payment_by_id(payment_id)
    if not payment:
        await callback.answer("Payment not found.", show_alert=True)
        return

    if payment.get("status") != "pending":
        await callback.answer("Already handled.", show_alert=True)
        return

    await payments.update_one(
        {"_id": payment["_id"]},
        {"$set": {
            "status": "rejected",
            "handled_by": callback.from_user.id,
            "handled_at": utcnow(),
            "updated_at": utcnow(),
        }},
    )

    await safe_send_message(
        payment["user_id"],
        f"❌ Deposit rejected.\n\nAmount: {bold(money(payment.get('amount', 0)))}\nContact support if this is a mistake."
    )
    await callback.message.answer("❌ Deposit rejected.")
    await callback.answer("Rejected")
    await audit("deposit_rejected", callback.from_user.id, {"payment_id": payment_id, "user_id": payment["user_id"]})


async def support_direct_open(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)
    await state.clear()
    username = clean_username(str(await get_setting("support_username", "support") or "support"))
    await message.answer(
        "📞 <b>Support</b>\n\nClick the button below to contact admin/support directly.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 Open Support Chat", url=f"https://t.me/{username}")]
        ]),
    )

# ==========================================================
# SUPPORT SYSTEM
# ==========================================================

@router.message(F.text == "📞 SUPPORT")
async def support_start(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)

    contacts = await get_setting("support_contacts", [])
    text = f"📞 {bold('Support')}\n\n"
    if contacts:
        text += "Contacts:\n"
        for contact in contacts:
            text += f"• @{escape(clean_username(contact))}\n"
        text += "\n"
    text += "Create a ticket.\nSend ticket subject:"

    await state.set_state(SupportTicketState.subject)
    await message.answer(text)

@router.message(SupportTicketState.subject)
async def support_subject(message: Message, state: FSMContext) -> None:
    subject = truncate((message.text or "").strip(), 100)
    if len(subject) < 3:
        await message.answer("❌ Subject too short. Send again.")
        return
    await state.update_data(subject=subject)
    await state.set_state(SupportTicketState.message)
    await message.answer("Send your support message:")

@router.message(SupportTicketState.message)
async def support_message(message: Message, state: FSMContext) -> None:
    body = truncate((message.text or "").strip(), MAX_SUPPORT_MESSAGE)
    if len(body) < 3:
        await message.answer("❌ Message too short. Send again.")
        return

    data = await state.get_data()
    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "subject": data["subject"],
        "status": "open",
        "messages": [
            {"from": "user", "user_id": message.from_user.id, "text": body, "at": utcnow()}
        ],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await tickets.insert_one(doc)
    tid = str(result.inserted_id)

    await state.clear()
    await message.answer(
        f"✅ Ticket created.\nTicket ID: {code(short_id(tid))}\n\nAdmin will reply soon.",
        reply_markup=main_menu(message.from_user.id),
    )

    for admin_id in ADMIN_IDS:
        await safe_send_message(
            admin_id,
            f"🎟 {bold('New Support Ticket')}\n\n"
            f"Ticket ID: {code(short_id(tid))}\n"
            f"User: {code(message.from_user.id)} @{escape(message.from_user.username or '')}\n"
            f"Subject: {bold(data['subject'])}\n\n"
            f"{escape(body)}",
            reply_markup=ticket_admin_keyboard(tid),
        )

    await audit("ticket_created", message.from_user.id, {"ticket_id": tid})

# ==========================================================
# ADMIN PANEL
# ==========================================================

@router.message(F.text == "👑 ADMIN PANEL")
async def show_admin_panel(message: Message) -> None:
    if not await admin_only_message(message):
        return
    await message.answer("👑 Admin Panel", reply_markup=admin_menu())

@router.callback_query(F.data == "admin:settings")
async def admin_settings(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await callback.message.answer("⚙️ Settings", reply_markup=settings_keyboard())
    await callback.answer()

# ==========================================================
# ADMIN PRODUCT MANAGEMENT
# ==========================================================

@router.callback_query(F.data == "admin:add_product")
async def admin_add_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddProductState.name)
    await callback.message.answer("Send product name:")
    await callback.answer()

@router.message(AddProductState.name)
async def admin_add_product_name(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    name = truncate((message.text or "").strip(), 80)
    if len(name) < 2:
        await message.answer("❌ Product name too short.")
        return

    existing = await products.find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    if existing:
        await message.answer("❌ Product with this name already exists.")
        return

    await state.update_data(name=name)
    await state.set_state(AddProductState.price)
    await message.answer("Send product price in USDT:")

@router.message(AddProductState.price)
async def admin_add_product_price(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    price = parse_float(message.text or "")
    if price is None or price < 0:
        await message.answer("❌ Invalid price.")
        return

    await state.update_data(price=price)
    await state.set_state(AddProductState.description)
    await message.answer("Send product description:")

@router.message(AddProductState.description)
async def admin_add_product_description(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    description = truncate((message.text or "").strip(), MAX_PRODUCT_DESCRIPTION)
    data = await state.get_data()

    doc = {
        "name": data["name"],
        "position": await next_position(products),
        "price": float(data["price"]),
        "description": description,
        "stock": [],
        "active": True,
        "sales": 0,
        "revenue": 0.0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await products.insert_one(doc)
    await state.clear()
    await message.answer(f"✅ Product added.\nID: {code(result.inserted_id)}")
    await audit("product_added", message.from_user.id, {"product_id": str(result.inserted_id), "name": data["name"]})

@router.callback_query(F.data.startswith("admin:products:"))
async def admin_products(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    try:
        page = int(callback.data.split(":")[2])
    except Exception:
        page = 1
    page = max(page, 1)
    limit = 5
    skip = (page - 1) * limit

    cursor = products.find({}).sort("created_at", DESCENDING).skip(skip).limit(limit)
    count = await products.count_documents({})
    found = False
    await callback.message.answer(f"📦 Products — page {page}")
    async for product in cursor:
        found = True
        await callback.message.answer(
            await format_product_display(product),
            reply_markup=admin_product_keyboard(str(product["_id"])),
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:products:{page-1}"))
    if skip + limit < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:products:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))

    if not found:
        await callback.message.answer("No products found.")
    await callback.answer()

@router.callback_query(F.data.startswith("prod:toggle:"))
async def admin_toggle_product(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    new_active = not bool(product.get("active", True))
    await products.update_one({"_id": product["_id"]}, {"$set": {"active": new_active, "updated_at": utcnow()}})
    await callback.message.answer(f"✅ Product status changed to: {bold('Active' if new_active else 'Hidden')}")
    await callback.answer()
    await audit("product_toggle", callback.from_user.id, {"product_id": pid, "active": new_active})

@router.callback_query(F.data.startswith("prod:delete:"))
async def admin_delete_product(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    await products.delete_one({"_id": product["_id"]})
    await callback.message.answer(f"🗑 Product deleted: {bold(product.get('name'))}")
    await callback.answer()
    await audit("product_deleted", callback.from_user.id, {"product_id": pid, "name": product.get("name")})

@router.callback_query(F.data.startswith("prod:edit_price:"))
async def admin_edit_price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    await state.update_data(product_id=pid)
    await state.set_state(EditProductPriceState.price)
    await callback.message.answer(f"Send new price for {bold(product.get('name'))}:")
    await callback.answer()

@router.message(EditProductPriceState.price)
async def admin_edit_price_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    price = parse_float(message.text or "")
    if price is None or price < 0:
        await message.answer("❌ Invalid price.")
        return
    data = await state.get_data()
    product = await product_by_id(data["product_id"])
    if not product:
        await state.clear()
        await message.answer("Product not found.")
        return
    await products.update_one({"_id": product["_id"]}, {"$set": {"price": price, "updated_at": utcnow()}})
    await state.clear()
    await message.answer(f"✅ Price updated to {bold(money(price))}.")
    await audit("product_price_updated", message.from_user.id, {"product_id": data["product_id"], "price": price})

@router.callback_query(F.data.startswith("prod:edit_desc:"))
async def admin_edit_desc_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    await state.update_data(product_id=pid)
    await state.set_state(EditProductDescriptionState.description)
    await callback.message.answer(f"Send new description for {bold(product.get('name'))}:")
    await callback.answer()

@router.message(EditProductDescriptionState.description)
async def admin_edit_desc_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    description = truncate((message.text or "").strip(), MAX_PRODUCT_DESCRIPTION)
    data = await state.get_data()
    product = await product_by_id(data["product_id"])
    if not product:
        await state.clear()
        await message.answer("Product not found.")
        return
    await products.update_one({"_id": product["_id"]}, {"$set": {"description": description, "updated_at": utcnow()}})
    await state.clear()
    await message.answer("✅ Description updated.")
    await audit("product_description_updated", message.from_user.id, {"product_id": data["product_id"]})

@router.callback_query(F.data.startswith("prod:view_stock:"))
async def admin_view_stock(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return

    stock = product.get("stock", [])
    preview = stock[:MAX_STOCK_PREVIEW]
    text = (
        f"📦 Stock for {bold(product.get('name'))}\n\n"
        f"Total stock: {bold(len(stock))}\n\n"
    )
    if preview:
        text += "\n".join(code(x) for x in preview)
        if len(stock) > MAX_STOCK_PREVIEW:
            text += f"\n\nShowing first {MAX_STOCK_PREVIEW} only."
    else:
        text += "No stock."
    await callback.message.answer(text)
    await callback.answer()

@router.callback_query(F.data == "admin:add_stock")
async def admin_add_stock_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    cursor = products.find({}).sort("name", ASCENDING)
    text = "Send product ID to add stock:\n\n"
    async for product in cursor:
        text += f"{code(product['_id'])} — {escape(product.get('name'))}\n"
    await state.set_state(AddStockState.product_id)
    await callback.message.answer(text)
    await callback.answer()

@router.callback_query(F.data.startswith("prod:add_stock:"))
async def admin_add_stock_direct(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":")[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    await state.update_data(product_id=pid)
    await state.set_state(AddStockState.stock)
    await callback.message.answer(
        f"Send stock lines for {bold(product.get('name'))}.\n\n"
        f"One digital item per line. For bigger multi-line accounts, separate each item with a line containing ---"
    )
    await callback.answer()

@router.message(AddStockState.product_id)
async def admin_add_stock_product_id(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    pid = (message.text or "").strip()
    product = await product_by_id(pid)
    if not product:
        await message.answer("❌ Product not found. Send valid product ID.")
        return
    await state.update_data(product_id=pid)
    await state.set_state(AddStockState.stock)
    await message.answer(
        f"Send stock lines for {bold(product.get('name'))}.\n\n"
        f"One digital item per line. For bigger multi-line accounts, separate each item with a line containing ---"
    )

@router.message(AddStockState.stock)
async def admin_add_stock_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    raw_stock = (message.text or "").strip()
    # Supports normal one-item-per-line stock AND multi-line stock blocks.
    # For multi-line accounts, separate each item with a line containing ---
    # Example:
    # email: pass
    # recovery: abc
    # ---
    # second email: pass
    if "---" in raw_stock:
        lines = [truncate(block.strip(), 4000) for block in re.split(r"(?m)^---+$", raw_stock) if block.strip()]
    else:
        lines = []
        for line in raw_stock.splitlines():
            line = line.strip()
            if line:
                lines.append(truncate(line, 4000))

    if not lines:
        await message.answer("❌ No valid lines found.")
        return

    data = await state.get_data()
    product = await product_by_id(data["product_id"])
    if not product:
        await state.clear()
        await message.answer("Product not found.")
        return

    await products.update_one(
        {"_id": product["_id"]},
        {"$push": {"stock": {"$each": lines}}, "$set": {"updated_at": utcnow()}},
    )
    await state.clear()
    await message.answer(f"✅ Added {bold(len(lines))} stock item(s) to {bold(product.get('name'))}.")
    await audit("stock_added", message.from_user.id, {"product_id": data["product_id"], "count": len(lines)})

# ==========================================================
# ADMIN PAYMENT METHODS
# ==========================================================

@router.callback_query(F.data == "admin:methods")
async def admin_methods_menu(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Method", callback_data="method:add")],
        [InlineKeyboardButton(text="📋 List Methods", callback_data="method:list")],
    ])
    await callback.message.answer("💳 Payment Methods", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "method:add")
async def method_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddMethodState.name)
    await callback.message.answer("Send payment method name:")
    await callback.answer()

@router.message(AddMethodState.name)
async def method_add_name(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    name = truncate((message.text or "").strip(), 60)
    if len(name) < 2:
        await message.answer("❌ Name too short.")
        return
    await state.update_data(name=name)
    await state.set_state(AddMethodState.details)
    await message.answer("Send method details/address:")

@router.message(AddMethodState.details)
async def method_add_details(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    details = truncate((message.text or "").strip(), 1000)
    data = await state.get_data()
    count = await payment_methods.count_documents({})
    result = await payment_methods.insert_one({
        "name": data["name"],
        "details": details,
        "position": await next_position(payment_methods),
        "default": count == 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    await state.clear()
    await message.answer("✅ Payment method added.")
    await audit("payment_method_added", message.from_user.id, {"method_id": str(result.inserted_id), "name": data["name"]})

@router.callback_query(F.data == "method:list")
async def method_list(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for method in payment_methods.find({}).sort([("position", ASCENDING), ("default", DESCENDING), ("created_at", ASCENDING)]):
        found = True
        text = (
            f"{'⭐ ' if method.get('default') else ''}{bold(method.get('name'))}\n\n"
            f"{code(method.get('details'))}"
        )
        await callback.message.answer(text, reply_markup=payment_method_keyboard(str(method["_id"])))
    if not found:
        await callback.message.answer("No methods added.")
    await callback.answer()

@router.callback_query(F.data.startswith("method:default:"))
async def method_set_default(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":")[2]
    method = await method_by_id(mid)
    if not method:
        await callback.answer("Method not found.", show_alert=True)
        return
    await payment_methods.update_many({}, {"$set": {"default": False}})
    await payment_methods.update_one({"_id": method["_id"]}, {"$set": {"default": True, "updated_at": utcnow()}})
    await callback.message.answer(f"✅ Default method set: {bold(method.get('name'))}")
    await callback.answer()
    await audit("payment_method_default", callback.from_user.id, {"method_id": mid})

@router.callback_query(F.data.startswith("method:delete:"))
async def method_delete(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":")[2]
    method = await method_by_id(mid)
    if not method:
        await callback.answer("Method not found.", show_alert=True)
        return
    await payment_methods.delete_one({"_id": method["_id"]})
    await callback.message.answer(f"🗑 Deleted method: {bold(method.get('name'))}")
    await callback.answer()
    await audit("payment_method_deleted", callback.from_user.id, {"method_id": mid})

@router.callback_query(F.data.startswith("method:edit:"))
async def method_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":")[2]
    method = await method_by_id(mid)
    if not method:
        await callback.answer("Method not found.", show_alert=True)
        return
    await state.update_data(method_id=mid)
    await state.set_state(EditMethodState.details)
    await callback.message.answer(f"Send new details for {bold(method.get('name'))}:")
    await callback.answer()

@router.message(EditMethodState.details)
async def method_edit_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    details = truncate((message.text or "").strip(), 1000)
    data = await state.get_data()
    method = await method_by_id(data["method_id"])
    if not method:
        await state.clear()
        await message.answer("Method not found.")
        return
    await payment_methods.update_one({"_id": method["_id"]}, {"$set": {"details": details, "updated_at": utcnow()}})
    await state.clear()
    await message.answer("✅ Method updated.")
    await audit("payment_method_updated", message.from_user.id, {"method_id": data["method_id"]})

# ==========================================================
# ADMIN DEPOSITS / ORDERS / USERS
# ==========================================================

@router.callback_query(F.data.startswith("admin:deposits:"))
async def admin_deposits(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    parts = callback.data.split(":")
    status = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    limit = 10
    skip = (page - 1) * limit

    query = {} if status == "all" else {"status": status}
    cursor = payments.find(query).sort("created_at", DESCENDING).skip(skip).limit(limit)
    count = await payments.count_documents(query)

    text = f"💰 Deposits — {status} — page {page}\n\n"
    found = False
    async for payment in cursor:
        found = True
        text += (
            f"• {code(short_id(payment['_id']))} | User {payment.get('user_id')} | "
            f"{money(payment.get('amount', 0))} | {escape(payment.get('method'))}\n"
        )

    await callback.message.answer(text if found else "No deposits found.")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:deposits:{status}:{page-1}"))
    if skip + limit < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:deposits:{status}:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))

    await callback.answer()

@router.callback_query(F.data.startswith("admin:orders:"))
async def admin_orders(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    page = int(callback.data.split(":")[2]) if callback.data.split(":")[2].isdigit() else 1
    limit = 10
    skip = (page - 1) * limit

    cursor = orders.find({}).sort("created_at", DESCENDING).skip(skip).limit(limit)
    count = await orders.count_documents({})
    text = f"🧾 Orders — page {page}\n\n"
    found = False
    async for order in cursor:
        found = True
        text += (
            f"• {code(short_id(order['_id']))} | User {order.get('user_id')} | "
            f"{escape(order.get('product_name'))} x{order.get('quantity', order.get('qty', 0))} | "
            f"{money(order.get('total', 0))} | {escape(order.get('status'))}\n"
        )

    await callback.message.answer(text if found else "No orders found.")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:orders:{page-1}"))
    if skip + limit < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:orders:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))

    await callback.answer()

@router.callback_query(F.data.startswith("admin:users:"))
async def admin_users(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    page = int(callback.data.split(":")[2]) if callback.data.split(":")[2].isdigit() else 1
    limit = 10
    skip = (page - 1) * limit

    cursor = users.find({}).sort([("referral_count", DESCENDING), ("total_orders", DESCENDING), ("created_at", DESCENDING)]).skip(skip).limit(limit)
    count = await users.count_documents({})
    text = f"👥 Users — page {page}\n\n"
    found = False
    async for user in cursor:
        found = True
        text += (
            f"• {code(user.get('user_id'))} @{escape(user.get('username') or 'None')} | "
            f"Bal {money(user.get('balance', 0))} | Pts {user.get('points', 0)} | "
            f"{'BANNED' if user.get('banned') else 'OK'}\n"
        )

    await callback.message.answer(text if found else "No users found.")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:users:{page-1}"))
    if skip + limit < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:users:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))

    await callback.answer()

@router.callback_query(F.data.in_({"admin:block_user", "admin:unblock_user"}))
async def admin_block_user_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mode = "block" if callback.data == "admin:block_user" else "unblock"
    await state.update_data(mode=mode)
    await state.set_state(BlockUserState.query)
    await callback.message.answer("Send user ID or username to " + ("block:" if mode == "block" else "unblock:"))
    await callback.answer()

@router.message(BlockUserState.query)
async def admin_block_user_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    mode = data.get("mode", "block")
    q = (message.text or "").strip().replace("@", "")
    if q.isdigit():
        query = {"$or": [{"user_id": int(q)}, {"username": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}]}
    else:
        query = {"username": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}
    user = await users.find_one(query)
    await state.clear()
    if not user:
        await message.answer("❌ User not found.")
        return
    uid = int(user["user_id"])
    if mode == "block":
        await users.update_one({"user_id": uid}, {"$set": {"banned": True, "ban_reason": "Blocked by admin", "blocked_at": utcnow()}})
        await message.answer(f"🚫 User blocked: {code(uid)}")
        await safe_send_message(uid, "⛔ You have been blocked from using this bot.")
        await audit("user_blocked", message.from_user.id, {"user_id": uid})
    else:
        await users.update_one({"user_id": uid}, {"$set": {"banned": False, "ban_reason": None}, "$unset": {"blocked_at": ""}})
        await message.answer(f"✅ User unblocked: {code(uid)}")
        await safe_send_message(uid, "✅ You have been unblocked. You can use the bot again.")
        await audit("user_unblocked", message.from_user.id, {"user_id": uid})

@router.callback_query(F.data == "admin:user_search")
async def user_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(UserSearchState.query)
    await callback.message.answer("Send user ID or username:")
    await callback.answer()

@router.message(UserSearchState.query)
async def user_search_do(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    q = (message.text or "").strip().replace("@", "")
    query: Dict[str, Any]
    if q.isdigit():
        query = {"$or": [{"user_id": int(q)}, {"username": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}]}
    else:
        query = {"username": {"$regex": f"^{re.escape(q)}$", "$options": "i"}}

    user = await users.find_one(query)
    await state.clear()
    if not user:
        await message.answer("❌ User not found.")
        return

    uid = user["user_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Ban", callback_data=f"user:ban:{uid}"),
            InlineKeyboardButton(text="Unban", callback_data=f"user:unban:{uid}"),
        ],
        [
            InlineKeyboardButton(text="Add Points", callback_data=f"user:add_points:{uid}"),
            InlineKeyboardButton(text="Remove Points", callback_data=f"user:remove_points:{uid}"),
        ],
        [
            InlineKeyboardButton(text="Add Balance", callback_data=f"user:add_balance:{uid}"),
            InlineKeyboardButton(text="Remove Balance", callback_data=f"user:remove_balance:{uid}"),
        ],
        [
            InlineKeyboardButton(text="View Orders", callback_data=f"user:view_orders:{uid}"),
        ],
    ])
    await message.answer(format_user(user), reply_markup=keyboard)

@router.callback_query(F.data.startswith("user:ban:"))
async def admin_ban_user(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[2])
    await users.update_one({"user_id": uid}, {"$set": {"banned": True, "ban_reason": "Admin ban"}})
    await callback.message.answer("✅ User banned.")
    await safe_send_message(uid, "⛔ You have been banned.")
    await callback.answer()
    await audit("user_banned", callback.from_user.id, {"user_id": uid})

@router.callback_query(F.data.startswith("user:unban:"))
async def admin_unban_user(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[2])
    await users.update_one({"user_id": uid}, {"$set": {"banned": False, "ban_reason": None}})
    await callback.message.answer("✅ User unbanned.")
    await safe_send_message(uid, "✅ You have been unbanned.")
    await callback.answer()
    await audit("user_unbanned", callback.from_user.id, {"user_id": uid})

@router.callback_query(F.data.startswith("user:add_points:") | F.data.startswith("user:remove_points:"))
async def admin_points_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    parts = callback.data.split(":")
    mode = parts[1]
    uid = int(parts[2])
    await state.update_data(user_id=uid, mode=mode)
    await state.set_state(PointsState.amount)
    await callback.message.answer("Send points amount:")
    await callback.answer()

@router.message(PointsState.amount)
async def admin_points_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    amount = parse_int(message.text or "")
    if amount is None or amount <= 0:
        await message.answer("❌ Invalid amount.")
        return
    data = await state.get_data()
    inc = amount if data["mode"] == "add_points" else -amount
    await users.update_one({"user_id": data["user_id"]}, {"$inc": {"points": inc}})
    await state.clear()
    await message.answer("✅ Points updated.")
    await audit("points_updated", message.from_user.id, {"user_id": data["user_id"], "inc": inc})

@router.callback_query(F.data.startswith("user:add_balance:") | F.data.startswith("user:remove_balance:"))
async def admin_balance_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    parts = callback.data.split(":")
    mode = parts[1]
    uid = int(parts[2])
    await state.update_data(user_id=uid, mode=mode)
    await state.set_state(BalanceState.amount)
    await callback.message.answer("Send balance amount:")
    await callback.answer()

@router.message(BalanceState.amount)
async def admin_balance_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    amount = parse_float(message.text or "")
    if amount is None or amount <= 0:
        await message.answer("❌ Invalid amount.")
        return
    data = await state.get_data()
    inc = amount if data["mode"] == "add_balance" else -amount
    await users.update_one({"user_id": data["user_id"]}, {"$inc": {"balance": inc}})
    await state.clear()
    await message.answer("✅ Balance updated.")
    await audit("balance_updated", message.from_user.id, {"user_id": data["user_id"], "inc": inc})

@router.callback_query(F.data.startswith("user:view_orders:"))
async def admin_user_orders(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[2])
    cursor = orders.find({"user_id": uid}).sort("created_at", DESCENDING).limit(20)
    text = f"🧾 Orders for {code(uid)}\n\n"
    found = False
    async for order in cursor:
        found = True
        text += (
            f"• {code(short_id(order['_id']))} | {escape(order.get('product_name'))} "
            f"x{order.get('quantity', order.get('qty', 0))} | {money(order.get('total', 0))} | {escape(order.get('status'))}\n"
        )
    await callback.message.answer(text if found else "No orders.")
    await callback.answer()

# ==========================================================
# ADMIN TICKETS
# ==========================================================

@router.callback_query(F.data.startswith("admin:tickets:"))
async def admin_tickets(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    parts = callback.data.split(":")
    status = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    limit = 5
    skip = (page - 1) * limit
    query = {} if status == "all" else {"status": status}

    cursor = tickets.find(query).sort("updated_at", DESCENDING).skip(skip).limit(limit)
    count = await tickets.count_documents(query)
    found = False
    await callback.message.answer(f"🎟 Tickets — {status} — page {page}")
    async for ticket in cursor:
        found = True
        await callback.message.answer(format_ticket(ticket), reply_markup=ticket_admin_keyboard(str(ticket["_id"])))

    if not found:
        await callback.message.answer("No tickets found.")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:tickets:{status}:{page-1}"))
    if skip + limit < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:tickets:{status}:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))
    await callback.answer()

@router.callback_query(F.data.startswith("ticket:reply:"))
async def ticket_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    tid = callback.data.split(":")[2]
    ticket = await ticket_by_id(tid)
    if not ticket:
        await callback.answer("Ticket not found.", show_alert=True)
        return
    await state.update_data(ticket_id=tid)
    await state.set_state(AdminTicketReplyState.message)
    await callback.message.answer(f"Send reply for ticket {code(short_id(tid))}:")
    await callback.answer()

@router.message(AdminTicketReplyState.message)
async def ticket_reply_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    body = truncate((message.text or "").strip(), MAX_SUPPORT_MESSAGE)
    data = await state.get_data()
    ticket = await ticket_by_id(data["ticket_id"])
    if not ticket:
        await state.clear()
        await message.answer("Ticket not found.")
        return

    await tickets.update_one(
        {"_id": ticket["_id"]},
        {
            "$push": {"messages": {"from": "admin", "admin_id": message.from_user.id, "text": body, "at": utcnow()}},
            "$set": {"updated_at": utcnow()},
        },
    )
    await safe_send_message(
        ticket["user_id"],
        f"📞 Support reply\n\nTicket: {bold(ticket.get('subject'))}\n\n{escape(body)}"
    )
    await state.clear()
    await message.answer("✅ Reply sent.")
    await audit("ticket_replied", message.from_user.id, {"ticket_id": data["ticket_id"]})

@router.callback_query(F.data.startswith("ticket:close:"))
async def ticket_close(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    tid = callback.data.split(":")[2]
    ticket = await ticket_by_id(tid)
    if not ticket:
        await callback.answer("Ticket not found.", show_alert=True)
        return
    await tickets.update_one({"_id": ticket["_id"]}, {"$set": {"status": "closed", "updated_at": utcnow()}})
    await safe_send_message(ticket["user_id"], f"✅ Your support ticket has been closed.\nTicket: {bold(ticket.get('subject'))}")
    await callback.message.answer("✅ Ticket closed.")
    await callback.answer()
    await audit("ticket_closed", callback.from_user.id, {"ticket_id": tid})

# ==========================================================
# ADMIN BROADCAST / SETTINGS / STATS
# ==========================================================

@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(BroadcastState.message)
    await callback.message.answer("Send broadcast message:")
    await callback.answer()

@router.message(BroadcastState.message)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    text = truncate((message.text or "").strip(), MAX_BROADCAST_TEXT)
    sent = 0
    failed = 0
    await message.answer("📢 Broadcasting started...")

    async for user in users.find({"banned": {"$ne": True}}, {"user_id": 1}):
        ok = await safe_send_message(user["user_id"], f"📢 {bold('Broadcast')}\n\n{escape(text)}")
        if ok:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await state.clear()
    await message.answer(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")
    await audit("broadcast_sent", message.from_user.id, {"sent": sent, "failed": failed})

@router.callback_query(F.data.startswith("settings:set:"))
async def setting_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    key = callback.data.split(":")[2]

    if key == "canva_delivery_mode":
        await state.clear()
        current_mode = str(await get_setting("canva_delivery_mode", "gmail") or "gmail").lower().strip()
        await callback.message.answer(
            f"🎁 {bold('Choose Canva Delivery Mode')}\n\n"
            f"Current mode: {bold('Invite Link' if current_mode == 'link' else 'User Sends Gmail')}\n\n"
            f"🔗 {bold('Send Invite Link')}: bot sends saved Canva link to user and deletes it after 30 minutes.\n"
            f"📧 {bold('User Sends Gmail')}: user sends Gmail and admin receives the request.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Send Invite Link", callback_data="settings:canvamode:link")],
                [InlineKeyboardButton(text="📧 User Sends Gmail", callback_data="settings:canvamode:gmail")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")],
            ]),
        )
        await callback.answer()
        return

    await state.update_data(key=key)
    await state.set_state(SettingState.value)

    prompts = {
        "support_username": "📞 Send support/admin Telegram username without @:\nExample: zedox5",
        "support_contacts": "📞 Send support usernames separated by comma:\nExample: zedox5,backupadmin",
        "referral_reward_points": "👥 Send referral reward points number:\nExample: 5",
        "canva_price_usdt": "🎁 Send Canva USDT price:\nExample: 1",
        "canva_points_required": "🎁 Send Canva points requirement:\nExample: 10",
        "canva_first_free_enabled": "🎁 Send ON to make first Canva account free, or OFF to require payment/points for everyone:",
        "canva_invite_link": "🔗 Send Canva invite link to send users automatically:\nExample: https://www.canva.com/brand/join?token=xxxxx\n\nThis link will be shown to users for 30 minutes, then the bot will delete that message.",
        "start_message": "🚀 Send the new /start welcome message:",
        "force_join_channels": "🔒 Send required CHANNEL usernames separated by comma:\nExample: @channel1,@channel2\nSend off/none to disable channels.",
        "force_join_groups": "👥 Send required GROUP usernames separated by comma:\nExample: @zedoxchat,@mygroup\n\nImportant: bot must be admin/member in the group and the group should have a public @username so Telegram can verify join. Send off/none to disable groups.",
        "force_join_scope": "🔒 Send force-join scope: none, all, or free_canva",
        "bulk_discount_rules": "📦 Send bulk discounts like:\n2=5,5=10,10=15\n\nThis means: buy 2 get 5% off, buy 5 get 10% off, buy 10 get 15% off.",
        "points_per_usdt": "💱 Send exchange rate like: 100:1\nThis means: 100 points = 1 USDT.\nYou can also send 500:5, 1000:10, etc.",
        "binance_auto_verify_enabled": "💳 Send ON to enable Binance TXID auto verification, or OFF to disable it:",
        "binance_wallet_address": "💳 Send your Binance deposit wallet/address that users pay to:",
        "binance_network": "💳 Send Binance deposit network name:\nExample: TRC20, BEP20, ERC20",
        "binance_min_deposit": "💳 Send minimum Binance auto-deposit amount:\nExample: 1",
    }
    await callback.message.answer(prompts.get(key, f"Send value for {key}:"))
    await callback.answer()

@router.callback_query(F.data.startswith("settings:canvamode:"))
async def setting_canva_mode_save(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mode = callback.data.split(":", 2)[2].strip().lower()
    if mode not in {"link", "gmail"}:
        await callback.answer("Invalid mode.", show_alert=True)
        return
    await set_setting("canva_delivery_mode", mode)
    await state.clear()
    await callback.message.answer(
        f"✅ Canva delivery mode updated: {bold('Send Invite Link' if mode == 'link' else 'User Sends Gmail')}"
    )
    await callback.answer("Saved")
    await audit("setting_updated", callback.from_user.id, {"key": "canva_delivery_mode", "value": mode})

@router.message(SettingState.value)
async def setting_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    key = data["key"]
    raw = (message.text or "").strip()

    try:
        if key == "support_username":
            username = clean_username(raw)
            if not is_valid_username(username):
                await message.answer("❌ Invalid Telegram username.")
                return
            value = username
        elif key == "support_contacts":
            value = [clean_username(x) for x in raw.split(",") if clean_username(x)]
            value = [x for x in value if is_valid_username(x)]
            if not value:
                await message.answer("❌ No valid usernames.")
                return
        elif key in {"referral_reward_points", "canva_points_required"}:
            value = int(raw)
            if value < 0:
                raise ValueError()
        elif key == "points_per_usdt":
            text = raw.replace(" ", "")
            if ":" in text:
                parts = text.split(":")
            elif "=" in text:
                parts = text.split("=")
            else:
                parts = [text, "1"]

            if len(parts) != 2:
                await message.answer("❌ Invalid value.\nExample: 100:1")
                return

            points = parse_int(parts[0].strip())
            usdt = parse_float(parts[1].strip())

            if points is None or usdt is None or points <= 0 or usdt <= 0:
                await message.answer("❌ Invalid value.\nExample: 100:1")
                return

            value = max(1, int(round(points / usdt)))
        elif key == "canva_first_free_enabled":
            value = raw.lower().strip() in {"on", "yes", "true", "1", "enable", "enabled"}
        elif key == "canva_delivery_mode":
            value = raw.lower().strip()
            if value not in {"link", "gmail"}:
                await message.answer("❌ Canva mode must be: link or gmail")
                return
        elif key == "canva_invite_link":
            value = raw.strip()
            if not (value.startswith("http://") or value.startswith("https://")):
                await message.answer("❌ Send a valid Canva invite link starting with http:// or https://")
                return
        elif key == "binance_auto_verify_enabled":
            value = raw.lower().strip() in {"on", "yes", "true", "1", "enable", "enabled"}
        elif key == "binance_min_deposit":
            value = float(raw)
            if value < 0:
                raise ValueError()
        elif key == "canva_price_usdt":
            value = float(raw)
            if value < 0:
                raise ValueError()
        elif key == "force_join_channels":
            if raw.lower() in {"", "off", "none", "disable", "disabled"}:
                value = []
            else:
                value = _normalize_force_join_entries(raw)
        elif key == "force_join_groups":
            if raw.lower() in {"", "off", "none", "disable", "disabled"}:
                value = []
            else:
                value = _normalize_force_join_entries(raw)
        elif key == "force_join_scope":
            value = raw.lower().strip()
            if value not in {"none", "all", "free_canva"}:
                await message.answer("❌ Scope must be: none, all, or free_canva")
                return
        elif key == "bulk_discount_rules":
            value = normalize_bulk_discount_rules(raw)
            if not value:
                await message.answer("❌ Invalid format. Example: 2=5,5=10,10=15")
                return
        elif key == "start_message":
            value = truncate(raw, 3500) or f"✅ Welcome to {APP_NAME}\n\nChoose an option below:"
        else:
            value = raw
    except Exception:
        await message.answer("❌ Invalid value.")
        return

    await set_setting(key, value)
    await state.clear()
    if key == "points_per_usdt":
        await message.answer(
            f"✅ Exchange rate updated.\n\n"
            f"{bold(value)} points = {bold(money(1))}"
        )
    else:
        await message.answer(f"✅ Setting saved.\n{code(key)} = {code(value)}")
    await audit("setting_updated", message.from_user.id, {"key": key, "value": value})


async def send_top_users(callback: CallbackQuery, sort_field: str, title: str, value_label: str) -> None:
    if not await admin_only_callback(callback):
        return
    text = f"🏆 {bold(title)}\n\n"
    found = False
    async for user in users.find({}).sort(sort_field, DESCENDING).limit(20):
        found = True
        value = user.get(sort_field, 0)
        if sort_field == "balance" or sort_field == "total_spent":
            value_text = money(value)
        else:
            value_text = str(value)
        text += f"• {code(user.get('user_id'))} @{escape(user.get('username') or 'None')} — {value_label}: {bold(value_text)} — refs: {bold(user.get('referral_count', 0))}\n"
    await callback.message.answer(text if found else "No users found.")
    await callback.answer()

@router.callback_query(F.data == "admin:top_referrals")
async def admin_top_referrals(callback: CallbackQuery) -> None:
    await send_top_users(callback, "referral_count", "Top Referral Users", "referrals")

@router.callback_query(F.data == "admin:top_buyers")
async def admin_top_buyers(callback: CallbackQuery) -> None:
    await send_top_users(callback, "total_orders", "Top Buyers", "orders")

@router.callback_query(F.data == "admin:top_balances")
async def admin_top_balances(callback: CallbackQuery) -> None:
    await send_top_users(callback, "balance", "Top Money in Account", "balance")

@router.callback_query(F.data == "admin:stats")
async def admin_statistics(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return

    day_ago = utcnow() - timedelta(days=1)
    week_ago = utcnow() - timedelta(days=7)
    month_ago = utcnow() - timedelta(days=30)

    total_users = await users.count_documents({})
    daily_users = await users.count_documents({"created_at": {"$gte": day_ago}})
    weekly_users = await users.count_documents({"created_at": {"$gte": week_ago}})
    monthly_users = await users.count_documents({"created_at": {"$gte": month_ago}})

    total_orders = await orders.count_documents({})
    pending_payments = await payments.count_documents({"status": "pending"})
    total_products = await products.count_documents({})
    active_products = await products.count_documents({"active": {"$ne": False}})
    open_tickets = await tickets.count_documents({"status": "open"})

    revenue = 0.0
    pipeline = [{"$group": {"_id": None, "revenue": {"$sum": "$total"}}}]
    async for row in orders.aggregate(pipeline):
        revenue = float(row.get("revenue", 0) or 0)

    top_products = ""
    async for product in products.find({}).sort("sales", DESCENDING).limit(5):
        top_products += f"• {escape(product.get('name'))}: {product.get('sales', 0)} sales — {money(product.get('revenue', 0))}\n"

    await callback.message.answer(
        f"📊 {bold('Statistics')}\n\n"
        f"Total users: {bold(total_users)}\n"
        f"Daily users: {bold(daily_users)}\n"
        f"Weekly users: {bold(weekly_users)}\n"
        f"Monthly users: {bold(monthly_users)}\n\n"
        f"Products: {bold(active_products)}/{bold(total_products)} active\n"
        f"Orders: {bold(total_orders)}\n"
        f"Revenue: {bold(money(revenue))}\n"
        f"Pending payments: {bold(pending_payments)}\n"
        f"Open tickets: {bold(open_tickets)}\n\n"
        f"Top products:\n{top_products or 'None'}"
    )
    await callback.answer()


# ==========================================================
# FREE PRODUCTS USER + ADMIN
# ==========================================================

def free_product_keyboard(pid: str, stock_count: int) -> InlineKeyboardMarkup:
    rows = []
    if stock_count > 0:
        rows.append([InlineKeyboardButton(text="Buy with referral points", callback_data=f"fp:buy_points:{pid}")])
        rows.append([InlineKeyboardButton(text="Buy with USDT balance", callback_data=f"fp:buy_usdt:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(F.text == "🎁 FREE ITEMS")
async def show_free_products(message: Message) -> None:
    if not await security_check_event(message):
        return
    if await send_force_join_message(message, "all"):
        return
    await ensure_user(message)
    rows = [
        [InlineKeyboardButton(text="🎁 Free Canva", callback_data="freeitems:free_canva")]
    ]
    async for product in free_products.find({"active": {"$ne": False}}).sort("created_at", DESCENDING):
        stock_count = len(product.get("stock", []))
        rows.append([InlineKeyboardButton(text=f"{product.get('name')} ({stock_count})", callback_data=f"fp:view:{product['_id']}")])
    await message.answer(
        f"🎁 {bold('Free Items')}\n\nChoose Free Canva or select another free item below. Other free items still work with referral points or USDT balance.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )

@router.callback_query(F.data.startswith("fp:view:"))
async def view_free_product(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    product = await free_products.find_one({"_id": oid}) if oid else None
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    stock_count = len(product.get("stock", []))
    await callback.message.answer(
        f"🎁 {bold(product.get('name'))}\n\n"
        f"USDT price: {bold(money(product.get('price', 0)))}\n"
        f"Referral points: {bold(product.get('points', 0))}\n"
        f"Stock: {bold(stock_count)}\n\n"
        f"{escape(product.get('description', ''))}",
        reply_markup=free_product_keyboard(pid, stock_count),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("fp:buy_points:") | F.data.startswith("fp:buy_usdt:"))
async def buy_free_product(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    parts = callback.data.split(":")
    mode, pid = parts[1], parts[2]
    oid = object_id(pid)
    product = await free_products.find_one({"_id": oid}) if oid else None
    user = await get_user(callback.from_user.id)
    if not product or not user or not product.get("stock"):
        await callback.answer("Unavailable.", show_alert=True)
        return
    item = product["stock"][0]
    total = 0.0
    paid_with = ""
    if mode == "buy_points":
        need = int(product.get("points", 0))
        if int(user.get("points", 0)) < need:
            await callback.answer("Not enough referral points.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"points": -need, "total_orders": 1}})
        paid_with = f"{need} points"
    else:
        total = float(product.get("price", 0))
        if float(user.get("balance", 0)) < total:
            await callback.answer("Insufficient balance.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"balance": -total, "total_spent": total, "total_orders": 1}})
        paid_with = money(total)
    await free_products.update_one({"_id": product["_id"]}, {"$pop": {"stock": -1}, "$inc": {"sales": 1, "revenue": total}})
    order_doc = {"user_id": callback.from_user.id, "username": callback.from_user.username, "product_id": pid, "product_name": f"FREE: {product.get('name')}", "quantity": 1, "subtotal": total, "discount": 0, "total": total, "paid_with": paid_with, "delivered": [item], "status": "completed", "created_at": utcnow()}
    result = await orders.insert_one(order_doc)
    order_doc["_id"] = result.inserted_id
    await notify_admin_order(order_doc, user)
    await callback.message.answer(f"✅ Delivered!\nOrder ID: {code(short_id(result.inserted_id))}\nPaid with: {bold(paid_with)}\n\nYour data:\n{code(item)}", reply_markup=await contact_keyboard())
    await callback.answer("Delivered!")

@router.callback_query(F.data == "admin:free_products")
async def admin_free_products(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await callback.message.answer("🎁 Free Items Admin", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Free Product", callback_data="fpadmin:add")],
        [InlineKeyboardButton(text="📦 List / Delete Free Items", callback_data="fpadmin:list")],
        [InlineKeyboardButton(text="➕ Add Free Stock", callback_data="fpadmin:addstock")],
    ]))
    await callback.answer()

@router.callback_query(F.data == "fpadmin:add")
async def add_free_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddFreeProductState.name)
    await callback.message.answer("Send free item name:")
    await callback.answer()

@router.message(AddFreeProductState.name)
async def add_free_product_name(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    await state.update_data(name=truncate((message.text or '').strip(), 80))
    await state.set_state(AddFreeProductState.price)
    await message.answer("Send USDT price for this free item:")

@router.message(AddFreeProductState.price)
async def add_free_product_price(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    price = parse_float(message.text or '')
    if price is None or price < 0:
        await message.answer("Invalid price.")
        return
    await state.update_data(price=price)
    await state.set_state(AddFreeProductState.points)
    await message.answer("Send referral points required:")

@router.message(AddFreeProductState.points)
async def add_free_product_points(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    points = parse_int(message.text or '')
    if points is None or points < 0:
        await message.answer("Invalid points.")
        return
    await state.update_data(points=points)
    await state.set_state(AddFreeProductState.description)
    await message.answer("Send description:")

@router.message(AddFreeProductState.description)
async def add_free_product_desc(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    result = await free_products.insert_one({"name": data["name"], "price": data["price"], "points": data["points"], "description": truncate(message.text or '', MAX_PRODUCT_DESCRIPTION), "stock": [], "active": True, "success_rate": 0, "patch_note": "", "sales": 0, "revenue": 0.0, "created_at": utcnow(), "updated_at": utcnow()})
    await state.clear()
    await message.answer(f"✅ Free item added. ID: {code(result.inserted_id)}")

@router.callback_query(F.data == "fpadmin:list")
async def list_free_products_admin(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for p in free_products.find({}).sort("created_at", DESCENDING):
        found = True
        await callback.message.answer(
            f"🎁 {bold(p.get('name'))}\n"
            f"ID: {code(p['_id'])}\n"
            f"USDT: {bold(money(p.get('price', 0)))}\n"
            f"Points: {bold(p.get('points', 0))}\n"
            f"Stock: {bold(len(p.get('stock', [])))}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Delete Free Product", callback_data=f"fpadmin:delete:{p['_id']}")],
            ]),
        )
    if not found:
        await callback.message.answer("No free items.")
    await callback.answer()

@router.callback_query(F.data.startswith("fpadmin:delete:"))
async def delete_free_product_admin(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    product = await free_products.find_one({"_id": oid}) if oid else None
    if not product:
        await callback.answer("Free item not found.", show_alert=True)
        return
    await free_products.delete_one({"_id": product["_id"]})
    await callback.message.answer(f"🗑 Deleted free item: {bold(product.get('name'))}")
    await callback.answer("Deleted")
    await audit("free_product_deleted", callback.from_user.id, {"product_id": pid, "name": product.get("name")})

@router.callback_query(F.data == "fpadmin:addstock")
async def add_free_stock_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    text = "Send free item ID:\n\n"
    async for p in free_products.find({}).sort("name", ASCENDING):
        text += f"{code(p['_id'])} — {escape(p.get('name'))}\n"
    await state.set_state(AddFreeStockState.product_id)
    await callback.message.answer(text)
    await callback.answer()

@router.message(AddFreeStockState.product_id)
async def add_free_stock_pid(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    pid = (message.text or '').strip()
    oid = object_id(pid)
    product = await free_products.find_one({"_id": oid}) if oid else None
    if not product:
        await message.answer("Free item not found.")
        return
    await state.update_data(product_id=pid)
    await state.set_state(AddFreeStockState.stock)
    await message.answer("Send multiple free item stock items at once. Put one item per line:")

@router.message(AddFreeStockState.stock)
async def add_free_stock_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    lines = [x.strip() for x in (message.text or '').splitlines() if x.strip()]
    oid = object_id(data["product_id"])
    await free_products.update_one({"_id": oid}, {"$push": {"stock": {"$each": lines}}, "$set": {"updated_at": utcnow()}})
    await state.clear()
    await message.answer(f"✅ Added {len(lines)} free item stock items.")

# ==========================================================
# PAID CONTENT METHODS ADMIN
# ==========================================================

@router.callback_query(F.data == "admin:content_methods")
async def admin_content_methods(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await callback.message.answer("🎬 Paid Methods Admin", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Paid Method", callback_data="cmadmin:add")],
        [InlineKeyboardButton(text="📋 List Paid Methods", callback_data="cmadmin:list")],
    ]))
    await callback.answer()

@router.callback_query(F.data == "cmadmin:add")
async def add_content_method_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddContentMethodState.name)
    await callback.message.answer("Send method name:")
    await callback.answer()

@router.message(AddContentMethodState.name)
async def add_content_method_name(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    await state.update_data(name=truncate((message.text or '').strip(), 80))
    await state.set_state(AddContentMethodState.price)
    await message.answer("Send method price in USDT:")

@router.message(AddContentMethodState.price)
async def add_content_method_price(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    price = parse_float(message.text or '')
    if price is None or price < 0:
        await message.answer("Invalid price.")
        return
    await state.update_data(price=price)
    await state.set_state(AddContentMethodState.description)
    await message.answer("Send public description:")

@router.message(AddContentMethodState.description)
async def add_content_method_desc(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    await state.update_data(description=truncate(message.text or '', MAX_PRODUCT_DESCRIPTION), delivery_items=[])
    await state.set_state(AddContentMethodState.delivery)
    await message.answer(
        "Send method delivery content now.\n\nYou can send multiple texts, files/documents, and videos.\nAfter each item, bot will ask for more. Click ✅ Done when finished."
    )

def method_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Send More", callback_data="cmadmin:more")],
        [InlineKeyboardButton(text="✅ Done", callback_data="cmadmin:done")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")],
    ])

@router.message(AddContentMethodState.delivery, F.video)
async def add_content_method_video(message: Message, state: FSMContext) -> None:
    await append_content_method_delivery(message, state, {"type": "video", "value": message.video.file_id, "caption": message.caption or "🎬 Method video"})

@router.message(AddContentMethodState.delivery, F.document)
async def add_content_method_document(message: Message, state: FSMContext) -> None:
    await append_content_method_delivery(message, state, {"type": "document", "value": message.document.file_id, "caption": message.caption or f"📁 {message.document.file_name or 'Method file'}", "file_name": message.document.file_name})

@router.message(AddContentMethodState.delivery)
async def add_content_method_text(message: Message, state: FSMContext) -> None:
    text = message.text or message.caption or ""
    if not text.strip():
        await message.answer("Send text, document/file, video, or click Done after adding at least one item.")
        return
    await append_content_method_delivery(message, state, {"type": "text", "value": text})

async def append_content_method_delivery(message: Message, state: FSMContext, item: Dict[str, Any]) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    items = list(data.get("delivery_items", []))
    items.append(item)
    await state.update_data(delivery_items=items)
    await message.answer(f"✅ Added item #{len(items)} ({item.get('type')}).\n\nSend more files/videos/text or click Done.", reply_markup=method_done_keyboard())

@router.callback_query(F.data == "cmadmin:more")
async def content_method_more(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddContentMethodState.delivery)
    await callback.message.answer("Send next text, document/file, or video.")
    await callback.answer()

@router.callback_query(F.data == "cmadmin:done")
async def finish_content_method_delivery(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    data = await state.get_data()
    items = list(data.get("delivery_items", []))
    if not items:
        await callback.answer("Add at least one text/file/video first.", show_alert=True)
        return
    result = await content_methods.insert_one({"name": data["name"], "price": float(data["price"]), "description": data["description"], "delivery_type": "multi", "delivery": "", "delivery_items": items, "position": await next_position(content_methods), "active": True, "success_rate": 0, "patch_note": "", "sales": 0, "revenue": 0.0, "created_at": utcnow(), "updated_at": utcnow()})
    await state.clear()
    await callback.message.answer(f"✅ Paid method added with {len(items)} delivery item(s).\nID: {code(result.inserted_id)}")
    await callback.answer("Done")

@router.callback_query(F.data == "cmadmin:list")
async def list_content_methods_admin(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for m in content_methods.find({}).sort([("position", ASCENDING), ("created_at", DESCENDING)]):
        found = True
        await callback.message.answer(
            f"🎬 {bold(m.get('name'))}\n"
            f"ID: {code(m['_id'])}\n"
            f"Price: {bold(money(m.get('price', 0)))}\n"
            f"Type: {bold(m.get('delivery_type', 'text'))}\n"
            f"Active: {bold(m.get('active', True))}\n"
            f"Success: {bold(str(m.get('success_rate', 0)) + '%')}\n"
            f"Patch: {escape(m.get('patch_note') or 'No patch yet')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧩 Patch Method", callback_data=f"cmadmin:patch:{m['_id']}")],
                [InlineKeyboardButton(text="📌 Set Position", callback_data=f"cmadmin:position:{m['_id']}")],
                [InlineKeyboardButton(text="🟢/🔴 Toggle", callback_data=f"cmadmin:toggle:{m['_id']}")],
                [InlineKeyboardButton(text="🗑 Delete", callback_data=f"cmadmin:delete:{m['_id']}")],
            ]),
        )
    if not found:
        await callback.message.answer("No paid methods.")
    await callback.answer()


@router.callback_query(F.data.startswith("cmadmin:patch:"))
async def content_method_patch_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    m = await content_methods.find_one({"_id": oid}) if oid else None
    if not m:
        await callback.answer("Not found.", show_alert=True)
        return
    await state.update_data(method_id=mid)
    await state.set_state(PatchContentMethodState.patch)
    await callback.message.answer("Send patch/update note for users to see:", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(PatchContentMethodState.patch)
async def content_method_patch_note(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    patch = truncate((message.text or "").strip(), 1000)
    if len(patch) < 2:
        await message.answer("❌ Patch note too short.", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(patch_note=patch)
    await state.set_state(PatchContentMethodState.success_rate)
    await message.answer("Send success rate percentage, example: 85", reply_markup=cancel_inline_keyboard())

@router.message(PatchContentMethodState.success_rate)
async def content_method_patch_success(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    rate = parse_int(message.text or "")
    if rate is None or rate < 0 or rate > 100:
        await message.answer("❌ Send a number from 0 to 100.", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    oid = object_id(data.get("method_id"))
    m = await content_methods.find_one({"_id": oid}) if oid else None
    if not m:
        await state.clear()
        await message.answer("❌ Method not found.")
        return
    await content_methods.update_one({"_id": m["_id"]}, {"$set": {"patch_note": data.get("patch_note"), "success_rate": rate, "patched_at": utcnow(), "updated_at": utcnow()}})
    await state.clear()
    await message.answer(f"✅ Method patched. Success rate set to {rate}%.")
    await audit("content_method_patched", message.from_user.id, {"method_id": str(m["_id"]), "success_rate": rate})

@router.callback_query(F.data.startswith("cmadmin:toggle:"))
async def toggle_content_method(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    m = await content_methods.find_one({"_id": oid}) if oid else None
    if not m:
        await callback.answer("Not found.", show_alert=True)
        return
    new_active = not bool(m.get("active", True))
    await content_methods.update_one({"_id": m["_id"]}, {"$set": {"active": new_active, "updated_at": utcnow()}})
    await callback.message.answer(f"✅ Paid method active: {new_active}")
    await callback.answer()


@router.callback_query(F.data.startswith("cmadmin:delete:"))
async def delete_content_method(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    m = await content_methods.find_one({"_id": oid}) if oid else None
    if not m:
        await callback.answer("Not found.", show_alert=True)
        return
    await content_methods.delete_one({"_id": m["_id"]})
    await callback.message.answer(f"🗑 Deleted paid method: {bold(m.get('name'))}")
    await callback.answer("Deleted")

@router.callback_query(F.data == "admin:canva_requests")
async def admin_canva_requests(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for req in canva_requests.find({"status": "pending"}).sort("created_at", DESCENDING).limit(20):
        found = True
        await callback.message.answer(f"📩 Canva Request\nID: {code(req['_id'])}\nUser: {code(req.get('user_id'))} @{escape(req.get('username') or '')}\nGmail: {code(req.get('gmail'))}\nStatus: {bold(req.get('status'))}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Mark Added", callback_data=f"canvareq:done:{req['_id']}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"canvareq:reject:{req['_id']}")]]))
    if not found:
        await callback.message.answer("No pending Canva requests.")
    await callback.answer()


# ==========================================================
# MANUAL POSITIONS / STOCKOUT CONTROLS
# ==========================================================

@router.callback_query(F.data.startswith("prod:position:"))
async def product_position_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    await state.update_data(item_type="product", item_id=pid)
    await state.set_state(SetPositionState.position)
    await callback.message.answer(
        f"📌 Set position for {bold(product.get('name'))}\n\n"
        "Send position number. Example: 1 = front, 2 = second, 3 = third.\n"
        "Stockout products still show at the end automatically.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("prod:stockout:"))
async def product_stockout_info(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    product = await product_by_id(pid)
    if not product:
        await callback.answer("Product not found.", show_alert=True)
        return
    await callback.message.answer(
        f"📛 Stockout button is automatic for {bold(product.get('name'))}.\n\n"
        "When stock is 0, it will still appear on the products page at the bottom with 'Stockout'.\n"
        "To remove Stockout, add stock to this product."
    )
    await callback.answer("Automatic")

@router.callback_query(F.data.startswith("cmadmin:position:"))
async def content_method_position_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":", 2)[2]
    oid = object_id(mid)
    m = await content_methods.find_one({"_id": oid}) if oid else None
    if not m:
        await callback.answer("Method not found.", show_alert=True)
        return
    await state.update_data(item_type="content_method", item_id=mid)
    await state.set_state(SetPositionState.position)
    await callback.message.answer(
        f"📌 Set position for paid method {bold(m.get('name'))}\n\n"
        "Send position number. Example: 1 = front, 2 = second, 3 = third.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("method:position:"))
async def payment_method_position_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    mid = callback.data.split(":", 2)[2]
    method = await method_by_id(mid)
    if not method:
        await callback.answer("Payment method not found.", show_alert=True)
        return
    await state.update_data(item_type="payment_method", item_id=mid)
    await state.set_state(SetPositionState.position)
    await callback.message.answer(
        f"📌 Set position for payment method {bold(method.get('name'))}\n\n"
        "Send position number. Example: 1 = front, 2 = second, 3 = third.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(SetPositionState.position)
async def save_manual_position(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    position = parse_int(message.text or "")
    if position is None or position < 1 or position > 9999:
        await message.answer("❌ Send a valid position number from 1 to 9999.")
        return
    data = await state.get_data()
    item_type = data.get("item_type")
    item_id = data.get("item_id")
    collection = None
    label = "item"
    if item_type == "product":
        collection = products
        label = "product"
    elif item_type == "content_method":
        collection = content_methods
        label = "paid method"
    elif item_type == "payment_method":
        collection = payment_methods
        label = "payment method"
    if collection is None or not item_id:
        await state.clear()
        await message.answer("❌ Position setup expired. Try again.")
        return
    ok = await set_item_position(collection, item_id, position)
    await state.clear()
    if not ok:
        await message.answer("❌ Item not found.")
        return
    await message.answer(f"✅ {label.title()} position set to {bold(position)}.")
    await audit("position_updated", message.from_user.id, {"type": item_type, "item_id": item_id, "position": position})


# ==========================================================
# POINTS TO USDT EXCHANGE + OWNER ADMIN MANAGEMENT
# ==========================================================

@router.message(F.text == "💱 EXCHANGE")
async def exchange_points_button(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    user = await ensure_user(message)
    rate = int(await get_setting("points_per_usdt", 10))
    await state.set_state(PointExchangeState.points)
    await message.answer(
        f"💱 Convert referral points to USDT balance\n\n"
        f"Your points: {bold(user.get('points', 0))}\n"
        f"Rate: {bold(rate)} points = {bold(money(1))}\n\n"
        f"Send how many points you want to exchange. Admin approval is required.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")]])
    )

@router.callback_query(F.data == "points:exchange")
async def exchange_points_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Please /start first.", show_alert=True)
        return
    rate = int(await get_setting("points_per_usdt", 10))
    await state.set_state(PointExchangeState.points)
    await callback.message.answer(
        f"💱 Send points amount to exchange.\n\n"
        f"Your points: {bold(user.get('points', 0))}\n"
        f"Rate: {bold(rate)} points = {bold(money(1))}\n"
        f"Admin approval is required.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")]])
    )
    await callback.answer()

@router.message(PointExchangeState.points)
async def exchange_points_submit(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    amount = parse_int(message.text or "")
    user = await get_user(message.from_user.id)
    if not amount or amount <= 0 or not user:
        await message.answer("❌ Send a valid points amount.")
        return
    if int(user.get("points", 0)) < amount:
        await message.answer("❌ You do not have enough points.")
        return
    rate = int(await get_setting("points_per_usdt", 10))
    usdt_amount = round(amount / rate, 2)
    result = await point_exchanges.insert_one({
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "points": amount,
        "usdt": usdt_amount,
        "rate": rate,
        "status": "pending",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    await state.clear()
    await message.answer(
        f"✅ Exchange request submitted.\n\n"
        f"Points: {bold(amount)}\n"
        f"You will receive: {bold(money(usdt_amount))}\n"
        f"Status: {bold('pending admin approval')}",
        reply_markup=main_menu(message.from_user.id),
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"exchange:approve:{result.inserted_id}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"exchange:reject:{result.inserted_id}"),
    ]])
    await notify_admins(
        f"💱 {bold('New Points Exchange Request')}\n\n"
        f"ID: {code(short_id(result.inserted_id))}\n"
        f"User: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\n"
        f"Points: {bold(amount)}\n"
        f"USDT: {bold(money(usdt_amount))}\n"
        f"Rate: {bold(rate)} points = 1 USDT",
        reply_markup=keyboard,
    )

@router.callback_query(F.data == "admin:point_exchanges")
async def admin_point_exchanges(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for ex in point_exchanges.find({"status": "pending"}).sort("created_at", DESCENDING).limit(20):
        found = True
        await callback.message.answer(
            f"💱 Exchange {code(short_id(ex.get('_id')))}\n\n"
            f"User: {code(ex.get('user_id'))} @{escape(ex.get('username') or 'None')}\n"
            f"Points: {bold(ex.get('points', 0))}\n"
            f"USDT: {bold(money(ex.get('usdt', 0)))}\n"
            f"Status: {bold(ex.get('status'))}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Approve", callback_data=f"exchange:approve:{ex['_id']}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"exchange:reject:{ex['_id']}"),
            ]])
        )
    if not found:
        await callback.message.answer("No pending exchange requests.")
    await callback.answer()

@router.callback_query(F.data.startswith("exchange:approve:"))
async def approve_point_exchange(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    ex_id = callback.data.split(":", 2)[2]
    oid = object_id(ex_id)
    ex = await point_exchanges.find_one({"_id": oid}) if oid else None
    if not ex or ex.get("status") != "pending":
        await callback.answer("Exchange not found or already handled.", show_alert=True)
        return
    user = await users.find_one({"user_id": ex["user_id"]})
    if not user or int(user.get("points", 0)) < int(ex.get("points", 0)):
        await callback.answer("User does not have enough points now.", show_alert=True)
        return
    await users.update_one({"user_id": ex["user_id"]}, {"$inc": {"points": -int(ex["points"]), "balance": float(ex["usdt"])}})
    await point_exchanges.update_one({"_id": ex["_id"]}, {"$set": {"status": "approved", "handled_by": callback.from_user.id, "updated_at": utcnow()}})
    await safe_send_message(ex["user_id"], f"✅ Points exchange approved!\n\nDeducted: {bold(ex['points'])} points\nAdded: {bold(money(ex['usdt']))}")
    await callback.message.answer("✅ Exchange approved.")
    await callback.answer("Approved")
    await audit("point_exchange_approved", callback.from_user.id, {"exchange_id": ex_id, "user_id": ex["user_id"]})

@router.callback_query(F.data.startswith("exchange:reject:"))
async def reject_point_exchange(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    ex_id = callback.data.split(":", 2)[2]
    oid = object_id(ex_id)
    ex = await point_exchanges.find_one({"_id": oid}) if oid else None
    if not ex or ex.get("status") != "pending":
        await callback.answer("Exchange not found or already handled.", show_alert=True)
        return
    await point_exchanges.update_one({"_id": ex["_id"]}, {"$set": {"status": "rejected", "handled_by": callback.from_user.id, "updated_at": utcnow()}})
    await safe_send_message(ex["user_id"], f"❌ Points exchange rejected.\n\nPoints: {bold(ex.get('points', 0))}\nUSDT: {bold(money(ex.get('usdt', 0)))}")
    await callback.message.answer("❌ Exchange rejected.")
    await callback.answer("Rejected")
    await audit("point_exchange_rejected", callback.from_user.id, {"exchange_id": ex_id, "user_id": ex["user_id"]})

@router.callback_query(F.data == "admin:add_admin")
async def owner_add_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only.", show_alert=True)
        return
    await state.set_state(AddAdminState.chat_id)
    await callback.message.answer("Send new admin chat ID:")
    await callback.answer()

@router.message(AddAdminState.chat_id)
async def owner_add_admin_save(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        await message.answer("Owner only.")
        return
    uid = parse_int(message.text or "")
    if not uid or uid <= 0:
        await message.answer("❌ Send valid numeric chat ID.")
        return
    await admin_accounts.update_one(
        {"user_id": uid},
        {"$set": {"user_id": uid, "role": "admin", "active": True, "added_by": message.from_user.id, "updated_at": utcnow()}, "$setOnInsert": {"created_at": utcnow()}},
        upsert=True,
    )
    if uid not in ADMIN_IDS:
        ADMIN_IDS.append(uid)
    await state.clear()
    await message.answer(f"✅ Admin added: {code(uid)}")
    await safe_send_message(uid, "✅ You have been added as admin.")
    await audit("admin_added", message.from_user.id, {"new_admin": uid})

@router.callback_query(F.data == "admin:list_admins")
async def owner_list_admins(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only.", show_alert=True)
        return
    text = f"👑 {bold('Admins')}\n\nOwner: {code(OWNER_ID)}\n\n"
    async for adm in admin_accounts.find({}).sort("created_at", ASCENDING):
        status = "Active" if adm.get("active", True) else "Disabled"
        role = adm.get("role", "admin")
        uid = adm.get("user_id")
        text += f"• {code(uid)} — {escape(role)} — {escape(status)}\n"
    await callback.message.answer(text)
    await callback.answer()

# ==========================================================
# ERROR HANDLING / FALLBACK
# ==========================================================

@router.message(StateFilter("*"), lambda message: bool((message.text or "").strip()) and "X PREMIUM" in (message.text or "").upper())
async def x_premium_any_text_before_fallback(message: Message, state: FSMContext) -> None:
    await x_premium_menu(message, state)

@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Unknown command. Use /help.")
    else:
        await message.answer("Use the menu below.", reply_markup=main_menu(message.from_user.id))

@dp.errors()
async def error_handler(event: Any) -> bool:
    logger.error("Unhandled error: %s", event)
    logger.error(traceback.format_exc())
    try:
        await audit("unhandled_error", None, {"event": str(event)[:1000]})
    except Exception:
        pass
    return True

# ==========================================================
# STARTUP
# ==========================================================

async def main() -> None:
    await ensure_indexes()
    me = await bot.get_me()
    logger.info("Bot started as @%s", me.username)
    logger.info("Admins: %s", ADMIN_IDS)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())



# ==========================================================
# ADDON HANDLERS MOVED BEFORE BOT STARTUP
# ==========================================================

# Developer note 0001: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0001: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0002: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0002: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0003: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0003: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0004: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0004: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0005: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0005: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0005: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0006: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0006: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0007: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0007: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0008: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0008: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0009: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0009: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0010: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0010: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0010: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0011: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0011: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0012: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0012: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0013: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0013: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0014: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0014: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0015: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0015: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0015: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0016: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0016: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0017: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0017: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0018: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0018: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0019: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0019: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0020: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0020: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0020: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0021: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0021: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0022: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0022: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0023: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0023: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0024: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0024: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0025: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0025: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0025: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0026: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0026: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0027: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0027: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0028: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0028: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0029: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0029: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0030: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0030: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0030: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0031: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0031: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0032: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0032: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0033: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0033: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0034: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0034: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0035: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0035: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0035: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0036: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0036: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0037: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0037: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0038: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0038: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0039: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0039: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0040: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0040: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0040: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0041: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0041: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0042: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0042: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0043: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0043: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0044: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0044: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0045: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0045: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0045: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0046: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0046: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0047: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0047: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0048: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0048: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0049: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0049: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0050: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0050: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0050: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0051: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0051: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0052: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0052: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0053: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0053: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0054: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0054: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0055: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0055: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0055: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0056: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0056: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0057: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0057: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0058: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0058: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0059: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0059: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0060: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0060: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0060: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0061: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0061: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0062: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0062: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0063: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0063: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0064: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0064: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0065: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0065: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0065: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0066: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0066: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0067: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0067: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0068: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0068: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0069: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0069: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0070: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0070: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0070: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0071: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0071: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0072: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0072: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0073: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0073: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0074: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0074: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0075: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0075: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0075: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0076: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0076: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0077: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0077: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0078: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0078: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0079: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0079: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0080: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0080: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0080: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0081: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0081: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0082: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0082: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0083: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0083: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0084: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0084: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0085: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0085: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0085: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0086: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0086: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0087: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0087: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0088: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0088: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0089: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0089: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0090: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0090: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0090: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0091: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0091: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0092: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0092: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0093: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0093: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0094: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0094: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0095: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0095: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0095: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0096: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0096: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0097: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0097: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0098: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0098: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0099: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0099: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0100: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0100: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0100: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0101: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0101: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0102: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0102: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0103: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0103: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0104: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0104: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0105: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0105: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0105: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0106: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0106: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0107: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0107: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0108: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0108: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0109: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0109: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0110: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0110: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0110: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0111: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0111: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0112: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0112: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0113: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0113: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0114: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0114: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0115: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0115: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0115: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0116: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0116: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0117: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0117: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0118: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0118: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0119: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0119: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0120: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0120: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0120: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0121: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0121: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0122: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0122: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0123: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0123: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0124: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0124: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0125: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0125: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0125: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0126: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0126: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0127: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0127: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0128: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0128: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0129: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0129: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0130: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0130: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0130: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0131: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0131: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0132: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0132: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0133: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0133: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0134: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0134: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0135: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0135: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0135: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0136: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0136: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0137: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0137: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0138: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0138: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0139: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0139: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0140: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0140: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0140: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0141: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0141: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0142: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0142: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0143: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0143: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0144: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0144: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0145: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0145: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0145: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0146: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0146: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0147: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0147: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0148: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0148: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0149: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0149: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0150: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0150: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0150: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0151: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0151: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0152: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0152: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0153: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0153: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0154: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0154: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0155: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0155: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0155: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0156: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0156: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0157: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0157: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0158: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0158: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0159: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0159: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0160: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0160: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0160: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0161: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0161: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0162: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0162: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0163: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0163: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0164: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0164: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0165: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0165: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0165: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0166: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0166: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0167: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0167: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0168: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0168: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0169: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0169: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0170: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0170: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0170: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0171: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0171: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0172: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0172: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0173: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0173: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0174: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0174: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0175: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0175: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0175: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0176: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0176: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0177: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0177: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0178: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0178: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0179: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0179: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0180: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0180: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0180: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0181: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0181: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0182: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0182: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0183: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0183: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0184: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0184: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0185: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0185: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0185: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0186: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0186: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0187: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0187: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0188: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0188: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0189: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0189: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0190: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0190: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0190: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0191: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0191: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0192: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0192: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0193: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0193: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0194: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0194: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0195: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0195: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0195: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0196: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0196: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0197: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0197: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0198: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0198: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0199: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0199: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0200: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0200: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0200: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0201: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0201: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0202: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0202: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0203: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0203: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0204: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0204: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0205: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0205: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0205: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0206: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0206: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0207: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0207: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0208: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0208: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0209: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0209: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0210: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0210: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0210: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0211: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0211: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0212: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0212: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0213: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0213: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0214: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0214: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0215: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0215: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0215: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0216: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0216: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0217: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0217: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0218: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0218: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0219: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0219: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0220: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0220: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0220: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0221: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0221: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0222: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0222: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0223: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0223: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0224: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0224: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0225: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0225: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0225: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0226: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0226: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0227: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0227: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0228: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0228: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0229: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0229: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0230: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0230: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0230: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0231: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0231: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0232: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0232: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0233: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0233: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0234: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0234: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0235: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0235: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0235: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0236: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0236: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0237: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0237: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0238: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0238: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0239: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0239: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0240: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0240: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0240: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0241: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0241: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0242: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0242: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0243: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0243: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0244: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0244: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0245: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0245: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0245: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0246: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0246: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0247: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0247: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0248: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0248: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0249: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0249: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0250: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0250: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0250: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0251: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0251: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0252: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0252: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0253: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0253: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0254: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0254: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0255: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0255: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0255: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0256: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0256: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0257: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0257: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0258: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0258: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0259: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0259: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0260: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0260: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0260: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0261: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0261: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0262: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0262: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0263: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0263: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0264: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0264: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0265: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0265: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0265: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0266: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0266: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0267: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0267: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0268: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0268: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0269: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0269: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0270: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0270: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0270: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0271: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0271: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0272: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0272: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0273: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0273: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0274: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0274: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0275: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0275: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0275: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0276: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0276: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0277: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0277: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0278: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0278: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0279: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0279: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0280: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0280: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0280: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0281: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0281: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0282: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0282: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0283: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0283: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0284: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0284: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0285: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0285: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0285: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0286: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0286: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0287: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0287: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0288: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0288: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0289: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0289: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0290: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0290: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0290: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0291: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0291: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0292: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0292: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0293: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0293: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0294: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0294: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0295: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0295: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0295: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0296: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0296: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0297: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0297: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0298: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0298: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0299: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0299: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0300: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0300: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0300: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0301: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0301: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0302: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0302: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0303: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0303: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0304: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0304: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0305: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0305: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0305: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0306: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0306: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0307: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0307: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0308: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0308: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0309: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0309: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0310: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0310: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0310: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0311: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0311: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0312: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0312: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0313: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0313: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0314: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0314: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0315: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0315: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0315: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0316: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0316: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0317: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0317: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0318: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0318: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0319: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0319: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0320: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0320: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0320: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0321: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0321: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0322: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0322: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0323: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0323: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0324: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0324: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0325: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0325: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0325: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0326: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0326: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0327: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0327: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0328: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0328: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0329: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0329: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0330: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0330: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0330: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0331: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0331: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0332: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0332: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0333: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0333: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0334: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0334: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0335: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0335: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0335: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0336: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0336: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0337: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0337: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0338: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0338: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0339: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0339: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0340: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0340: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0340: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0341: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0341: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0342: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0342: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0343: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0343: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0344: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0344: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0345: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0345: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0345: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0346: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0346: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0347: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0347: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0348: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0348: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0349: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0349: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0350: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0350: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0350: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0351: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0351: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0352: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0352: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0353: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0353: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0354: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0354: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0355: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0355: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0355: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0356: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0356: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0357: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0357: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0358: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0358: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0359: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0359: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0360: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0360: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0360: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0361: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0361: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0362: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0362: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0363: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0363: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0364: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0364: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0365: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0365: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0365: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0366: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0366: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0367: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0367: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0368: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0368: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0369: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0369: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0370: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0370: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0370: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0371: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0371: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0372: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0372: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0373: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0373: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0374: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0374: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0375: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0375: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0375: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0376: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0376: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0377: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0377: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0378: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0378: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0379: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0379: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0380: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0380: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0380: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0381: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0381: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0382: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0382: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0383: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0383: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0384: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0384: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0385: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0385: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0385: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0386: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0386: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0387: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0387: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0388: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0388: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0389: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0389: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0390: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0390: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0390: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0391: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0391: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0392: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0392: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0393: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0393: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0394: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0394: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0395: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0395: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0395: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0396: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0396: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0397: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0397: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0398: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0398: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0399: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0399: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0400: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0400: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0400: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0401: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0401: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0402: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0402: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0403: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0403: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0404: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0404: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0405: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0405: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0405: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0406: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0406: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0407: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0407: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0408: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0408: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0409: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0409: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0410: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0410: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0410: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0411: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0411: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0412: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0412: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0413: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0413: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0414: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0414: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0415: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0415: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0415: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0416: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0416: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0417: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0417: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0418: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0418: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0419: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0419: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0420: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0420: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0420: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0421: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0421: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0422: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0422: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0423: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0423: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0424: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0424: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0425: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0425: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0425: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0426: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0426: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0427: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0427: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0428: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0428: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0429: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0429: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0430: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0430: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0430: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0431: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0431: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0432: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0432: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0433: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0433: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0434: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0434: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0435: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0435: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0435: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0436: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0436: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0437: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0437: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0438: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0438: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0439: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0439: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0440: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0440: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0440: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0441: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0441: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0442: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0442: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0443: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0443: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0444: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0444: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0445: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0445: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0445: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0446: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0446: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0447: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0447: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0448: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0448: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0449: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0449: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0450: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0450: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0450: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0451: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0451: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0452: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0452: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0453: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0453: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0454: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0454: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0455: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0455: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0455: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0456: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0456: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0457: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0457: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0458: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0458: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0459: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0459: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0460: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0460: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0460: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0461: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0461: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0462: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0462: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0463: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0463: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0464: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0464: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0465: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0465: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0465: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0466: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0466: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0467: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0467: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0468: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0468: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0469: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0469: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0470: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0470: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0470: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0471: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0471: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0472: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0472: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0473: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0473: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0474: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0474: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0475: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0475: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0475: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0476: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0476: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0477: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0477: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0478: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0478: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0479: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0479: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0480: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0480: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0480: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0481: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0481: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0482: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0482: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0483: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0483: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0484: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0484: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0485: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0485: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0485: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0486: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0486: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0487: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0487: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0488: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0488: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0489: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0489: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0490: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0490: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0490: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0491: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0491: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0492: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0492: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0493: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0493: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0494: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0494: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0495: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0495: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0495: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0496: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0496: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0497: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0497: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0498: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0498: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0499: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0499: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0500: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0500: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0500: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0501: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0501: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0502: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0502: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0503: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0503: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0504: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0504: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0505: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0505: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0505: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0506: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0506: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0507: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0507: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0508: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0508: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0509: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0509: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0510: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0510: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0510: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0511: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0511: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0512: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0512: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0513: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0513: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0514: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0514: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0515: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0515: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0515: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0516: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0516: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0517: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0517: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0518: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0518: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0519: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0519: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0520: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0520: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0520: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0521: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0521: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0522: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0522: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0523: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0523: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0524: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0524: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0525: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0525: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0525: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0526: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0526: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0527: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0527: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0528: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0528: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0529: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0529: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0530: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0530: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0530: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0531: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0531: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0532: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0532: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0533: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0533: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0534: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0534: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0535: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0535: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0535: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0536: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0536: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0537: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0537: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0538: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0538: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0539: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0539: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0540: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0540: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0540: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0541: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0541: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0542: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0542: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0543: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0543: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0544: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0544: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0545: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0545: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0545: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0546: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0546: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0547: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0547: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0548: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0548: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0549: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0549: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0550: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0550: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0550: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0551: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0551: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0552: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0552: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0553: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0553: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0554: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0554: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0555: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0555: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0555: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0556: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0556: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0557: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0557: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0558: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0558: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0559: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0559: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0560: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0560: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0560: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0561: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0561: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0562: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0562: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0563: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0563: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0564: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0564: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0565: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0565: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0565: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0566: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0566: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0567: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0567: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0568: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0568: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0569: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0569: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0570: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0570: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0570: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0571: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0571: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0572: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0572: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0573: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0573: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0574: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0574: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0575: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0575: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0575: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0576: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0576: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0577: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0577: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0578: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0578: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0579: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0579: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0580: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0580: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0580: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0581: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0581: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0582: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0582: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0583: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0583: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0584: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0584: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0585: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0585: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0585: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0586: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0586: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0587: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0587: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0588: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0588: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0589: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0589: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0590: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0590: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0590: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0591: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0591: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0592: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0592: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0593: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0593: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0594: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0594: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0595: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0595: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0595: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0596: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0596: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0597: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0597: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0598: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0598: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0599: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0599: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0600: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0600: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0600: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0601: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0601: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0602: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0602: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0603: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0603: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0604: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0604: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0605: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0605: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0605: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0606: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0606: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0607: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0607: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0608: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0608: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0609: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0609: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0610: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0610: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0610: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0611: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0611: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0612: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0612: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0613: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0613: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0614: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0614: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0615: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0615: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0615: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0616: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0616: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0617: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0617: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0618: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0618: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0619: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0619: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0620: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0620: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0620: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0621: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0621: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0622: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0622: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0623: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0623: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0624: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0624: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0625: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0625: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0625: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0626: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0626: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0627: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0627: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0628: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0628: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0629: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0629: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0630: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0630: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0630: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0631: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0631: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0632: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0632: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0633: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0633: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0634: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0634: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0635: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0635: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0635: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0636: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0636: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0637: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0637: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0638: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0638: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0639: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0639: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0640: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0640: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0640: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0641: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0641: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0642: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0642: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0643: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0643: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0644: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0644: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0645: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0645: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0645: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0646: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0646: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0647: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0647: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0648: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0648: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0649: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0649: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0650: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0650: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0650: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0651: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0651: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0652: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0652: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0653: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0653: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0654: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0654: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0655: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0655: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0655: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0656: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0656: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0657: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0657: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0658: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0658: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0659: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0659: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0660: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0660: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0660: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0661: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0661: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0662: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0662: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0663: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0663: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0664: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0664: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0665: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0665: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0665: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0666: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0666: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0667: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0667: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0668: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0668: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0669: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0669: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0670: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0670: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0670: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0671: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0671: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0672: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0672: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0673: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0673: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0674: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0674: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0675: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0675: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0675: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0676: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0676: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0677: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0677: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0678: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0678: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0679: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0679: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0680: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0680: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0680: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0681: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0681: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0682: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0682: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0683: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0683: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0684: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0684: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0685: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0685: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0685: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0686: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0686: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0687: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0687: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0688: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0688: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0689: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0689: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0690: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0690: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0690: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0691: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0691: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0692: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0692: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0693: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0693: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0694: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0694: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0695: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0695: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0695: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0696: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0696: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0697: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0697: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0698: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0698: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0699: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0699: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0700: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0700: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0700: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0701: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0701: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0702: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0702: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0703: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0703: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0704: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0704: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0705: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0705: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0705: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0706: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0706: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0707: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0707: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0708: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0708: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0709: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0709: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0710: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0710: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0710: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0711: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0711: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0712: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0712: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0713: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0713: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0714: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0714: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0715: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0715: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0715: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0716: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0716: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0717: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0717: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0718: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0718: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0719: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0719: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0720: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0720: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0720: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0721: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0721: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0722: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0722: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0723: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0723: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0724: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0724: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0725: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0725: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0725: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0726: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0726: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0727: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0727: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0728: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0728: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0729: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0729: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0730: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0730: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0730: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0731: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0731: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0732: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0732: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0733: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0733: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0734: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0734: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0735: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0735: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0735: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0736: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0736: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0737: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0737: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0738: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0738: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0739: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0739: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0740: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0740: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0740: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0741: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0741: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0742: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0742: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0743: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0743: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0744: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0744: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0745: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0745: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0745: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0746: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0746: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0747: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0747: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0748: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0748: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0749: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0749: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0750: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0750: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0750: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0751: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0751: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0752: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0752: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0753: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0753: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0754: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0754: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0755: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0755: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0755: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0756: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0756: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0757: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0757: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0758: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0758: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0759: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0759: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0760: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0760: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0760: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0761: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0761: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0762: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0762: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0763: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0763: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0764: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0764: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0765: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0765: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0765: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0766: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0766: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0767: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0767: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0768: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0768: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0769: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0769: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0770: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0770: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0770: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0771: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0771: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0772: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0772: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0773: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0773: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0774: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0774: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0775: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0775: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0775: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0776: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0776: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0777: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0777: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0778: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0778: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0779: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0779: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0780: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0780: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0780: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0781: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0781: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0782: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0782: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0783: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0783: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0784: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0784: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0785: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0785: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0785: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0786: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0786: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0787: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0787: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0788: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0788: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0789: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0789: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0790: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0790: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0790: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0791: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0791: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0792: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0792: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0793: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0793: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0794: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0794: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0795: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0795: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0795: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0796: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0796: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0797: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0797: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0798: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0798: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0799: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0799: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0800: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0800: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0800: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0801: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0801: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0802: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0802: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0803: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0803: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0804: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0804: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0805: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0805: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0805: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0806: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0806: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0807: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0807: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0808: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0808: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0809: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0809: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0810: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0810: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0810: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0811: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0811: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0812: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0812: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0813: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0813: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0814: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0814: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0815: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0815: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0815: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0816: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0816: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0817: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0817: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0818: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0818: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0819: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0819: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0820: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0820: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0820: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0821: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0821: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0822: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0822: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0823: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0823: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0824: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0824: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0825: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0825: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0825: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0826: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0826: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0827: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0827: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0828: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0828: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0829: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0829: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0830: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0830: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0830: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0831: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0831: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0832: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0832: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0833: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0833: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0834: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0834: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0835: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0835: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0835: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0836: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0836: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0837: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0837: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0838: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0838: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0839: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0839: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0840: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0840: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0840: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0841: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0841: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0842: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0842: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0843: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0843: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0844: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0844: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0845: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0845: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0845: Railway should run this app as a worker using Procfile: worker: python main.py
# Developer note 0846: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0846: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0847: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0847: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0848: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0848: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0849: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0849: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Developer note 0850: This bot uses MongoDB collections for users, products, stock, orders, payments, methods, tickets, settings, refunds, logs, and audit events.
# Security note 0850: Keep BOT_TOKEN, MONGODB_URI, and ADMIN_IDS private. Never commit .env to GitHub.
# Operations note 0850: Railway should run this app as a worker using Procfile: worker: python main.py


# ==========================================================
# V4 UPDATE NOTES
# ==========================================================
# - Free Items button swapped with Referral button in main menu.
# - Admin can delete free items from the free items list.
# - Admin panel includes direct Block User and Unblock User buttons.
# - Stock upload wording now clearly supports adding multiple stock lines at once.
# - Bulk stock button label added in admin panel.

# Update v6:
# - FREE PRODUCTS button renamed to FREE ITEMS everywhere in user/admin text.
# - Admin can configure bulk discount quantities and percentages from Settings.
# - Product buy buttons now use admin-defined bulk quantities and discount percentages.
# - Checkout discount calculation now uses the admin-defined bulk discount rules.

# V9 updates:
# - Added PREORDER user button and full preorder request/admin answer/fulfilled/reject flow.
# - Admin receives preorder details and client receives notifications/answers.
# - Added Canva first-account-free setting; after first free request users must use points or USDT.


# ==========================================================
# PREORDER PRODUCT CATALOG ADDON
# ==========================================================

def preorder_product_admin_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Edit Name", callback_data=f"pprod:edit_name:{pid}"),
            InlineKeyboardButton(text="💵 Edit Price", callback_data=f"pprod:edit_price:{pid}"),
        ],
        [
            InlineKeyboardButton(text="📝 Edit Description", callback_data=f"pprod:edit_desc:{pid}"),
            InlineKeyboardButton(text="🟢 Toggle Active", callback_data=f"pprod:toggle:{pid}"),
        ],
        [
            InlineKeyboardButton(text="📌 Set Position", callback_data=f"pprod:position:{pid}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"pprod:delete:{pid}"),
        ],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="global:cancel")],
    ])

@router.callback_query(F.data == "preorder:manual")
async def preorder_manual_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    await state.update_data(back_to="preorder")
    await state.set_state(ManualPreorderQuantityState.quantity)
    await callback.message.answer(
        "✍️ <b>Manual Preorder</b>\n\n"
        "Send number of accounts you want first.",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(ManualPreorderQuantityState.quantity)
async def preorder_manual_quantity(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    qty = parse_int(message.text or "")
    if not qty or qty <= 0:
        await message.answer("❌ Send a valid number of accounts.", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(quantity=qty)
    await state.set_state(PreorderState.details)
    await message.answer(
        "Now send what account/product you need, duration, and any note.\n\n"
        "Example:\n<code>ChatGPT Plus, 30 days</code>",
        reply_markup=cancel_inline_keyboard(),
    )

async def show_preorder_product_by_id(target: Message, pid: str, admin_edit: bool = False) -> None:
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp or pp.get("active") is False:
        await target.answer("❌ Preorder product not found.")
        return
    base_price = float(pp.get("price", 0) or 0)
    sample_subtotal, sample_fee, sample_total = preorder_advance_total(base_price, 1)
    rows = [
        [
            InlineKeyboardButton(text="1 account", callback_data=f"preprod:{pid}:1"),
            InlineKeyboardButton(text="2 accounts", callback_data=f"preprod:{pid}:2"),
        ],
        [
            InlineKeyboardButton(text="5 accounts", callback_data=f"preprod:{pid}:5"),
            InlineKeyboardButton(text="10 accounts", callback_data=f"preprod:{pid}:10"),
        ],
        [InlineKeyboardButton(text="🔢 Manual number", callback_data=f"preprodmanual:{pid}")],
    ]
    if admin_edit:
        rows.append([InlineKeyboardButton(text="✏️ Admin Edit Price", callback_data=f"pprod:edit_price:{pid}")])
    rows += [
        [InlineKeyboardButton(text="⬅️ Back to Preorder", callback_data="nav:preorder")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
    ]
    await target.answer(
        f"📝 {bold(pp.get('name', 'Preorder Product'))}\n\n"
        f"💵 Base price per account: {bold(money(base_price))}\n"
        f"➕ Advance fee: {bold(str(int(PREORDER_ADVANCE_FEE_PERCENT)) + '%')}\n"
        f"💰 Pay per account now: {bold(money(sample_total))}\n"
        f"📌 Payment: {bold('Advance payment required')}\n\n"
        f"{escape(pp.get('description', ''))}\n\n"
        "Select how many accounts you want to preorder:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )

@router.callback_query(F.data.startswith("preproduct:view:"))
async def preorder_product_view(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 2)[2]
    await show_preorder_product_by_id(callback.message, pid, admin_edit=is_admin(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("preprodmanual:"))
async def preorder_product_manual_quantity_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    pid = callback.data.split(":", 1)[1]
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp or pp.get("active") is False:
        await callback.answer("Preorder product not found.", show_alert=True)
        return
    await state.update_data(product_id=pid, back_to=f"preproduct:{pid}")
    await state.set_state(PreorderProductQuantityState.quantity)
    await callback.message.answer(
        f"🔢 Send number of accounts for {bold(pp.get('name', 'Preorder Product'))}.\n\nBase price each: {bold(money(pp.get('price', 0)))}\nAdvance fee: {bold(str(int(PREORDER_ADVANCE_FEE_PERCENT)) + '%')}",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(PreorderProductQuantityState.quantity)
async def preorder_product_manual_quantity_save(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    qty = parse_int(message.text or "")
    if not qty or qty <= 0:
        await message.answer("❌ Send a valid number of accounts.", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    pid = data.get("product_id")
    await state.clear()
    # Reuse the existing confirmation callback flow by sending the same confirmation UI.
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp or pp.get("active") is False:
        await message.answer("❌ Preorder product not found.", reply_markup=main_menu(message.from_user.id))
        return
    subtotal, fee, total = preorder_advance_total(pp.get("price", 0), qty)
    await message.answer(
        f"📝 <b>Confirm Preorder</b>\n\n"
        f"Product: {bold(pp.get('name'))}\n"
        f"Accounts: {bold(qty)}\n"
        f"Base price each: {bold(money(pp.get('price', 0)))}\n"
        f"Subtotal: {bold(money(subtotal))}\n"
        f"Advance fee ({int(PREORDER_ADVANCE_FEE_PERCENT)}%): {bold(money(fee))}\n"
        f"Total to pay now: {bold(money(total))}\n\n"
        f"Click approve to continue with advance payment.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Continue to Payment", callback_data=f"preprod_confirm:{pid}:{qty}")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data=f"preproduct:view:{pid}")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
        ]),
    )

@router.callback_query(F.data.startswith("preprod:"))
async def preorder_product_selected(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid preorder.", show_alert=True)
        return
    pid, qty_raw = parts[1], parts[2]
    qty = parse_int(qty_raw)
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp or pp.get("active") is False:
        await callback.answer("Preorder product not found.", show_alert=True)
        return
    if not qty or qty <= 0:
        await callback.answer("Invalid quantity.", show_alert=True)
        return
    subtotal, fee, total = preorder_advance_total(pp.get("price", 0), qty)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Continue to Payment", callback_data=f"preprod_confirm:{pid}:{qty}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"preproduct:view:{pid}")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
    ])
    await callback.message.answer(
        f"📝 <b>Confirm Preorder</b>\n\n"
        f"Product: {bold(pp.get('name'))}\n"
        f"Accounts: {bold(qty)}\n"
        f"Base price each: {bold(money(pp.get('price', 0)))}\n"
        f"Subtotal: {bold(money(subtotal))}\n"
        f"Advance fee ({int(PREORDER_ADVANCE_FEE_PERCENT)}%): {bold(money(fee))}\n"
        f"Total to pay now: {bold(money(total))}\n\n"
        f"Click approve to continue with advance payment.",
        reply_markup=keyboard,
    )
    await callback.answer()

@router.callback_query(F.data.startswith("preprod_confirm:"))
async def preorder_product_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid preorder.", show_alert=True)
        return
    pid, qty_raw = parts[1], parts[2]
    qty = parse_int(qty_raw)
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp or pp.get("active") is False or not qty:
        await callback.answer("Preorder product not found.", show_alert=True)
        return

    subtotal, fee, total = preorder_advance_total(pp.get("price", 0), qty)
    details = f"Catalog preorder: {pp.get('name')} | Quantity: {qty} account(s) | Subtotal: {money(subtotal)} | {int(PREORDER_ADVANCE_FEE_PERCENT)}% advance fee: {money(fee)} | Advance total: {money(total)}"
    await state.update_data(preorder_type="catalog", details=details, product_id=str(pp["_id"]), product_name=pp.get("name"), quantity=qty, price_each=float(pp.get("price", 0)), subtotal=subtotal, advance_fee=fee, estimated_total=total, back_to=f"preproduct:{pid}")

    user = await get_user(callback.from_user.id)
    user_balance = float((user or {}).get("balance", 0) or 0)
    if user_balance >= total:
        data = await state.get_data()
        await users.update_one({"user_id": callback.from_user.id}, {"$inc": {"balance": -total, "total_spent": total}})
        preorder_oid = await create_balance_paid_preorder(callback.from_user.id, callback.from_user.username, data, source="catalog_balance")
        await state.clear()
        await callback.message.answer(
            f"✅ {bold('Preorder submitted successfully!')}{chr(10)*2}"
            f"Preorder ID: {code(short_id(preorder_oid))}{chr(10)}"
            f"Product: {bold(pp.get('name'))}{chr(10)}"
            f"Accounts: {bold(qty)}{chr(10)}"
            f"Paid from your bot balance: {bold(money(total))}{chr(10)}"
            f"Remaining balance: {bold(money(user_balance - total))}{chr(10)*2}"
            f"Admin will review and reply with your products.",
            reply_markup=main_menu(callback.from_user.id),
        )
        await callback.answer("Preorder submitted")
        return

    await callback.message.answer(
        f"💰 <b>Advance Payment Required</b>{chr(10)*2}"
        f"Product: {bold(pp.get('name'))}{chr(10)}"
        f"Accounts: {bold(qty)}{chr(10)}"
        f"Subtotal: {bold(money(subtotal))}{chr(10)}"
        f"Advance fee ({int(PREORDER_ADVANCE_FEE_PERCENT)}%): {bold(money(fee))}{chr(10)}"
        f"Total to pay now: {bold(money(total))}{chr(10)}"
        f"Your bot balance: {bold(money(user_balance))}{chr(10)*2}"
        f"Your balance is not enough. Choose a payment method and send TXID, paid amount, and screenshot."
    )
    await send_preorder_payment_methods(callback.message, state)
    await callback.answer()

@router.callback_query(F.data == "admin:add_preorder_product")
async def admin_add_preorder_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(AddPreorderProductState.name)
    await callback.message.answer("Send preorder product name:", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(AddPreorderProductState.name)
async def admin_add_preorder_product_name(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    name = truncate((message.text or "").strip(), 80)
    if len(name) < 2:
        await message.answer("❌ Name too short.")
        return
    await state.update_data(name=name)
    await state.set_state(AddPreorderProductState.price)
    await message.answer("Send preorder price per account in USDT:", reply_markup=cancel_inline_keyboard())

@router.message(AddPreorderProductState.price)
async def admin_add_preorder_product_price(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    price = parse_float(message.text or "")
    if price is None or price < 0:
        await message.answer("❌ Invalid price.")
        return
    await state.update_data(price=price)
    await state.set_state(AddPreorderProductState.description)
    await message.answer("Send preorder product description:", reply_markup=cancel_inline_keyboard())

@router.message(AddPreorderProductState.description)
async def admin_add_preorder_product_description(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    description = truncate((message.text or "").strip(), 1500)
    position = await next_position(preorder_products)
    result = await preorder_products.insert_one({
        "name": data["name"],
        "price": float(data["price"]),
        "description": description,
        "active": True,
        "position": position,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    await state.clear()
    await message.answer(f"✅ Preorder product added.\nID: {code(result.inserted_id)}")
    await audit("preorder_product_added", message.from_user.id, {"product_id": str(result.inserted_id), "name": data["name"]})

@router.callback_query(F.data == "admin:preorder_products")
async def admin_list_preorder_products(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for pp in preorder_products.find({}).sort([("position", ASCENDING), ("created_at", DESCENDING)]):
        found = True
        pid = str(pp["_id"])
        await callback.message.answer(
            f"📝 {bold(pp.get('name', 'Preorder Product'))}\n\n"
            f"ID: {code(pid)}\n"
            f"Price/account: {bold(money(pp.get('price', 0)))}\n"
            f"Position: {bold(pp.get('position', 0))}\n"
            f"Status: {bold('Active' if pp.get('active', True) else 'Hidden')}\n\n"
            f"{escape(pp.get('description', ''))}",
            reply_markup=preorder_product_admin_keyboard(pid),
        )
    if not found:
        await callback.message.answer("No preorder products added yet.")
    await callback.answer()

@router.callback_query(F.data.startswith("pprod:edit_name:") | F.data.startswith("pprod:edit_price:") | F.data.startswith("pprod:edit_desc:"))
async def admin_edit_preorder_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    parts = callback.data.split(":", 2)
    action = parts[1]
    pid = parts[2]
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp:
        await callback.answer("Preorder product not found.", show_alert=True)
        return

    field_map = {
        "edit_name": "name",
        "edit_price": "price",
        "edit_desc": "description",
    }
    field = field_map.get(action)
    await state.update_data(product_id=pid, field=field)
    await state.set_state(EditPreorderProductState.value)

    if field == "price":
        prompt = f"Send new preorder price per account for {bold(pp.get('name'))}:"
    elif field == "description":
        prompt = f"Send new description for {bold(pp.get('name'))}:"
    else:
        prompt = f"Send new name for {bold(pp.get('name'))}:"
    await callback.message.answer(prompt, reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(EditPreorderProductState.value)
async def admin_edit_preorder_product_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    pid = data.get("product_id")
    field = data.get("field")
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp:
        await state.clear()
        await message.answer("❌ Preorder product not found.")
        return

    raw = (message.text or "").strip()
    update_value = None
    if field == "price":
        price = parse_float(raw)
        if price is None or price < 0:
            await message.answer("❌ Invalid price. Send a number like 2.5", reply_markup=cancel_inline_keyboard())
            return
        update_value = float(price)
    elif field == "description":
        update_value = truncate(raw, 1500)
    elif field == "name":
        update_value = truncate(raw, 80)
        if len(update_value) < 2:
            await message.answer("❌ Name too short.", reply_markup=cancel_inline_keyboard())
            return
    else:
        await state.clear()
        await message.answer("❌ Invalid edit field.")
        return

    await preorder_products.update_one(
        {"_id": pp["_id"]},
        {"$set": {field: update_value, "updated_at": utcnow()}},
    )
    await state.clear()
    await message.answer("✅ Preorder product updated.")
    await audit("preorder_product_updated", message.from_user.id, {"product_id": pid, "field": field})

@router.callback_query(F.data.startswith("pprod:position:"))
async def admin_position_preorder_product(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp:
        await callback.answer("Preorder product not found.", show_alert=True)
        return
    await state.update_data(item_type="preorder_product", item_id=pid)
    await state.set_state(SetPositionState.position)
    await callback.message.answer("Send new position number. Example: 1 means first, 2 means second.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("pprod:toggle:"))
async def admin_toggle_preorder_product(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp:
        await callback.answer("Preorder product not found.", show_alert=True)
        return
    new_active = not bool(pp.get("active", True))
    await preorder_products.update_one({"_id": pp["_id"]}, {"$set": {"active": new_active, "updated_at": utcnow()}})
    await callback.message.answer(f"✅ Preorder product is now {bold('Active' if new_active else 'Hidden')}.")
    await callback.answer()

@router.callback_query(F.data.startswith("pprod:delete:"))
async def admin_delete_preorder_product(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    oid = object_id(pid)
    pp = await preorder_products.find_one({"_id": oid}) if oid else None
    if not pp:
        await callback.answer("Preorder product not found.", show_alert=True)
        return
    await preorder_products.delete_one({"_id": pp["_id"]})
    await callback.message.answer(f"🗑 Deleted preorder product: {bold(pp.get('name'))}")
    await callback.answer()
    await audit("preorder_product_deleted", callback.from_user.id, {"product_id": pid, "name": pp.get("name")})

# v10 update notes:
# - Admin can add preorder products with price and description.
# - User preorder page now shows product buttons like products section.
# - User selects account quantity and approves before sending to admin.
# - Manual preorder remains available.
# - Admin can toggle or delete preorder products.


# v11 update notes:
# - Preorder catalog products now require advance payment before submission.
# - Users choose a payment method and upload screenshot/photo/document with preorder.
# - Manual preorder also asks for advance payment screenshot before sending to admin.
# - Admin receives full preorder details plus payment screenshot and can answer, fulfill, or reject.

# v12 update notes:
# - Fixed preorder product admin buttons by adding complete handlers.
# - Added edit name, edit price, edit description, delete, toggle, and position controls for preorder products.
# - Admin preorder replies, fulfilled notices, and rejected notices now include a support/contact button for the user.


# v14 update notes:
# - Fixed FSM preorder product add flow: fallback now only catches messages when no state is active.
# - Admin can type preorder product name, price, and description without being interrupted by main menu fallback.


# ==========================================================
# RESELLER MARKETPLACE / VIP SELLERS
# ==========================================================

class ResellListState(StatesGroup):
    name = State()
    price = State()
    description = State()
    stock = State()

class SellerAdminState(StatesGroup):
    user_id = State()
    action = State()
    value = State()

async def get_seller_profile(user_id: int) -> Dict[str, Any]:
    profile = await seller_profiles.find_one({"user_id": int(user_id)})
    if profile:
        return profile
    default_commission = float(await get_setting("default_reseller_commission_percent", 10) or 10)
    profile = {
        "user_id": int(user_id),
        "vip": False,
        "trusted": False,
        "badge": "Seller",
        "commission_percent": default_commission,
        "reach_boost": 0,
        "total_sales": 0,
        "total_revenue": 0.0,
        "total_payout": 0.0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    await seller_profiles.insert_one(profile)
    return profile

async def seller_badge_text(user_id: int) -> str:
    profile = await get_seller_profile(user_id)
    badges = []
    if profile.get("vip"):
        badges.append("👑 VIP")
    if profile.get("trusted"):
        badges.append("✅ Trusted")
    badges.append(str(profile.get("badge") or "Seller"))
    return " • ".join(dict.fromkeys([b for b in badges if b]))

async def sync_seller_products(user_id: int) -> None:
    profile = await get_seller_profile(user_id)
    boost = 100000 if profile.get("vip") else 0
    if profile.get("trusted"):
        boost += 10000
    boost += int(profile.get("reach_boost", 0) or 0)
    await products.update_many(
        {"seller_id": int(user_id), "is_resell": True},
        {"$set": {
            "seller_vip": bool(profile.get("vip")),
            "seller_trusted": bool(profile.get("trusted")),
            "seller_badge_label": await seller_badge_text(user_id),
            "seller_commission_percent": float(profile.get("commission_percent", 10) or 10),
            "seller_reach_boost": boost,
            "updated_at": utcnow(),
        }},
    )

def parse_stock_blocks(raw_stock: str) -> List[str]:
    raw_stock = (raw_stock or "").strip()
    if not raw_stock:
        return []
    if "---" in raw_stock:
        return [truncate(block.strip(), 4000) for block in re.split(r"(?m)^---+$", raw_stock) if block.strip()]
    return [truncate(line.strip(), 4000) for line in raw_stock.splitlines() if line.strip()]

async def resell_menu_text(user_id: int) -> str:
    profile = await get_seller_profile(user_id)
    pending = await products.count_documents({"seller_id": user_id, "is_resell": True, "approval_status": "pending"})
    active = await products.count_documents({"seller_id": user_id, "is_resell": True, "approval_status": "approved", "active": True})
    sold = int(profile.get("total_sales", 0) or 0)
    return (
        f"♻️ {bold('Seller / Resell Panel')}\n\n"
        f"Badge: {bold(await seller_badge_text(user_id))}\n"
        f"Commission: {bold(str(profile.get('commission_percent', 10)) + '%')}\n"
        f"Approved products: {bold(active)}\n"
        f"Pending approval: {bold(pending)}\n"
        f"Total sales: {bold(sold)}\n\n"
        "List your digital product, upload stock, and wait for admin approval. "
        "Buyer payment stays in the platform first. Seller payout is released after buyer confirms the order."
    )

@router.message(F.text == "♻️ RESELL")
async def reseller_panel(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)
    if not bool(await get_setting("resell_enabled", True)) and not is_admin(message.from_user.id):
        await message.answer("♻️ Resell is currently disabled by admin.")
        return
    await state.clear()
    await message.answer(
        await resell_menu_text(message.from_user.id),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ List New Product", callback_data="resell:new")],
            [InlineKeyboardButton(text="📦 My Products", callback_data="resell:mine")],
            [InlineKeyboardButton(text="💰 My Seller Payouts", callback_data="resell:payouts")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")],
        ]),
    )

@router.callback_query(F.data == "resell:new")
async def resell_new_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    await get_seller_profile(callback.from_user.id)
    await state.set_state(ResellListState.name)
    await callback.message.answer("➕ Send product name for resell listing:", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(ResellListState.name)
async def resell_name(message: Message, state: FSMContext) -> None:
    name = truncate((message.text or "").strip(), 80)
    if len(name) < 2:
        await message.answer("❌ Product name too short.", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(name=name)
    await state.set_state(ResellListState.price)
    await message.answer("Send selling price in USDT:", reply_markup=cancel_inline_keyboard())

@router.message(ResellListState.price)
async def resell_price(message: Message, state: FSMContext) -> None:
    price = parse_float(message.text or "")
    if price is None or price <= 0:
        await message.answer("❌ Invalid price. Example: 2.50", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(price=price)
    await state.set_state(ResellListState.description)
    await message.answer("Send product description. You can use multiple lines.", reply_markup=cancel_inline_keyboard())

@router.message(ResellListState.description)
async def resell_description(message: Message, state: FSMContext) -> None:
    description = truncate((message.text or "").strip(), 2500)
    if len(description) < 3:
        await message.answer("❌ Description too short.", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(description=description)
    await state.set_state(ResellListState.stock)
    await message.answer(
        "📥 Upload stock now.\n\n"
        "Normal: one item per line.\n"
        "For bigger multi-line accounts, separate each product with a line containing ---\n\n"
        "Example:\nemail:pass\nrecovery:abc\n---\nsecond@email:pass",
        reply_markup=cancel_inline_keyboard(),
    )

@router.message(ResellListState.stock)
async def resell_stock(message: Message, state: FSMContext) -> None:
    stock = parse_stock_blocks(message.text or "")
    if not stock:
        await message.answer("❌ No stock found. Send stock text first.", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    profile = await get_seller_profile(message.from_user.id)
    badge = await seller_badge_text(message.from_user.id)
    boost = 100000 if profile.get("vip") else 0
    if profile.get("trusted"):
        boost += 10000
    boost += int(profile.get("reach_boost", 0) or 0)
    doc = {
        "name": data["name"],
        "price": float(data["price"]),
        "description": data["description"],
        "stock": stock,
        "active": False,
        "approval_status": "pending",
        "is_resell": True,
        "seller_id": message.from_user.id,
        "seller_username": message.from_user.username,
        "seller_vip": bool(profile.get("vip")),
        "seller_trusted": bool(profile.get("trusted")),
        "seller_badge_label": badge,
        "seller_commission_percent": float(profile.get("commission_percent", 10) or 10),
        "seller_reach_boost": boost,
        "position": await next_position(products),
        "sales": 0,
        "revenue": 0.0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await products.insert_one(doc)
    await state.clear()
    await message.answer(
        f"✅ Listing submitted for admin approval.\n\nProduct ID: {code(result.inserted_id)}\nStock items: {bold(len(stock))}\nCommission: {bold(str(doc['seller_commission_percent']) + '%')}",
        reply_markup=main_menu(message.from_user.id),
    )
    await notify_admins(
        f"♻️ {bold('New Resell Product Pending Approval')}\n\n"
        f"Product ID: {code(result.inserted_id)}\n"
        f"Seller: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\n"
        f"Badge: {bold(badge)}\n"
        f"Product: {bold(data['name'])}\n"
        f"Price: {bold(money(data['price']))}\n"
        f"Stock: {bold(len(stock))}\n"
        f"Commission: {bold(str(doc['seller_commission_percent']) + '%')}\n\n"
        f"Description:\n{escape(data['description'])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"resell:approve:{result.inserted_id}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"resell:reject:{result.inserted_id}")],
            [InlineKeyboardButton(text="📌 Put On Top", callback_data=f"prod:position:{result.inserted_id}")],
        ]),
    )
    await audit("resell_listing_submitted", message.from_user.id, {"product_id": str(result.inserted_id), "stock": len(stock)})

@router.callback_query(F.data == "resell:mine")
async def resell_my_products(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    found = False
    await callback.message.answer("📦 Your reseller products:")
    async for product in products.find({"seller_id": callback.from_user.id, "is_resell": True}).sort("created_at", DESCENDING).limit(10):
        found = True
        await callback.message.answer(await format_product_display(product))
    if not found:
        await callback.message.answer("No reseller products yet.")
    await callback.answer()

@router.callback_query(F.data == "resell:payouts")
async def resell_my_payouts(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    text = "💰 Your recent seller payouts:\n\n"
    found = False
    async for pdoc in reseller_payouts.find({"seller_id": callback.from_user.id}).sort("created_at", DESCENDING).limit(10):
        found = True
        text += f"• {escape(pdoc.get('product_name'))} — Gross {money(pdoc.get('gross',0))} | Commission {money(pdoc.get('commission_amount',0))} | Net {money(pdoc.get('seller_amount',0))} — {escape(pdoc.get('status'))}\n"
    await callback.message.answer(text if found else "No payouts yet. Payout appears after buyer confirms a reseller order.")
    await callback.answer()

@router.callback_query(F.data.startswith("admin:resell:pending:"))
async def admin_resell_pending(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    try:
        page = int(callback.data.split(":")[-1])
    except Exception:
        page = 1
    limit = 5
    skip = (page - 1) * limit
    count = await products.count_documents({"is_resell": True, "approval_status": "pending"})
    cursor = products.find({"is_resell": True, "approval_status": "pending"}).sort("created_at", DESCENDING).skip(skip).limit(limit)
    found = False
    await callback.message.answer(f"♻️ Pending reseller listings — page {page}")
    async for product in cursor:
        found = True
        await callback.message.answer(
            await format_product_display(product),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Approve", callback_data=f"resell:approve:{product['_id']}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"resell:reject:{product['_id']}")],
                [InlineKeyboardButton(text="📌 Set Top Position", callback_data=f"prod:position:{product['_id']}"), InlineKeyboardButton(text="✏️ Edit Price", callback_data=f"prod:edit_price:{product['_id']}")],
            ]),
        )
    if not found:
        await callback.message.answer("No pending reseller listings.")
    nav=[]
    if page>1:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin:resell:pending:{page-1}"))
    if skip+limit<count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:resell:pending:{page+1}"))
    if nav:
        await callback.message.answer("Pages:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav]))
    await callback.answer()

@router.callback_query(F.data.startswith("resell:approve:"))
async def admin_resell_approve(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    product = await product_by_id(pid)
    if not product or not product.get("is_resell"):
        await callback.answer("Resell product not found.", show_alert=True)
        return
    await sync_seller_products(int(product.get("seller_id")))
    await products.update_one({"_id": product["_id"]}, {"$set": {"active": True, "approval_status": "approved", "approved_by": callback.from_user.id, "approved_at": utcnow(), "updated_at": utcnow()}})
    await safe_send_message(int(product.get("seller_id")), f"✅ Your reseller product was approved and is now live: {bold(product.get('name'))}")
    await callback.message.answer("✅ Reseller listing approved and published.")
    await callback.answer()
    await audit("resell_approved", callback.from_user.id, {"product_id": pid})

@router.callback_query(F.data.startswith("resell:reject:"))
async def admin_resell_reject(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    pid = callback.data.split(":", 2)[2]
    product = await product_by_id(pid)
    if not product or not product.get("is_resell"):
        await callback.answer("Resell product not found.", show_alert=True)
        return
    await products.update_one({"_id": product["_id"]}, {"$set": {"active": False, "approval_status": "rejected", "rejected_by": callback.from_user.id, "rejected_at": utcnow(), "updated_at": utcnow()}})
    await safe_send_message(int(product.get("seller_id")), f"❌ Your reseller product was rejected by admin: {bold(product.get('name'))}")
    await callback.message.answer("❌ Reseller listing rejected.")
    await callback.answer()
    await audit("resell_rejected", callback.from_user.id, {"product_id": pid})

@router.callback_query(F.data.startswith("admin:sellers:"))
async def admin_sellers(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    try:
        page = int(callback.data.split(":")[-1])
    except Exception:
        page = 1
    limit = 8
    skip = (page - 1) * limit
    await callback.message.answer(
        "👑 Seller controls\n\nUse Add/Edit Seller to enter Chat ID, then choose VIP/trusted/commission options.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add/Edit Seller By Chat ID", callback_data="selleradmin:start")],
            [InlineKeyboardButton(text="♻️ Pending Listings", callback_data="admin:resell:pending:1")],
        ]),
    )
    found=False
    async for sp in seller_profiles.find({}).sort([("vip", DESCENDING), ("trusted", DESCENDING), ("total_sales", DESCENDING)]).skip(skip).limit(limit):
        found=True
        uid=int(sp.get('user_id'))
        await callback.message.answer(
            f"👤 Seller {code(uid)}\n"
            f"Badge: {bold(await seller_badge_text(uid))}\n"
            f"VIP: {bold('YES' if sp.get('vip') else 'NO')} | Trusted: {bold('YES' if sp.get('trusted') else 'NO')}\n"
            f"Commission: {bold(str(sp.get('commission_percent', 10)) + '%')}\n"
            f"Reach boost: {bold(sp.get('reach_boost', 0))}\n"
            f"Sales: {bold(sp.get('total_sales', 0))} | Payout: {bold(money(sp.get('total_payout', 0)))}",
            reply_markup=seller_admin_keyboard(uid),
        )
    if not found:
        await callback.message.answer("No sellers yet.")
    await callback.answer()

def seller_admin_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Toggle VIP", callback_data=f"seller:toggle_vip:{user_id}"), InlineKeyboardButton(text="✅ Toggle Trusted", callback_data=f"seller:toggle_trusted:{user_id}")],
        [InlineKeyboardButton(text="💸 Set Commission", callback_data=f"seller:set_comm:{user_id}"), InlineKeyboardButton(text="🏷️ Set Badge", callback_data=f"seller:set_badge:{user_id}")],
        [InlineKeyboardButton(text="🚀 Set Reach Boost", callback_data=f"seller:set_boost:{user_id}")],
    ])

@router.callback_query(F.data == "selleradmin:start")
async def selleradmin_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(SellerAdminState.user_id)
    await callback.message.answer("Send seller Chat ID:", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(SellerAdminState.user_id)
async def selleradmin_user_id(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    uid = parse_int(message.text or "")
    if not uid:
        await message.answer("❌ Invalid Chat ID.", reply_markup=cancel_inline_keyboard())
        return
    await get_seller_profile(uid)
    await state.clear()
    await message.answer(f"✅ Seller profile opened for {code(uid)}", reply_markup=seller_admin_keyboard(uid))

@router.callback_query(F.data.startswith("seller:toggle_vip:"))
async def seller_toggle_vip(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[-1])
    sp = await get_seller_profile(uid)
    new_value = not bool(sp.get("vip"))
    await seller_profiles.update_one({"user_id": uid}, {"$set": {"vip": new_value, "updated_at": utcnow()}}, upsert=True)
    await sync_seller_products(uid)
    await callback.message.answer(f"✅ VIP set to {bold('YES' if new_value else 'NO')} for {code(uid)}")
    await callback.answer()

@router.callback_query(F.data.startswith("seller:toggle_trusted:"))
async def seller_toggle_trusted(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[-1])
    sp = await get_seller_profile(uid)
    new_value = not bool(sp.get("trusted"))
    await seller_profiles.update_one({"user_id": uid}, {"$set": {"trusted": new_value, "updated_at": utcnow()}}, upsert=True)
    await sync_seller_products(uid)
    await callback.message.answer(f"✅ Trusted badge set to {bold('YES' if new_value else 'NO')} for {code(uid)}")
    await callback.answer()

@router.callback_query(F.data.startswith("seller:set_comm:"))
async def seller_set_comm_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[-1])
    await state.update_data(user_id=uid, action="commission")
    await state.set_state(SellerAdminState.value)
    await callback.message.answer("Send commission percent for this seller. Example: 10 or 7.5", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("seller:set_badge:"))
async def seller_set_badge_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[-1])
    await state.update_data(user_id=uid, action="badge")
    await state.set_state(SellerAdminState.value)
    await callback.message.answer("Send badge text. Example: Trusted Pro Seller", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("seller:set_boost:"))
async def seller_set_boost_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    uid = int(callback.data.split(":")[-1])
    await state.update_data(user_id=uid, action="boost")
    await state.set_state(SellerAdminState.value)
    await callback.message.answer("Send reach boost number. Example: 0, 100, 1000. Higher appears above other sellers after VIP/trusted.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(SellerAdminState.value)
async def selleradmin_value(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    uid = int(data.get("user_id"))
    action = data.get("action")
    update = {"updated_at": utcnow()}
    if action == "commission":
        val = parse_float(message.text or "")
        if val is None or val < 0 or val > 100:
            await message.answer("❌ Commission must be between 0 and 100.", reply_markup=cancel_inline_keyboard())
            return
        update["commission_percent"] = float(val)
    elif action == "badge":
        update["badge"] = truncate((message.text or "Seller").strip(), 40)
    elif action == "boost":
        val = parse_int(message.text or "")
        if val is None or val < 0:
            await message.answer("❌ Boost must be 0 or higher.", reply_markup=cancel_inline_keyboard())
            return
        update["reach_boost"] = int(val)
    else:
        await state.clear()
        await message.answer("Unknown seller action.")
        return
    await seller_profiles.update_one({"user_id": uid}, {"$set": update, "$setOnInsert": {"created_at": utcnow()}}, upsert=True)
    await sync_seller_products(uid)
    await state.clear()
    await message.answer(f"✅ Seller updated for {code(uid)}", reply_markup=seller_admin_keyboard(uid))

@router.callback_query(F.data.startswith("buyerconfirm:"))
async def buyer_confirm_resell_order(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    oid = object_id(callback.data.split(":", 1)[1])
    order = await orders.find_one({"_id": oid}) if oid else None
    if not order or order.get("user_id") != callback.from_user.id:
        await callback.answer("Order not found.", show_alert=True)
        return
    if order.get("status") != "awaiting_buyer_confirm" or not order.get("is_resell"):
        await callback.answer("This order is already confirmed or not a reseller order.", show_alert=True)
        return
    seller_id = int(order.get("seller_id"))
    gross = float(order.get("total", 0) or 0)
    commission_percent = float(order.get("commission_percent", 10) or 10)
    commission_amount = round(gross * commission_percent / 100, 2)
    seller_amount = round(gross - commission_amount, 2)
    await orders.update_one({"_id": order["_id"]}, {"$set": {"status": "completed", "buyer_confirmed_at": utcnow(), "admin_commission_amount": commission_amount, "seller_payout_amount": seller_amount}})
    await users.update_one({"user_id": seller_id}, {"$inc": {"balance": seller_amount}})
    await seller_profiles.update_one({"user_id": seller_id}, {"$inc": {"total_sales": int(order.get("quantity", 1) or 1), "total_revenue": gross, "total_payout": seller_amount}, "$set": {"updated_at": utcnow()}}, upsert=True)
    await reseller_payouts.update_one(
        {"order_id": str(order["_id"])},
        {"$set": {"order_id": str(order["_id"]), "seller_id": seller_id, "buyer_id": callback.from_user.id, "product_name": order.get("product_name"), "gross": gross, "commission_percent": commission_percent, "commission_amount": commission_amount, "seller_amount": seller_amount, "status": "released", "released_at": utcnow(), "created_at": utcnow()}},
        upsert=True,
    )
    await safe_send_message(seller_id, f"💰 Buyer confirmed order. Seller payout released: {bold(money(seller_amount))}\nAdmin commission: {bold(money(commission_amount))}")
    await callback.message.answer(f"✅ Order confirmed. Seller payout released. Thank you!")
    await callback.answer("Confirmed")


# ==========================================================
# X PREMIUM + GROK ACCOUNT SYSTEM - FULL WORKING PATCH
# ==========================================================

X_PLAN_KEYS = ["3m", "6m", "1y"]
X_PLAN_VISIBILITY = {"3m": "x_plan_3m", "6m": "x_plan_6m", "1y": "x_plan_1y"}

async def get_x_plans() -> Dict[str, Dict[str, Any]]:
    defaults = {
        "3m": {"name": "X Premium + Grok 3 Months", "months": 3, "price_usdt": 5.0, "points": 50},
        "6m": {"name": "X Premium + Grok 6 Months", "months": 6, "price_usdt": 9.0, "points": 90},
        "1y": {"name": "X Premium + Grok 1 Year", "months": 12, "price_usdt": 15.0, "points": 150},
    }
    plans = await get_setting("x_plans", defaults)
    if not isinstance(plans, dict):
        plans = {}
    for key, val in defaults.items():
        if not isinstance(plans.get(key), dict):
            plans[key] = val.copy()
        else:
            for dk, dv in val.items():
                plans[key].setdefault(dk, dv)
    return plans

def normalize_x_username(text: str) -> str:
    username = (text or "").strip()
    username = username.replace("https://x.com/", "").replace("http://x.com/", "")
    username = username.replace("https://twitter.com/", "").replace("http://twitter.com/", "")
    username = username.split("?")[0].split("/")[0].replace("@", "").strip()
    return username

def valid_x_username(username: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,30}", username or ""))

async def x_plan_keyboard(plans: Dict[str, Dict[str, Any]], user_id: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for key in X_PLAN_KEYS:
        if not button_visible(X_PLAN_VISIBILITY[key], user_id):
            continue
        plan = plans[key]
        rows.append([InlineKeyboardButton(
            text=f"⭐ {plan.get('points', 0)} pts | 🐦 {plan.get('name')} | {money(plan.get('price_usdt', 0))}",
            callback_data=f"x:plan:{key}",
        )])
    if button_visible("x_method", user_id):
        rows.append([InlineKeyboardButton(text="📝 Buy X Premium + Grok Method", callback_data="x:method")])
    if button_visible("x_my_orders", user_id):
        rows.append([InlineKeyboardButton(text="🧾 My X + Grok Orders", callback_data="x:myorders")])
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="global:main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def render_x_premium_menu(target: Message, state: FSMContext, user_id: int) -> None:
    await state.clear()
    user = await get_user(user_id) or {}
    plans = await get_x_plans()
    await target.answer(
        f"🐦 {bold('X Premium + Grok Store')}\n\n"
        f"⭐ Points: {bold(user.get('points', 0))}\n"
        f"💰 USDT Balance: {bold(money(user.get('balance', 0)))}\n\n"
        f"Choose a plan below.",
        reply_markup=await x_plan_keyboard(plans, user_id),
    )

async def x_premium_menu(message: Message, state: FSMContext) -> None:
    if not await security_check_event(message):
        return
    await ensure_user(message)
    if not await ensure_button_allowed(message, "x_premium"):
        return
    if await send_force_join_message(message, "all"):
        return
    await award_referral_if_eligible(message.from_user.id)
    await render_x_premium_menu(message, state, message.from_user.id)

@router.message(Command("x"))
@router.message(Command("xpremium"))
async def x_premium_command(message: Message, state: FSMContext) -> None:
    await x_premium_menu(message, state)

@router.message(StateFilter("*"), F.text.in_({"🐦 X PREMIUM + GROK", "X PREMIUM + GROK", "X Premium + Grok", "x premium plus"}))
async def x_premium_text_fallback(message: Message, state: FSMContext) -> None:
    await x_premium_menu(message, state)

@router.callback_query(F.data.in_({"x:menu", "x_premium", "xpremium"}))
async def x_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    if not await ensure_button_allowed(callback, "x_premium"):
        return
    if await send_force_join_message(callback, "all"):
        return
    await award_referral_if_eligible(callback.from_user.id)
    await render_x_premium_menu(callback.message, state, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.startswith("x:plan:"))
async def x_plan_view(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    if await send_force_join_message(callback, "all"):
        return
    key = callback.data.rsplit(":", 1)[-1]
    if key not in X_PLAN_KEYS:
        await callback.answer("Plan not found.", show_alert=True)
        return
    if not await ensure_button_allowed(callback, X_PLAN_VISIBILITY[key]):
        return
    plans = await get_x_plans()
    plan = plans[key]
    rows: List[List[InlineKeyboardButton]] = []
    if button_visible("x_pay_points", callback.from_user.id):
        rows.append([InlineKeyboardButton(text="⭐ Buy with Points", callback_data=f"xbuy:points:{key}")])
    if button_visible("x_pay_balance", callback.from_user.id):
        rows.append([InlineKeyboardButton(text="💵 Buy with USDT Balance", callback_data=f"xbuy:balance:{key}")])
    if button_visible("x_pay_manual", callback.from_user.id):
        rows.append([InlineKeyboardButton(text="💵 Pay Binance Manually", callback_data=f"xbuy:manual:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="x:back")])
    await state.clear()
    await callback.message.answer(
        f"🐦 {bold(plan.get('name'))}\n\n"
        f"⭐ Points Price: {bold(plan.get('points', 0))} points\n"
        f"💵 USDT Price: {bold(money(plan.get('price_usdt', 0)))}\n\n"
        f"Choose payment type below.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()

@router.callback_query(F.data == "x:back")
async def x_back(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    await render_x_premium_menu(callback.message, state, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.startswith("xbuy:points:"))
@router.callback_query(F.data.startswith("xbuy:balance:"))
async def x_buy_internal(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    if await send_force_join_message(callback, "all"):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid button.", show_alert=True)
        return
    pay_type, key = parts[1], parts[2]
    if key not in X_PLAN_KEYS:
        await callback.answer("Plan not found.", show_alert=True)
        return
    if not await ensure_button_allowed(callback, {"points": "x_pay_points", "balance": "x_pay_balance"}.get(pay_type, "x_premium")):
        return
    plans = await get_x_plans()
    plan = plans[key]
    user = await get_user(callback.from_user.id) or {}
    if pay_type == "points" and int(user.get("points", 0) or 0) < int(plan.get("points", 0) or 0):
        await callback.answer("Not enough points.", show_alert=True)
        return
    if pay_type == "balance" and float(user.get("balance", 0) or 0) + 1e-9 < float(plan.get("price_usdt", 0) or 0):
        await callback.answer("Not enough USDT balance. Deposit first or use manual Binance payment.", show_alert=True)
        return
    await state.clear()
    await state.set_state(XUsernameState.plan_key)
    await state.update_data(plan_key=key, pay_type=pay_type)
    await callback.message.answer("Send the X username/account handle. Example: @username", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(XUsernameState.plan_key)
async def x_username_received(message: Message, state: FSMContext) -> None:
    username = normalize_x_username(message.text or "")
    if not valid_x_username(username):
        await message.answer("❌ Send a valid X username only. Example: @username", reply_markup=cancel_inline_keyboard())
        return
    data = await state.get_data()
    key, pay_type = data.get("plan_key"), data.get("pay_type")
    plans = await get_x_plans()
    plan = plans.get(key)
    if not plan or pay_type not in {"points", "balance"}:
        await state.clear()
        await message.answer("❌ Order session expired. Please try again.", reply_markup=main_menu(message.from_user.id))
        return
    amount = float(plan.get("price_usdt", 0) or 0)
    points = int(plan.get("points", 0) or 0)
    if pay_type == "points":
        res = await users.update_one({"user_id": message.from_user.id, "points": {"$gte": points}}, {"$inc": {"points": -points}})
        if res.modified_count <= 0:
            await state.clear()
            await message.answer("❌ Not enough points now.", reply_markup=main_menu(message.from_user.id))
            return
        paid_value_text = f"{points} points"
    else:
        res = await users.update_one({"user_id": message.from_user.id, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}})
        if res.modified_count <= 0:
            await state.clear()
            await message.answer("❌ Not enough USDT balance now.", reply_markup=main_menu(message.from_user.id))
            return
        paid_value_text = money(amount)
    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "x_username": username,
        "plan_key": key,
        "plan_name": plan.get("name"),
        "pay_type": pay_type,
        "amount_usdt": amount if pay_type == "balance" else 0.0,
        "points": points if pay_type == "points" else 0,
        "status": "pending",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await x_orders.insert_one(doc)
    await state.clear()
    await message.answer(
        f"✅ X + Grok order created.\n\nOrder ID: {code(short_id(result.inserted_id))}\nPlan: {bold(plan.get('name'))}\nX Username: @{escape(username)}\nPaid: {bold(paid_value_text)}\nStatus: {bold('Pending admin completion')}",
        reply_markup=main_menu(message.from_user.id),
    )
    await notify_admins(
        f"🐦 {bold('New X Premium + Grok Order')}\n\nOrder ID: {code(str(result.inserted_id))}\nUser: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\nPlan: {bold(plan.get('name'))}\nX Username: @{escape(username)}\nPaid: {bold(paid_value_text)}",
        reply_markup=x_order_admin_keyboard(str(result.inserted_id)),
    )

@router.callback_query(F.data.startswith("xbuy:manual:"))
async def x_manual_payment_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await security_check_event(callback):
        return
    if await send_force_join_message(callback, "all"):
        return
    if not await ensure_button_allowed(callback, "x_pay_manual"):
        return
    key = callback.data.rsplit(":", 1)[-1]
    plans = await get_x_plans()
    plan = plans.get(key)
    if not plan:
        await callback.answer("Plan not found.", show_alert=True)
        return
    binance_id = str(await get_setting("x_binance_id", "") or await get_setting("binance_wallet_address", "") or "").strip()
    if not binance_id:
        await callback.message.answer("❌ Binance ID/wallet is not set by admin yet.", reply_markup=cancel_inline_keyboard())
        await callback.answer()
        return
    await state.clear()
    await state.set_state(XPaymentState.x_username)
    await state.update_data(plan_key=key)
    await callback.message.answer(
        f"📤 {bold('Manual Binance Payment')}\n\nPlan: {bold(plan.get('name'))}\nAmount: {bold(money(plan.get('price_usdt', 0)))}\nBinance ID / Wallet:\n{code(binance_id)}\n\nFirst send the X username/account handle. Example: @username",
        reply_markup=cancel_inline_keyboard(),
    )
    await callback.answer()

@router.message(XPaymentState.x_username)
async def x_payment_username(message: Message, state: FSMContext) -> None:
    username = normalize_x_username(message.text or "")
    if not valid_x_username(username):
        await message.answer("❌ Send valid X username only. Example: @username", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(x_username=username)
    await state.set_state(XPaymentState.txid)
    await message.answer("Now send transaction ID / TXID.", reply_markup=cancel_inline_keyboard())

@router.message(XPaymentState.txid)
async def x_payment_txid(message: Message, state: FSMContext) -> None:
    txid = (message.text or "").strip()
    if len(txid) < 5:
        await message.answer("❌ TXID is too short. Send correct transaction ID.", reply_markup=cancel_inline_keyboard())
        return
    existing = await x_payments.find_one({"txid": {"$regex": f"^{re.escape(txid)}$", "$options": "i"}})
    if existing:
        await message.answer("❌ This TXID was already submitted.", reply_markup=cancel_inline_keyboard())
        return
    await state.update_data(txid=txid)
    await state.set_state(XPaymentState.screenshot)
    await message.answer("Upload payment screenshot/photo or document.", reply_markup=cancel_inline_keyboard())

@router.message(XPaymentState.screenshot, F.photo)
async def x_payment_photo(message: Message, state: FSMContext) -> None:
    await create_x_payment(message, state, message.photo[-1].file_id, "photo")

@router.message(XPaymentState.screenshot, F.document)
async def x_payment_document(message: Message, state: FSMContext) -> None:
    await create_x_payment(message, state, message.document.file_id, "document")

@router.message(XPaymentState.screenshot)
async def x_payment_need_screenshot(message: Message) -> None:
    await message.answer("❌ Please upload screenshot/photo or document.", reply_markup=cancel_inline_keyboard())

async def create_x_payment(message: Message, state: FSMContext, file_id: str, file_type: str) -> None:
    data = await state.get_data()
    plans = await get_x_plans()
    plan = plans.get(data.get("plan_key"))
    if not plan:
        await state.clear()
        await message.answer("Plan not found.", reply_markup=main_menu(message.from_user.id))
        return
    doc = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "x_username": data.get("x_username"),
        "plan_key": data.get("plan_key"),
        "plan_name": plan.get("name"),
        "amount_usdt": float(plan.get("price_usdt", 0) or 0),
        "txid": data.get("txid"),
        "screenshot_file_id": file_id,
        "screenshot_type": file_type,
        "status": "pending",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await x_payments.insert_one(doc)
    await state.clear()
    await message.answer(f"✅ X + Grok payment submitted.\n\nPayment ID: {code(short_id(result.inserted_id))}\nStatus: {bold('Pending admin approval')}", reply_markup=main_menu(message.from_user.id))
    caption = f"💰 {bold('New X Manual Payment')}\n\nPayment ID: {code(str(result.inserted_id))}\nUser: {code(message.from_user.id)} @{escape(message.from_user.username or 'None')}\nPlan: {bold(plan.get('name'))}\nAmount: {bold(money(plan.get('price_usdt', 0)))}\nX Username: @{escape(data.get('x_username'))}\nTXID: {code(data.get('txid'))}"
    for admin_id in list(dict.fromkeys(ADMIN_IDS)):
        try:
            if file_type == "photo":
                await bot.send_photo(admin_id, photo=file_id, caption=caption, reply_markup=x_payment_admin_keyboard(str(result.inserted_id)))
            else:
                await bot.send_document(admin_id, document=file_id, caption=caption, reply_markup=x_payment_admin_keyboard(str(result.inserted_id)))
        except Exception:
            await safe_send_message(admin_id, caption, reply_markup=x_payment_admin_keyboard(str(result.inserted_id)))

def x_order_admin_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Done", callback_data=f"xorder:done:{order_id}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"xorder:reject:{order_id}"),
    ]])

def x_payment_admin_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"xpay:approve:{payment_id}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"xpay:reject:{payment_id}"),
    ]])

@router.callback_query(F.data.startswith("xpay:approve:"))
async def x_payment_approve(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    oid = object_id(callback.data.split(":", 2)[2])
    payment = await x_payments.find_one({"_id": oid}) if oid else None
    if not payment or payment.get("status") != "pending":
        await callback.answer("Payment not pending.", show_alert=True)
        return
    await x_payments.update_one({"_id": oid}, {"$set": {"status": "approved", "approved_by": callback.from_user.id, "updated_at": utcnow()}})
    order_doc = {k: payment.get(k) for k in ["user_id", "username", "x_username", "plan_key", "plan_name", "amount_usdt"]}
    order_doc.update({"pay_type": "manual_usdt", "payment_id": str(payment["_id"]), "status": "pending", "created_at": utcnow(), "updated_at": utcnow()})
    res = await x_orders.insert_one(order_doc)
    await safe_send_message(int(payment["user_id"]), f"✅ Your X + Grok payment was approved.\n\nOrder ID: {code(short_id(res.inserted_id))}\nPlan: {bold(payment.get('plan_name'))}\nX Username: @{escape(payment.get('x_username'))}\nStatus: {bold('Pending admin completion')}")
    await callback.message.answer("✅ Payment approved and X + Grok order created.", reply_markup=x_order_admin_keyboard(str(res.inserted_id)))
    await callback.answer("Approved")

@router.callback_query(F.data.startswith("xpay:reject:"))
async def x_payment_reject_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(XPaymentRejectState.reason)
    await state.update_data(payment_id=callback.data.split(":", 2)[2])
    await callback.message.answer("Send rejection reason for this payment.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(XPaymentRejectState.reason)
async def x_payment_reject_reason(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    oid = object_id(data.get("payment_id"))
    reason = truncate(message.text or "Rejected by admin", 500)
    payment = await x_payments.find_one({"_id": oid}) if oid else None
    if payment and payment.get("status") == "pending":
        await x_payments.update_one({"_id": oid}, {"$set": {"status": "rejected", "reason": reason, "updated_at": utcnow(), "rejected_by": message.from_user.id}})
        await safe_send_message(int(payment["user_id"]), f"❌ Your X + Grok payment was rejected.\nReason: {escape(reason)}")
    await state.clear()
    await message.answer("✅ Payment rejected.", reply_markup=main_menu(message.from_user.id))

@router.callback_query(F.data.startswith("xorder:done:"))
async def x_order_done(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    oid = object_id(callback.data.split(":", 2)[2])
    order = await x_orders.find_one({"_id": oid}) if oid else None
    if not order or order.get("status") != "pending":
        await callback.answer("Order not pending.", show_alert=True)
        return
    await x_orders.update_one({"_id": oid}, {"$set": {"status": "done", "done_by": callback.from_user.id, "done_at": utcnow(), "updated_at": utcnow()}})
    await safe_send_message(int(order["user_id"]), f"🎉 Congratulations!\n\nYour {bold(order.get('plan_name'))} order is completed.\nX Username: @{escape(order.get('x_username'))}")
    await callback.message.answer("✅ X + Grok order marked done.")
    await callback.answer("Done")

@router.callback_query(F.data.startswith("xorder:reject:"))
async def x_order_reject_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(XRejectState.reason)
    await state.update_data(order_id=callback.data.split(":", 2)[2])
    await callback.message.answer("Send rejection reason. Payment/points will be refunded automatically.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(XRejectState.reason)
async def x_order_reject_reason(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    oid = object_id(data.get("order_id"))
    reason = truncate(message.text or "Rejected by admin", 500)
    order = await x_orders.find_one({"_id": oid}) if oid else None
    if not order or order.get("status") != "pending":
        await state.clear()
        await message.answer("Order not pending.", reply_markup=main_menu(message.from_user.id))
        return
    if order.get("pay_type") == "points":
        pts = int(order.get("points", 0) or 0)
        await users.update_one({"user_id": order["user_id"]}, {"$inc": {"points": pts}})
        refund_text = f"Refunded: {pts} points"
    else:
        amount = float(order.get("amount_usdt", 0) or 0)
        await users.update_one({"user_id": order["user_id"]}, {"$inc": {"balance": amount}})
        refund_text = f"Refunded to bot balance: {money(amount)}"
    await x_orders.update_one({"_id": oid}, {"$set": {"status": "rejected", "reason": reason, "updated_at": utcnow(), "rejected_by": message.from_user.id}})
    await safe_send_message(int(order["user_id"]), f"❌ Your X + Grok order was rejected.\nReason: {escape(reason)}\n{refund_text}")
    await state.clear()
    await message.answer("✅ Order rejected and refunded.", reply_markup=main_menu(message.from_user.id))

@router.callback_query(F.data == "x:myorders")
async def x_my_orders(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    if not await ensure_button_allowed(callback, "x_my_orders"):
        return
    text = "🧾 <b>Your X + Grok Orders</b>\n\n"
    found = False
    async for order in x_orders.find({"user_id": callback.from_user.id}).sort("created_at", DESCENDING).limit(10):
        found = True
        text += f"• {bold(order.get('plan_name'))} | @{escape(order.get('x_username'))} | {bold(order.get('status'))} | {code(short_id(order['_id']))}\n"
    if not found:
        text += "No X + Grok orders yet."
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="x:back")]]))
    await callback.answer()

@router.callback_query(F.data == "x:method")
async def x_method_view(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    if not await ensure_button_allowed(callback, "x_method"):
        return
    title = str(await get_setting("x_method_title", "X Premium + Grok Method"))
    price = float(await get_setting("x_method_price_usdt", 10.0) or 0)
    points = int(await get_setting("x_method_points", 100) or 0)
    await callback.message.answer(
        f"📝 {bold(title)}\n\n⭐ Points Price: {bold(points)} points\n💵 USDT Price: {bold(money(price))}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Buy Method with Points", callback_data="xmethod:points")],
            [InlineKeyboardButton(text="💵 Buy Method with Balance", callback_data="xmethod:balance")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="x:back")],
        ]),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("xmethod:"))
async def x_method_buy(callback: CallbackQuery) -> None:
    if not await security_check_event(callback):
        return
    pay_type = callback.data.split(":", 1)[1]
    title = str(await get_setting("x_method_title", "X Premium + Grok Method"))
    price = float(await get_setting("x_method_price_usdt", 10.0) or 0)
    points = int(await get_setting("x_method_points", 100) or 0)
    content = str(await get_setting("x_method_content", "Admin has not added the method yet."))
    user = await get_user(callback.from_user.id) or {}
    if pay_type == "points":
        if int(user.get("points", 0) or 0) < points:
            await callback.answer("Not enough points.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id, "points": {"$gte": points}}, {"$inc": {"points": -points}})
    else:
        if float(user.get("balance", 0) or 0) + 1e-9 < price:
            await callback.answer("Not enough balance.", show_alert=True)
            return
        await users.update_one({"user_id": callback.from_user.id, "balance": {"$gte": price}}, {"$inc": {"balance": -price}})
    await callback.message.answer(f"✅ {bold(title)} unlocked.\n\n{escape(content)}")
    await callback.answer("Unlocked")

@router.callback_query(F.data == "xadmin:orders")
async def x_admin_orders(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for order in x_orders.find({"status": "pending"}).sort("created_at", ASCENDING).limit(15):
        found = True
        await callback.message.answer(
            f"🐦 {bold('Pending X + Grok Order')}\n\nOrder ID: {code(str(order['_id']))}\nUser: {code(order.get('user_id'))} @{escape(order.get('username') or 'None')}\nPlan: {bold(order.get('plan_name'))}\nX Username: @{escape(order.get('x_username'))}\nPaid type: {bold(order.get('pay_type'))}",
            reply_markup=x_order_admin_keyboard(str(order["_id"])),
        )
    if not found:
        await callback.message.answer("No pending X + Grok orders.")
    await callback.answer()

@router.callback_query(F.data == "xadmin:payments")
async def x_admin_payments(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    found = False
    async for payment in x_payments.find({"status": "pending"}).sort("created_at", ASCENDING).limit(15):
        found = True
        await callback.message.answer(
            f"💰 {bold('Pending X + Grok Payment')}\n\nPayment ID: {code(str(payment['_id']))}\nUser: {code(payment.get('user_id'))} @{escape(payment.get('username') or 'None')}\nPlan: {bold(payment.get('plan_name'))}\nAmount: {bold(money(payment.get('amount_usdt', 0)))}\nX Username: @{escape(payment.get('x_username'))}\nTXID: {code(payment.get('txid'))}",
            reply_markup=x_payment_admin_keyboard(str(payment["_id"])),
        )
    if not found:
        await callback.message.answer("No pending X + Grok payments.")
    await callback.answer()

@router.callback_query(F.data == "xadmin:settings")
async def x_admin_settings(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    plans = await get_x_plans()
    binance_id = await get_setting("x_binance_id", "")
    text = f"⚙️ {bold('X Settings')}\n\nBinance ID/Wallet: {code(binance_id or 'Not set')}\nReferral reward uses main setting: {bold(await get_setting('referral_reward_points', 1))} points\n\n"
    for k, p in plans.items():
        text += f"{k}: {p.get('name')} — {money(p.get('price_usdt', 0))} / {p.get('points', 0)} pts\n"
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set Binance ID", callback_data="xset:x_binance_id")],
        [InlineKeyboardButton(text="Set 3M USDT", callback_data="xset:x_3m_price"), InlineKeyboardButton(text="Set 3M Points", callback_data="xset:x_3m_points")],
        [InlineKeyboardButton(text="Set 6M USDT", callback_data="xset:x_6m_price"), InlineKeyboardButton(text="Set 6M Points", callback_data="xset:x_6m_points")],
        [InlineKeyboardButton(text="Set 1Y USDT", callback_data="xset:x_1y_price"), InlineKeyboardButton(text="Set 1Y Points", callback_data="xset:x_1y_points")],
        [InlineKeyboardButton(text="Set Referral Points", callback_data="settings:set:referral_reward_points")],
    ]))
    await callback.answer()

@router.callback_query(F.data.startswith("xset:"))
async def x_setting_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(XSettingsState.value)
    await state.update_data(key=callback.data.split(":", 1)[1])
    await callback.message.answer("Send new value.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(XSettingsState.value)
async def x_setting_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    key = data.get("key")
    val = (message.text or "").strip()
    if key == "x_binance_id":
        await set_setting("x_binance_id", val)
    else:
        plans = await get_x_plans()
        mapping = {
            "x_3m_price": ("3m", "price_usdt"), "x_3m_points": ("3m", "points"),
            "x_6m_price": ("6m", "price_usdt"), "x_6m_points": ("6m", "points"),
            "x_1y_price": ("1y", "price_usdt"), "x_1y_points": ("1y", "points"),
        }
        if key not in mapping:
            await message.answer("Unknown setting.", reply_markup=main_menu(message.from_user.id)); await state.clear(); return
        plan_key, field = mapping[key]
        num = parse_float(val) if field == "price_usdt" else parse_int(val)
        if num is None or num < 0:
            await message.answer("Invalid number.", reply_markup=cancel_inline_keyboard())
            return
        plans[plan_key][field] = num
        await set_setting("x_plans", plans)
    await state.clear()
    await message.answer("✅ X setting saved.", reply_markup=main_menu(message.from_user.id))

@router.callback_query(F.data == "xadmin:method")
async def x_admin_method(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await callback.message.answer("📝 X Method Settings", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set Title", callback_data="xmethodset:x_method_title")],
        [InlineKeyboardButton(text="Set USDT Price", callback_data="xmethodset:x_method_price_usdt")],
        [InlineKeyboardButton(text="Set Points Price", callback_data="xmethodset:x_method_points")],
        [InlineKeyboardButton(text="Set Method Content", callback_data="xmethodset:x_method_content")],
    ]))
    await callback.answer()

@router.callback_query(F.data.startswith("xmethodset:"))
async def x_method_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await admin_only_callback(callback):
        return
    await state.set_state(XMethodEditState.value)
    await state.update_data(field=callback.data.split(":", 1)[1])
    await callback.message.answer("Send new value/content.", reply_markup=cancel_inline_keyboard())
    await callback.answer()

@router.message(XMethodEditState.value)
async def x_method_edit_save(message: Message, state: FSMContext) -> None:
    if not await admin_only_message(message):
        return
    data = await state.get_data()
    field = data.get("field")
    val = message.text or ""
    if field == "x_method_price_usdt":
        num = parse_float(val)
        if num is None:
            await message.answer("Invalid price.")
            return
        await set_setting(field, num)
    elif field == "x_method_points":
        num = parse_int(val)
        if num is None:
            await message.answer("Invalid points.")
            return
        await set_setting(field, num)
    elif field in {"x_method_title", "x_method_content"}:
        await set_setting(field, val)
    else:
        await message.answer("Unknown field.")
        return
    await state.clear()
    await message.answer("✅ X method updated.", reply_markup=main_menu(message.from_user.id))

# ==========================================================
# ADMIN BUTTON MANAGER
# ==========================================================

@router.callback_query(F.data == "admin:panel")
async def admin_panel_back_callback(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await callback.message.answer("👑 Admin Panel", reply_markup=admin_menu())
    await callback.answer()

@router.callback_query(F.data == "admin:button_manager")
async def admin_button_manager(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    await load_button_visibility()
    await callback.message.answer(
        "👁 <b>Button Manager</b>\n\n"
        "🟢 = visible to users\n"
        "🔴 = hidden from users\n\n"
        "Admins will still see all buttons so they can manage the bot.",
        reply_markup=button_manager_keyboard(),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("btnvis:toggle:"))
async def admin_button_toggle(callback: CallbackQuery) -> None:
    if not await admin_only_callback(callback):
        return
    key = callback.data.split(":", 2)[2]
    if key not in DEFAULT_BUTTON_VISIBILITY:
        await callback.answer("Unknown button key.", show_alert=True)
        return
    data = normalize_button_visibility(await get_setting("button_visibility", DEFAULT_BUTTON_VISIBILITY))
    data[key] = not bool(data.get(key, True))
    await save_button_visibility(data)
    status = "visible" if data[key] else "hidden"
    await callback.message.answer(
        f"✅ {bold(BUTTON_TITLES.get(key, key))} is now {bold(status)} for users.",
        reply_markup=button_manager_keyboard(),
    )
    await callback.answer("Updated")

# ==========================================================
# START BOT
# ==========================================================
if __name__ == "__main__":
    asyncio.run(main())

# v15 update: Removed FREE CANVA from main reply menu and moved Free Canva inside FREE ITEMS.
