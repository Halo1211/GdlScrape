"""Guided URL recipes checked against the installed gallery-dl extractors."""

from __future__ import annotations

import re
from urllib.parse import quote, quote_plus, urlsplit


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
    "instagram": ("user", "posts", "reels", "stories", "highlights", "tagged", "post", "tag"),
    "bluesky": ("user", "media", "posts", "post", "search", "hashtag"),
    "tumblr": ("user", "post", "likes", "search"),
    "pinterest": ("user", "board", "pin", "search"),
    "imgur": ("user", "image", "album", "gallery", "favorite", "tag", "search"),
    "artstation": ("user", "artwork", "album", "collection", "likes", "search"),
    "mangadex": ("manga", "chapter", "covers", "list", "author"),
    "patreon": ("creator", "post", "collection"),
    "coomer": ("user", "post"),
}

URL_MODE_LABELS = {
    "url": ("Paste a complete link", "Tempel tautan lengkap"),
    "user": ("User profile", "Profil pengguna"), "tag": ("Tag search", "Pencarian tag"),
    "post": ("One post", "Satu postingan"), "pool": ("Image pool", "Kumpulan gambar / pool"),
    "favorite": ("User favorites", "Favorit pengguna"), "artwork": ("One artwork", "Satu karya"),
    "gallery": ("User gallery", "Galeri pengguna"), "subreddit": ("Community / subreddit", "Komunitas / subreddit"),
    "submission": ("One Reddit post", "Satu postingan Reddit"), "media": ("User media", "Media pengguna"),
    "tweet": ("One X/Twitter post", "Satu postingan X/Twitter"), "creator": ("Creator profile", "Profil kreator"),
    "image": ("One photo", "Satu foto"), "posts": ("User posts", "Postingan pengguna"),
    "reels": ("User Reels", "Reels pengguna"), "stories": ("User Stories", "Story pengguna"),
    "highlights": ("User Highlights", "Sorotan pengguna"), "tagged": ("Posts tagging this user", "Postingan yang menandai pengguna"),
    "search": ("Search", "Pencarian"), "hashtag": ("Hashtag", "Hashtag"),
    "likes": ("Liked posts", "Postingan yang disukai"), "board": ("One board", "Satu papan / board"),
    "pin": ("One pin", "Satu pin"),
    "album": ("One album", "Satu album"), "collection": ("One collection", "Satu koleksi"),
    "manga": ("Manga title", "Judul manga"), "chapter": ("One chapter", "Satu bab"),
    "covers": ("Cover artwork", "Gambar sampul"), "list": ("Reading list", "Daftar bacaan"),
    "author": ("Author's manga", "Manga dari penulis"),
}


def url_recipe_fields(site: str, mode: str) -> tuple[tuple[str, str, str], ...]:
    """English label, Indonesian label, and illustrative input for each field."""
    if mode == "url":
        return (("Complete URL", "Tautan lengkap", "https://…"),)
    if site in {"kemono", "coomer"}:
        fields = (("Service", "Layanan", "onlyfans" if site == "coomer" else "patreon"), ("Creator ID / username", "ID / nama kreator", "artist" if site == "coomer" else "12345"))
        return fields + (("Post ID", "ID postingan", "67890"),) if mode == "post" else fields
    if site == "mangadex":
        return (("UUID from the page URL", "UUID dari URL halaman", "12345678-1234-1234-1234-123456789abc"),)
    if site == "artstation" and mode in {"album", "collection"}:
        return (("Username", "Nama pengguna", "artist"), ("Numeric ID", "ID angka", "12345"))
    if site == "artstation" and mode == "artwork":
        return (("Artwork code", "Kode karya", "abc123"),)
    if site == "imgur" and mode in {"image", "album", "gallery"}:
        return (("Image / album code", "Kode gambar / album", "abc12"),)
    if site == "patreon" and mode in {"post", "collection"}:
        return (("Numeric ID", "ID angka", "12345"),)
    if mode in {"submission", "tweet", "image"} or mode == "post" and site in {"tumblr", "bluesky"} or mode == "board":
        first = ("Subreddit name", "Nama subreddit", "art") if site == "reddit" else ("User / blog", "Pengguna / blog", "artist.bsky.social" if site == "bluesky" else "artist")
        second = ("Board name", "Nama papan", "inspiration") if mode == "board" else ("Post / photo ID", "ID postingan / foto", "abc123" if site == "reddit" else "3lxyzabc12345" if site == "bluesky" else "12345")
        return (first, second)
    if mode in {"tag", "search", "hashtag"}:
        return (("Tags / search words", "Tag / kata pencarian", "landscape" if site == "instagram" or mode == "hashtag" else "landscape sky"),)
    if mode in {"post", "pool", "favorite", "artwork", "pin"} or site == "pixiv":
        return (("Post code" if site == "instagram" else "Numeric ID", "Kode postingan" if site == "instagram" else "ID angka", "ABC123xyz" if site == "instagram" else "12345"),)
    return (("Username / handle", "Nama pengguna / handle", "artist.bsky.social" if site == "bluesky" else "artist"),)


