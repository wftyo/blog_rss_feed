#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError as exc:
    raise SystemExit("Missing dependency: beautifulsoup4. Run: pip install -r requirements.txt") from exc

try:
    from dateutil import parser as date_parser
except ModuleNotFoundError as exc:
    raise SystemExit("Missing dependency: python-dateutil. Run: pip install -r requirements.txt") from exc

ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle"}


@dataclass
class FeedItem:
    title: str
    link: str
    summary: str | None
    published: datetime | None


@dataclass
class FeedSource:
    source_id: str
    url: str
    site_url: str
    feed_title: str
    feed_description: str
    output_rss: Path
    max_items: int
    include_url_patterns: list[str]
    exclude_url_patterns: list[str]
    link_scope_selectors: list[str]
    use_json_ld: bool
    user_agent: str
    timeout_seconds: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate RSS feeds from blog listing pages")
    parser.add_argument("--config", default="config/sources.json", help="Path to JSON config")
    parser.add_argument("--source-id", help="Only process a single source_id")
    parser.add_argument("--html-file", help="Use local HTML file for parsing (debug/testing)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print results without writing XML")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def load_sources(config_path: Path) -> list[FeedSource]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        source_rows = raw.get("sources", [])
    elif isinstance(raw, list):
        source_rows = raw
    else:
        raise ValueError("Config must be an object or array")

    sources: list[FeedSource] = []
    for row in source_rows:
        source_url = row["url"].strip()
        site_url = row.get("site_url") or f"{urlparse(source_url).scheme}://{urlparse(source_url).netloc}"
        source_id = row["id"].strip()
        sources.append(
            FeedSource(
                source_id=source_id,
                url=source_url,
                site_url=site_url,
                feed_title=row.get("feed_title") or source_id,
                feed_description=row.get("feed_description") or f"Generated feed for {source_url}",
                output_rss=Path(row.get("output_rss") or f"feeds/{source_id}.rss.xml"),
                max_items=int(row.get("max_items", 30)),
                include_url_patterns=list(row.get("include_url_patterns", [])),
                exclude_url_patterns=list(row.get("exclude_url_patterns", [])),
                link_scope_selectors=list(row.get("link_scope_selectors", [])),
                use_json_ld=parse_bool(row.get("use_json_ld"), default=True),
                user_agent=row.get("user_agent") or "Mozilla/5.0 (compatible; blog-rss-feed/1.0)",
                timeout_seconds=int(row.get("timeout_seconds", 20)),
            )
        )
    return sources


def fetch_html(url: str, *, user_agent: str, timeout_seconds: int) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        raw = response.read()
        return raw.decode(charset, errors="replace")


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            parsed = date_parser.parse(value)
            return normalize_datetime(parsed)
        except Exception:
            return None
    return None


