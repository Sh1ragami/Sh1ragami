# -*- coding: utf-8 -*-
"""
README の「Writing」セクションを Qiita / note の最新記事で自動更新します。
- 複数 README 対応: README_PATHS="README.md,README.en.md"（未指定なら README_PATH を使用）
- セクションの開始/終了マーカーは環境変数で変更可能
- Qiita は API（任意でトークン）、note は RSS から取得
- 通信はリトライつき、タイトルは Markdown エスケープ、重複URLは排除

推奨: GitHub Actions から実行
"""

import os
import re
import sys
import time
import html
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dateutil import parser as dtparser
import requests
import feedparser

# ========= 環境変数 =========
QIITA_USER = os.getenv("QIITA_USER", "Sh1ragami")
NOTE_USER = os.getenv("NOTE_USER", "sh1ragami")

# 単一 or 複数 README 対応
README_PATH = os.getenv("README_PATH", "README.md")
README_PATHS = [p.strip() for p in os.getenv("README_PATHS", "").split(",") if p.strip()]
if not README_PATHS:
    README_PATHS = [README_PATH]

MAX_ITEMS = int(os.getenv("MAX_ITEMS", "5"))
# 並び順: "desc" / "asc"
SORT_ORDER = os.getenv("SORT_ORDER", "desc").lower()
# マーカー
START = os.getenv("WRITING_SECTION_START", "<!--START:WRITING-->")
END = os.getenv("WRITING_SECTION_END", "<!--END:WRITING-->")

QIITA_TOKEN = os.getenv("QIITA_TOKEN")  # 任意
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
RETRY_TIMES = int(os.getenv("RETRY_TIMES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "1.5"))  # 1.5倍で指数的に後退
# 表示フォーマット（必要なら環境変数で差し替え可能）
LINE_TEMPLATE = os.getenv(
    "LINE_TEMPLATE",
    "- {date} · **{source}** — [{title}]({link})"
)
# 日付フォーマット（ISO形式固定にしたい場合は "%Y-%m-%d" などを設定）
DATE_FORMAT = os.getenv("DATE_FORMAT", "%Y-%m-%d")

# ========= ユーティリティ =========
def log(msg: str) -> None:
    print(msg, flush=True)

def md_escape(text: str) -> str:
    """
    Markdown のリンクテキストでトラブルになりがちな記号を最低限エスケープ。
    """
    text = html.unescape(text or "")
    return text.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(").replace(")", r"\)")

def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    """
    リトライ付き GET
    """
    last_err: Optional[Exception] = None
    for i in range(1, RETRY_TIMES + 1):
        try:
            r = requests.get(url, headers=headers or {}, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            sleep_sec = RETRY_BACKOFF ** (i - 1)
            log(f"[warn] GET failed (try {i}/{RETRY_TIMES}): {e} -> retry in {sleep_sec:.1f}s")
            time.sleep(sleep_sec)
    assert last_err is not None
    raise last_err

def parse_date(d: Optional[str]) -> Optional[datetime]:
    if not d:
        return None
    try:
        return dtparser.parse(d)
    except Exception:
        return None

def fmt_date(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime(DATE_FORMAT)
    except Exception:
        # フォーマット不正時は ISO にフォールバック
        return dt.date().isoformat()

# ========= 取得系 =========
def fetch_qiita(user: str, max_items: int) -> List[Dict[str, str]]:
    url = f"https://qiita.com/api/v2/users/{user}/items?per_page={max_items}"
    headers = {"Accept": "application/json"}
    if QIITA_TOKEN:
        headers["Authorization"] = f"Bearer {QIITA_TOKEN}"
    r = http_get(url, headers=headers)
    items = r.json()
    posts: List[Dict[str, str]] = []
    for it in items:
        title = it.get("title") or "Untitled"
        link = it.get("url") or ""
        # created_at が無い場合 updated_at を使う
        dt_raw = it.get("created_at") or it.get("updated_at")
        dt_parsed = parse_date(dt_raw)
        posts.append({
            "source": "Qiita",
            "title": title,
            "link": link,
            "date": fmt_date(dt_parsed),
            "dt_sort": dt_parsed.isoformat() if dt_parsed else "",
        })
    return posts

def fetch_note(user: str, max_items: int) -> List[Dict[str, str]]:
    url = f"https://note.com/{user}/rss"
    # feedparser は内部でHTTPしますが、失敗時のため先に到達確認をしておく
    _ = http_get(url)  # 200以外ならここで例外
    feed = feedparser.parse(url)
    posts: List[Dict[str, str]] = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "Untitled")
        link = getattr(entry, "link", "") or ""
        dt_raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
        dt_parsed = parse_date(dt_raw)
        posts.append({
            "source": "note",
            "title": title,
            "link": link,
            "date": fmt_date(dt_parsed),
            "dt_sort": dt_parsed.isoformat() if dt_parsed else "",
        })
    return posts

# ========= 整形・出力 =========
def merge_and_sort(posts: List[Dict[str, str]], max_items: int, order: str = "desc") -> List[Dict[str, str]]:
    # 重複URLで排除（Qiita/note双方に同タイトル/同リンクがあり得るため）
    seen: set = set()
    deduped: List[Dict[str, str]] = []
    for p in posts:
        key = p.get("link", "") or (p.get("source", "") + ":" + p.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    reverse = order != "asc"
    deduped.sort(key=lambda x: (x.get("dt_sort", ""), x.get("title", "")), reverse=reverse)
    return deduped[:max_items]

def render_lines(posts: List[Dict[str, str]]) -> str:
    if not posts:
        return "- （まだ投稿が見つかりませんでした）"
    lines: List[str] = []
    for p in posts:
        lines.append(LINE_TEMPLATE.format(
            date=p.get("date", ""),
            source=p.get("source", ""),
            title=md_escape((p.get("title") or "").strip()),
            link=p.get("link", ""),
        ))
    return "\n".join(lines)

def replace_in_file(path: str, start_marker: str, end_marker: str, new_block: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(rf"({re.escape(start_marker)})(.*?){re.escape(end_marker)}", flags=re.DOTALL)
    if not pattern.search(content):
        log(f"[err] Markers not found in {path}. Please add {start_marker} / {end_marker}.")
        return False

    updated = pattern.sub(rf"\1\n{new_block}\n{end_marker}", content)
    if updated != content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        log(f"[ok] README updated: {path}")
        return True
    else:
        log(f"[skip] No change: {path}")
        return False

# ========= メイン =========
def main() -> int:
    try:
        qiita_posts: List[Dict[str, str]] = []
        note_posts: List[Dict[str, str]] = []

        try:
            qiita_posts = fetch_qiita(QIITA_USER, MAX_ITEMS)
        except Exception as e:
            log(f"[warn] Qiita fetch failed: {e}")

        try:
            note_posts = fetch_note(NOTE_USER, MAX_ITEMS)
        except Exception as e:
            log(f"[warn] note fetch failed: {e}")

        merged = merge_and_sort(qiita_posts + note_posts, MAX_ITEMS, SORT_ORDER)
        block = render_lines(merged)

        changed_any = False
        for path in README_PATHS:
            if not os.path.exists(path):
                log(f"[warn] README not found: {path}")
                continue
            changed = replace_in_file(path, START, END, block)
            changed_any = changed_any or changed

        return 0 if changed_any else 0
    except Exception as e:
        log(f"[fatal] Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
