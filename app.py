import os
import re
import json
import urllib.parse
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)




def get_live_page_details(url):
    """ Заходить на сайт, витягує ціну, наявність та OEM-код """
    default_res = ("В наявності", "Ціна на сайті", "")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code != 200:
            return default_res
       
        html_text = response.text
        html_lower = html_text.lower()

        # --- НАЯВНІСТЬ ---
        stop_words = ["немає в наявності", "немає на складі", "товар закінчився", "нет в наличии", "out of stock", "sold out", "brak na stanie"]
        availability = "В наявності"
        for word in stop_words:
            if word in html_lower:
                availability = "Немає в наявності"
                break

        # --- ВАЛЮТА ---
        currency = ""
        meta_curr = re.search(r'property="og:price:currency"\s+content="([^"]+)"|itemprop="priceCurrency"[^>]*content="([^"]+)"|"priceCurrency"\s*:\s*"([^"]+)"', html_text, re.I)
        if meta_curr:
            raw = next((g for g in meta_curr.groups() if g), "").strip().upper()
            currency_map = {"UAH": "грн", "USD": "$", "EUR": "€", "PLN": "zł", "ГРН": "грн"}
            currency = currency_map.get(raw, raw)
        if not currency:
            currency = "zł" if ".pl" in url else "грн"

        # --- ЦІНА ---
        price = "Ціна на сайті"
        for pattern in [
            r'property="og:price:amount"\s+content="([^"]+)"',
            r'itemprop="price"[^>]*content="([^"]+)"',
            r'"price"\s*:\s*"([^"]+)"',
            r'"price"\s*:\s*([0-9.,]+)'
        ]:
            m = re.search(pattern, html_text, re.I)
            if m and m.group(1):
                price = f"{m.group(1).strip()} {currency}"
                break
        if price == "Ціна на сайті":
            price_match = re.search(r'(\b\d[\d\s.,]*\s*(?:грн|UAH|zł|PLN|€|\$))', html_text, re.I)
            if price_match:
                price = price_match.group(0).strip()

        # =======================================================
        # ПОКРАЩЕНИЙ ПОШУК OEM-КОДУ
        # =======================================================
        detected_oem = ""
        patterns = [
            r'itemprop=["\']sku["\'][^>]*content=["\']([^"\']+)["\']',
            r'itemprop=["\']mpn["\'][^>]*content=["\']([^"\']+)["\']',
            r'"sku"\s*:\s*["\']([^"\']+)["\']',
            r'"mpn"\s*:\s*["\']([^"\']+)["\']',
            r'ОЕМ[:\s]*([A-Z0-9-]{5,15})',
            r'Артикул[:\s]*([A-Z0-9-]{5,15})'
        ]
        for pat in patterns:
            m = re.search(pat, html_text, re.I)
            if m and m.group(1):
                candidate = m.group(1).strip()
                if len(candidate) >= 5 and any(c.isdigit() for c in candidate):
                    detected_oem = candidate
                    break

        if not detected_oem or len(detected_oem) < 6:
            candidates = re.findall(r'\b([0-9A-Z]{5,12}(?:-[0-9A-Z]{1,3})?)\b', html_text)
            for cand in candidates:
                if (any(c.isdigit() for c in cand) and
                    not cand.startswith(('20', '19', '202')) and
                    len(cand) >= 6):
                    detected_oem = cand
                    break

        return availability, price, detected_oem
    except Exception:
        return default_res


