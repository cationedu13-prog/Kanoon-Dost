#!/usr/bin/env python3
"""
⚖️ Kanoon Dost - AI Legal Drafting Assistant Bot
Free, automated legal notice & document drafting with 1-hour auto-delete.
Deploy on Render as a Web Service with Webhook.
"""

import os
import json
import logging
import traceback
from datetime import datetime

# ========== NEW GOOGLE GENAI SDK ==========
from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ========== CONFIGURATION (Environment Variables) ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # Optional

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("TELEGRAM_TOKEN and GEMINI_API_KEY must be set in environment variables.")

# Gemini Client (New SDK)
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-2.5-flash"  # Latest free model

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== SYSTEM PROMPTS (UPDATED & IMPROVED) ==========
FIELD_EXTRACTION_PROMPT = """
एक उपयोगकर्ता ने निम्नलिखित अनुरोध किया है: "{user_text}"
आप एक अनुभवी भारतीय कानूनी और प्रशासनिक ड्राफ्टिंग विशेषज्ञ हैं। इस अनुरोध के आधार पर एक पेशेवर दस्तावेज़ तैयार करने के लिए वास्तव में किन-किन जानकारियों (fields) की आवश्यकता होगी?
कृपया केवल और केवल एक JSON ऐरे के रूप में उत्तर दें, जिसमें हर फील्ड का एक संक्षिप्त, सरल हिंदी विवरण और एक उदाहरण शामिल हो।
उदाहरण स्वरूप:
[
    "आपका पूरा नाम (उदाहरण: रोहित शर्मा)",
    "दुकान का नाम और पता (उदाहरण: मोबाइल वर्ल्ड, 12 मेन रोड)",
    "खरीद की तारीख और रसीद संख्या (उदाहरण: 10 मई 2026, रसीद नं. 4567)",
    "फोन का मॉडल और कीमत (उदाहरण: सैमसंग M14, ₹15,000)",
    "खराबी का विवरण (उदाहरण: फोन चालू नहीं हो रहा है)",
    ...
]
- यदि दस्तावेज़ कानूनी प्रकृति का है, तो आवश्यक कानूनी विवरण (जैसे, विवाद की राशि, तिथियाँ, धाराएँ) ज़रूर शामिल करें।
- हर फील्ड का विवरण सरल हिंदी में और स्व-व्याख्यात्मक (self-explanatory) होना चाहिए।
- बिल्कुल कोई अतिरिक्त टेक्स्ट, स्पष्टीकरण या मार्कडाउन नहीं। केवल एक मान्य (valid) JSON ऐरे दें।
- उत्तर में बस '[', फिर फील्ड्स के स्ट्रिंग, और ']' होना चाहिए।
"""

FINAL_DOC_PROMPT = """
तुम एक वरिष्ठ भारतीय वकील और कानूनी ड्राफ्टिंग विशेषज्ञ हो, जिसे 25 वर्षों का अनुभव है। तुम्हारा कार्य उपयोगकर्ता द्वारा दी गई जानकारी के आधार पर एक पूर्ण, सटीक और प्रोफेशनल दस्तावेज़ तैयार करना है।

### सख्त निर्देश:
1. **कानूनी सटीकता**: केवल और केवल वास्तविक, प्रचलित भारतीय कानूनों (जैसे IPC, CrPC, कंज़्यूमर प्रोटेक्शन एक्ट, नेगोशिएबल इंस्ट्रूमेंट्स एक्ट, आदि) की धाराओं का उपयोग करें। यदि किसी धारा के बारे में 100% सुनिश्चित नहीं हैं, तो उसे बिल्कुल शामिल न करें। कभी भी कोई मनगढ़ंत या अस्तित्वहीन कानूनी प्रावधान न लिखें।
2. **भाषा**: उपयोगकर्ता ने जिस भाषा (हिंदी या अंग्रेजी) में अनुरोध किया है, दस्तावेज़ की मुख्य भाषा वही रखें। कानूनी शब्दावली को सरल और समझने योग्य रखें।
3. **फॉर्मेट**: दस्तावेज़ का एक स्पष्ट शीर्षक, दिनांक, प्रेषक व प्राप्तकर्ता का विवरण, विषय, मुख्य सामग्री (बॉडी), और अंत में एक औपचारिक समापन (जैसे "आपका विश्वासपात्र" या "हस्ताक्षर") अवश्य शामिल करें।
4. **कोई अटकलें नहीं**: दी गई जानकारी से बाहर जाकर कोई अतिरिक्त तथ्य गढ़कर न लिखें। यदि कोई जानकारी गायब है, तो "[कृपया यहाँ विवरण भरें]" जैसा प्लेसहोल्डर लगाएँ।
5. **चेतावनी**: यदि यह एक कानूनी नोटिस है, तो उसमें एक मानक चेतावनी अवश्य जोड़ें (जैसे, "यदि 15 दिनों के भीतर उचित उत्तर नहीं मिला तो कानूनी कार्रवाई की जाएगी")।

### उपयोगकर्ता का मूल अनुरोध:
{original_request}

### उपयोगकर्ता द्वारा भरी गई जानकारी:
{details_text}

अब, कृपया उपरोक्त सभी निर्देशों का पालन करते हुए एक संपूर्ण और पेशेवर दस्तावेज़ तैयार करें।
"""

