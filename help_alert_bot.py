#!/usr/bin/env python3
"""
Help-request alert bot (English + Arabic) -> Telegram        v2 (audited)

Watches Reddit, Stack Exchange and any RSS feeds. Sends a Telegram alert when a
NEW post sounds like a request for help about your topics.

Modes:
    python help_alert_bot.py --selftest   # offline keyword tests, no setup needed
    python help_alert_bot.py --dry-run    # fetch real posts, print matches, send nothing
    python help_alert_bot.py --test       # send one test message to Telegram
    python help_alert_bot.py --once       # single pass then exit (GitHub Actions / cron)
    python help_alert_bot.py              # run forever

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Install:  pip install requests feedparser
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# CONFIG - edit this section
# ----------------------------------------------------------------------------

CHECK_EVERY_SECONDS = 300          # loop mode only

# A post must mention one of these topics...
TOPIC_KEYWORDS = [
    # English - software
    "petrel", "eclipse e100", "eclipse e300", "eclipse simulator", "intersect",
    "arcgis", "arcmap", "arcpy", "qgis", "autocad", "civil 3d", "gis",
    "kingdom suite", "techlog", "interactive petrophysics", "global mapper",
    "golden software", "surfer", "geoserver", "postgis", "envi", "erdas",
    "global mapper", "landmark", "openworks", "decision space", "cmg", "tnavigator",
    "prosper", "pipesim", "ocean plugin", "ipm",
    # English - concepts
    "georeferencing", "shapefile", "geodatabase", "well log", "well logs",
    "seismic interpretation", "reservoir simulation", "petrophysics",
    "history matching", "velocity model", "geomodel", "facies model",
    # Arabic (no leading "ال" needed - it is handled automatically)
    "بيترل", "ارك جي اي اس", "ارك جيس", "كيو جيس", "اوتوكاد",
    "جي اي اس", "نظم المعلومات الجغرافية", "تفسير زلزالي", "محاكاة المكامن",
    "هندسة البترول", "جيولوجيا", "جيولوجي", "جيوفيزياء", "بتروفيزياء",
    "جيوريفرنس", "شيب فايل",
]

# ...and (if REQUIRE_HELP_PHRASE) also sound like they need help.
REQUIRE_HELP_PHRASE = True
HELP_KEYWORDS = [
    # English
    "help", "need help", "how do i", "how to", "how can i", "issue", "issues",
    "problem", "problems", "error", "errors", "not working", "doesn't work",
    "does not work", "crash", "crashes", "stuck", "can't", "cannot", "won't",
    "unable", "failed", "fails", "any idea", "anyone know", "?",
    # Arabic
    "مساعده", "ساعدوني", "ساعدني", "محتاج", "احتاج", "كيف", "كيفيه",
    "مشكله", "مشاكل", "خطا", "اخطاء", "لا يعمل", "لا يشتغل", "ما يشتغل",
    "ما اشتغل", "ما يفتح", "علقت", "عالق", "هل يمكن", "ممكن حد", "؟",
]

# Any match here drops the post (job ads, promos...). Empty list = disabled.
EXCLUDE_KEYWORDS = ["hiring", "we are hiring", "job opening", "discount code",
                    "تخفيض", "نوظف", "وظيفه شاغره"]

# Niche subreddits where EVERY post is on-topic, so the topic keyword is not
# required (only the help phrase). Names are case-insensitive.
TOPIC_OPTIONAL_SUBREDDITS = {"gis", "qgis", "autocad", "arcgis",
                             "petroleumengineering", "geophysics"}

REDDIT_SUBREDDITS = [
    "gis", "QGIS", "AutoCAD", "geology", "petroleumengineering",
    "Geophysics", "ArcGIS", "civilengineering",
]

# Stack Exchange tag feeds (no API quota): (site domain, tag).
# Every question there is a help request, so keywords are not required.
STACKEXCHANGE_TAGS = [
    ("gis.stackexchange.com", "autocad"),
    ("gis.stackexchange.com", "arcgis-desktop"),
    ("gis.stackexchange.com", "qgis"),
    ("gis.stackexchange.com", "arcpy"),
    ("earthscience.stackexchange.com", "geology"),
]

# Extra RSS/Atom feeds (Esri Community, Autodesk, SLB Community, SPE...).
# Keyword rules apply to these. Example: "https://example.com/board/rss"
EXTRA_RSS_FEEDS = []

STATE_FILE = Path(__file__).with_name("seen.json")
USER_AGENT = "help-alert-bot/2.0 (personal keyword alerts)"
MAX_ITEMS_REMEMBERED = 20000

# ----------------------------------------------------------------------------
# Text normalisation (Arabic + English)
# ----------------------------------------------------------------------------

_AR_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_AR_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "ـ": "", "؟": "?", "،": ",",
    "’": "'", "‘": "'", "`": "'", "“": '"', "”": '"',
})
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_AR = "\u0621-\u064A"
_AR_PREFIX = r"(?:وال|بال|كال|فال|لل|ولل|ال|و|ف|ب|ل|ك)?"


def normalize(text: str) -> str:
    """Lowercase, strip URLs + Arabic diacritics, unify letter variants."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _URL_RE.sub(" ", text)
    text = _AR_DIACRITICS.sub("", text).translate(_AR_MAP)
    return text.lower()


