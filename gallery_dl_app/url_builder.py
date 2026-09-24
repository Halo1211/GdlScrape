"""Guided URL recipes checked against the installed gallery-dl extractors."""

from __future__ import annotations

import re
from urllib.parse import quote, quote_plus


BOORU_V02_ROOTS = {
    "hypnohub": "https://hypnohub.net",
    "rule34": "https://rule34.xxx",
    "safebooru": "https://safebooru.org",
    "tbib": "https://tbib.org",
    "xbooru": "https://xbooru.com",
    "gelbooru": "https://gelbooru.com",
}

URL_RECIPE_MODES: dict[str, tuple[str, ...]] = {
    **{site: ("tag", "post", "pool", "favorite") for site in BOORU_V02_ROOTS},
    "paheal": ("tag", "post"),
    "danbooru": ("tag", "post", "pool"),
    "e621": ("tag", "post"),
    "e926": ("tag", "post"),
    "e6ai": ("tag", "post"),
    "pixiv": ("user", "artwork", "tag"),
    "deviantart": ("user", "gallery", "tag"),
    "reddit": ("subreddit", "user", "submission"),
    "twitter": ("user", "media", "tweet"),
    "kemono": ("user", "post"),
    "fanbox": ("creator",),
    "flickr": ("user", "image"),
}


def url_recipe_hint(site: str, mode: str) -> str:
    if site == "kemono":
        return "service:user-id:post-id" if mode == "post" else "service:user-id"
    if site in {"reddit", "twitter", "flickr"} and mode in {"submission", "tweet", "image"}:
        return "subreddit:post-id" if site == "reddit" else "user:post-id"
    if mode in {"post", "pool", "favorite", "artwork", "user"} and site == "pixiv":
        return "numeric ID"
    if mode in {"post", "pool", "favorite"}:
        return "numeric ID"
    if mode == "tag":
        return "space-separated tags or search text"
    return "name or handle"


def _components(value: str, count: int, *, allow_at: bool = False) -> list[str]:
    parts = [item.strip() for item in value.split(":", count - 1)]
    pattern = r"[\w@-]+" if allow_at else r"[\w-]+"
    if len(parts) != count or any(not part or not re.fullmatch(pattern, part) for part in parts):
        raise ValueError("Enter " + ("service:user-id:post-id" if count == 3 else "name:id"))
    return parts


def build_site_url(site: str, mode: str, target: str) -> tuple[str, str]:
    """Return (URL, extractor subcategory), rejecting unsupported recipes."""
    site = str(site or "").lower().strip()
    mode = str(mode or "").lower().strip()
    target = str(target or "").strip()
    if site not in URL_RECIPE_MODES or mode not in URL_RECIPE_MODES[site]:
        raise ValueError("Choose a supported site and URL type")
    if not target or any(char in target for char in "\r\n\x00"):
        raise ValueError("Enter a single-line target")
    if len(target) > 500:
        raise ValueError("Target is too long")
    tag = quote_plus(target)
    segment = quote(target, safe="-_~.")
    if mode in {"post", "pool", "favorite", "artwork"} and site not in {"kemono"}:
        if not target.isascii() or not target.isdecimal() or int(target) <= 0:
            raise ValueError("Enter a positive numeric ID")
    if site in BOORU_V02_ROOTS:
        root = BOORU_V02_ROOTS[site]
        page, action, parameter = {
            "tag": ("post", "list", "tags"),
            "post": ("post", "view", "id"),
            "pool": ("pool", "show", "id"),
            "favorite": ("favorites", "view", "id"),
        }[mode]
        url = f"{root}/index.php?page={page}&s={action}&{parameter}={tag if mode == 'tag' else target}"
    elif site == "paheal":
        url = f"https://rule34.paheal.net/post/{'list/' + segment + '/1' if mode == 'tag' else 'view/' + target}"
    elif site == "danbooru":
        url = (f"https://danbooru.donmai.us/posts?tags={tag}" if mode == "tag" else
               f"https://danbooru.donmai.us/{'pools' if mode == 'pool' else 'posts'}/{target}")
    elif site in {"e621", "e926", "e6ai"}:
        url = (f"https://{site}.net/posts?tags={tag}" if mode == "tag" else
               f"https://{site}.net/posts/{target}")
    elif site == "pixiv":
        if mode == "user" and (not target.isascii() or not target.isdecimal() or int(target) <= 0):
            raise ValueError("Enter a positive numeric Pixiv user ID")
        url = (f"https://www.pixiv.net/en/users/{target}" if mode == "user" else
               f"https://www.pixiv.net/artworks/{target}" if mode == "artwork" else
               f"https://www.pixiv.net/en/tags/{segment}")
    elif site == "deviantart":
        url = (f"https://www.deviantart.com/tag/{segment}" if mode == "tag" else
               f"https://www.deviantart.com/{segment}" + ("/gallery/" if mode == "gallery" else ""))
    elif site == "reddit":
        if mode == "submission":
            community, post_id = _components(target, 2)
            url = f"https://www.reddit.com/r/{community}/comments/{post_id}/"
        else:
            url = f"https://www.reddit.com/{'r' if mode == 'subreddit' else 'user'}/{segment}/"
    elif site == "twitter":
        if mode == "tweet":
            handle, tweet_id = _components(target, 2, allow_at=True)
            handle = handle.removeprefix("@")
            if not tweet_id.isascii() or not tweet_id.isdecimal() or int(tweet_id) <= 0:
                raise ValueError("Enter a positive numeric tweet ID")
            url = f"https://x.com/{handle}/status/{tweet_id}"
        else:
            handle = target.removeprefix("@")
            url = f"https://x.com/{handle}" + ("/media" if mode == "media" else "")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            raise ValueError("Enter a valid X/Twitter handle")
    elif site == "kemono":
        parts = _components(target, 3 if mode == "post" else 2)
        url = f"https://kemono.cr/{parts[0]}/user/{parts[1]}"
        if mode == "post":
            url += f"/post/{parts[2]}"
    elif site == "fanbox":
        url = f"https://{segment}.fanbox.cc/"
    elif site == "flickr":
        if mode == "image":
            user, image_id = _components(target, 2, allow_at=True)
            if not image_id.isascii() or not image_id.isdecimal() or int(image_id) <= 0:
                raise ValueError("Enter a positive numeric Flickr image ID")
            url = f"https://www.flickr.com/photos/{user}/{image_id}"
        else:
            url = f"https://www.flickr.com/photos/{quote(target, safe='-_~.@')}/"
    else:
        raise ValueError("Unsupported URL recipe")

    from gallery_dl import extractor

    found = extractor.find(url)
    if found is None or found.category != site:
        raise ValueError("The installed gallery-dl has no matching extractor for this URL")
    return url, found.subcategory
