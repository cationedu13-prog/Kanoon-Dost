#!/usr/bin/env python3
"""
⚖️ Kanoon Dost - AI Legal Draft Assistant
Powered by GPT-4o via AIMLAPI.com (Free Tier).
Deploy on Render as a Web Service with Webhook.
"""

import os
import json
import logging
import traceback
import time
import io
from datetime import datetime, timedelta
from collections import defaultdict

from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ========== CONFIGURATION ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# AIMLAPI Key (इसे Environment Variable में डालना सबसे सुरक्षित है, लेकिन टेस्टिंग के लिए नीचे हार्डकोड भी कर सकते हैं)
AIMLAPI_KEY = os.environ.get("AIMLAPI_KEY", "ecff35233388499e420e17d4655e574c")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN must be set in environment variables.")

# AIMLAPI Client (OpenAI Compatible)
client = OpenAI(
    api_key=AIMLAPI_KEY,
    base_url="https://api.aimlapi.com/v1"
)
MODEL_NAME = "gpt-4o"  # AIMLAPI पर GPT-4o का सही नाम

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== RATE LIMITER (In-Memory) ==========
user_request_times = defaultdict(list)

def check_rate_limit(user_id: int, max_per_minute: int = 5, max_per_day: int = 20) -> tuple[bool, str]:
    """Check if user is within rate limits. Returns (allowed, message)."""
    now = datetime.now()
    # Clean old entries
    user_request_times[user_id] = [
        t for t in user_request_times[user_id] if now - t < timedelta(days=1)
    ]
    times = user_request_times[user_id]
    
    # Check per minute
    minute_ago = now - timedelta(minutes=1)
    recent = [t for t in times if t > minute_ago]
    if len(recent) >= max_per_minute:
        wait_seconds = 60 - (now - min(recent)).seconds
        return False, f"⚠️ बहुत सारे अनुरोध! कृपया {wait_seconds} सेकंड रुकें।"
    
    # Check per day
    if len(times) >= max_per_day:
        return False, "⚠️ आज की सीमा (20 ड्राफ्ट) पूरी हो गई। कृपया कल पुनः प्रयास करें।"
    
    times.append(now)
    return True, ""

# ========== RETRY HELPER ==========
def chat_with_retry(messages, max_retries=3, delay=3):
    """Retry API call on transient errors."""
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.2,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exception = e
            err_str = str(e)
            if any(code in err_str for code in ['503', '429', '500', 'Server Error']):
                logger.warning(f"API transient error (attempt {attempt}): {err_str[:200]}")
                time.sleep(delay)
            else:
                raise
    raise last_exception

# ========== SYSTEM PROMPTS (Safe – No "Lawyer" Impersonation) ==========
FIELD_EXTRACTION_PROMPT = """
तुम एक भारतीय कानूनी दस्तावेज़ प्रारूप सहायक (Indian Legal Document Drafting Assistant) हो।
तुम्हारा कार्य उपयोगकर्ता के अनुरोध के आधार पर एक **सम्पूर्ण और पेशेवर दस्तावेज़** तैयार करने के लिए **हर आवश्यक जानकारी (fields)** की सूची बनाना है।
तुम कानूनी सलाह नहीं देते। केवल दिए गए तथ्यों पर आधारित draft तैयार करते हो।

### अनुरोध:
"{user_text}"

### महत्वपूर्ण निर्देश:
1. **कोई कमी नहीं**: ऐसी कोई भी जानकारी मत छोड़ना जिसके बिना दस्तावेज़ अधूरा या कमज़ोर लगे।
2. **संस्था का पता अनिवार्य**: यदि दस्तावेज़ किसी संस्था (स्कूल, कॉलेज, कार्यालय, दुकान, आदि) को संबोधित है, तो **पूरा डाक पता** अवश्य पूछें।
3. **कानूनी दस्तावेज़ों के लिए विशेष**: विवाद की सटीक तिथियाँ, राशि, लिखित समझौते, रसीद, बिल, साक्ष्य का विवरण ज़रूर शामिल करें।
4. **सरल हिंदी**: हर फील्ड का विवरण हिंदी में लिखें, साथ में उदाहरण।
5. **केवल JSON ऐरे**: कोई अतिरिक्त टेक्स्ट या मार्कडाउन नहीं। केवल एक मान्य JSON ऐरे दें जो '[' से शुरू हो और ']' पर खत्म हो।
6. **पर्याप्त फील्ड्स**: कम से कम 4 और अधिकतम 20 फील्ड्स।

### आदर्श उत्तर:
[
    "शिकायतकर्ता का पूरा नाम (उदाहरण: रोहित शर्मा)",
    "शिकायतकर्ता का पूरा पता (उदाहरण: मकान नं., सड़क, शहर, ज़िला, पिन कोड)",
    ...
]

अब इस अनुरोध के लिए JSON ऐरे तैयार करो:
"""

