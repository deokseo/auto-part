import os
import re
import json
import urllib.parse
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)



# =======================================================
# 1. ЖИВА ПЕРЕВІРКА НАЯВНОСТІ НА САЙТІ
# =======================================================
def verify_live_availability(url):
    """ Сканує сторінку лоту на наявність стоп-слів відсутності """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code != 200:
            return "Уточнюйте"
       
        html_text = response.text.lower()
        stop_words = [
            "немає в наявності", "немає", "відсутній", "закінчився", "товар продано",
            "brak na stanie", "wyprzedane", "produkt niedostępny", "out of stock",
            "sold out", "not available", "архівний", "архивный", "продано"
        ]
        for word in stop_words:
            if word in html_text:
                return "Немає в наявності"
        return "В наявності"
    except:
        return "В наявності"

# =======================================================
# 2. ОЧИЩЕННЯ ТА ФІЛЬТР ПОСИЛАНЬ
# =======================================================
def verify_and_clean_link(raw_url):
    if not raw_url:
        return None
    clean_url = raw_url
    if "google.com/url?" in raw_url or "/url?" in raw_url:
        parsed_query = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
        if 'url' in parsed_query: clean_url = parsed_query['url'][0]
        elif 'q' in parsed_query: clean_url = parsed_query['q'][0]
           
    url_lower = clean_url.lower()
    search_triggers = [
        "catalog/search", "query", "q=", "filter", "prom.ua/ua/sc-", "prom.ua/sc-",
        "olx.ua/uk/list", "olx.ua/list", "s?", "obyavlenie/search", "/search/",
        "?search=", "search_query"
    ]
    if any(trigger in url_lower for trigger in search_triggers):
        return None
    if "prom.ua" in url_lower:
        if not ("/p" in url_lower or any(char.isdigit() for char in url_lower.split('/')[-1])): return None
    elif "olx.ua" in url_lower:
        if not ("/d/" in url_lower or ".html" in url_lower): return None
    elif "allegro.pl" in url_lower:
        if "/oferta/" not in url_lower: return None
    elif "avto.pro" in url_lower:
        if "/zapchasti-" in url_lower or not any(x in url_lower for x in ["/part-", "/price-"]): return None
           
    return clean_url

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/search', methods=['POST'])
def search():
    data = request.get_json() or {}
    query = data.get('query', '').strip()
   
    if not query:
        return jsonify({"offers": [], "metadata": {"part_type": "Автозапчастина", "position": "Вузол автомобіля", "possible_matches": []}})
       
    print(f"[*] Пошук для запиту: {query}")
    offers = []
    seen_links = set()
   
    # 1. ЗАПИТ ДО GOOGLE IMAGES + ЦІНИ
    try:
        url = "https://google.serper.dev/images"
        payload = json.dumps({"q": f"{query} купити ціна", "gl": "ua", "hl": "uk", "num": 100})
        headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
       
        response = requests.post(url, headers=headers, data=payload, timeout=8)
        res_json = response.json()
       
        if "images" in res_json:
            for item in res_json["images"]:
                raw_link = item.get("link", "")
                raw_title = item.get("title", "")
                image_url = item.get("imageUrl", "")
                source = item.get("source", "shop")
               
                verified_link = verify_and_clean_link(raw_link)
                if not verified_link or verified_link in seen_links:
                    continue
                   
                try:
                    domain = urllib.parse.urlparse(verified_link).netloc.replace('www.', '')
                except:
                    domain = source
                   
                if "google.com" in domain:
                    continue
               
                # === ВИПРАВЛЕНИЙ ПОШУК ЦІН ===
                price_match = re.search(
                    r'(([\d\s\u00A0.,-]+)\s*(грн|UAH|PLN|zł|€|EUR|\$|USD|дол|евро|Euro|гривень))|'
                    r'((грн|UAH|PLN|zł|€|EUR|\$|USD|дол|евро|Euro|гривень)\s*([\d\s\u00A0.,-]+))',
                    raw_title, re.IGNORECASE
                )
                price = price_match.group(0).strip() if price_match else "Ціна на сайті"
               
                display_title = raw_title
                if price_match:
                    display_title = display_title.replace(price_match.group(0), "")
               
                display_title = re.sub(r'\s*[-\|•].*$', '', display_title)
                display_title = re.sub(r'(купити|ціна|цена|интернет магазин|shop|price).*?$', '', display_title, flags=re.IGNORECASE)
                display_title = display_title.strip()
               
                if len(display_title) > 42:
                    display_title = display_title[:39] + "..."
                if not display_title:
                    display_title = "Автозапчастина"
               
                country = "UA"
                if domain.endswith('.pl'): country = "PL"
                elif domain.endswith('.de'): country = "DE"
                elif domain.endswith('.cz'): country = "CZ"
               
                seen_links.add(verified_link)
                live_status = verify_live_availability(verified_link)
               
                offers.append({
                    "title": display_title,
                    "link": verified_link,
                    "source": domain,
                    "price": price,
                    "country": country,
                    "image": image_url,
                    "availability": live_status
                })
               
                if len(offers) >= 24:
                    break
    except Exception as e:
        print(f"[-] Помилка карток: {e}")

    # 2. КОНТЕКСТ ДЛЯ AI
    web_context = ""
    try:
        search_url = "https://google.serper.dev/search"
        search_payload = json.dumps({"q": f"{query} OEM part number cross reference", "gl": "ua", "hl": "uk", "num": 10})
        search_res = requests.post(search_url, headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}, data=search_payload, timeout=6)
        search_data = search_res.json()
        if "organic" in search_data:
            for org in search_data["organic"][:6]:
                web_context += f"{org.get('title')}\n{org.get('snippet')}\n\n"
    except Exception as e:
        print(f"[-] Контекст: {e}")

    # 3. ШІ АНАЛІЗ — ПОСИЛЕНИЙ ПРОМПТ
    metadata = None
    prompt = f"""Ти експерт по автозапчастинах.
Запит: {query}
Контекст з пошуку: {web_context}

Визнач:
- Тип деталі
- Модель авто
- OEM код

Відповідай ТІЛЬКИ JSON:
{{
  "part_type": "Назва деталі",
  "position": "Позиція",
  "possible_matches": [
    {{
      "model": "Марка і модель",
      "chassis": "Кузов",
      "years": "Роки",
      "oem_code": "OEM КОД"
    }}
  ]
}}"""

    try:
        ai_res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "google/gemini-2.5-flash:free", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=12
        )
        if ai_res.status_code == 200:
            raw_text = ai_res.json()['choices'][0]['message']['content'].strip()
            raw_text = re.sub(r'^```json\s*|```$', '', raw_text, flags=re.MULTILINE).strip()
            metadata = json.loads(raw_text)
    except Exception as e:
        print(f"[-] AI error: {e}")

    if not metadata:
        metadata = {
            "part_type": "Автозапчастина",
            "position": "Вузол автомобіля",
            "possible_matches": [{
                "model": "Універсальний тип",
                "chassis": "Заводські параметри",
                "years": "Всі роки",
                "oem_code": "Перевірте специфікацію лоту"
            }]
        }

    return jsonify({
        "offers": offers,
        "metadata": metadata
    })

if __name__ == '__main__':
    print("[*] Сервер PARTS FINDER v8.5 [Fixed Prices + OEM] запущено!")
    app.run(debug=True, port=5000)
