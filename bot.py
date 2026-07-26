import logging
import sqlite3
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_IDS = {123456789}
DB_PATH = "orders.db"

# ---------- GitHub sync config ----------
# Personal access token with 'repo' scope. Keep this secret - use an
# environment variable in production instead of hardcoding it.
GITHUB_TOKEN = "YOUR_GITHUB_TOKEN_HERE"
GITHUB_OWNER = "aaronlngdn-afk"
GITHUB_REPO = "telegram-order-bot"
GITHUB_BRANCH = "main"
GITHUB_ORDERS_DIR = "orders"
GITHUB_API_BASE = "https://api.github.com"
SYNC_ORDERS_TO_GITHUB = True  # set False to disable GitHub syncing

(SELECT_CATEGORY, SELECT_PRODUCT, ENTER_QTY, CART_MENU,
 CHECKOUT_NAME, CHECKOUT_PHONE, CHECKOUT_ADDRESS, CHECKOUT_CONFIRM,
 ADMIN_ADD_NAME, ADMIN_ADD_CATEGORY, ADMIN_ADD_PRICE) = range(11)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE,
            user_id INTEGER NOT NULL,
            username TEXT,
            customer_name TEXT,
            phone TEXT,
            address TEXT,
            status TEXT DEFAULT 'pending',
            total REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            qty INTEGER,
            price REAL,
            FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        """)
        cur = conn.execute("SELECT COUNT(*) c FROM products")
        if cur.fetchone()["c"] == 0:
            sample = [
                ("Margherita Pizza", "Pizza", 8.99),
                ("Pepperoni Pizza", "Pizza", 9.99),
                ("Cheeseburger", "Burgers", 6.50),
                ("Veggie Burger", "Burgers", 6.00),
                ("Coke", "Drinks", 1.50),
                ("Lemonade", "Drinks", 2.00),
            ]
            conn.executemany(
                "INSERT INTO products (name, category, price) VALUES (?,?,?)",
                sample
            )


def generate_order_number(order_id: int) -> str:
    """Human friendly order number, e.g. ORD-20260726-0007"""
    date_part = datetime.utcnow().strftime("%Y%m%d")
    return f"ORD-{date_part}-{order_id:04d}"


def github_request(method, path, payload=None):
    if not GITHUB_TOKEN or GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
        logger.warning("GitHub token not configured, skipping GitHub sync.")
        return None
    url = f"{GITHUB_API_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "telegram-order-bot")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        logger.error(f"GitHub API error {e.code}: {e.read().decode()}")
        return None
    except Exception as e:
        logger.error(f"GitHub API request failed: {e}")
        return None


def push_order_to_github(order_number, order_row, items):
    """Create an organized markdown file for the order in the orders/ folder,
    plus update a running orders/INDEX.md summary table."""
    if not SYNC_ORDERS_TO_GITHUB:
        return

    created = order_row["created_at"][:10]
    file_path = f"{GITHUB_ORDERS_DIR}/{created}/{order_number}.md"

    lines = [
        f"# Order {order_number}",
        "",
        f"- **Status:** {order_row['status']}",
        f"- **Customer:** {order_row['customer_name']}",
        f"- **Phone:** {order_row['phone']}",
        f"- **Address:** {order_row['address']}",
        f"- **Telegram user:** @{order_row['username'] or order_row['user_id']}",
        f"- **Placed at (UTC):** {order_row['created_at']}",
        "",
        "## Items",
        "",
        "| Product | Qty | Price | Subtotal |",
        "|---|---|---|---|",
    ]
    for it in items:
        subtotal = it["qty"] * it["price"]
        lines.append(f"| {it['product_name']} | {it['qty']} | ${it['price']:.2f} | ${subtotal:.2f} |")
    lines.append("")
    lines.append(f"**Total: ${order_row['total']:.2f}**")
    content = "\n".join(lines)

    encoded = base64.b64encode(content.encode()).decode()
    body = {
        "message": f"Add order {order_number}",
        "content": encoded,
        "branch": GITHUB_BRANCH,
    }
    path = f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
    result = github_request("PUT", path, body)
    if result:
        logger.info(f"Order {order_number} pushed to GitHub at {file_path}")
    update_orders_index(order_number, order_row, file_path)


def update_orders_index(order_number, order_row, file_path):
    """Append a row to orders/INDEX.md so all orders are browsable in one place."""
    index_path_api = f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_ORDERS_DIR}/INDEX.md"
    existing = github_request("GET", index_path_api + f"?ref={GITHUB_BRANCH}")

    header = "# Orders Index\n\n| Order # | Date | Customer | Total | Status | Link |\n|---|---|---|---|---|---|\n"
    row = (f"| {order_number} | {order_row['created_at'][:10]} | {order_row['customer_name']} "
           f"| ${order_row['total']:.2f} | {order_row['status']} | [view]({file_path}) |\n")

    if existing and "content" in existing:
        current = base64.b64decode(existing["content"]).decode()
        new_content = current.rstrip("\n") + "\n" + row
        sha = existing["sha"]
    else:
        new_content = header + row
        sha = None

    body = {
        "message": f"Update orders index for {order_number}",
        "content": base64.b64encode(new_content.encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    github_request("PUT", index_path_api, body)


def get_categories():
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM products WHERE active=1"
        ).fetchall()
    return [r["category"] for r in rows]


def get_products(category):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE category=? AND active=1", (category,)
        ).fetchall()
    return rows


def get_product(pid):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE id=?", (pid,)
        ).fetchone()


def cart(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.setdefault("cart", {})


def cart_total(context):
    total = 0.0
    for pid, qty in cart(context).items():
        p = get_product(pid)
        if p:
            total += p["price"] * qty
    return total


def cart_text(context):
    items = cart(context)
    if not items:
        return "Your cart is empty."
    lines = ["Your Cart:"]
    for pid, qty in items.items():
        p = get_product(pid)
        if p:
            lines.append(f"- {p['name']} x{qty} = ${p['price']*qty:.2f}")
    lines.append(f"\nTotal: ${cart_total(context):.2f}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", {})
    keyboard = [
        [InlineKeyboardButton("Browse Menu", callback_data="browse")],
        [InlineKeyboardButton("View Cart", callback_data="view_cart")],
        [InlineKeyboardButton("My Orders", callback_data="my_orders")],
    ]
    await update.message.reply_text(
        "Welcome to OrderBot! What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_CATEGORY


async def start_from_query(query, context):
    keyboard = [
        [InlineKeyboardButton("Browse Menu", callback_data="browse")],
        [InlineKeyboardButton("View Cart", callback_data="view_cart")],
        [InlineKeyboardButton("My Orders", callback_data="my_orders")],
    ]
    await query.edit_message_text("Welcome to OrderBot! What would you like to do?",
                                   reply_markup=InlineKeyboardMarkup(keyboard))


async def browse_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cats = get_categories()
    if not cats:
        await query.edit_message_text("No products available right now.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(c, callback_data=f"cat:{c}")] for c in cats]
    keyboard.append([InlineKeyboardButton("Back", callback_data="back_start")])
    await query.edit_message_text("Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_PRODUCT


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    context.user_data["current_category"] = category
    products = get_products(category)
    keyboard = [
        [InlineKeyboardButton(f"{p['name']} - ${p['price']:.2f}", callback_data=f"prod:{p['id']}")]
        for p in products
    ]
    keyboard.append([InlineKeyboardButton("Back to categories", callback_data="browse")])
    await query.edit_message_text(f"{category} menu:",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_PRODUCT


async def ask_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split(":", 1)[1])
    context.user_data["pending_product"] = pid
    p = get_product(pid)
    keyboard = [
        [InlineKeyboardButton(str(n), callback_data=f"qty:{n}") for n in [1, 2, 3]],
        [InlineKeyboardButton(str(n), callback_data=f"qty:{n}") for n in [4, 5, 6]],
        [InlineKeyboardButton("Back", callback_data=f"cat:{context.user_data['current_category']}")]
    ]
    await query.edit_message_text(
        f"How many {p['name']} (${p['price']:.2f} each)?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ENTER_QTY


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Added to cart!")
    qty = int(query.data.split(":", 1)[1])
    pid = context.user_data["pending_product"]
    c = cart(context)
    c[pid] = c.get(pid, 0) + qty
    p = get_product(pid)
    keyboard = [
        [InlineKeyboardButton("View Cart", callback_data="view_cart")],
        [InlineKeyboardButton("Add more", callback_data="browse")],
        [InlineKeyboardButton("Checkout", callback_data="checkout")],
    ]
    await query.edit_message_text(
        f"Added {qty} x {p['name']} to your cart.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CART_MENU


async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("Add more items", callback_data="browse")],
        [InlineKeyboardButton("Clear cart", callback_data="clear_cart")],
        [InlineKeyboardButton("Checkout", callback_data="checkout")],
        [InlineKeyboardButton("Back", callback_data="back_start")],
    ]
    await query.edit_message_text(cart_text(context),
                                   reply_markup=InlineKeyboardMarkup(keyboard))
    return CART_MENU


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["cart"] = {}
    await query.answer("Cart cleared.")
    await start_from_query(query, context)
    return SELECT_CATEGORY


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start_from_query(query, context)
    return SELECT_CATEGORY
