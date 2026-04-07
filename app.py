from flask import Flask, render_template, request, redirect, url_for, jsonify, make_response
import qrcode
import os
import sqlite3
from datetime import datetime, timedelta
from flask_socketio import SocketIO
import requests
import threading
import shutil
import glob
from functools import wraps
from dotenv import load_dotenv
import re
import logging
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from collections import defaultdict
import uuid
import sys
import io

# ========== ENCODING FIX FOR WINDOWS ==========
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ========== LOAD ENVIRONMENT VARIABLES ==========
load_dotenv(override=True)

# ========== LOGGING SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('restaurant.log', maxBytes=10485760, backupCount=5, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("🚀 Restaurant QR Menu System Starting...")

# ========== APP CONFIGURATION ==========
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-secret-key')

# Make sure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ========== TELEGRAM SETUP ==========
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

if not TELEGRAM_BOT_TOKEN:
    logger.warning("⚠️ TELEGRAM_BOT_TOKEN not found in environment!")

def send_telegram_message(message):
    """Send a notification to your Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

# ========== DATABASE CONTEXT MANAGER ==========
@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect('orders.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# ========== DATABASE SETUP ==========
def init_db():
    with get_db() as conn:
        c = conn.cursor()
        
        # Menu items table
        c.execute('''CREATE TABLE IF NOT EXISTS menu_items
                     (id INTEGER PRIMARY KEY,
                      name TEXT,
                      price REAL,
                      description TEXT,
                      image TEXT,
                      category TEXT)''')
        
        # Orders table
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            item_id INTEGER,
            item_name TEXT,
            quantity INTEGER,
            customer_name TEXT,
            table_number TEXT,
            status TEXT,
            order_time TEXT,
            completed_time TEXT,
            discount_code TEXT,
            original_total REAL,
            discount_amount REAL,
            final_total REAL,
            payment_method TEXT,
            transaction_id TEXT,
            payment_status TEXT,
            telegram_username TEXT
        )''')

        # MIGRATION: Ensure all columns exist for existing databases
        try:
            c.execute("PRAGMA table_info(orders)")
            columns = [col[1] for col in c.fetchall()]
            required_columns = {
                'telegram_username': 'TEXT',
                'payment_method': 'TEXT',
                'transaction_id': 'TEXT',
                'payment_status': 'TEXT',
                'discount_code': 'TEXT',
                'original_total': 'REAL',
                'discount_amount': 'REAL',
                'final_total': 'REAL'
            }
            for col_name, col_type in required_columns.items():
                if col_name not in columns:
                    c.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Migration: Added {col_name} column to orders table")
            
            # Migration for bookings table
            c.execute("PRAGMA table_info(bookings)")
            booking_columns = [col[1] for col in c.fetchall()]
            if 'telegram_username' not in booking_columns:
                c.execute("ALTER TABLE bookings ADD COLUMN telegram_username TEXT")
                logger.info("Migration: Added telegram_username column to bookings table")
        except Exception as e:
            logger.error(f"Migration error: {e}")
        
        # Discount codes table
        c.execute('''CREATE TABLE IF NOT EXISTS discount_codes
                     (id INTEGER PRIMARY KEY,
                      code TEXT UNIQUE,
                      discount_type TEXT,
                      discount_value REAL,
                      valid_from TEXT,
                      valid_until TEXT,
                      usage_limit INTEGER,
                      times_used INTEGER,
                      is_active INTEGER,
                      description TEXT)''')
        
        # Insert example discount codes
        c.execute("SELECT COUNT(*) FROM discount_codes")
        if c.fetchone()[0] == 0:
            example_codes = [
                ("WELCOME10", "percentage", 10, "2024-01-01", "2025-12-31", 100, 0, 1, "10% off your first order"),
                ("SAVE5", "fixed", 5, "2024-01-01", "2025-12-31", 50, 0, 1, "$5 off any order"),
                ("HAPPYHOUR", "percentage", 15, "2024-01-01", "2025-12-31", 200, 0, 1, "15% off happy hour special"),
            ]
            for code in example_codes:
                c.execute("""INSERT INTO discount_codes 
                            (code, discount_type, discount_value, valid_from, valid_until, 
                             usage_limit, times_used, is_active, description)
                            VALUES (?,?,?,?,?,?,?,?,?)""", code)
        
        # Telegram users table
        c.execute('''CREATE TABLE IF NOT EXISTS telegram_users
                     (chat_id INTEGER PRIMARY KEY,
                      username TEXT,
                      first_name TEXT,
                      registered_at TEXT)''')
        
        # Restaurant settings table
        c.execute('''CREATE TABLE IF NOT EXISTS restaurant_settings
                     (id INTEGER PRIMARY KEY,
                      restaurant_name TEXT,
                      opening_hours TEXT,
                      phone TEXT,
                      address TEXT,
                      logo_color TEXT,
                      accent_color TEXT)''')
        # Add to init_db() function - Booking tables
        c.execute('''CREATE TABLE IF NOT EXISTS bookings
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name TEXT,
                    customer_phone TEXT,
                    customer_email TEXT,
                    booking_date TEXT,
                    booking_time TEXT,
                    party_size INTEGER,
                    table_number INTEGER,
                    special_requests TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS tables
                    (id INTEGER PRIMARY KEY,
                    table_number INTEGER,
                    capacity INTEGER,
                    is_active INTEGER)''')

        # Insert default tables (1-10)
        c.execute("SELECT COUNT(*) FROM tables")
        if c.fetchone()[0] == 0:
            for i in range(1, 11):
                capacity = 4 if i <= 8 else 6  # Tables 1-8 seat 4, 9-10 seat 6
                c.execute("INSERT INTO tables (table_number, capacity, is_active) VALUES (?,?,1)", (i, capacity))
                
                c.execute("SELECT COUNT(*) FROM restaurant_settings")
                if c.fetchone()[0] == 0:
                    c.execute("""INSERT INTO restaurant_settings 
                                (restaurant_name, opening_hours, phone, address, logo_color, accent_color) 
                                VALUES (?,?,?,?,?,?)""",
                            ("My Restaurant", "Mon-Sun: 11am - 10pm", "+1 234 567 890", "123 Main Street", "#ff6b35", "#28a745"))
                
        
        conn.commit()
        logger.info("Database initialized successfully")
        
init_db()

# ========== RATE LIMITING ==========
last_order_time = defaultdict(lambda: None)

def check_rate_limit(customer_name, min_seconds=30):
    """Prevent customers from ordering too frequently"""
    if last_order_time[customer_name]:
        time_diff = datetime.now() - last_order_time[customer_name]
        if time_diff.total_seconds() < min_seconds:
            wait_seconds = min_seconds - time_diff.total_seconds()
            return False, f"Please wait {int(wait_seconds)} seconds before ordering again"
    return True, "OK"

# ========== INPUT VALIDATION ==========
def validate_customer_name(name):
    """Check if customer name is valid"""
    if not name or len(name) < 2:
        return False, "Name must be at least 2 characters"
    if len(name) > 50:
        return False, "Name is too long"
    if not re.match(r"^[a-zA-Z0-9\s\-']+$", name):
        return False, "Name contains invalid characters"
    return True, name.strip()

def validate_table_number(table):
    """Check if table number is valid"""
    try:
        table_num = int(table)
        if 1 <= table_num <= 50:
            return True, table_num
        return False, "Table number must be between 1 and 50"
    except:
        return False, "Invalid table number"

# ========== LOGIN SYSTEM ==========
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
login_attempts = {}

def check_session_timeout(f):
    """Auto logout after 30 minutes of inactivity"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        login_time = request.cookies.get('login_time')
        if login_time:
            try:
                login_dt = datetime.strptime(login_time, '%Y-%m-%d %H:%M:%S')
                time_diff = (datetime.now() - login_dt).total_seconds()
                if time_diff > 1800:
                    resp = make_response(redirect(url_for('login')))
                    resp.set_cookie('logged_in', '', expires=0)
                    resp.set_cookie('login_time', '', expires=0)
                    return resp
            except:
                pass
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    """Decorator to protect admin pages"""
    @wraps(f)
    @check_session_timeout
    def decorated_function(*args, **kwargs):
        if request.cookies.get('logged_in') != 'true':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.cookies.get('logged_in') == 'true':
        return redirect(url_for('admin'))
    
    ip = request.remote_addr
    
    if ip in login_attempts and login_attempts[ip] >= 5:
        last_attempt = login_attempts.get(f'{ip}_time')
        if last_attempt:
            wait_time = 300
            if (datetime.now() - last_attempt).total_seconds() < wait_time:
                remaining_time = int(wait_time - (datetime.now() - last_attempt).total_seconds())
                return render_template('login.html', error=f"Too many attempts. Wait {remaining_time // 60} minutes.")
    
    if request.method == 'POST':
        password = request.form.get('password')
        
        if password == ADMIN_PASSWORD:
            login_attempts[ip] = 0
            resp = make_response(redirect(url_for('admin')))
            resp.set_cookie('logged_in', 'true', max_age=28800)
            resp.set_cookie('login_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            logger.info(f"Successful login from {ip}")
            return resp
        else:
            login_attempts[ip] = login_attempts.get(ip, 0) + 1
            login_attempts[f'{ip}_time'] = datetime.now()
            logger.warning(f"Failed login attempt from {ip}")
            return render_template('login.html', error="❌ Wrong password! Please try again.")
    
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    resp = make_response(redirect(url_for('login')))
    resp.set_cookie('logged_in', '', expires=0)
    resp.set_cookie('login_time', '', expires=0)
    return resp

# ========== BACKUP SYSTEM ==========
def ensure_backup_folder():
    if not os.path.exists('backups'):
        os.makedirs('backups')

def cleanup_old_backups(keep_count=10):
    backup_files = sorted(glob.glob('backups/backup_*.db'))
    auto_backups = sorted(glob.glob('backups/auto_backup_*.db'))
    all_backups = backup_files + auto_backups
    all_backups.sort(reverse=True)
    
    for old_backup in all_backups[keep_count:]:
        try:
            os.remove(old_backup)
            logger.info(f"Deleted old backup: {old_backup}")
        except Exception as e:
            logger.error(f"Could not delete {old_backup}: {e}")

@app.route('/backup')
@login_required
def backup_page():
    ensure_backup_folder()
    backup_files = glob.glob('backups/backup_*.db')
    backups = []
    
    for file in backup_files:
        filename = os.path.basename(file)
        size = os.path.getsize(file) / 1024
        modified = datetime.fromtimestamp(os.path.getmtime(file))
        backups.append({
            'filename': filename,
            'size': round(size, 1),
            'date': modified.strftime('%Y-%m-%d %H:%M:%S'),
        })
    
    backups.sort(key=lambda x: x['date'], reverse=True)
    return render_template('backup.html', backups=backups)

@app.route('/create_backup')
@login_required
def create_backup():
    ensure_backup_folder()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f'backups/backup_{timestamp}.db'
    
    try:
        shutil.copy2('orders.db', backup_filename)
        cleanup_old_backups(keep_count=10)
        logger.info(f"Backup created: {backup_filename}")
        return redirect(url_for('backup_page'))
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return f"Backup failed: {e}"

@app.route('/restore_backup/<filename>')
@login_required
def restore_backup(filename):
    backup_path = f'backups/{filename}'
    if not os.path.exists(backup_path):
        return "Backup file not found!"
    
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        emergency_backup = f'backups/emergency_before_restore_{timestamp}.db'
        shutil.copy2('orders.db', emergency_backup)
        shutil.copy2(backup_path, 'orders.db')
        logger.info(f"Restored from backup: {filename}")
        return redirect(url_for('backup_page'))
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return f"Restore failed: {e}"

@app.route('/delete_backup/<filename>')
@login_required
def delete_backup(filename):
    backup_path = f'backups/{filename}'
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
            logger.info(f"Deleted backup: {filename}")
    except Exception as e:
        logger.error(f"Could not delete backup: {e}")
    return redirect(url_for('backup_page'))

@app.route('/auto_backup')
@login_required
def auto_backup():
    ensure_backup_folder()
    timestamp = datetime.now().strftime('%Y%m%d')
    backup_filename = f'backups/auto_backup_{timestamp}.db'
    
    if not os.path.exists(backup_filename):
        shutil.copy2('orders.db', backup_filename)
        logger.info(f"Auto-backup created: {backup_filename}")
    
    return "Auto-backup completed"

@app.route('/check_admin_password')
def check_admin_password():
    is_default = (ADMIN_PASSWORD == 'admin123')
    return jsonify({'is_default': is_default})

# ========== RESTAURANT ADMIN PAGES ==========
@app.route('/')
def home():
    return redirect(url_for('admin'))

@app.route('/admin')
@login_required
def admin():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM menu_items")
        items = c.fetchall()
    items_list = [list(item) for item in items]
    return render_template('admin.html', items=items_list)

@app.route('/add_item', methods=['POST'])
@login_required
def add_item():
    name = request.form['name']
    price = float(request.form['price'])
    description = request.form['description']
    category = request.form.get('category', 'Main')
    
    image = request.files['image']
    if image and image.filename:
        image_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image.filename}"
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
    else:
        image_filename = 'default.jpg'
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO menu_items (name, price, description, image, category) VALUES (?,?,?,?,?)",
                  (name, price, description, image_filename, category))
        conn.commit()
    
    logger.info(f"Menu item added: {name}")
    return redirect(url_for('admin'))