def normalize_link(href: str, base_url: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
        return None
    full_url = urljoin(base_url, href)
    parsed = urlparse(full_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return full_url


def same_url(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        parsed = urlparse(u)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    return norm(a) == norm(b)


def matches_patterns(url: str, include_patterns: list[str], exclude_patterns: list[str]) -> bool:
    if include_patterns and not any(re.search(pattern, url) for pattern in include_patterns):
        return False
    if exclude_patterns and any(re.search(pattern, url) for pattern in exclude_patterns):
        return False
    return True


def slug_to_title(link: str) -> str:
    slug = urlparse(link).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[-_]+", " ", slug)
    return slug.strip().title() or link


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def should_keep_link(link: str, source: FeedSource) -> bool:
    if same_url(link, source.url):
        return False
    return matches_patterns(link, source.include_url_patterns, source.exclude_url_patterns)


def extract_items_from_json_ld(soup: BeautifulSoup, source: FeedSource) -> list[FeedItem]:
    items: list[FeedItem] = []

    def node_types(node: dict[str, Any]) -> set[str]:
        type_value = node.get("@type")
        if isinstance(type_value, str):
            return {type_value}
        if isinstance(type_value, list):
            return {str(entry) for entry in type_value}
        return set()

    def node_url(node: dict[str, Any]) -> str | None:
        value = node.get("url")
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            url_value = value.get("@id")
            if isinstance(url_value, str):
                return url_value
        entity = node.get("mainEntityOfPage")
        if isinstance(entity, str):
            return entity
        if isinstance(entity, dict):
            entity_id = entity.get("@id")
            if isinstance(entity_id, str):
                return entity_id
        return None

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return

        types = node_types(node)
        if types.intersection(ARTICLE_TYPES):
            raw_link = node_url(node)
            link = normalize_link(raw_link or "", source.url)
            if link and should_keep_link(link, source):
                title = clean_text(node.get("headline") or node.get("name")) or slug_to_title(link)
                summary = clean_text(node.get("description"))
                published = parse_date(
                    node.get("datePublished") or node.get("dateCreated") or node.get("dateModified")
                )
                items.append(FeedItem(title=title, link=link, summary=summary, published=published))

        for value in node.values():
            walk(value)

    for script in soup.select("script[type='application/ld+json']"):
        raw_text = script.string or script.get_text() or ""
        raw_text = raw_text.strip()
        if not raw_text:
            continue
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        walk(payload)

    return items


def parse_nearby_date(anchor: Any) -> datetime | None:
    scope = [anchor]
    parent = anchor.parent
    steps = 0
    while parent is not None and steps < 3:
        scope.append(parent)
        parent = parent.parent
        steps += 1

    for node in scope:
        time_node = node.find("time") if hasattr(node, "find") else None
        if time_node:
            parsed = parse_date(time_node.get("datetime") or time_node.get_text(" ", strip=True))
            if parsed:
                return parsed

    return None


def select_link_anchors(soup: BeautifulSoup, source: FeedSource) -> list[Any]:
    if not source.link_scope_selectors:
        return list(soup.select("a[href]"))

    anchors: list[Any] = []
    for selector in source.link_scope_selectors:
        for container in soup.select(selector):
            anchors.extend(container.select("a[href]"))

    if not anchors:
        logging.warning(
            "No anchors found in configured link scopes source=%s selectors=%s",
            source.source_id,
            source.link_scope_selectors,
        )
    return anchors


def extract_items_from_links(soup: BeautifulSoup, source: FeedSource) -> list[FeedItem]:
    items: list[FeedItem] = []

    for anchor in select_link_anchors(soup, source):
        link = normalize_link(anchor.get("href", ""), source.url)
        if not link or not should_keep_link(link, source):
            continue

        title = clean_text(anchor.get_text(" ", strip=True))
        if not title or len(title) < 8:
            title = slug_to_title(link)

        summary = None
        container_text = clean_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else None
        if container_text and container_text != title:
            summary = container_text

        items.append(
            FeedItem(
                title=title,
                link=link,
                summary=summary,
                published=parse_nearby_date(anchor),
            )
        )

    return items


def dedupe_and_rank(items: list[FeedItem], max_items: int) -> list[FeedItem]:
    merged: dict[str, tuple[FeedItem, int]] = {}

    for index, item in enumerate(items):
        existing_pair = merged.get(item.link)
        existing = existing_pair[0] if existing_pair else None
        if existing is None:
            merged[item.link] = (item, index)
            continue

        if not existing.summary and item.summary:
            existing.summary = item.summary
        if not existing.published and item.published:
            existing.published = item.published
        if existing.title == slug_to_title(existing.link) and item.title:
            existing.title = item.title

    result = list(merged.values())
    result.sort(
        key=lambda pair: (
            0 if pair[0].published else 1,
            -(pair[0].published.timestamp() if pair[0].published else 0),
            pair[1],
        )
    )
    return [item for item, _ in result[:max_items]]


def newest_timestamp(items: list[FeedItem]) -> datetime | None:
    timestamps = [item.published for item in items if item.published is not None]
    if not timestamps:
        return None
    return max(timestamps)


def build_rss_xml(source: FeedSource, items: list[FeedItem]) -> ET.Element:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = source.feed_title
    ET.SubElement(channel, "link").text = source.site_url
    ET.SubElement(channel, "description").text = source.feed_description

    latest = newest_timestamp(items)
    if latest:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(latest)

    for item in items:
        item_node = ET.SubElement(channel, "item")
        ET.SubElement(item_node, "title").text = item.title
        ET.SubElement(item_node, "link").text = item.link
        guid = ET.SubElement(item_node, "guid", isPermaLink="true")
        guid.text = item.link
        if item.summary:
            ET.SubElement(item_node, "description").text = item.summary
        if item.published:
            ET.SubElement(item_node, "pubDate").text = format_datetime(item.published)

    return rss


def write_xml(root: ET.Element, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def process_source(source: FeedSource, html_override: Path | None, dry_run: bool) -> None:
    logging.info("Processing source=%s url=%s", source.source_id, source.url)

    if html_override:
        html = html_override.read_text(encoding="utf-8")
    else:
        html = fetch_html(source.url, user_agent=source.user_agent, timeout_seconds=source.timeout_seconds)

    soup = BeautifulSoup(html, "html.parser")
    extracted: list[FeedItem] = []
    if source.use_json_ld:
        extracted.extend(extract_items_from_json_ld(soup, source))
    extracted.extend(extract_items_from_links(soup, source))
    items = dedupe_and_rank(extracted, source.max_items)

    if not items:
        logging.warning("No items extracted for source=%s", source.source_id)

    rss_root = build_rss_xml(source, items)

    if dry_run:
        logging.info(
            "Dry run source=%s items=%d rss=%s",
            source.source_id,
            len(items),
            source.output_rss,
        )
        return

    write_xml(rss_root, source.output_rss)
    logging.info(
        "Wrote source=%s items=%d rss=%s",
        source.source_id,
        len(items),
        source.output_rss,
    )


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    config_path = Path(args.config)
    if not config_path.exists():
        logging.error("Config not found: %s", config_path)
        return 1

    try:
        sources = load_sources(config_path)
    except Exception as exc:
        logging.error("Failed to load config: %s", exc)
        return 1

    if args.source_id:
        sources = [source for source in sources if source.source_id == args.source_id]
        if not sources:
            logging.error("source_id not found: %s", args.source_id)
            return 1

    html_override = Path(args.html_file) if args.html_file else None
    if html_override and not html_override.exists():
        logging.error("--html-file does not exist: %s", html_override)
        return 1

    if html_override and len(sources) > 1:
        logging.error("--html-file can only be used when processing one source")
        return 1

    errors = 0
    for source in sources:
        try:
            process_source(source, html_override, args.dry_run)
        except Exception as exc:
            errors += 1
            logging.exception("Source failed source=%s error=%s", source.source_id, exc)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
