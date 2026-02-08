# blog_rss_feed

Convert regular blog listing pages into subscribable RSS/Atom feeds, and keep them updated on a schedule with GitHub Actions.

## Currently Configured Source

- `https://claude.com/blog`
  - RSS: `feeds/claude-blog.rss.xml`
  - Atom: `feeds/claude-blog.atom.xml`

## How It Works

1. Fetch HTML from the target URL.
2. Prefer extracting article data from `application/ld+json`.
3. If JSON-LD is incomplete, fall back to extracting from page links.
4. If body/summary cannot be extracted, still keep `title + link` (worst case: title from URL slug).
5. Generate RSS/Atom XML files into `feeds/`.
6. Run on schedule with GitHub Actions and auto commit/push updates.

## Usage

1. Run locally:

```bash
pip install -r requirements.txt
python scripts/generate_feeds.py --config config/sources.json
```

2. After enabling GitHub Actions, feeds are updated automatically. You can subscribe via raw URLs:

- `https://raw.githubusercontent.com/<owner>/<repo>/main/feeds/claude-blog.rss.xml`
- `https://raw.githubusercontent.com/<owner>/<repo>/main/feeds/claude-blog.atom.xml`

Notes:

- `<owner>` is your GitHub username or organization name, and `<repo>` is your repository name.
- Only public repositories can usually be subscribed to directly by most RSS readers.
- Private repositories are typically not directly accessible by RSS readers (authentication required).

## Add or Modify Sources

Edit the `sources` array in `config/sources.json`. Multiple sources are supported and generated in one run. Common fields:

- `id`: source identifier (output filenames are based on this)
- `url`: listing page URL
- `include_url_patterns`: article URL match rules (regex)
- `exclude_url_patterns`: filter rules (regex)
- `output_rss` / `output_atom`: output paths