@app.route('/delete_item/<int:item_id>')
@login_required
def delete_item(item_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
        conn.commit()
    logger.info(f"Menu item deleted: {item_id}")
    return redirect(url_for('admin'))

# ========== CUSTOMER MENU ==========
@app.route('/menu')
def menu():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM menu_items")
        items = c.fetchall()
    return render_template('menu.html', items=[list(item) for item in items])

@app.route('/get_menu_api')
def get_menu_api():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM menu_items")
        items = c.fetchall()
    return jsonify([list(item) for item in items])

# ========== PLACE ORDER ==========

@app.route('/place_order', methods=['POST'])
def place_order():
    try:
        data = request.json
        customer_name = data.get('customer_name')
        table_number = data.get('table_number', 'Not specified')
        items = data.get('items', [])
        discount_code = data.get('discount_code')
        discount_amount = data.get('discount_amount', 0)
        original_total = data.get('original_total', 0)
        final_total = data.get('final_total', 0)
        telegram_username = data.get('telegram_username', '')
        
        logger.info(f"Placing order for: {customer_name}, Total: ${final_total}")
        
        if not customer_name or len(customer_name) < 2:
            return jsonify({'success': False, 'error': 'Please enter a valid name'}), 400
        
        if not table_number or table_number == '':
            return jsonify({'success': False, 'error': 'Please select a table number'}), 400
        
        if not items or len(items) == 0:
            return jsonify({'success': False, 'error': 'Cart is empty'}), 400
        
        order_id = str(uuid.uuid4())[:8].upper()
        
        with get_db() as conn:
            c = conn.cursor()
            
            for item in items:
                c.execute("""INSERT INTO "orders" 
                            (item_id, item_name, quantity, customer_name, table_number, 
                             status, order_time, discount_code, original_total, discount_amount, 
                             final_total, payment_status, telegram_username)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (item['id'], item['name'], item['quantity'], 
                           customer_name, str(table_number), 'pending_payment', 
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           discount_code, original_total, discount_amount, final_total,
                           'waiting_for_payment', telegram_username))
            
            conn.commit()
            logger.info(f"Order #{order_id} inserted successfully")
        
        # Send notification to kitchen
        def send_notification():
            message = f"""
🆕 NEW ORDER AWAITING PAYMENT!
━━━━━━━━━━━━━━━━
👤 Customer: {customer_name}
📋 Table: {table_number}
💰 Amount: ${final_total:.2f}
🆔 Order ID: {order_id}
"""
            if discount_code:
                message += f"🏷️ Discount: {discount_code} (-${discount_amount:.2f})\n"
            send_telegram_message(message)
        
        threading.Thread(target=send_notification).start()
        
        # Send confirmation to customer if they have Telegram
        if telegram_username:
            chat_id = get_chat_id_by_username(telegram_username)
            if chat_id:
                def send_customer_message():
                    msg = f"""
🎉 <b>Order Received!</b>
━━━━━━━━━━━━━━━━
Thank you {customer_name}!

📋 Table: {table_number}
💰 Total: ${final_total:.2f}
🆔 Order ID: {order_id}

⏱️ We'll notify you when your order is ready!

Thank you for ordering! 🍽️
                    """
                    send_telegram_message_to_chat(chat_id, msg)
                
                threading.Thread(target=send_customer_message).start()
                logger.info(f"Sent confirmation to Telegram user: {telegram_username}")
        
        return jsonify({
            'success': True, 
            'order_id': order_id,
            'customer_name': customer_name,
            'table_number': table_number,
            'final_total': final_total,
            'items': items,
            'discount_code': discount_code,
            'discount_amount': discount_amount
        })
    
    except Exception as e:
        logger.error(f"Order error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
      
# ========== CONFIRM PAYMENT ==========
@app.route('/confirm_payment', methods=['POST'])
def confirm_payment():
    try:
        data = request.json
        customer_name = data.get('customer_name')
        payment_method = data.get('payment_method')
        transaction_id = data.get('transaction_id', '')
        
        logger.info(f"Confirming payment for: {customer_name} via {payment_method}")
        
        with get_db() as conn:
            c = conn.cursor()
            
            c.execute("""
                SELECT id, final_total 
                FROM "orders" 
                WHERE customer_name = ? AND status = 'pending_payment'
                ORDER BY order_time DESC 
                LIMIT 1
            """, (customer_name,))
            
            result = c.fetchone()
            
            if not result:
                return jsonify({'success': False, 'error': 'No pending order found. Please place an order first.'}), 404
            
            order_id = result[0]
            order_total = result[1]
            
            c.execute("""
                UPDATE "orders" 
                SET status = 'pending', 
                    payment_method = ?, 
                    transaction_id = ?, 
                    payment_status = 'paid'
                WHERE id = ?
            """, (payment_method, transaction_id, order_id))
            
            conn.commit()
            logger.info(f"Successfully updated order #{order_id} to paid status")
        
        def send_notification():
            message = f"""
✅ PAYMENT CONFIRMED!
━━━━━━━━━━━━━━━━
👤 Customer: {customer_name}
💳 Paid via: {payment_method}
💰 Amount: ${order_total:.2f}
🆔 Transaction: {transaction_id or 'Not provided'}

Kitchen: Start preparing the order!
"""
            send_telegram_message(message)
        
        threading.Thread(target=send_notification).start()
        
        return jsonify({'success': True, 'message': 'Payment confirmed! Kitchen will start preparing your order.'})
    
    except Exception as e:
        logger.error(f"Payment confirmation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== PAYMENT PAGE ==========
@app.route('/payment')
def payment_page():
    return render_template('payment.html')

# ========== DASHBOARD ==========
@app.route('/dashboard')
@login_required
def dashboard():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM "orders" ORDER BY order_time DESC')
        orders = c.fetchall()
    return render_template('dashboard.html', orders=[list(order) for order in orders])

@app.route('/get_orders_api')
def get_orders_api():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM "orders" ORDER BY order_time DESC')
        orders = c.fetchall()
    return jsonify([list(order) for order in orders])
# 
@app.route('/update_order/<int:order_id>')
@login_required
def update_order(order_id):
    with get_db() as conn:
        c = conn.cursor()
        # Get customer info including telegram username
        c.execute('SELECT customer_name, table_number, item_name, quantity, telegram_username FROM "orders" WHERE id=?', (order_id,))
        order = c.fetchone()
        
        if not order:
            return redirect(url_for('dashboard'))

        telegram_username = order[4]
        
        completed_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('UPDATE "orders" SET status="completed", completed_time=? WHERE id=?', (completed_time, order_id))
        conn.commit()
    
    # Send notification to kitchen
    def send_notification():
        message = f"""
✅ ORDER COMPLETED!
━━━━━━━━━━━━━━━━
Customer: {order[0]}
Table: {order[1]}
Item: {order[2]} x{order[3]}
The order is ready for pickup!
        """
        send_telegram_message(message)
    
    threading.Thread(target=send_notification).start()
    
    # ========== NEW: Send READY notification to CUSTOMER ==========
    if telegram_username:
        chat_id = get_chat_id_by_username(telegram_username)
        if chat_id:
            def send_ready():
                msg = f"""
✅ <b>Your Order is Ready!</b>
━━━━━━━━━━━━━━━━
Dear {order[0]},

Your food is ready for pickup at the counter!

📋 Table: {order[1]}

Thank you for your patience! 🍽️
                """
                send_telegram_message_to_chat(chat_id, msg)
            
            threading.Thread(target=send_ready).start()
            logger.info(f"Sent ready notification to Telegram user: {telegram_username}")
    
    logger.info(f"Order {order_id} marked as completed")
    return redirect(url_for('dashboard'))

# ========== QR CODE ==========
@app.route('/generate_qr')
def generate_qr():
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    menu_url = f"http://{local_ip}:5000/menu"
    
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(menu_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img.save('static/qrcode.png')
    big_qr = qrcode.make(menu_url)
    big_qr.save('static/qrcode_big.png')
    
    return render_template('qr_code.html', menu_url=menu_url)

# ========== SALES REPORT ==========
@app.route('/sales_report')
@login_required
def sales_report():
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute("""
            SELECT COALESCE(SUM(final_total), 0), 
                   COUNT(DISTINCT id), 
                   COALESCE(SUM(quantity), 0)
            FROM orders 
            WHERE status='completed' 
            AND DATE(order_time) BETWEEN ? AND ?
        """, (start_date, end_date))
        result = c.fetchone()
        total_sales = float(result[0]) if result[0] else 0
        total_orders = int(result[1]) if result[1] else 0
        total_items_sold = int(result[2]) if result[2] else 0
        
        avg_order_value = total_sales / total_orders if total_orders > 0 else 0
        
        c.execute("""
            SELECT item_name, SUM(quantity) as total 
            FROM orders 
            WHERE status='completed'
            AND DATE(order_time) BETWEEN ? AND ?
            GROUP BY item_name 
            ORDER BY total DESC 
            LIMIT 5
        """, (start_date, end_date))
        popular_rows = c.fetchall()
        popular_items = [[row[0], int(row[1])] for row in popular_rows]
        
        daily_sales = []
        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            c.execute("""
                SELECT COALESCE(SUM(final_total), 0)
                FROM orders 
                WHERE status='completed' 
                AND DATE(order_time) = ?
            """, (date,))
            revenue = c.fetchone()[0]
            daily_sales.append({'date': date, 'revenue': float(revenue) if revenue else 0})
        
        hourly_sales = []
        for hour in range(24):
            c.execute("""
                SELECT COUNT(*)
                FROM orders 
                WHERE status='completed'
                AND CAST(strftime('%H', order_time) AS INTEGER) = ?
                AND DATE(order_time) BETWEEN ? AND ?
            """, (hour, start_date, end_date))
            orders_count = c.fetchone()[0]
            hourly_sales.append({'hour': hour, 'orders': int(orders_count) if orders_count else 0})
        
        c.execute("""
            SELECT COALESCE(m.category, 'Other'), COALESCE(SUM(o.final_total), 0)
            FROM orders o
            LEFT JOIN menu_items m ON o.item_id = m.id
            WHERE o.status='completed'
            AND DATE(o.order_time) BETWEEN ? AND ?
            GROUP BY m.category
        """, (start_date, end_date))
        category_rows = c.fetchall()
        category_sales = [{'category': row[0] or 'Other', 'revenue': float(row[1]) if row[1] else 0} for row in category_rows]
    
    return render_template('report.html',
                         total_sales=total_sales,
                         total_orders=total_orders,
                         total_items_sold=total_items_sold,
                         avg_order_value=avg_order_value,
                         popular_items=popular_items,
                         daily_sales=daily_sales,
                         hourly_sales=hourly_sales,
                         category_sales=category_sales,
                         start_date=start_date,
                         end_date=end_date)

@app.route('/get_sales_trends')
@login_required
def get_sales_trends():
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute("SELECT COALESCE(SUM(final_total), 0) FROM orders WHERE status='completed' AND DATE(order_time)=?", (today,))
        today_revenue = float(c.fetchone()[0] or 0)
        
        c.execute("SELECT COALESCE(SUM(final_total), 0) FROM orders WHERE status='completed' AND DATE(order_time)=?", (yesterday,))
        yesterday_revenue = float(c.fetchone()[0] or 0)
        
        c.execute("SELECT COUNT(*) FROM orders WHERE status='completed' AND DATE(order_time)=?", (today,))
        today_orders = int(c.fetchone()[0] or 0)
        
        c.execute("SELECT COUNT(*) FROM orders WHERE status='completed' AND DATE(order_time)=?", (yesterday,))
        yesterday_orders = int(c.fetchone()[0] or 0)
        
        revenue_change = 0
        if yesterday_revenue > 0:
            revenue_change = round(((today_revenue - yesterday_revenue) / yesterday_revenue) * 100, 1)
        
        orders_change = 0
        if yesterday_orders > 0:
            orders_change = round(((today_orders - yesterday_orders) / yesterday_orders) * 100, 1)
    
    return jsonify({
        'revenue_change': revenue_change,
        'orders_change': orders_change,
        'today_revenue': today_revenue,
        'yesterday_revenue': yesterday_revenue
    })

# ========== RESTAURANT SETTINGS ==========
@app.route('/settings')
@login_required
def settings():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM restaurant_settings WHERE id=1")
        settings = c.fetchone()
    return render_template('settings.html', settings=settings)

@app.route('/save_settings', methods=['POST'])
@login_required
def save_settings():
    restaurant_name = request.form['restaurant_name']
    opening_hours = request.form['opening_hours']
    phone = request.form['phone']
    address = request.form['address']
    logo_color = request.form['logo_color']
    accent_color = request.form['accent_color']
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""UPDATE restaurant_settings 
                     SET restaurant_name=?, opening_hours=?, phone=?, address=?, logo_color=?, accent_color=?
                     WHERE id=1""",
                  (restaurant_name, opening_hours, phone, address, logo_color, accent_color))
        conn.commit()
    
    return redirect(url_for('settings', saved=1))

@app.route('/get_restaurant_info')
def get_restaurant_info():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT restaurant_name, opening_hours, phone, address, logo_color, accent_color FROM restaurant_settings WHERE id=1")
        settings = c.fetchone()
    
    if settings:
        return jsonify({
            'restaurant_name': settings[0],
            'opening_hours': settings[1],
            'phone': settings[2],
            'address': settings[3],
            'logo_color': settings[4],
            'accent_color': settings[5]
        })
    return jsonify({'error': 'No settings found'})

# ========== WAIT TIME ESTIMATOR ==========
@app.route('/get_wait_time')
def get_wait_time():
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
            pending_count = c.fetchone()[0]
            
            PREP_TIME_PER_ITEM = {'Burger': 8, 'Pizza': 12, 'Pasta': 10, 'Salad': 5, 'Drink': 2, 'Fries': 6, 'Dessert': 4, 'default': 7}
            
            c.execute("SELECT item_name, quantity FROM orders WHERE status='pending'")
            pending_orders = c.fetchall()
            
            total_prep_minutes = 0
            for order in pending_orders:
                prep_time = PREP_TIME_PER_ITEM.get(order[0], PREP_TIME_PER_ITEM['default'])
                total_prep_minutes += prep_time * order[1]
            
            if pending_count == 0:
                wait_minutes = 5
                estimated_time = "5-10 minutes"
                description = "No orders ahead of you!"
            else:
                wait_minutes = max(5, total_prep_minutes // 2)
                if wait_minutes < 10:
                    estimated_time = f"{wait_minutes}-{wait_minutes + 5} minutes"
                elif wait_minutes < 20:
                    estimated_time = f"{wait_minutes}-{wait_minutes + 10} minutes"
                else:
                    estimated_time = f"{wait_minutes}-{wait_minutes + 15} minutes"
                description = f"{pending_count} order(s) ahead of you"
        
        if pending_count < 3:
            confidence = "High 🎯"
        elif pending_count < 8:
            confidence = "Medium 📊"
        else:
            confidence = "Low ⚠️ (busy hour)"
        
        return jsonify({
            'wait_minutes': wait_minutes,
            'estimated_time': estimated_time,
            'pending_orders': pending_count,
            'description': description,
            'confidence': confidence,
            'avg_prep_time': 10
        })
    
    except Exception as e:
        logger.error(f"Wait time error: {e}")
        return jsonify({'wait_minutes': 15, 'estimated_time': '15-20 minutes', 'pending_orders': 0, 'description': 'Kitchen is preparing your order', 'confidence': 'Medium', 'avg_prep_time': 10})

# ========== DISCOUNT CODE SYSTEM ==========
@app.route('/discounts')
@login_required
def discounts():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM discount_codes ORDER BY id DESC")
        codes = c.fetchall()
    return render_template('discounts.html', codes=[list(code) for code in codes])

@app.route('/add_discount', methods=['POST'])
@login_required
def add_discount():
    code = request.form['code'].upper()
    discount_type = request.form['discount_type']
    discount_value = float(request.form['discount_value'])
    valid_from = request.form['valid_from']
    valid_until = request.form['valid_until']
    usage_limit = int(request.form['usage_limit'])
    description = request.form['description']
    
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT INTO discount_codes 
                        (code, discount_type, discount_value, valid_from, valid_until, 
                         usage_limit, times_used, is_active, description)
                        VALUES (?,?,?,?,?,?,0,1,?)""",
                      (code, discount_type, discount_value, valid_from, valid_until, usage_limit, description))
            conn.commit()
        logger.info(f"Discount code added: {code}")
        return redirect(url_for('discounts', message="✅ Discount code added successfully!"))
    except sqlite3.IntegrityError:
        return redirect(url_for('discounts', message="❌ That code already exists!"))

@app.route('/toggle_discount/<int:discount_id>')
@login_required
def toggle_discount(discount_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT is_active FROM discount_codes WHERE id=?", (discount_id,))
        current = c.fetchone()
        new_status = 0 if current[0] == 1 else 1
        c.execute("UPDATE discount_codes SET is_active=? WHERE id=?", (new_status, discount_id))
        conn.commit()
    return redirect(url_for('discounts'))

@app.route('/delete_discount/<int:discount_id>')
@login_required
def delete_discount(discount_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM discount_codes WHERE id=?", (discount_id,))
        conn.commit()
    return redirect(url_for('discounts'))

@app.route('/validate_discount', methods=['POST'])
def validate_discount():
    data = request.json
    code = data.get('code', '').upper()
    cart_total = data.get('total', 0)
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""SELECT discount_type, discount_value, valid_from, valid_until, 
                            usage_limit, times_used, is_active 
                     FROM discount_codes WHERE code=?""", (code,))
        result = c.fetchone()
    
    if not result:
        return jsonify({'valid': False, 'message': 'Invalid discount code'})
    
    discount_type, discount_value, valid_from, valid_until, usage_limit, times_used, is_active = result
    
    if not is_active:
        return jsonify({'valid': False, 'message': 'This discount code is not active'})
    
    today = datetime.now().strftime('%Y-%m-%d')
    if valid_from and today < valid_from:
        return jsonify({'valid': False, 'message': f'This code is not valid until {valid_from}'})
    if valid_until and today > valid_until:
        return jsonify({'valid': False, 'message': f'This code expired on {valid_until}'})
    
    if usage_limit and times_used >= usage_limit:
        return jsonify({'valid': False, 'message': 'This discount code has reached its usage limit'})
    
    if discount_type == 'percentage':
        discount_amount = (discount_value / 100) * cart_total
        discount_amount = min(discount_amount, cart_total)
        display_text = f"{discount_value}% off (${discount_amount:.2f})"
    else:
        discount_amount = min(discount_value, cart_total)
        display_text = f"${discount_value} off (${discount_amount:.2f})"
    
    return jsonify({
        'valid': True,
        'discount_amount': round(discount_amount, 2),
        'discount_type': discount_type,
        'discount_value': discount_value,
        'message': f'✅ {display_text} applied!',
        'new_total': round(cart_total - discount_amount, 2)
    })

@app.route('/record_discount_usage', methods=['POST'])
def record_discount_usage():
    data = request.json
    code = data.get('code', '').upper()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE discount_codes SET times_used = times_used + 1 WHERE code=?", (code,))
        conn.commit()
    return jsonify({'success': True})

# ========== PRINT MENU ==========
@app.route('/print_menu')
def print_menu():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM menu_items")
        items = c.fetchall()
    
    html = '''
    <html>
    <head>
        <style>
            body { font-family: Arial; padding: 20px; }
            .item { border: 1px solid #ddd; margin: 10px; padding: 10px; display: inline-block; width: 200px; }
            img { width: 100%; height: 150px; object-fit: cover; }
            @media print { button { display: none; } }
        </style>
    </head>
    <body>
        <h1>Restaurant Menu</h1>
        <a href="/admin" style="background: blue; color: white; padding: 10px 20px; text-decoration: none; display: inline-block; margin-bottom: 10px;">← Back to Admin</a>
        <button onclick="window.print()">🖨️ Print Menu</button>
        <div style="display: flex; flex-wrap: wrap;">
    '''
    
    for item in items:
        html += f'''
        <div class="item">
            <img src="/static/uploads/{item[4]}" onerror="this.src='https://via.placeholder.com/150'">
            <h3>{item[1]}</h3>
            <p>{item[3]}</p>
            <p style="color: green; font-size: 20px;">${item[2]}</p>
        </div>
        '''
    
    html += '</div></body></html>'
    return html

# ========== TELEGRAM WEBHOOK HANDLER ==========
@app.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    """Handle incoming Telegram messages from customers"""
    print(f"DEBUG: Webhook received request for {request.path}")
    
    # Optional: Verify if the token matches (security)
    # The URL no longer contains the token for stability
    
    try:
        data = request.get_json()
        
        if 'message' in data:
            message = data['message']
            chat_id = message['chat']['id']
            text = message.get('text', '')
            username = message['from'].get('username', '')
            
            if text == '/start':
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute('''
                        INSERT OR REPLACE INTO telegram_users (chat_id, username, registered_at)
                        VALUES (?, ?, ?)
                    ''', (chat_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                
                send_telegram_message_to_chat(chat_id, f"""
🎉 Welcome to our restaurant!

To receive order updates:
1. Place an order on our website
2. Enter your Telegram username: @{username}

We'll notify you when your order is ready!

Thank you! 🍽️
                """)
        
        return jsonify({'ok': True})
    
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'ok': False}), 500

def get_chat_id_by_username(username):
    """Get stored chat_id for a Telegram username"""
    if not username:
        return None
    username = username.replace('@', '')
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id FROM telegram_users WHERE username = ?", (username,))
        result = c.fetchone()
        return result[0] if result else None

def send_telegram_message_to_chat(chat_id, message):
    """Send message to a specific chat_id"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        print(f"Error sending to chat {chat_id}: {e}")
        return False

# ========== ERROR HANDLERS ==========
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error_code=404, message="Page not found. Check the URL and try again."), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Server error: {e}")
    return render_template('error.html', error_code=500, message="Something went wrong. Our team has been notified."), 500

@app.errorhandler(Exception)
def handle_all_errors(e):
    logger.error(f"Unexpected error: {e}")
    return render_template('error.html', error_code=500, message="An unexpected error occurred."), 500

# 
# ========== BOOKING SYSTEM ==========

@app.route('/booking')
def booking_page():
    """Show booking form for customers"""
    return render_template('booking.html')

@app.route('/get_available_times')
def get_available_times():
    """Get available time slots for a specific date"""
    booking_date = request.args.get('date')
    party_size = int(request.args.get('party_size', 2))
    
    if not booking_date:
        return jsonify({'error': 'No date provided'}), 400
    
    # Define time slots
    time_slots = [
        '11:00', '11:30', '12:00', '12:30', '13:00', '13:30',
        '14:00', '17:00', '17:30', '18:00', '18:30', '19:00', '19:30', '20:00'
    ]
    
    with get_db() as conn:
        c = conn.cursor()
        
        # Get all tables that can accommodate the party size
        c.execute("SELECT table_number FROM tables WHERE capacity >= ? AND is_active = 1", (party_size,))
        available_tables = [row[0] for row in c.fetchall()]
        
        if not available_tables:
            return jsonify({'error': 'No tables available for this party size'}), 400
        
        # Get existing bookings for this date
        c.execute("""
            SELECT booking_time, table_number 
            FROM bookings 
            WHERE booking_date = ? AND status IN ('confirmed', 'pending')
        """, (booking_date,))
        existing_bookings = c.fetchall()
        
        # Count bookings per time slot
        booked_counts = {}
        for booking in existing_bookings:
            time_slot = booking[0]
            if time_slot not in booked_counts:
                booked_counts[time_slot] = []
            booked_counts[time_slot].append(booking[1])
        
        # Determine available slots
        available_slots = []
        for slot in time_slots:
            booked_tables = booked_counts.get(slot, [])
            free_tables = [t for t in available_tables if t not in booked_tables]
            
            if free_tables:
                available_slots.append({
                    'time': slot,
                    'available_tables': len(free_tables),
                    'tables': free_tables[:3]  # Show first 3 available tables
                })
        
        return jsonify({'available_slots': available_slots})

@app.route('/create_booking', methods=['POST'])
def create_booking():
    """Create a new booking"""
    try:
        data = request.json
        customer_name = data.get('customer_name')
        customer_phone = data.get('customer_phone')
        customer_email = data.get('customer_email')
        telegram_username = data.get('telegram_username', '')
        booking_date = data.get('booking_date')
        booking_time = data.get('booking_time')
        party_size = int(data.get('party_size', 2))
        table_number = int(data.get('table_number', 0))
        special_requests = data.get('special_requests', '')
        
        # Validate inputs
        if not customer_name or len(customer_name) < 2:
            return jsonify({'success': False, 'error': 'Please enter a valid name'}), 400
        
        if not customer_phone:
            return jsonify({'success': False, 'error': 'Please enter a phone number'}), 400
        
        if not booking_date or not booking_time:
            return jsonify({'success': False, 'error': 'Please select date and time'}), 400
        
        # Check if table is already booked
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM bookings 
                WHERE booking_date = ? AND booking_time = ? AND table_number = ? 
                AND status IN ('confirmed', 'pending')
            """, (booking_date, booking_time, table_number))
            
            if c.fetchone()[0] > 0:
                return jsonify({'success': False, 'error': 'This table is already booked for that time'}), 400
            
            # Create booking
            c.execute("""
                INSERT INTO bookings 
                (customer_name, customer_phone, customer_email, telegram_username, booking_date, booking_time, 
                 party_size, table_number, special_requests, status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (customer_name, customer_phone, customer_email, telegram_username, booking_date, booking_time,
                  party_size, table_number, special_requests, 'pending',
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            booking_id = c.lastrowid
        
        # Send notification to restaurant
        def send_booking_notification():
            message = f"""
📅 <b>NEW TABLE BOOKING!</b>
━━━━━━━━━━━━━━━━
👤 Customer: {customer_name}
📞 Phone: {customer_phone}
📅 Date: {booking_date}
⏰ Time: {booking_time}
👥 Party Size: {party_size}
🪑 Table: {table_number}
📱 Telegram: {telegram_username or 'Not provided'}
📝 Requests: {special_requests or 'None'}

Please confirm the booking in the admin panel.
"""
            send_telegram_message(message)
            
            # If customer provided telegram, send them a confirmation request received
            if telegram_username:
                chat_id = get_chat_id_by_username(telegram_username)
                if chat_id:
                    cust_msg = f"""
📅 <b>Booking Request Received!</b>
━━━━━━━━━━━━━━━━
Dear {customer_name},

We have received your table booking request:
📅 Date: {booking_date}
⏰ Time: {booking_time}
👥 Party Size: {party_size}

We will notify you once your booking is confirmed!
                    """
                    send_telegram_message_to_chat(chat_id, cust_msg)

        threading.Thread(target=send_booking_notification).start()
        
        return jsonify({
            'success': True,
            'booking_id': booking_id,
            'message': f'Booking request sent! Table {table_number} reserved for {party_size} people on {booking_date} at {booking_time}'
        })
    
    except Exception as e:
        logger.error(f"Booking error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/bookings')
@login_required
def admin_bookings():
    """Admin page to manage bookings"""
    status_filter = request.args.get('status', 'all')
    
    with get_db() as conn:
        c = conn.cursor()
        
        if status_filter == 'all':
            c.execute("SELECT * FROM bookings ORDER BY booking_date DESC, booking_time ASC")
        else:
            c.execute("SELECT * FROM bookings WHERE status = ? ORDER BY booking_date DESC, booking_time ASC", (status_filter,))
        
        bookings = c.fetchall()
        
        # Get stats
        c.execute("SELECT COUNT(*) FROM bookings WHERE status = 'pending'")
        pending_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM bookings WHERE booking_date = ?", (datetime.now().strftime('%Y-%m-%d'),))
        today_count = c.fetchone()[0]
    
    return render_template('admin_bookings.html', 
                         bookings=[list(b) for b in bookings],
                         pending_count=pending_count,
                         today_count=today_count,
                         current_filter=status_filter)

@app.route('/admin/update_booking/<int:booking_id>/<status>')
@login_required
def update_booking_status(booking_id, status):
    """Update booking status (confirm/cancel)"""
    if status not in ['confirmed', 'cancelled', 'completed']:
        return redirect(url_for('admin_bookings'))
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE bookings 
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), booking_id))
        conn.commit()
        
        # Get customer info for notification
        c.execute("SELECT customer_name, customer_phone, booking_date, booking_time, table_number, telegram_username FROM bookings WHERE id = ?", (booking_id,))
        booking = c.fetchone()
    
    # Send notification to customer
    if booking:
        def send_booking_update():
            # ... (rest of the logic)
            
            status_emoji = "✅" if status == 'confirmed' else "❌" if status == 'cancelled' else "🏁"
            status_text = status.upper()
            telegram_username = booking[5]
            
            message = f"""
{status_emoji} <b>BOOKING {status_text}!</b>
━━━━━━━━━━━━━━━━
Dear {booking[0]},
Your table booking has been {status}.

📅 Date: {booking[2]}
⏰ Time: {booking[3]}
🪑 Table: {booking[4]}
"""
            if status == 'confirmed':
                message += "\nWe look forward to serving you! 🍽️"
            elif status == 'cancelled':
                message += "\nWe apologize for the inconvenience. Please contact us if you have questions."
            
            # Send to restaurant admin
            send_telegram_message(message)
            
            # Send to CUSTOMER if registered
            if telegram_username:
                chat_id = get_chat_id_by_username(telegram_username)
                if chat_id:
                    send_telegram_message_to_chat(chat_id, message)
                    logger.info(f"Sent booking {status} alert to customer: {telegram_username}")
            
            logger.info(f"Booking {status} for {booking[0]}")
            
        threading.Thread(target=send_booking_update).start()
    
    logger.info(f"Booking {booking_id} updated to {status}")
    return redirect(url_for('admin_bookings'))

@app.route('/api/check_booking_availability')
def check_booking_availability():
    """API to check if a table is available"""
    booking_date = request.args.get('date')
    booking_time = request.args.get('time')
    party_size = int(request.args.get('party_size', 2))
    
    with get_db() as conn:
        c = conn.cursor()
        
        # Find suitable tables
        c.execute("SELECT table_number, capacity FROM tables WHERE capacity >= ? AND is_active = 1", (party_size,))
        suitable_tables = c.fetchall()
        
        available_tables = []
        for table in suitable_tables:
            table_num = table[0]
            c.execute("""
                SELECT COUNT(*) FROM bookings 
                WHERE booking_date = ? AND booking_time = ? AND table_number = ?
                AND status IN ('confirmed', 'pending')
            """, (booking_date, booking_time, table_num))
            
            if c.fetchone()[0] == 0:
                available_tables.append({'table': table_num, 'capacity': table[1]})
        
        return jsonify({'available_tables': available_tables})
    
# ========== RUN APP ==========
if __name__ == '__main__':
    logger.info("Starting server on http://0.0.0.0:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)