# ========== DISCLAIMER TEXT ==========
DISCLAIMER = (
    "\n\n---\n"
    "⚠️ *अस्वीकरण (Disclaimer):* यह ड्राफ्ट एक AI टूल द्वारा तैयार किया गया है। "
    "यह किसी योग्य वकील की कानूनी सलाह का विकल्प नहीं है। "
    "इस्तेमाल से पहले कृपया किसी विशेषज्ञ से जाँच अवश्य करवाएँ।"
)

# ========== TERMS & PRIVACY ==========
TERMS_TEXT = """
📜 **उपयोग की शर्तें (Terms & Conditions)**

1. **सेवा का स्वरूप:** यह बॉट एक AI-संचालित सेल्फ-हेल्प ड्राफ्टिंग सहायक है। यह केवल दस्तावेज़ों का मसौदा (ड्राफ्ट) तैयार करता है और किसी भी प्रकार की कानूनी सलाह, वकालत या विधिक सेवा प्रदान नहीं करता है। यह बॉट कोई वकील या कानूनी सलाहकार नहीं है।

2. **कोई वकील-मुवक्किल संबंध नहीं:** इस बॉट के उपयोग से आपके और बॉट या इसके निर्माता के बीच कोई वकील-मुवक्किल संबंध स्थापित नहीं होता है।

3. **सटीकता और जिम्मेदारी:** AI जनरेटेड सामग्री में त्रुटियाँ हो सकती हैं। किसी भी दस्तावेज़ पर अमल करने से पहले, एक योग्य वकील से जाँच अवश्य करवाएँ। आप इस बॉट द्वारा तैयार ड्राफ्ट का उपयोग अपने विवेक और जोखिम पर करते हैं।

4. **उपयोग की सीमा:** आप इस बॉट का उपयोग केवल वैध उद्देश्यों के लिए करेंगे। किसी भी गैरकानूनी, धोखाधड़ी या उत्पीड़न वाले दस्तावेज़ को तैयार करने के लिए इसका उपयोग न करें।

5. **बौद्धिक संपदा:** बॉट द्वारा तैयार ड्राफ्ट का उपयोग आप केवल अपने व्यक्तिगत कार्य के लिए कर सकते हैं। इसका पुनर्विक्रय या व्यावसायिक वितरण प्रतिबंधित है।

6. **सेवा में बदलाव:** हम किसी भी समय बिना पूर्व सूचना के सेवा में बदलाव या बंद कर सकते हैं।

7. **डेटा प्रतिधारण:** आपके द्वारा प्रदान की गई जानकारी को दस्तावेज़ तैयार करने के 1 घंटे के भीतर हमारे सिस्टम से स्वतः हटा दिया जाता है।

यदि आप इन शर्तों से सहमत नहीं हैं, तो कृपया इस बॉट का उपयोग न करें।
"""