FINAL_DOC_PROMPT = """
तुम एक भारतीय कानूनी दस्तावेज़ प्रारूप सहायक (Indian Legal Document Drafting Assistant) हो।
तुम्हारा कार्य उपयोगकर्ता द्वारा दी गई जानकारी के आधार पर एक पूर्ण और प्रोफेशनल दस्तावेज़ तैयार करना है।
तुम कानूनी सलाह नहीं देते। केवल दिए गए तथ्यों पर आधारित draft तैयार करते हो।

### सख्त निर्देश:
1. **दस्तावेज़ का फॉर्मेट**: स्पष्ट शीर्षक, दिनांक, प्रेषक व प्राप्तकर्ता का विवरण, विषय, मुख्य सामग्री, और अंत में औपचारिक समापन।
2. **भाषा**: उपयोगकर्ता की भाषा (हिंदी या अंग्रेजी) में उत्तर दें।
3. **कानूनी संदर्भ**: केवल तभी कानूनी धाराएँ लिखें जब आप पूरी तरह सुनिश्चित हों। संदेह होने पर "संबंधित कानूनी प्रावधानों के अनुसार" लिखें।
4. **कोई अटकलें नहीं**: गुम जानकारी के लिए "[कृपया यहाँ विवरण भरें]" जैसा प्लेसहोल्डर लगाएँ।
5. **चेतावनी**: कानूनी नोटिस में 15 दिन की मानक चेतावनी जोड़ें।

### उपयोगकर्ता का मूल अनुरोध:
{original_request}

### उपयोगकर्ता द्वारा भरी गई जानकारी:
{details_text}

अब उपरोक्त सभी निर्देशों का पालन करते हुए एक संपूर्ण और प्रोफेशनल दस्तावेज़ तैयार करें। **दस्तावेज़ के बाद** निम्नलिखित तीन चीज़ें भी बताएँ (हर एक को नई लाइन पर लिखें):

**LEGAL_BASIS:** (इस्तेमाल किए गए कानूनी प्रावधान, यदि कोई)
**ASSUMPTIONS:** (जो धारणाएँ ली गई हैं)
**MISSING_INFORMATION:** (कौन-सी जानकारी गायब थी जिससे ड्राफ्ट और बेहतर हो सकता था)
"""

# ========== DISCLAIMER & LEGAL TEXTS ==========
DISCLAIMER = (
    "⚠️ *अस्वीकरण (Disclaimer):* यह ड्राफ्ट एक AI टूल द्वारा तैयार किया गया है। "
    "यह किसी योग्य वकील की कानूनी सलाह का विकल्प नहीं है। "
    "इस्तेमाल से पहले कृपया किसी विशेषज्ञ से जाँच अवश्य करवाएँ।"
)

TERMS_TEXT = """
📜 **उपयोग की शर्तें (Terms & Conditions)**

1. **सेवा का स्वरूप:** यह बॉट एक AI-संचालित दस्तावेज़ प्रारूप सहायक (AI Legal Draft Assistant) है। यह केवल दस्तावेज़ों का मसौदा (ड्राफ्ट) तैयार करता है और किसी भी प्रकार की कानूनी सलाह, वकालत या विधिक सेवा प्रदान नहीं करता है।

2. **कोई वकील-मुवक्किल संबंध नहीं:** इस बॉट के उपयोग से कोई वकील-मुवक्किल संबंध स्थापित नहीं होता।

3. **सटीकता और जिम्मेदारी:** AI जनरेटेड सामग्री में त्रुटियाँ हो सकती हैं। किसी भी दस्तावेज़ पर अमल करने से पहले, एक योग्य वकील से जाँच अवश्य करवाएँ।

4. **डेटा प्रतिधारण:** एप्लिकेशन द्वारा एकत्रित ड्राफ्टिंग डेटा दस्तावेज़ तैयार होने के 1 घंटे बाद स्वतः हटा दिया जाता है।

5. **दैनिक सीमा:** प्रति उपयोगकर्ता प्रतिदिन 20 ड्राफ्ट तक की सीमा है।

/start से शुरू करें।
"""

PRIVACY_TEXT = """
🔐 **गोपनीयता नीति (Privacy Policy)**

1. **डेटा संग्रह:** हम केवल वही जानकारी एकत्र करते हैं जो आप बॉट के साथ बातचीत में स्वेच्छा से प्रदान करते हैं।

2. **उपयोग का उद्देश्य:** यह डेटा केवल आपके अनुरोधित दस्तावेज़ का मसौदा तैयार करने के लिए उपयोग किया जाता है।

3. **डेटा भंडारण और स्वतः विलोपन:** एप्लिकेशन द्वारा एकत्रित ड्राफ्टिंग डेटा दस्तावेज़ तैयार होने के 1 घंटे बाद स्वतः स्थायी रूप से हटा दिया जाता है।

4. **तृतीय पक्ष:** हम आपका डेटा किसी तीसरे पक्ष के साथ साझा नहीं करते।

5. **AI प्रसंस्करण:** आपके टेक्स्ट को ड्राफ्ट तैयार करने के लिए AI API को भेजा जाता है।

यदि आपके कोई प्रश्न हों, तो कृपया संपर्क करें।
"""