def _is_arabic(s: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", s))


def compile_keywords(words):
    """
    English -> whole-word match ('gis' will not match 'logistics').
    Arabic  -> whole-word match that tolerates attached prefixes (ال/و/ب/ل...)
               and up to 2 trailing letters (plurals/pronouns).
    Symbols ('?') -> plain substring.
    """
    out = []
    for w in words:
        n = normalize(w).strip()
        if not n:
            continue
        esc = re.escape(n)
        if _is_arabic(n):
            pat = rf"(?<![{_AR}]){_AR_PREFIX}{esc}[{_AR}]{{0,2}}(?![{_AR}])"
        elif re.search(r"\w", n):
            pat = rf"(?<!\w){esc}(?!\w)"
        else:
            pat = esc
        out.append((w, re.compile(pat)))
    return out


TOPIC_RE = compile_keywords(TOPIC_KEYWORDS)
HELP_RE = compile_keywords(HELP_KEYWORDS)
EXCLUDE_RE = compile_keywords(EXCLUDE_KEYWORDS)


def matches(title, body, topic_required=True, help_required=None):
    """Return list of matched topic labels, or None if the post should be skipped."""
    if help_required is None:
        help_required = REQUIRE_HELP_PHRASE
    text = normalize(f"{title} {body}")
    if any(p.search(text) for _, p in EXCLUDE_RE):
        return None
    topics = [w for w, p in TOPIC_RE if p.search(text)]
    if topic_required and not topics:
        return None
    if help_required and not any(p.search(text) for _, p in HELP_RE):
        return None
    return topics or ["(on-topic source)"]

# ----------------------------------------------------------------------------
# Sources - each returns dicts: id, title, body, url, source, topic_required, help_required
# ----------------------------------------------------------------------------

def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def fetch_feed(url, source, topic_required=True, help_required=None, retry_urls=()):
    import feedparser
    import requests
    last = None
    for u in (url, *retry_urls):
        try:
            resp = SESSION.get(u, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            return [{
                "id": e.get("id") or e.get("link"),
                "title": strip_html(e.get("title", "")),
                "body": strip_html(e.get("summary", ""))[:1500],
                "url": e.get("link", ""),
                "source": source,
                "topic_required": topic_required,
                "help_required": help_required,
            } for e in feed.entries]
        except requests.RequestException as exc:
            last = exc
    raise last


def fetch_reddit(sub):
    tr = sub.lower() not in TOPIC_OPTIONAL_SUBREDDITS
    return fetch_feed(
        f"https://www.reddit.com/r/{sub}/new/.rss?limit=50", f"Reddit r/{sub}",
        topic_required=tr,
        retry_urls=(f"https://old.reddit.com/r/{sub}/new/.rss?limit=50",),
    )


def fetch_se(domain, tag):
    return fetch_feed(f"https://{domain}/feeds/tag/{tag}",
                      f"{domain.split('.')[0]}.SE [{tag}]",
                      topic_required=False, help_required=False)


def all_sources():
    for sub in REDDIT_SUBREDDITS:
        yield f"reddit:{sub.lower()}", (lambda s=sub: fetch_reddit(s))
    for dom, tag in STACKEXCHANGE_TAGS:
        yield f"se:{dom}:{tag}", (lambda d=dom, t=tag: fetch_se(d, t))
    for url in EXTRA_RSS_FEEDS:
        yield f"rss:{url}", (lambda u=url: fetch_feed(u, "RSS " + u.split("/")[2]))

# ----------------------------------------------------------------------------
# Telegram + state
# ----------------------------------------------------------------------------

def tg(token, method, payload):
    for attempt in range(2):
        r = SESSION.post(f"https://api.telegram.org/bot{token}/{method}",
                         json=payload, timeout=20)
        if r.status_code == 429 and attempt == 0:
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(min(int(wait) + 1, 60))
            continue
        if not r.ok:
            raise RuntimeError(f"Telegram {method} -> {r.status_code}: {r.text[:200]}")
        return r.json()


def send_alert(token, chat_id, item, topics):
    snippet = item["body"][:300] + ("…" if len(item["body"]) > 300 else "")
    text = (
        f"🔔 <b>{html.escape(item['title'])}</b>\n"
        f"📍 {html.escape(item['source'])}\n"
        f"🏷 {html.escape(', '.join(topics[:5]))}\n\n"
        f"{html.escape(snippet)}\n\n"
        f"{html.escape(item['url'])}"
    )
    tg(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000],
                              "parse_mode": "HTML",
                              "disable_web_page_preview": True})