PRIVACY_TEXT = """
🔐 **गोपनीयता नीति (Privacy Policy)**

1. **डेटा संग्रह:** हम केवल वही जानकारी एकत्र करते हैं जो आप बॉट के साथ बातचीत में स्वेच्छा से प्रदान करते हैं, जैसे दस्तावेज़ बनाने के लिए नाम, पता और विवरण।

2. **उपयोग का उद्देश्य:** यह डेटा केवल आपके अनुरोधित दस्तावेज़ का मसौदा तैयार करने के एकमात्र उद्देश्य के लिए उपयोग किया जाता है। हम इसका उपयोग किसी अन्य उद्देश्य (जैसे विज्ञापन या प्रोफाइलिंग) के लिए नहीं करते।

3. **डेटा भंडारण और स्वतः विलोपन:**
   - आपके द्वारा दी गई सभी जानकारी अस्थायी रूप से हमारे सर्वर पर संग्रहीत होती है।
   - जैसे ही आपका दस्तावेज़ तैयार हो जाता है और प्रदर्शित हो जाता है, उसके ठीक 1 घंटे बाद आपका सारा डेटा हमारे सिस्टम से स्वचालित रूप से और स्थायी रूप से हटा दिया जाता है।
   - इस अवधि के दौरान डेटा का उपयोग केवल आपको दोबारा एक्सेस देने या PDF जनरेट करने की सुविधा के लिए किया जा सकता है।

4. **तृतीय पक्ष:** हम आपका डेटा किसी तीसरे पक्ष के साथ साझा नहीं करते, सिवाय तब जब कानूनन आवश्यक हो।

5. **AI प्रसंस्करण:** आपके टेक्स्ट को ड्राफ्ट तैयार करने के लिए Google Gemini API को भेजा जाता है। कृपया Google की गोपनीयता नीति भी देखें।

6. **सुरक्षा:** हम आपके डेटा की सुरक्षा के लिए उचित तकनीकी उपाय करते हैं, लेकिन इंटरनेट पर 100% सुरक्षा की गारंटी नहीं दे सकते।

7. **आपके अधिकार:** आप कभी भी `/delete` कमांड का उपयोग करके अपना डेटा तुरंत हटाने का अनुरोध कर सकते हैं (यह सुविधा जल्द ही उपलब्ध होगी)।

यदि आपके कोई प्रश्न हों, तो कृपया संपर्क करें: your.email@example.com
"""

# ========== COMMAND HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    welcome_msg = (
        "⚖️ *नमस्ते! मैं आपका 'कानून दोस्त' हूँ।*\n"
        "मैं एक AI असिस्टेंट हूँ, वकील नहीं।\n\n"
        "मैं आपकी समस्या सुनकर एक प्रोफेशनल कानूनी दस्तावेज़ (नोटिस, शिकायत, RTI, आवेदन, आदि) का ड्राफ्ट तैयार कर सकता हूँ।\n\n"
        "बस नीचे लिखिए, उदाहरण: 'मुझे मकान मालिक के खिलाफ सिक्योरिटी न वापस करने का कानूनी नोटिस चाहिए।'"
    )
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TERMS_TEXT, parse_mode='Markdown')

async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVACY_TEXT, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ प्रक्रिया रद्द कर दी गई। नई शुरुआत के लिए /start दबाएँ।")

# ========== CORE MESSAGE HANDLER ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    if 'fields' in context.user_data:
        await collect_next_field(update, context)
    else:
        await start_new_request(update, context, user_text)

