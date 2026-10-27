"""特价网站订阅：读取 Secret Flying 等网站的 RSS，按出发地筛选。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from ..models import Deal

# 机场代码 → 特价帖子里常见的写法，用于判断是否和你的出发地有关
AIRPORT_KEYWORDS: dict[str, list[str]] = {
    "MAN": ["Manchester", "MAN"],
    "LHR": ["London", "Heathrow", "LHR"],
    "LGW": ["London", "Gatwick", "LGW"],
    "STN": ["London", "Stansted", "STN"],
    "LPL": ["Liverpool", "LPL"],
    "LBA": ["Leeds", "LBA"],
    "BHX": ["Birmingham", "BHX"],
    "EDI": ["Edinburgh", "EDI"],
    "GLA": ["Glasgow", "GLA"],
    "PVG": ["Shanghai", "PVG"],
    "PEK": ["Beijing", "PEK"],
    "CAN": ["Guangzhou", "CAN"],
    "HKG": ["Hong Kong", "HKG"],
}
_GENERIC_UK = ["UK", "United Kingdom"]
_PRICE = re.compile(r"([£€$¥])\s?(\d[\d,]*)")


def keywords_for(airports: list[str]) -> list[str]:
    words: list[str] = []
    for code in airports:
        words += AIRPORT_KEYWORDS.get(code.upper(), [code.upper()])
    return list(dict.fromkeys(words))


def match(text: str, keywords: list[str]) -> list[str]:
    hits = []
    for k in keywords:
        # 三字代码要求整词匹配，避免 "MAN" 命中 "Germany"
        pattern = rf"\b{re.escape(k)}\b"
        if re.search(pattern, text, flags=0 if k.isupper() and len(k) == 3 else re.IGNORECASE):
            hits.append(k)
    return hits


def extract_price(text: str) -> str | None:
    m = _PRICE.search(text)
    return f"{m.group(1)}{m.group(2)}" if m else None


def _published(entry) -> datetime | None:
    for key in ("published", "updated"):
        if entry.get(key):
            try:
                dt = parsedate_to_datetime(entry[key])
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def parse_feed(content: bytes | str, source: str, keywords: list[str], include_uk_wide: bool = True) -> list[Deal]:
    feed = feedparser.parse(content)
    deals: list[Deal] = []
    for e in feed.entries:
        title = e.get("title", "")
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        text = f"{title} {summary}"
        hits = match(text, keywords)
        if include_uk_wide and not hits:
            hits = [h for h in match(text, _GENERIC_UK)]
        deals.append(
            Deal(
                title=title,
                link=e.get("link", ""),
                published=_published(e),
                source=source,
                summary=summary.strip()[:300],
                matched=hits,
            )
        )
    return deals


async def fetch_deals(urls: list[str], airports: list[str], client: httpx.AsyncClient | None = None) -> tuple[list[Deal], list[str]]:
    """返回（特价列表，错误信息）。命中出发地的排在前面。"""
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "flightplanner/0.1 (personal RSS reader)"})
    keywords = keywords_for(airports)
    deals: list[Deal] = []
    errors: list[str] = []
    for url in urls:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            errors.append(f"{url}：{e}")
            continue
        source = httpx.URL(url).host.removeprefix("www.")
        deals += parse_feed(resp.content, source, keywords)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    deals.sort(key=lambda d: (not d.matched, -(d.published or epoch).timestamp()))
    return deals, errors