def verify_and_clean_link(raw_url):
    if not raw_url:
        return None
    clean_url = raw_url
    if "google.com/url?" in raw_url or "/url?" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query = urllib.parse.parse_qs(parsed.query)
        clean_url = query.get('url', query.get('q', [raw_url]))[0]
   
    url_lower = clean_url.lower()
    if any(t in url_lower for t in ["catalog/search", "query", "q=", "/search", "olx.ua/uk/list", "prom.ua/ua/sc-"]):
        return None
    if "prom.ua" in url_lower and not ("/p" in url_lower or any(c.isdigit() for c in url_lower.split('/')[-1])):
        return None
    if "olx.ua" in url_lower and not ("/d/" in url_lower or ".html" in url_lower):
        return None
    if "allegro.pl" in url_lower and "/oferta/" not in url_lower:
        return None
    return clean_url


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/search', methods=['POST'])
def search():
    data = request.get_json() or {}
    query = data.get('query', '').strip()
   
    if not query:
        return jsonify({"offers": [], "metadata": {"part_type": "Автозапчастина", "position": "", "possible_matches": []}})
       
    print(f"[*] Пошук для: {query}")
    offers = []
    seen_links = set()

    try:
        payload = json.dumps({"q": f"{query} купити оригінал", "gl": "ua", "hl": "uk", "num": 40})
        headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
       
        response = requests.post("https://google.serper.dev/images", headers=headers, data=payload, timeout=10)
        res_json = response.json()

        for item in res_json.get("images", []):
            raw_link = item.get("link", "")
            verified_link = verify_and_clean_link(raw_link)
            if not verified_link or verified_link in seen_links:
                continue
            domain = urllib.parse.urlparse(verified_link).netloc.replace('www.', '')
            if "google.com" in domain:
                continue

            live_status, live_price, live_oem = get_live_page_details(verified_link)
            if live_status == "Немає в наявності":
                continue

            display_title = re.sub(r'\s*[-\|•].*$', '', item.get("title", "")).strip()
            display_title = re.sub(r'(купити|ціна|shop|price).*?$', '', display_title, flags=re.I).strip()
            if len(display_title) > 45:
                display_title = display_title[:42] + "..."

            offers.append({
                "title": display_title or "Автозапчастина",
                "link": verified_link,
                "source": domain,
                "price": live_price,
                "country": "PL" if domain.endswith('.pl') else "UA",
                "image": item.get("imageUrl", ""),
                "availability": live_status,
                "extracted_oem": live_oem
            })
            seen_links.add(verified_link)
            if len(offers) >= 24:
                break
    except Exception as e:
        print(f"[-] Помилка пошуку: {e}")

    # =======================================================
    # ТЕХНІЧНИЙ КОНТЕКСТ ДЛЯ ШІ
    # =======================================================
    web_context = f"ЗАПИТ КОРИСТУВАЧА: {query}\n\nЗНАЙДЕНІ ЛОТИ:\n"
    for i, off in enumerate(offers[:15]):
        web_context += f"{i+1}. Назва: {off.get('title')} | OEM: {off.get('extracted_oem') or '—'} | Сайт: {off['source']}\n"

    # =======================================================
    # AI ЗАПИТ — ПІДПРАВЛЕНО ПІД ТВІЙ HTML
    # =======================================================
    prompt = f"""Ти — експерт з підбору автозапчастин.
Проаналізуй лоти і поверни **тільки** валідний JSON.

ДАНІ:
{web_context}

JSON схема:
{{
  "part_type": "Назва деталі українською",
  "position": "Де встановлюється",
  "main_oem": "ГОЛОВНИЙ OEM-КОД",
  "possible_matches": [
    {{
      "model": "МАРКА МОДЕЛЬ",
      "chassis": "Код шасі",
      "years": "Роки випуску",
      "oem_code": "Основний код",
      "oem_codes": [
        {{"type": "OEM", "code": "XXXXXXXX"}},
        {{"type": "Аналог", "code": "YYYYYYYY"}}
      ],
      "source": "джерело",
      "breakdown": ["опис 1", "опис 2"]
    }}
  ]
}}"""

    metadata = None
    try:
        ai_url = "https://text.pollinations.ai/"
        ai_res = requests.post(ai_url, json={
            "messages": [
                {"role": "system", "content": "Повертай тільки чистий JSON без будь-якого тексту поза ним."},
                {"role": "user", "content": prompt}
            ],
            "model": "openai"
        }, timeout=15)

        if ai_res.status_code == 200:
            raw = ai_res.text.strip()
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                metadata = json.loads(json_match.group(0))
    except Exception as e:
        print(f"[-] AI error: {e}")

    # Запасний варіант (адаптовано під фронт)
    if not metadata or not metadata.get("possible_matches"):
        first_oem = next((o['extracted_oem'] for o in offers if o.get('extracted_oem')), "")
        metadata = {
            "part_type": "Автозапчастина",
            "position": "Вузол автомобіля",
            "main_oem": first_oem,
            "possible_matches": [{
                "model": query.upper(),
                "chassis": "—",
                "years": "—",
                "oem_code": first_oem,
                "oem_codes": [{"type": "OEM", "code": first_oem}] if first_oem else [],
                "source": "Внутрішній парсинг",
                "breakdown": ["OEM витягнуто з оголошень"]
            }]
        }

    # Прибираємо службові поля
    for off in offers:
        off.pop("extracted_oem", None)

    return jsonify({
        "offers": offers,
        "metadata": metadata
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