async def start_new_request(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    await update.message.reply_text("⏳ विश्लेषण हो रहा है... मैं समझ रहा हूँ कि इस दस्तावेज़ के लिए क्या जानकारी चाहिए।")

    prompt = FIELD_EXTRACTION_PROMPT.format(user_text=user_text)
    try:
        # New SDK call
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        raw = response.text.strip()
        # किसी भी तरह के मार्कडाउन ब्लॉक को साफ करें
        for start_marker in ["```json", "```"]:
            if raw.startswith(start_marker):
                raw = raw.split(start_marker, 1)[1].rsplit("```", 1)[0].strip()
                break
        # अगर सीधे ऐरे न मिले तो last resort: पहले '[' से शुरू करें
        if not raw.startswith("["):
            bracket_idx = raw.find("[")
            if bracket_idx != -1:
                raw = raw[bracket_idx:]
                end_idx = raw.rfind("]")
                if end_idx != -1:
                    raw = raw[:end_idx+1]
        fields = json.loads(raw)
        if not isinstance(fields, list) or len(fields) == 0:
            raise ValueError("Received empty fields list from AI.")
    except Exception as e:
        logger.error(f"Field extraction error: {e}. Raw response: {response.text if 'response' in locals() else 'No response'}")
        await update.message.reply_text(
            "❌ क्षमा करें, आपके अनुरोध को समझने में दिक्कत हुई।\n"
            "कृपया अपनी बात थोड़ी और स्पष्टता से लिखें।\n"
            "उदाहरण: 'मुझे एक उपभोक्ता शिकायत का ड्राफ्ट चाहिए क्योंकि मैंने एक खराब फोन खरीदा था और दुकानदार रिफंड नहीं दे रहा।'",
        )
        return

    # डेटा स्टोर करें और पहला सवाल पूछें
    context.user_data['fields'] = fields
    context.user_data['collected_data'] = {}
    context.user_data['current_index'] = 0
    context.user_data['original_request'] = user_text

    remove_existing_jobs(context, update.effective_user.id)

    first_question = fields[0]
    await update.message.reply_text(f"📝 कृपया बताएँ:\n\n*{first_question}*", parse_mode='Markdown')

async def collect_next_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text
    user_data = context.user_data
    fields = user_data['fields']
    collected = user_data['collected_data']
    index = user_data['current_index']

    current_field = fields[index]
    collected[current_field] = answer

    next_idx = index + 1
    if next_idx < len(fields):
        user_data['current_index'] = next_idx
        await update.message.reply_text(f"📝 *{fields[next_idx]}*", parse_mode='Markdown')
    else:
        await generate_final_document(update, context)

async def generate_final_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    await update.message.reply_text("⏳ सारी जानकारी मिल गई। अब आपका डॉक्यूमेंट तैयार कर रहा हूँ...")

    details_lines = []
    for field, value in user_data['collected_data'].items():
        details_lines.append(f"{field}: {value}")
    details_text = "\n".join(details_lines)

    prompt = FINAL_DOC_PROMPT.format(
        original_request=user_data['original_request'],
        details_text=details_text
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        final_doc = response.text
        final_msg = f"📜 *आपका तैयार ड्राफ्ट:*\n\n{final_doc}{DISCLAIMER}"
        await update.message.reply_text(final_msg, parse_mode='Markdown')

        await update.message.reply_text(
            "🔻 *प्रिंट-रेडी PDF चाहिए?* सिर्फ ₹49 में।\n"
            "इस UPI ID पर भुगतान करें: `your-upi@bank`\n"
            "पेमेंट का स्क्रीनशॉट यहाँ भेज दें, हम आपको PDF भेज देंगे।",
            parse_mode='Markdown'
        )

        schedule_data_deletion(update, context)

    except Exception as e:
        logger.error(f"Final doc generation error: {e}")
        await update.message.reply_text("❌ दस्तावेज़ बनाने में तकनीकी दिक्कत आई। कृपया बाद में पुनः प्रयास करें।")
        context.user_data.clear()

def schedule_data_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    remove_existing_jobs(context, user_id)

    async def delete_user_data(ctx: ContextTypes.DEFAULT_TYPE):
        uid = ctx.job.data
        if uid in ctx.application.user_data:
            ctx.application.user_data[uid].clear()
            logger.info(f"User data deleted for {uid}")

    context.job_queue.run_once(
        delete_user_data,
        3600,
        data=user_id,
        name=str(user_id)
    )

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
    """Log the error and send a telegram message to notify the admin."""
    # पूरा traceback लॉग करें
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    logger.error(f"Unhandled exception: {tb_string}")

    # अगर एडमिन चैट सेट है तो वहाँ भेजें
    if ADMIN_CHAT_ID:
        try:
            error_msg = f"⚠️ *बॉट में अनजानी एरर आई*\n\n```\n{tb_string[:1500]}\n```"
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=error_msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send error to admin: {e}")

# ========== MAIN APPLICATION (WEBHOOK) ==========
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # एरर हैंडलर लगाएँ
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

    webhook_path = "/telegram"
    webhook_url = f"{RENDER_URL}{webhook_path}"

    logger.info(f"Starting webhook on port {PORT} with URL: {webhook_url}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
