import os
import re
import urllib.parse
from flask import Flask, request, jsonify, send_from_directory
import requests
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR)

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")

# Отримання актуального курсу валют
def get_exchange_rates():
    fallback = {"EUR_TO_UAH": 45.5, "PLN_TO_UAH": 10.6, "UAH_TO_UAH": 1.0}
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
        data = response.json()
        if data and "rates" in data:
            usd_rates = data["rates"]
            usd_to_uah = usd_rates.get("UAH", 41.5)
            usd_to_eur = usd_rates.get("EUR", 0.92)
            usd_to_pln = usd_rates.get("PLN", 4.0)
            
            return {
                "EUR_TO_UAH": usd_to_uah / usd_to_eur,
                "PLN_TO_UAH": usd_to_uah / usd_to_pln,
                "UAH_TO_UAH": 1.0
            }
    except Exception as e:
        print(f"[Rates Fetch Warning]: {e}. Використовуємо базовий курс.")
    return fallback

# Конвертер цін у гривню
def parse_and_convert_price(price_str, country_code, rates):
    if not price_str or "уточнюйте" in price_str.lower():
        return "Ціну уточнюйте"
        
    p_lower = price_str.lower()
    
    if 'zł' in p_lower or 'pln' in p_lower or country_code == 'pl':
        currency = 'PLN'
    elif '€' in p_lower or 'eur' in p_lower or country_code == 'de':
        currency = 'EUR'
    else:
        currency = 'UAH'
        
    cleaned = price_str.replace('\xa0', '').replace(' ', '')
    cleaned = re.sub(r'[^\d.,]', '', cleaned)
    
    if not cleaned:
        return price_str
        
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        if re.search(r',\d{2}$', cleaned): 
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
            
    try:
        val = float(cleaned)
        if currency == 'EUR':
            uah_val = val * rates["EUR_TO_UAH"]
            return f"{round(uah_val):,} грн".replace(',', ' ') + f" ({price_str})"
        elif currency == 'PLN':
            uah_val = val * rates["PLN_TO_UAH"]
            return f"{round(uah_val):,} грн".replace(',', ' ') + f" ({price_str})"
        else:
            return f"{round(val):,} грн".replace(',', ' ')
    except:
        return price_str

def smart_clean_query(raw_query):
    if not raw_query:
        return ""
    text = raw_query.lower().strip()
    text = re.sub(r'\b(р\.в\.|р\.в|р\b|рік\b|року\b|год\b|года\b)', '', text)
    replacements = {"передний": "передній", "задний": "задній", "левый": "лівий", "правый": "правий"}
    for wrong, right in replacements.items():
        text = re.sub(r'\b' + wrong + r'\b', right, text)
    text = re.sub(r'[.,\/#!$%\^&\*;:{}=\-_`~()]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

# ПОВНІСТЮ ОНОВЛЕНА ФУНКЦІЯ: Анти-блокування Google Shopping
def bulletproof_link_resolver(item):
    merchant_link = item.get('merchantLink', '').strip()
    serper_link = item.get('link', '').strip()
    title = item.get('title', 'Автозапчастина')
    source = item.get('source', '').lower()

    def extract_clean_url(url):
        if not url:
            return None
        
        # Якщо посилання вже чисте й не містить сервісів Google
        if "google." not in url and "googleadservices." not in url and url.startswith("http"):
            return url
            
        # Надійний парсинг параметрів редіректу через urlparse
        try:
            parsed = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed.query)
            
            # Шукаємо пряму адресу сайту в параметрах гугла
            for param in ['url', 'adurl', 'q', 'dest', 'redirect_url']:
                if param in query_params:
                    extracted = query_params[param][0]
                    if "%" in extracted:
                        extracted = urllib.parse.unquote(extracted)
                    if extracted.startswith("http") and "google." not in extracted:
                        return extracted
        except Exception:
            pass
        return None

    # Пробуємо дістати чисте посилання з обох джерел
    clean_url = extract_clean_url(merchant_link) or extract_clean_url(serper_link)
    if clean_url:
        return clean_url

    # ФОЛБЕК-СТРАТЕГІЯ: Якщо посилання веде на заблокований інтерфейс Google Shopping,
    # ми перенаправляємо користувача безпосередньо на сайт-джерело оголошення за назвою деталі.
    encoded_title = urllib.parse.quote(title)
    
    if "olx" in source:
        return f"https://www.olx.ua/list/q-{encoded_title}/"
    elif "prom" in source:
        return f"https://prom.ua/ua/search?search_term={encoded_title}"
    elif "allegro" in source:
        return f"https://allegro.pl/listing?string={encoded_title}"
    elif "ebay" in source:
        return f"https://www.ebay.com/sch/i.html?_nkw={encoded_title}"
    elif "otomoto" in source:
        return f"https://www.otomoto.pl/osobowe/q-{encoded_title}"

    # Якщо джерело не визначено, але є merchant_link — віддаємо його
    if merchant_link:
        return merchant_link
    if serper_link:
        return serper_link

    return f"https://www.google.com/search?q={encoded_title}"

def fetch_from_country(query, country_code, lang_code, flag, rates):
    url = "https://google.serper.dev/shopping"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "hl": lang_code, "gl": country_code, "num": 15}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=7)
        results = response.json().get('shopping', [])
        
        country_offers = []
        for item in results:
            final_secure_link = bulletproof_link_resolver(item)
            raw_price = item.get('price', 'Ціну уточнюйте')
            converted_price = parse_and_convert_price(raw_price, country_code, rates)
            
            country_offers.append({
                "title": item.get('title', 'Автозапчастина'),
                "link": final_secure_link, 
                "source": item.get('source', 'МАГАЗИН').upper(),
                "price": converted_price, 
                "image": item.get('imageUrl', ''),
                "country": flag
            })
        return country_offers
    except Exception as e:
        print(f"[📍 Country Error {country_code}]: {e}")
        return []

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/search', methods=['POST'])
def search_part():
    try:
        user_query = request.json.get('query', '').strip()
        if not user_query:
            return jsonify({"offers": []})
            
        optimized_query = smart_clean_query(user_query)
        print(f"[🛡️ Anti-Block Run]: '{optimized_query}'")
        
        rates = get_exchange_rates()
        
        targets = [
            {"gl": "ua", "hl": "uk", "flag": "🇺🇦 UA"},
            {"gl": "pl", "hl": "pl", "flag": "🇵🇱 PL"},
            {"gl": "de", "hl": "de", "flag": "🇩🇪 DE"}
        ]
        
        all_offers = []
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(fetch_from_country, optimized_query, t["gl"], t["hl"], t["flag"], rates)
                for t in targets
            ]
            for future in futures:
                all_offers.extend(future.result())
                
        return jsonify({"offers": all_offers})
        
    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"offers": []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
