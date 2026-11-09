"""Load and sanitize authored Markdown blog posts."""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import markdown
import nh3

BLOG_DIR = Path(__file__).resolve().parent.parent / "content" / "blog"
ALLOWED_MARKDOWN_TAGS = {
    "p", "br", "hr", "ul", "ol", "li", "strong", "b", "em", "i",
    "h2", "h3", "h4", "blockquote", "code", "pre", "a", "table",
    "thead", "tbody", "tr", "th", "td",
}


@dataclass(frozen=True)
class BlogPost:
    slug: str
    title: str
    description: str
    published: date
    updated: date
    html: str


def load_blog_posts() -> list[BlogPost]:
    posts = [_load_post(path) for path in BLOG_DIR.glob("*.md")]
    return sorted(posts, key=lambda post: (post.published, post.slug), reverse=True)


def get_blog_post(slug: str) -> BlogPost | None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return None
    path = BLOG_DIR / f"{slug}.md"
    return _load_post(path) if path.is_file() else None


def _load_post(path: Path) -> BlogPost:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"Blog post {path.name} is missing metadata")
    _, metadata_text, markdown_text = raw.split("---", 2)
    metadata = {}
    for line in metadata_text.strip().splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip() not in {"title", "description", "published", "updated"}:
            raise ValueError(f"Invalid blog metadata in {path.name}")
        metadata[key.strip()] = value.strip()
    title = metadata.get("title", "").strip()
    description = metadata.get("description", "").strip()
    if not title or not description or len(description) > 300:
        raise ValueError(f"Incomplete blog metadata in {path.name}")
    published = date.fromisoformat(metadata["published"])
    updated = date.fromisoformat(metadata.get("updated", metadata["published"]))
    rendered = markdown.markdown(markdown_text, extensions=["extra"])
    safe_html = nh3.clean(
        rendered,
        tags=ALLOWED_MARKDOWN_TAGS,
        attributes={"a": {"href", "title"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="nofollow noopener noreferrer",
    )
    return BlogPost(path.stem, title, description, published, updated, safe_html)
