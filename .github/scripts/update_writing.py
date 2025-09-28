# -*- coding: utf-8 -*-
import os, re, sys, json, datetime
from dateutil import parser as dtparser
import requests, feedparser

QIITA_USER = os.getenv("QIITA_USER", "Sh1ragami")
NOTE_USER = os.getenv("NOTE_USER", "sh1ragami")
README_PATH = os.getenv("README_PATH", "README.md")
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "5"))
QIITA_TOKEN = os.getenv("QIITA_TOKEN")

START = "<!--START:WRITING-->"
END = "<!--END:WRITING-->"


def fetch_qiita(user: str, max_items: int):
    url = f"https://qiita.com/api/v2/users/{user}/items?per_page={max_items}"
    headers = {"Accept": "application/json"}
    if QIITA_TOKEN:
        headers["Authorization"] = f"Bearer {QIITA_TOKEN}"
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    items = r.json()
    posts = []
    for it in items:
        title = it.get("title", "Untitled")
        link = it.get("url")
        date = it.get("created_at") or it.get("updated_at")
        dt = dtparser.parse(date).date() if date else None
        posts.append({
            "source": "Qiita",
            "title": title,
            "link": link,
            "date": dt.isoformat() if dt else "",
        })
    return posts


def fetch_note(user: str, max_items: int):
    # noteはRSS/Atomフィードが提供されています
    url = f"https://note.com/{user}/rss"
    feed = feedparser.parse(url)
    posts = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "Untitled")
        link = getattr(entry, "link", None)
        date = getattr(entry, "published", None) or getattr(entry, "updated", None)
        dt = dtparser.parse(date).date() if date else None
        posts.append({
            "source": "note",
            "title": title,
            "link": link,
            "date": dt.isoformat() if dt else "",
        })
    return posts


def render_section(posts):
    # 日付降順でミックス（Qiita+note）
    posts_sorted = sorted(
        posts, key=lambda x: x.get("date", ""), reverse=True
    )[:MAX_ITEMS]

    if not posts_sorted:
        return "- （まだ投稿が見つかりませんでした）"

    lines = []
    for p in posts_sorted:
        date = p["date"]
        src = p["source"]
        title = p["title"].strip()
        link = p["link"]
        # 例: - 2025-09-26 · Qiita — 記事タイトル
        lines.append(f"- {date} · **{src}** — [{title}]({link})")
    return "\n".join(lines)


def replace_in_readme(readme_path, new_block):
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        rf"({re.escape(START)})(.*?){re.escape(END)}",
        flags=re.DOTALL
    )
    if not pattern.search(content):
        print("Markers not found in README. Please add START/END markers.", file=sys.stderr)
        sys.exit(1)

    updated = pattern.sub(rf"\1\n{new_block}\n{END}", content)

    if updated != content:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(updated)
        print("README updated.")
    else:
        print("No change detected.")


def main():
    try:
        qiita_posts = fetch_qiita(QIITA_USER, MAX_ITEMS)
    except Exception as e:
        print(f"[warn] Qiita fetch failed: {e}")
        qiita_posts = []

    try:
        note_posts = fetch_note(NOTE_USER, MAX_ITEMS)
    except Exception as e:
        print(f"[warn] note fetch failed: {e}")
        note_posts = []

    merged = qiita_posts + note_posts
    block = render_section(merged)
    replace_in_readme(README_PATH, block)


if __name__ == "__main__":
    main()
