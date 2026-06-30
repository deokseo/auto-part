import os
from flask import Flask, request, jsonify, send_from_directory
import requests
import json
import re

app = Flask(__name__, static_folder='.')

# БЕЗПЕЧНО: Беремо ключі зі змінних оточення Render. Дефолтні текстові ключі видалено.
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def ask_free_ai(prompt_text):
    try:
        response = requests.post("https://chateverywhere.app/api/chat/", json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt_text}]
        }, timeout=10)
        return response.text
    except:
        return "Не вдалося отримати аналітику."

def ask_gemini(prompt_text):
    if not GEMINI_API_KEY:
        return ask_free_ai(prompt_text)
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt_text}]}]}
    try:
        response = requests.post(url, json=data, headers=headers, timeout=7)
        res_json = response.json()
        if 'candidates' in res_json:
            return res_json['candidates'][0]['content']['parts'][0]['text']
        else:
            return ask_free_ai(prompt_text)
    except:
        return ask_free_ai(prompt_text)

def search_google_image(search_query):
    if not SERPER_API_KEY:
        return None
        
    url = "https://google.serper.dev/images"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    data = {"q": f"{search_query} передній бампер шрот oem", "num": 1}
    try:
        response = requests.post(url, json=data, headers=headers, timeout=5)
        images = response.json().get('images', [])
        if images and 'thumbnailUrl' in images[0]:
            return images[0]['thumbnailUrl']
    except:
        pass
    return None

def parse_single_price(title, snippet):
    text = f"{title} {snippet}"
    text = re.sub(r'\b(19\d{2}|20[0-2]\d|2030)\b', ' ', text)
    text = re.sub(r'\b(240|200|320|220|280)\b', ' ', text)
    
    found = re.findall(r'(\b\d+[\s,.]?\d*\b)\s*(?:грн|uah|€|eur|zł|pln|\$)|(?:€|\$)\s*(\b\d+[\s,.]?\d*\b)', text, re.IGNORECASE)
    for f in found:
        num_str = f[0] if f[0] else f[1]
        clean_num = ''.join(c for c in num_str if c.isdigit())
        if clean_num:
            val = int(clean_num)
            if 1200 <= val <= 90000:
                return val

    match = re.search(r'(?:ціна|price|cena)[:\s\-]*(\b\d+[\s,.]?\d*\b)', text, re.IGNORECASE)
    if match:
        clean_num = ''.join(c for c in match.group(1) if c.isdigit())
        if clean_num:
            val = int(clean_num)
            if 1200 <= val <= 90000:
                return val

    raw_numbers = re.findall(r'\b\d{4,5}\b', text)
    for num in raw_numbers:
        val = int(num)
        if 1500 <= val <= 45000:
            return val

    return None

def search_global_offers(search_query):
    if not SERPER_API_KEY:
        return []
        
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    
    clean_query = search_query.replace("р.в.", "").replace("рік", "").strip()
    global_query = f"{clean_query} бампер (шрот OR розборка OR олх OR prom)"
    
    offers = []
    try:
        response = requests.post(url, json={"q": global_query, "num": 10}, headers=headers, timeout=7)
        results = response.json().get('organic', [])
        
        if not results:
            words = clean_query.split()
            short_query = f"{words[0]} бампер купити розборка" if words else "бампер купити розборка"
            response = requests.post(url, json={"q": short_query, "num": 8}, headers=headers, timeout=7)
            results = response.json().get('organic', [])

        for item in results:
            title = item.get('title', 'Автозапчастина')
            snippet = item.get('snippet', '')
            link = item.get('link', '#')
            
            raw_date = item.get('date', '').strip()
            date_text = raw_date if raw_date else "нещодавно"
            
            price_val = parse_single_price(title, snippet)
            if price_val:
                price_text = f"{price_val} грн"
            else:
                price_text = "Договірна"
            
            full_text = f"{title} {snippet}".lower()
            if "під замовлення" in full_text or "замовлення" in full_text or "not in stock" in full_text:
                stock_text = "Під замовлення"
            elif "немає в наявності" in full_text or "немає" in full_text:
                stock_text = "Немає в наявності"
            else:
                stock_text = "В наявності"
                
            offers.append({
                "title": title,
                "link": link,
                "price": price_text,
                "date": date_text,
                "snippet": snippet,
                "stock": stock_text
            })
    except Exception as e:
        print(f"Помилка глобального пошуку: {e}")
        
    return offers

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/search', methods=['POST'])
def search_part():
    try:
        user_data = request.json.get('query', '')
        
        prompt = f"""
        Ти професійний помічник авторозборщика. Розклади детально запит: {user_data}.
        У відповіді категорично заборонено використовувати будь-які символи решіток або зірочок.
        Видай інформацію строго за такою схемою, просто чистим текстом:
        
        АВТОМОБІЛЬ ТА ДЕТАЛЬ:
        Визнач марку, модель, рік та назву деталі.
        
        РОЗШИФРОВКА OEM КОДІВ:
        Знайди можливі оригінальні артикули. Для кожного коду розпиши, що означає кожна група символів (наприклад, для Mercedes: А - легкове авто, перші 3 цифри - кузов і т.д.).
        """
        
        ai_response = ask_gemini(prompt).replace('#', '').replace('*', '')
        image_url = search_google_image(user_data)
        offers = search_global_offers(user_data)
        
        return jsonify({
            "ai_text": ai_response,
            "image_url": image_url,
            "offers": offers
        })
    except Exception as e:
        return jsonify({
            "ai_text": f"Помилка сервера: {str(e)}",
            "image_url": None,
            "offers": []
        })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