def validate_supported_url(value: str, site: str = "") -> tuple[str, str]:
    """Check a URL's shape locally; no requests, login, or downloads are made."""
    value = value.strip()
    if not value or len(value) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Paste one complete URL without spaces")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use a complete http:// or https:// website URL")
    except ValueError as exc:
        raise ValueError("Use a complete http:// or https:// website URL") from exc
    from gallery_dl import extractor
    found = extractor.find(value)
    expected = {"kemonoparty": "kemono", "coomerparty": "coomer"}.get(site, site)
    if found is None:
        raise ValueError("The installed gallery-dl does not recognize this URL")
    if expected and expected not in {found.category, getattr(found, "basecategory", "")}:
        raise ValueError(f"This URL belongs to {found.category}; choose that website or automatic detection")
    return value, found.subcategory


def url_recipe_hint(site: str, mode: str) -> str:
    if site in {"kemono", "coomer"}:
        return "service:user-id:post-id" if mode == "post" else "service:user-id"
    if site in {"reddit", "twitter", "flickr"} and mode in {"submission", "tweet", "image"}:
        return "subreddit:post-id" if site == "reddit" else "user:post-id"
    if mode in {"post", "pool", "favorite", "artwork", "user"} and site == "pixiv":
        return "numeric ID"
    if site == "mangadex":
        return "complete UUID from the page URL"
    if site == "artstation" and mode in {"album", "collection"}:
        return "user:numeric-id"
    if site == "imgur" and mode in {"image", "album", "gallery"}:
        return "5- or 7-character code"
    if mode in {"post", "pool", "favorite"}:
        return "numeric ID"
    if mode == "tag":
        return "space-separated tags or search text"
    return "name or handle"


def _components(value: str, count: int, *, allow_at: bool = False, allow_dot: bool = False) -> list[str]:
    parts = [item.strip() for item in value.split(":", count - 1)]
    pattern = r"[\w" + ("@" if allow_at else "") + ("." if allow_dot else "") + r"-]+"
    if len(parts) != count or any(not part or not re.fullmatch(pattern, part) for part in parts):
        raise ValueError("Enter " + ("service:user-id:post-id" if count == 3 else "name:id"))
    return parts