def load_state():
    """Returns (seen: dict, titles: dict, sources: set, fresh: bool)."""
    if STATE_FILE.exists():
        try:
            d = json.loads(STATE_FILE.read_text())
            if isinstance(d, list):  # v1 format
                return dict.fromkeys(d), {}, set(), False
            return (dict.fromkeys(d.get("seen", [])), dict.fromkeys(d.get("titles", [])),
                    set(d.get("sources", [])), False)
        except Exception as exc:
            log(f"[warn] state file unreadable ({exc}); starting fresh")
    return {}, {}, set(), True


def save_state(seen, titles, sources):
    # Deterministic order => file only changes when something new is seen
    # (avoids a pointless git commit on every run).
    STATE_FILE.write_text(json.dumps({
        "seen": list(seen)[-MAX_ITEMS_REMEMBERED:],
        "titles": list(titles)[-5000:],
        "sources": sorted(sources),
    }, ensure_ascii=False))


def title_key(title):
    return hashlib.sha1(re.sub(r"\W+", " ", normalize(title)).strip().encode()).hexdigest()[:16]

# ----------------------------------------------------------------------------
# Self-test (offline)
# ----------------------------------------------------------------------------

SELFTESTS = [
    # (title, body, topic_required, expected, note)
    ("Need help with Petrel well tops import", "", True, True, "EN topic+help"),
    ("ArcGIS crashes when exporting layout", "", True, True, "EN crash"),
    ("ArcGIS won’t open, can’t login", "", True, True, "curly apostrophes"),
    ("Need help with logistics trucks", "", True, False, "'gis' not inside 'logistics'"),
    ("United Kingdom visa help", "", True, False, "no 'kingdom' false positive"),
    ("Solar eclipse tonight, any tips?", "", True, False, "no 'eclipse' false positive"),
    ("Beautiful map poster", "https://x.com/a?b=1 gis", True, False, "URL '?' ignored"),
    ("Hiring GIS analyst, questions?", "", True, False, "exclude list"),
    ("مساعدة في برنامج بيترل", "", True, True, "AR topic+help"),
    ("عندي مشكلة في الاوتوكاد", "", True, True, "AR prefix ال"),
    ("كيف اعمل جيوريفرنس في كيو جيس؟", "", True, True, "AR question mark/كيف"),
    ("عندي مشاكل في الجيولوجيا", "", True, True, "AR plural + ال"),
    ("أحتاج مساعدة بخصوص ArcGIS", "", True, True, "mixed AR/EN + hamza"),
    ("مساعدة في تكييف الهواء", "", True, False, "AR unrelated"),
    ("Need help georeferencing a scanned map", "", False, True, "trusted-source mode"),
    ("Look at my new map", "", False, False, "trusted-source, no help phrase"),
]


