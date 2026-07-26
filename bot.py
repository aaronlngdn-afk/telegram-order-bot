import logging
import sqlite3
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
    lines = ["\U0001F6D2 Your Cart:"]
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


async def start_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not cart(context):
        await query.edit_message_text("Your cart is empty. Add items first.")
        return SELECT_CATEGORY
    await query.edit_message_text("Let's checkout. Please enter your full name:")
    return CHECKOUT_NAME


async def checkout_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout_name"] = update.message.text
    await update.message.reply_text("Enter your phone number:")
    return CHECKOUT_PHONE


async def checkout_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout_phone"] = update.message.text
    await update.message.reply_text("Enter your delivery address:")
    return CHECKOUT_ADDRESS


async def checkout_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout_address"] = update.message.text
    summary = cart_text(context)
    name = context.user_data["checkout_name"]
    phone = context.user_data["checkout_phone"]
    address = context.user_data["checkout_address"]
    keyboard = [
        [InlineKeyboardButton("Confirm Order", callback_data="confirm_order")],
        [InlineKeyboardButton("Cancel", callback_data="back_start")],
    ]
    text = (f"{summary}\n\nName: {name}\nPhone: {phone}\nAddress: {address}\n\n"
            f"Confirm your order?")
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT_CONFIRM


async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    items = cart(context)
    total = cart_total(context)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, username, customer_name, phone, address, status, total, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user.id, user.username or "", context.user_data["checkout_name"],
             context.user_data["checkout_phone"], context.user_data["checkout_address"],
             "pending", total, datetime.utcnow().isoformat())
        )
        order_id = cur.lastrowid
        for pid, qty in items.items():
            p = get_product(pid)
            if p:
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, product_name, qty, price) "
                    "VALUES (?,?,?,?,?)",
                    (order_id, pid, p["name"], qty, p["price"])
                )
    context.user_data["cart"] = {}
    await query.edit_message_text(
        f"Order #{order_id} placed successfully! Total: ${total:.2f}\n"
        f"We'll contact you soon regarding delivery."
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"New order #{order_id} from {context.user_data.get('checkout_name')} "
                f"({user.username or user.id}) - Total: ${total:.2f}"
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")
    return ConversationHandler.END


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()
    if not rows:
        text = "You have no orders yet."
    else:
        lines = ["Your recent orders:"]
        for r in rows:
            lines.append(f"#{r['id']} - {r['status']} - ${r['total']:.2f} - {r['created_at'][:16]}")
        text = "\n".join(lines)
    keyboard = [[InlineKeyboardButton("Back", callback_data="back_start")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_CATEGORY


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await update.message.reply_text("No orders yet.")
        return
    lines = ["Recent orders:"]
    for r in rows:
        lines.append(
            f"#{r['id']} | {r['status']} | ${r['total']:.2f} | {r['customer_name']} | {r['phone']}"
        )
    await update.message.reply_text("\n".join(lines))


async def admin_setstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setstatus <order_id> <status>")
        return
    order_id, status = context.args[0], context.args[1]
    with db() as conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    await update.message.reply_text(f"Order #{order_id} status updated to {status}.")


async def admin_addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addproduct <name> <category> <price>")
        return
    name, category, price = context.args[0], context.args[1], context.args[2]
    try:
        price = float(price)
    except ValueError:
        await update.message.reply_text("Price must be a number.")
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO products (name, category, price) VALUES (?,?,?)",
            (name, category, price)
        )
    await update.message.reply_text(f"Product '{name}' added to {category} at ${price:.2f}.")


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_CATEGORY: [
                CallbackQueryHandler(browse_categories, pattern="^browse$"),
                CallbackQueryHandler(view_cart, pattern="^view_cart$"),
                CallbackQueryHandler(my_orders, pattern="^my_orders$"),
                CallbackQueryHandler(back_to_start, pattern="^back_start$"),
            ],
            SELECT_PRODUCT: [
                CallbackQueryHandler(show_products, pattern="^cat:"),
                CallbackQueryHandler(ask_quantity, pattern="^prod:"),
                CallbackQueryHandler(browse_categories, pattern="^browse$"),
            ],
            ENTER_QTY: [
                CallbackQueryHandler(add_to_cart, pattern="^qty:"),
                CallbackQueryHandler(show_products, pattern="^cat:"),
            ],
            CART_MENU: [
                CallbackQueryHandler(browse_categories, pattern="^browse$"),
                CallbackQueryHandler(view_cart, pattern="^view_cart$"),
                CallbackQueryHandler(clear_cart, pattern="^clear_cart$"),
                CallbackQueryHandler(start_checkout, pattern="^checkout$"),
                CallbackQueryHandler(back_to_start, pattern="^back_start$"),
            ],
            CHECKOUT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, checkout_name)],
            CHECKOUT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, checkout_phone)],
            CHECKOUT_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, checkout_address)],
            CHECKOUT_CONFIRM: [
                CallbackQueryHandler(confirm_order, pattern="^confirm_order$"),
                CallbackQueryHandler(back_to_start, pattern="^back_start$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("orders", admin_orders))
    app.add_handler(CommandHandler("setstatus", admin_setstatus))
    app.add_handler(CommandHandler("addproduct", admin_addproduct))

    logger.info("Bot started polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
