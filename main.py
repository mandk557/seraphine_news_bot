import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

# ============ НАСТРОЙКИ ============

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Ключевые слова для поиска (регистр не важен)
KEYWORDS = ["seraphine", "серафина", "серафину", "серафины"]

# Twitter-аккаунты, за которыми следим (без @).
TWITTER_ACCOUNTS = [
    "SkinSpotlights",
    "BigBadBear1000",
    "LoLEsportsNA",
    "RiotGames",
]

# Публичные Nitter-зеркала
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.tiekoetter.com",
]

# RSS фан-сайтов про LoL
RSS_FEEDS = [
    "https://www.surrenderat20.net/feeds/posts/default",
]

SEEN_FILE = "seen_ids.json"
MAX_MESSAGES_PER_RUN = 15  # защита от спама

# Ключ чемпиона в Data Dragon API Riot
CHAMPION_KEY = "Seraphine"
MSK = ZoneInfo("Europe/Moscow")


# ============ УТИЛИТЫ ============


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    seen_list = list(seen)[-5000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list, f, ensure_ascii=False)


def contains_keyword(text):
    text_l = text.lower()
    return any(k in text_l for k in KEYWORDS)


def clean_html(raw_html):
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def send_telegram(text, link=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if link:
        text = f"{text}\n{link}"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    if not resp.ok:
        print("Ошибка отправки в Telegram:", resp.status_code, resp.text)


def send_telegram_photo(caption, photo_url):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
            "photo": photo_url,
        },
        timeout=20,
    )
    if not resp.ok:
        print("Ошибка отправки фото в Telegram:", resp.status_code, resp.text)
        send_telegram(caption, photo_url)


# ============ ПРОВЕРКА КОМАНДЫ /PING ============


