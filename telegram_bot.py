import os
import requests
import time
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
import sys
import io

# ========== ENCODING FIX FOR WINDOWS ==========
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Load environment variables
load_dotenv()

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
last_update_id = 0

if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in .env file!")
    exit(1)

def get_db():
    conn = sqlite3.connect('orders.db')
    return conn

def store_user(chat_id, username, first_name):
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO telegram_users (chat_id, username, first_name, registered_at)
        VALUES (?, ?, ?, ?)
    ''', (chat_id, username, first_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    print(f"✅ Stored user: @{username}")

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending: {e}")

print("🤖 Telegram Bot Started (Polling Mode)")
print("Waiting for messages...")

while True:
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"offset": last_update_id + 1, "timeout": 30}
        
        response = requests.get(url, params=params, timeout=35)
        data = response.json()
        
        if data.get('ok') and data.get('result'):
            for update in data['result']:
                last_update_id = update['update_id']
                
                if 'message' in update:
                    msg = update['message']
                    chat_id = msg['chat']['id']
                    text = msg.get('text', '')
                    username = msg['from'].get('username', 'unknown')
                    first_name = msg['from'].get('first_name', 'Customer')
                    
                    print(f"📩 Message from @{username}: {text}")
                    
                    if text == '/start':
                        store_user(chat_id, username, first_name)
                        send_message(chat_id, f"""
🎉 Welcome to our restaurant, {first_name}!

You will now receive order updates.

Just place an order on our website and enter your Telegram username: @{username}

We'll notify you when your order is ready!

Thank you! 🍽️
                        """)
                    elif text == '/help':
                        send_message(chat_id, """
📋 Available commands:
/start - Register for order updates
/help - Show this message
/status - Check your registration status
                        """)
                    elif text == '/status':
                        send_message(chat_id, f"✅ You are registered, @{username}! You will receive order updates.")
        
    except Exception as e:
        print(f"Error: {e}")
    
    time.sleep(1)