# ========== COMMAND HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    welcome_msg = (
        "⚖️ *नमस्ते! मैं आपका 'कानून दोस्त' हूँ।*\n"
        "मैं एक AI ड्राफ्ट असिस्टेंट हूँ, वकील नहीं।\n\n"
        "मैं आपकी समस्या सुनकर एक प्रोफेशनल कानूनी दस्तावेज़ (नोटिस, शिकायत, RTI, आवेदन, आदि) का ड्राफ्ट तैयार कर सकता हूँ।\n\n"
        "📝 बस नीचे लिखिए, उदाहरण: 'मुझे मकान मालिक के खिलाफ सिक्योरिटी न वापस करने का कानूनी नोटिस चाहिए।'\n\n"
        "⚠️ *यह कानूनी सलाह नहीं है।* इस्तेमाल से पहले विशेषज्ञ से जाँच करवाएँ।"
    )
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TERMS_TEXT, parse_mode='Markdown')

async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVACY_TEXT, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई। /start से नई शुरुआत करें।")

# ========== CORE MESSAGE HANDLER ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # Rate limit check
    allowed, msg = check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(msg)
        return

    if 'fields' in context.user_data:
        await collect_next_field(update, context)
    else:
        await start_new_request(update, context, user_text)

async def start_new_request(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    await update.message.reply_text("⏳ विश्लेषण हो रहा है...")

    messages = [
        {"role": "system", "content": "तुम एक भारतीय कानूनी दस्तावेज़ प्रारूप सहायक हो। कानूनी सलाह नहीं देते। उत्तर केवल JSON ऐरे में दो, अन्य कुछ नहीं।"},
        {"role": "user", "content": FIELD_EXTRACTION_PROMPT.format(user_text=user_text)}
    ]
    try:
        raw = chat_with_retry(messages)
        raw = raw.strip()
        for marker in ["```json", "```"]:
            if raw.startswith(marker):
                raw = raw.split(marker, 1)[1].rsplit("```", 1)[0].strip()
                break
        if not raw.startswith("["):
            idx = raw.find("[")
            if idx != -1:
                raw = raw[idx:]
                end = raw.rfind("]")
                if end != -1:
                    raw = raw[:end+1]
        fields = json.loads(raw)
        if not isinstance(fields, list) or len(fields) == 0:
            raise ValueError("Empty fields list")
    except Exception as e:
        logger.error(f"Field extraction error: {e}. Raw: {raw if 'raw' in locals() else 'No response'}")
        await update.message.reply_text(
            "❌ अनुरोध को समझने में अस्थायी समस्या हुई। कृपया थोड़ी देर बाद पुनः प्रयास करें।\n"
            "यदि समस्या बनी रहती है तो /start करके नया सत्र शुरू करें।"
        )
        return

    context.user_data['fields'] = fields
    context.user_data['collected_data'] = {}
    context.user_data['current_index'] = 0
    context.user_data['original_request'] = user_text
    remove_existing_jobs(context, update.effective_user.id)
    await update.message.reply_text(f"📝 कृपया बताएँ:\n\n*{fields[0]}*", parse_mode='Markdown')

async def collect_next_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text
    ud = context.user_data
    ud['collected_data'][ud['fields'][ud['current_index']]] = answer
    ud['current_index'] += 1
    if ud['current_index'] < len(ud['fields']):
        await update.message.reply_text(f"📝 *{ud['fields'][ud['current_index']]}*", parse_mode='Markdown')
    else:
        await generate_final_document(update, context)

async def generate_final_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    await update.message.reply_text("⏳ डॉक्यूमेंट तैयार हो रहा है...")
    details = "\n".join([f"{k}: {v}" for k, v in ud['collected_data'].items()])

    messages = [
        {"role": "system", "content": "तुम एक भारतीय कानूनी दस्तावेज़ प्रारूप सहायक हो। कानूनी सलाह नहीं देते।"},
        {"role": "user", "content": FINAL_DOC_PROMPT.format(original_request=ud['original_request'], details_text=details)}
    ]
    try:
        response_text = chat_with_retry(messages)

        # Parse response into draft and metadata sections
        final_doc = response_text
        legal_basis = ""
        assumptions = ""
        missing_info = ""

        # Split using markers
        if "**LEGAL_BASIS:**" in response_text:
            parts = response_text.split("**LEGAL_BASIS:**", 1)
            final_doc = parts[0].strip()
            rest = parts[1]
            if "**ASSUMPTIONS:**" in rest:
                lb_part, rest = rest.split("**ASSUMPTIONS:**", 1)
                legal_basis = lb_part.strip()
            else:
                legal_basis = rest.strip()
                rest = ""
            if "**MISSING_INFORMATION:**" in rest:
                assump_part, mi_part = rest.split("**MISSING_INFORMATION:**", 1)
                assumptions = assump_part.strip()
                missing_info = mi_part.strip()
            elif rest:
                assumptions = rest.strip()

        # Send the draft
        await update.message.reply_text("📜 *आपका तैयार ड्राफ्ट:*", parse_mode='Markdown')
        safe_doc = final_doc.replace('```', "'''")

        # If document is very long (>3500 chars), send as file
        if len(safe_doc) > 3500:
            file_obj = io.BytesIO(safe_doc.encode('utf-8'))
            file_obj.name = "draft.txt"
            await update.message.reply_document(file_obj, caption="📄 आपका तैयार ड्राफ्ट (फाइल के रूप में)")
        else:
            await update.message.reply_text(f"```\n{safe_doc}\n```")

        # Send metadata (legal_basis, assumptions, missing_info) if available
        meta_parts = []
        if legal_basis:
            meta_parts.append(f"📚 *कानूनी आधार:*\n{legal_basis}")
        if assumptions:
            meta_parts.append(f"💡 *धारणाएँ:*\n{assumptions}")
        if missing_info:
            meta_parts.append(f"⚠️ *गुम जानकारी:*\n{missing_info}")
        if meta_parts:
            meta_msg = "\n\n".join(meta_parts)
            await update.message.reply_text(meta_msg, parse_mode='Markdown')

        await update.message.reply_text(
            DISCLAIMER + "\n\n" +
            "🔻 *प्रिंट-रेडी PDF चाहिए?* सिर्फ ₹49 में।\n"
            "UPI ID: `your-upi@bank` पर भुगतान कर स्क्रीनशॉट भेजें।\n"
            "*(पहले भुगतान करें, फिर PDF अनलॉक होगी)*",
            parse_mode='Markdown'
        )
        schedule_data_deletion(update, context)
    except Exception as e:
        logger.error(f"Final doc error: {e}")
        await update.message.reply_text("❌ दस्तावेज़ बनाने में अस्थायी समस्या आई। कृपया थोड़ी देर बाद पुनः प्रयास करें।")
        context.user_data.clear()

def schedule_data_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    remove_existing_jobs(context, user_id)

    async def delete_user_data(ctx: ContextTypes.DEFAULT_TYPE):
        uid = ctx.job.data
        if uid in ctx.application.user_data:
            ctx.application.user_data[uid].clear()
            logger.info(f"User data deleted for {uid}")

    context.job_queue.run_once(delete_user_data, 3600, data=user_id, name=str(user_id))

def remove_existing_jobs(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

# ========== PAYMENT SCREENSHOT HANDLER ==========
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    caption = update.message.caption or "कोई कैप्शन नहीं"
    await update.message.reply_text("📎 आपका स्क्रीनशॉट प्राप्त हुआ। हम जल्द ही सत्यापन करके PDF भेज देंगे।")
    if ADMIN_CHAT_ID:
        try:
            admin_msg = (
                f"📬 *नया भुगतान स्क्रीनशॉट*\n"
                f"👤 उपयोगकर्ता: {user.full_name} (@{user.username})\n"
                f"🆔 ID: `{user.id}`\n"
                f"📝 कैप्शन: {caption}"
            )
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode='Markdown')
            await update.message.forward(ADMIN_CHAT_ID)
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

# ========== GLOBAL ERROR HANDLER ==========
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    logger.error(f"Unhandled exception: {tb_string}")
    if ADMIN_CHAT_ID:
        try:
            error_msg = f"⚠️ *बॉट में अनजानी एरर आई*\n\n```\n{tb_string[:1500]}\n```"
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=error_msg, parse_mode='Markdown')
        except:
            pass

# ========== MAIN APPLICATION ==========
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("terms", terms))
    application.add_handler(CommandHandler("privacy", privacy))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    PORT = int(os.environ.get("PORT", 8080))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not RENDER_URL:
        RENDER_URL = f"http://localhost:{PORT}"
    webhook_url = f"{RENDER_URL}/telegram"
    logger.info(f"Starting webhook on port {PORT}")
    application.run_webhook(listen="0.0.0.0", port=PORT, url_path="telegram", webhook_url=webhook_url)

if __name__ == "__main__":
    main()