def selftest():
    bad = 0
    for title, body, topic_req, expected, note in SELFTESTS:
        got = bool(matches(title, body, topic_required=topic_req))
        ok = got == expected
        bad += not ok
        print(("PASS" if ok else "FAIL"), f"| {note:34s} | {title[:45]}")
    print(f"\n{len(SELFTESTS) - bad}/{len(SELFTESTS)} passed")
    return bad == 0

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    args = set(sys.argv[1:])
    if "--selftest" in args:
        sys.exit(0 if selftest() else 1)

    global SESSION
    import requests
    SESSION = requests.Session()
    SESSION.headers["User-Agent"] = USER_AGENT

    dry = "--dry-run" in args
    once = "--once" in args or dry
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not dry:
        if not token or not chat_id:
            raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        try:
            tg(token, "getMe", {})
        except Exception as exc:
            raise SystemExit(f"Telegram token check failed: {exc}")
        if "--test" in args:
            send_alert(token, chat_id, {"title": "Test message ✅", "source": "help-alert-bot",
                                        "body": "Telegram is configured correctly.",
                                        "url": ""}, ["test"])
            log("Test message sent.")
            return

    seen, titles, sources, fresh = load_state()
    if dry:
        seen, titles, sources = {}, {}, set()

    while True:
        for name, fetch in all_sources():
            try:
                items = fetch()
            except Exception as exc:
                log(f"[warn] {name}: {exc}")
                continue

            bootstrap = (name not in sources) and not dry
            stop_source = False
            for item in items:
                if stop_source or not item["id"] or item["id"] in seen:
                    continue
                if bootstrap:                      # first time we see this source:
                    seen[item["id"]] = None        # remember posts, don't alert
                    continue
                topics = matches(item["title"], item["body"],
                                 item["topic_required"], item["help_required"])
                tk = title_key(item["title"])
                if not topics or tk in titles:     # no match, or cross-post duplicate
                    seen[item["id"]] = None
                    continue
                if dry:
                    print(f"MATCH [{item['source']}] {item['title'][:80]}  {topics[:3]}")
                    seen[item["id"]] = None
                    continue
                try:
                    send_alert(token, chat_id, item, topics)
                    seen[item["id"]] = None
                    titles[tk] = None
                    log(f"[sent] {item['title'][:70]}")
                except Exception as exc:           # not marked seen -> retried next pass
                    log(f"[warn] {exc}")
                    stop_source = True
            if bootstrap:
                sources.add(name)
                log(f"[init] {name}: recorded {len(items)} existing posts")
            if not dry:
                save_state(seen, titles, sources)
            time.sleep(2)

        if fresh and not dry:
            fresh = False
            try:
                send_alert(token, chat_id, {"title": "Bot is running ✅", "source": "help-alert-bot",
                                            "body": "You'll be notified when matching help requests appear.",
                                            "url": ""}, ["startup"])
            except Exception as exc:
                log(f"[warn] {exc}")
        if once:
            return
        time.sleep(CHECK_EVERY_SECONDS)


SESSION = None

if __name__ == "__main__":
    main()