def check_ping_command(seen):
    """Проверяет входящие сообщения в Telegram и отвечает на /ping или /status"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            return

        data = resp.json()
        if not data.get("ok"):
            return

        for result in data.get("result", []):
            update_id = f"cmd_{result.get('update_id')}"
            message = result.get("message", {})
            text = message.get("text", "").strip().lower()
            chat_id = str(message.get("chat", {}).get("id"))

            # Проверяем, что сообщение из нашего чата, это команда ping/status и её еще не обрабатывали
            if (
                chat_id == str(TELEGRAM_CHAT_ID)
                and text in ["/ping", "/status", "ping"]
                and update_id not in seen
            ):
                now_msk = datetime.now(MSK).strftime("%d.%m.%Y %H:%M:%S")
                send_telegram(
                    f"🏓 **Pong!**\n"
                    f"✅ Бот работает исправно.\n"
                    f"🕒 Время проверки: {now_msk} (МСК)"
                )
                seen.add(update_id)
    except Exception as e:
        print("Ошибка проверки команд Telegram:", e)


# ============ ИСТОЧНИК 0: ВЫХОД СКИНА В ИГРЕ ============


def check_skin_release(seen, found):
    try:
        versions_resp = requests.get(
            "https://ddragon.leagueoflegends.com/api/versions.json", timeout=20
        )
        if not versions_resp.ok:
            return
        latest_version = versions_resp.json()[0]

        champ_url = (
            f"https://ddragon.leagueoflegends.com/cdn/{latest_version}"
            f"/data/en_US/champion/{CHAMPION_KEY}.json"
        )
        champ_resp = requests.get(champ_url, timeout=20)
        if not champ_resp.ok:
            return
        champ_data = champ_resp.json()
        skins = champ_data.get("data", {}).get(CHAMPION_KEY, {}).get("skins", [])

        now_msk = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        is_first_run = not any(
            s.startswith(f"skin_{CHAMPION_KEY}_") for s in seen
        )

        for skin in skins:
            num = skin.get("num")
            name = skin.get("name")
            if num is None or name in (None, "default"):
                continue

            uid = f"skin_{CHAMPION_KEY}_{num}"
            if uid in seen:
                continue

            if is_first_run:
                seen.add(uid)
                continue

            splash_url = (
                f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/"
                f"{CHAMPION_KEY}_{num}.jpg"
            )

            found.append(
                {
                    "id": uid,
                    "text": (
                        f"🎉 Новый скин Seraphine вышел в игре!\n"
                        f"Название: {name}\n"
                        f"Обнаружено: {now_msk} (МСК)\n"
                        f"Версия патча: {latest_version}"
                    ),
                    "photo": splash_url,
                }
            )
    except Exception as e:
        print("Ошибка проверки релиза скина:", e)


# ============ ИСТОЧНИК 1: REDDIT ============


def check_reddit(seen, found):
    for kw in KEYWORDS:
        try:
            url = "https://www.reddit.com/search.json"
            params = {"q": kw, "sort": "new", "limit": 15, "t": "day"}
            headers = {"User-Agent": "seraphine-news-bot/1.0"}
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if not r.ok:
                continue
            data = r.json()
            for post in data.get("data", {}).get("children", []):
                p = post["data"]
                uid = "reddit_" + p["id"]
                if uid in seen:
                    continue
                title = p.get("title", "")
                if not contains_keyword(title) and not contains_keyword(
                    p.get("selftext", "")
                ):
                    continue
                permalink = "https://www.reddit.com" + p.get("permalink", "")
                found.append(
                    {
                        "id": uid,
                        "text": f"🟠 Reddit (r/{p.get('subreddit')}): {title}",
                        "link": permalink,
                    }
                )
        except Exception as e:
            print("Ошибка Reddit:", e)


# ============ ИСТОЧНИК 2: RSS (фан-сайты) ============


def check_rss(seen, found):
    for feed_url in RSS_FEEDS:
        try:
            r = requests.get(feed_url, timeout=20)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            if entries:
                for entry in entries:
                    title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                    link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                    content_el = entry.find(
                        "{http://www.w3.org/2005/Atom}content"
                    )
                    title = title_el.text if title_el is not None else ""
                    content = (
                        content_el.text if content_el is not None else ""
                    )
                    link = (
                        link_el.get("href") if link_el is not None else ""
                    )
                    uid = "rss_" + (link or title)
                    if uid in seen:
                        continue
                    if not contains_keyword(title) and not contains_keyword(
                        content or ""
                    ):
                        continue
                    found.append(
                        {
                            "id": uid,
                            "text": f"📰 Новость: {clean_html(title)}",
                            "link": link,
                        }
                    )
            else:
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    uid = "rss_" + (link or title)
                    if uid in seen:
                        continue
                    if not contains_keyword(title) and not contains_keyword(
                        desc
                    ):
                        continue
                    found.append(
                        {
                            "id": uid,
                            "text": f"📰 Новость: {clean_html(title)}",
                            "link": link,
                        }
                    )
        except Exception as e:
            print("Ошибка RSS", feed_url, ":", e)


# ============ ИСТОЧНИК 3: TWITTER (через Nitter) ============


def check_twitter(seen, found):
    for account in TWITTER_ACCOUNTS:
        entries = None
        for instance in NITTER_INSTANCES:
            try:
                url = f"{instance}/{account}/rss"
                r = requests.get(
                    url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}
                )
                if r.ok and "<rss" in r.text.lower():
                    root = ET.fromstring(r.content)
                    entries = root.findall(".//item")
                    break
            except Exception:
                continue
        if not entries:
            continue
        for item in entries[:20]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            desc = item.findtext("description", "")
            uid = "tw_" + (link or title)
            if uid in seen:
                continue
            if not contains_keyword(title) and not contains_keyword(desc):
                continue
            found.append(
                {
                    "id": uid,
                    "text": f"🐦 @{account}: {clean_html(title)}",
                    "link": link,
                }
            )


# ============ ГЛАВНАЯ ЛОГИКА ============


def main():
    seen = load_seen()

    # Сначала проверяем, не отправлял ли пользователь команду /ping
    check_ping_command(seen)

    found = []
    check_skin_release(seen, found)
    check_reddit(seen, found)
    check_rss(seen, found)
    check_twitter(seen, found)

    if not found:
        print("Новых упоминаний Seraphine не найдено.")
        save_seen(seen)
        return

    for item in found[:MAX_MESSAGES_PER_RUN]:
        if "photo" in item:
            send_telegram_photo(item["text"], item["photo"])
        else:
            send_telegram(item["text"], item.get("link"))
        seen.add(item["id"])
        time.sleep(1)

    save_seen(seen)
    print(f"Отправлено сообщений: {min(len(found), MAX_MESSAGES_PER_RUN)}")


if __name__ == "__main__":
    main()