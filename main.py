# main.py
import telebot
import openai
import stripe
from flask import Flask, request
import threading
import sqlite3
import time
import json
import os

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

bot = telebot.TeleBot(BOT_TOKEN)
openai.api_key = OPENAI_API_KEY
stripe.api_key = STRIPE_SECRET_KEY

# --- DATABASE SETUP ---
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, 
              subscription TEXT DEFAULT 'free',
              daily_count INTEGER DEFAULT 0,
              last_reset TEXT DEFAULT '',
              stripe_customer_id TEXT DEFAULT '')''')
conn.commit()

# --- PAYMENT HANDLER (Flask webhook) ---
app = Flask(__name__)

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400
    
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        user_id = session['metadata']['user_id']
        # Update user to premium
        c.execute("UPDATE users SET subscription='premium' WHERE user_id=?", (user_id,))
        conn.commit()
        bot.send_message(user_id, "✅ Payment successful! You now have unlimited access.")
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=5000)

threading.Thread(target=run_flask).start()

# --- BOT COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    # Register user if new
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    
    bot.reply_to(message, 
        "🤖 *AI Content Generator*\n\n"
        "I generate SEO blog posts, social media captions, and more.\n\n"
        "Commands:\n"
        "/generate [topic] - Create content\n"
        "/subscribe - Go premium ($9.99/month)\n"
        "/status - Check your plan\n"
        "/help - See all features",
        parse_mode='Markdown')

@bot.message_handler(commands=['generate'])
def generate_content(message):
    user_id = message.from_user.id
    topic = message.text.replace('/generate', '').strip()
    
    if not topic:
        bot.reply_to(message, "Please provide a topic. Example: /generate best SEO tips for 2024")
        return
    
    # Check subscription status
    c.execute("SELECT subscription, daily_count FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    sub, daily_count = user
    
    # Free tier limit check (5 per day)
    if sub == 'free' and daily_count >= 5:
        bot.reply_to(message, "❌ Free limit reached (5/day). Upgrade to premium: /subscribe")
        return
    
    # Generate content using OpenAI
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert SEO content writer. Generate a 500-word blog post about the given topic. Include headings, bullet points, and SEO keywords."},
                {"role": "user", "content": f"Write an SEO-optimized blog post about: {topic}"}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        content = response.choices[0].message.content
        
        # Update daily count
        c.execute("UPDATE users SET daily_count = daily_count + 1 WHERE user_id=?", (user_id,))
        conn.commit()
        
        # Send in chunks if too long
        if len(content) > 4000:
            for i in range(0, len(content), 4000):
                bot.send_message(user_id, content[i:i+4000])
        else:
            bot.send_message(user_id, content)
            
    except Exception as e:
        bot.send_message(user_id, f"❌ Error generating content: {str(e)}")

@bot.message_handler(commands=['subscribe'])
def subscribe(message):
    user_id = message.from_user.id
    
    # Create Stripe Checkout Session
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'AI Content Generator Premium',
                        'description': 'Unlimited AI content generation',
                    },
                    'unit_amount': 999,  # $9.99
                    'recurring': {'interval': 'month'},
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url='https://t.me/YourBotUsername?start=success',
            cancel_url='https://t.me/YourBotUsername?start=cancel',
            metadata={'user_id': str(user_id)}
        )
        
        # Store customer ID
        c.execute("UPDATE users SET stripe_customer_id=? WHERE user_id=?", 
                  (checkout_session.customer, user_id))
        conn.commit()
        
        bot.send_message(user_id, 
            f"💳 Click to subscribe: {checkout_session.url}\n\n"
            f"$9.99/month - Cancel anytime.")
    except Exception as e:
        bot.send_message(user_id, f"Payment error: {str(e)}")

@bot.message_handler(commands=['status'])
def check_status(message):
    user_id = message.from_user.id
    c.execute("SELECT subscription, daily_count FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    sub, count = user
    
    status_msg = f"📊 *Your Status*\n\nPlan: {sub.upper()}\n"
    if sub == 'free':
        status_msg += f"Generations today: {count}/5\n"
        status_msg += "Upgrade: /subscribe"
    else:
        status_msg += "✅ Unlimited access"
    
    bot.send_message(user_id, status_msg, parse_mode='Markdown')

# --- START BOT ---
if __name__ == '__main__':
    print("Bot is running...")
    bot.infinity_polling()