def build_site_url(site: str, mode: str, target: str) -> tuple[str, str]:
    """Return (URL, extractor subcategory), rejecting unsupported recipes."""
    site = str(site or "").lower().strip()
    mode = str(mode or "").lower().strip()
    target = str(target or "").strip()
    if mode == "url":
        return validate_supported_url(target, site)
    if site not in URL_RECIPE_MODES or mode not in URL_RECIPE_MODES[site]:
        raise ValueError("Choose a supported site and URL type")
    if not target or any(char in target for char in "\r\n\x00"):
        raise ValueError("Enter a single-line target")
    if len(target) > 500:
        raise ValueError("Target is too long")
    tag = quote_plus(target)
    segment = quote(target, safe="-_~.")
    if mode in {"post", "pool", "favorite", "artwork", "pin"} and site not in {"kemono", "coomer", "instagram", "bluesky", "tumblr", "imgur", "artstation"}:
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
            target = target.removeprefix("r/") if mode == "subreddit" else target.removeprefix("u/")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
                raise ValueError("Enter a subreddit or username without spaces")
            url = f"https://www.reddit.com/{'r' if mode == 'subreddit' else 'user'}/{target}/"
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
    elif site in {"kemono", "coomer"}:
        parts = _components(target, 3 if mode == "post" else 2, allow_dot=True)
        root = "https://kemono.cr" if site == "kemono" else "https://coomer.st"
        url = f"{root}/{parts[0]}/user/{parts[1]}"
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
    elif site == "instagram":
        if mode == "tag":
            if not re.fullmatch(r"[\w]+", target.removeprefix("#")):
                raise ValueError("Enter one Instagram hashtag without spaces")
            url = "https://www.instagram.com/explore/tags/" + quote(target.removeprefix("#"), safe="") + "/"
        elif mode == "post":
            if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
                raise ValueError("Enter the post code from /p/CODE/ or paste the complete link")
            url = f"https://www.instagram.com/p/{target}/"
        else:
            handle = target.removeprefix("@")
            if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", handle) or handle.startswith(".") or handle.endswith("."):
                raise ValueError("Enter a valid Instagram username")
            url = f"https://www.instagram.com/stories/{handle}/" if mode == "stories" else f"https://www.instagram.com/{handle}/" + (mode + "/" if mode != "user" else "")
    elif site == "bluesky":
        if mode in {"search", "hashtag"}:
            url = f"https://bsky.app/search?q={tag}" if mode == "search" else f"https://bsky.app/hashtag/{quote(target.removeprefix('#'), safe='')}"
        else:
            if mode == "post":
                handle, separator, post_id = target.rpartition(":")
                if not separator or not re.fullmatch(r"[A-Za-z0-9]+", post_id):
                    raise ValueError("Enter a Bluesky handle or DID and a post ID")
            else:
                handle, post_id = target, ""
            handle = handle.removeprefix("@")
            if not (re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", handle)
                    or re.fullmatch(r"did:(?:plc:[A-Za-z0-9]+|web:[A-Za-z0-9.-]+)", handle)):
                raise ValueError("Enter a full Bluesky handle (artist.bsky.social) or DID (did:plc:…)")
            url = f"https://bsky.app/profile/{handle}" + (f"/post/{post_id}" if mode == "post" else "/" + mode if mode != "user" else "")
    elif site == "tumblr":
        if mode == "search":
            url = "https://www.tumblr.com/search/" + segment
        else:
            blog, post_id = _components(target, 2) if mode == "post" else (target.removeprefix("@"), "")
            if not re.fullmatch(r"[A-Za-z0-9-]+", blog):
                raise ValueError("Enter the Tumblr blog name without .tumblr.com")
            if post_id and (not post_id.isascii() or not post_id.isdecimal() or int(post_id) <= 0):
                raise ValueError("Enter a positive numeric Tumblr post ID")
            url = f"https://{blog}.tumblr.com" + (f"/post/{post_id}" if mode == "post" else "/likes" if mode == "likes" else "/")
    elif site == "pinterest":
        if mode == "search":
            url = f"https://www.pinterest.com/search/pins/?q={tag}"
        elif mode == "pin":
            url = f"https://www.pinterest.com/pin/{target}/"
        else:
            handle, board = _components(target, 2) if mode == "board" else (target.removeprefix("@"), "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", handle):
                raise ValueError("Enter a Pinterest username without spaces")
            url = f"https://www.pinterest.com/{handle}/" + (board + "/" if mode == "board" else "")
    elif site == "mangadex":
        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", target):
            raise ValueError("Enter the complete MangaDex UUID from the page URL")
        page = "title" if mode in {"manga", "covers"} else mode
        url = f"https://mangadex.org/{page}/{target.lower()}" + ("?tab=art" if mode == "covers" else "")
    elif site == "imgur":
        if mode in {"image", "album", "gallery"}:
            if not re.fullmatch(r"[A-Za-z0-9]{5}(?:[A-Za-z0-9]{2})?", target):
                raise ValueError("Enter a 5- or 7-character Imgur code")
            page = "a/" if mode == "album" else "gallery/" if mode == "gallery" else ""
            url = f"https://imgur.com/{page}{target}"
        elif mode == "search":
            url = f"https://imgur.com/search?q={tag}"
        elif mode == "tag":
            url = f"https://imgur.com/t/{segment}"
        else:
            handle = target.removeprefix("@")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", handle) or handle.lower() == "me":
                raise ValueError("Enter an Imgur username")
            url = f"https://imgur.com/user/{handle}" + ("/favorites" if mode == "favorite" else "")
    elif site == "artstation":
        if mode == "search":
            url = f"https://www.artstation.com/search?sort_by=relevance&query={tag}"
        elif mode == "artwork":
            if not re.fullmatch(r"[A-Za-z0-9]+", target):
                raise ValueError("Enter the artwork code from /artwork/CODE")
            url = f"https://www.artstation.com/artwork/{target}"
        else:
            handle, item_id = _components(target, 2) if mode in {"album", "collection"} else (target.removeprefix("@"), "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", handle):
                raise ValueError("Enter an ArtStation username")
            if item_id and (not item_id.isascii() or not item_id.isdecimal() or int(item_id) <= 0):
                raise ValueError("Enter a positive numeric album or collection ID")
            url = f"https://www.artstation.com/{handle}" + (f"/{mode}s/{item_id}" if item_id else "/likes" if mode == "likes" else "")
    elif site == "patreon":
        if mode == "creator":
            handle = target.removeprefix("@")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", handle):
                raise ValueError("Enter a Patreon creator name")
            url = f"https://www.patreon.com/c/{handle}"
        else:
            if not target.isascii() or not target.isdecimal() or int(target) <= 0:
                raise ValueError("Enter a positive numeric Patreon ID")
            url = f"https://www.patreon.com/{'posts' if mode == 'post' else 'collection'}/{target}"
    else:
        raise ValueError("Unsupported URL recipe")

    from gallery_dl import extractor

    found = extractor.find(url)
    if found is None or found.category != site:
        raise ValueError("The installed gallery-dl has no matching extractor for this URL")
    return url, found.subcategory
