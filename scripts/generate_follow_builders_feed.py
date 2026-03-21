#!/usr/bin/env python3
"""Fetch follow-builders JSON feeds from GitHub and generate RSS XML."""
from __future__ import annotations

import json
import logging
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.request import Request, urlopen

FEED_X_URL = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json"
FEED_PODCASTS_URL = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-podcasts.json"

OUTPUT_DIR = Path("feeds")
OUTPUT_X = OUTPUT_DIR / "follow-builders-x.rss.xml"
OUTPUT_PODCASTS = OUTPUT_DIR / "follow-builders-podcasts.rss.xml"

USER_AGENT = "Mozilla/5.0 (compatible; blog-rss-feed/1.0)"


def fetch_json(url: str) -> dict | None:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logging.error("Failed to fetch %s: %s", url, exc)
        return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def build_x_rss(data: dict) -> ET.Element:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Follow Builders — X/Twitter"
    ET.SubElement(channel, "link").text = "https://github.com/zarazhangrui/follow-builders"
    ET.SubElement(channel, "description").text = "AI builders' latest tweets, curated by follow-builders"

    generated_at = parse_datetime(data.get("generatedAt"))
    if generated_at:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(generated_at)

    for builder in data.get("x", []):
        name = builder.get("name", "")
        handle = builder.get("handle", "")
        bio = builder.get("bio", "")

        for tweet in builder.get("tweets", []):
            item = ET.SubElement(channel, "item")
            text = tweet.get("text", "")
            title_text = text[:100] + "..." if len(text) > 100 else text
            ET.SubElement(item, "title").text = f"{name} (@{handle}): {title_text}"
            url = tweet.get("url", f"https://x.com/{handle}")
            ET.SubElement(item, "link").text = url
            guid = ET.SubElement(item, "guid", isPermaLink="true")
            guid.text = url

            desc_parts = [text]
            if bio:
                desc_parts.append(f"\n\nBio: {bio}")
            likes = tweet.get("likes", 0)
            retweets = tweet.get("retweets", 0)
            if likes or retweets:
                desc_parts.append(f"\n❤️ {likes}  🔁 {retweets}")
            ET.SubElement(item, "description").text = "".join(desc_parts)

            pub_date = parse_datetime(tweet.get("createdAt"))
            if pub_date:
                ET.SubElement(item, "pubDate").text = format_datetime(pub_date)

    return rss


def build_podcasts_rss(data: dict) -> ET.Element:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Follow Builders — Podcasts"
    ET.SubElement(channel, "link").text = "https://github.com/zarazhangrui/follow-builders"
    ET.SubElement(channel, "description").text = "AI podcasts tracked by follow-builders"

    generated_at = parse_datetime(data.get("generatedAt"))
    if generated_at:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(generated_at)

    for episode in data.get("podcasts", []):
        item = ET.SubElement(channel, "item")
        title = episode.get("title", "Untitled Episode")
        name = episode.get("name", "")
        if name:
            ET.SubElement(item, "title").text = f"[{name}] {title}"
        else:
            ET.SubElement(item, "title").text = title

        url = episode.get("url", "")
        if url:
            ET.SubElement(item, "link").text = url
            guid = ET.SubElement(item, "guid", isPermaLink="true")
            guid.text = url

        transcript = episode.get("transcript", "")
        if transcript:
            summary = transcript[:500] + "..." if len(transcript) > 500 else transcript
            ET.SubElement(item, "description").text = summary

        pub_date = parse_datetime(episode.get("publishedAt") or episode.get("createdAt"))
        if pub_date:
            ET.SubElement(item, "pubDate").text = format_datetime(pub_date)

    return rss


def write_xml(root: ET.Element, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    errors = 0

    # X/Twitter feed
    x_data = fetch_json(FEED_X_URL)
    if x_data:
        rss = build_x_rss(x_data)
        write_xml(rss, OUTPUT_X)
        tweet_count = sum(len(b.get("tweets", [])) for b in x_data.get("x", []))
        logging.info("Wrote %s (%d tweets)", OUTPUT_X, tweet_count)
    else:
        errors += 1

    # Podcasts feed
    pod_data = fetch_json(FEED_PODCASTS_URL)
    if pod_data:
        rss = build_podcasts_rss(pod_data)
        write_xml(rss, OUTPUT_PODCASTS)
        ep_count = len(pod_data.get("podcasts", []))
        logging.info("Wrote %s (%d episodes)", OUTPUT_PODCASTS, ep_count)
    else:
        errors += 1

    return 1 if errors == 2 else 0


if __name__ == "__main__":
    sys.exit(main())
