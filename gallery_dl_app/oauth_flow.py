"""Site-specific instructions and safe localhost OAuth callback relay."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit


OAUTH1_SITES = frozenset({"flickr", "smugmug", "tumblr"})
OAUTH2_SITES = frozenset({"deviantart", "reddit", "mastodon"})


def oauth_flow_guidance(site: str) -> str:
    site = str(site or "").lower().strip()
    if site == "pixiv":
        return (
            "Pixiv: open Developer Tools (F12) > Network before signing in. "
            "Copy the code parameter from the callback request and send it here quickly; "
            "gallery-dl says the code expires after 30 seconds."
        )
    if site == "flickr":
        return (
            "Flickr requires your own API key and API secret in extractor.flickr before OAuth. "
            "The authorization page returns through gallery-dl's HTTPS redirect page to localhost:6414. "
            "If the local callback does not arrive, paste the full callback URL here."
        )
    if site == "deviantart":
        return (
            "DeviantArt returns through gallery-dl's HTTPS redirect page to localhost:6414. "
            "A custom client ID must register that HTTPS redirect URI. "
            "If the local callback does not arrive, paste the full callback URL here."
        )
    if site == "mastodon":
        return (
            "Mastodon may first register an application on the selected instance. "
            "After approval the browser returns to localhost:6414. "
            "If that fails, paste the full callback URL here."
        )
    if site in OAUTH1_SITES | OAUTH2_SITES:
        return (
            "Authorize in the browser and allow its redirect to localhost:6414. "
            "If the local callback does not arrive, paste the full callback URL here."
        )
    raise ValueError("Unsupported OAuth site")


def local_oauth_callback_url(site: str, response: str) -> str:
    """Accept only a gallery-dl OAuth callback and relay it to local port 6414."""
    site = str(site or "").lower().strip()
    if site not in OAUTH1_SITES | OAUTH2_SITES:
        raise ValueError("This site does not use a localhost OAuth callback")
    response = str(response or "").strip()
    if any(char in response for char in "\r\n\x00"):
        raise ValueError("Paste one complete callback URL")
    parsed = urlsplit(response)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Callback URL has an invalid port") from exc
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Paste the callback URL without credentials or a fragment")
    is_redirect_page = (
        parsed.scheme == "https" and parsed.hostname == "mikf.github.io"
        and parsed.path == "/gallery-dl/oauth-redirect.html" and port is None
    )
    is_local = (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        and port == 6414 and parsed.path in {"", "/"}
    )
    if not (is_redirect_page or is_local):
        raise ValueError("Paste the official gallery-dl redirect or localhost:6414 callback URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    required = {"oauth_token", "oauth_verifier"} if site in OAUTH1_SITES else {"state", "code"}
    if not required.issubset(query):
        raise ValueError("Callback URL is missing " + ", ".join(sorted(required - query.keys())))
    # gallery-dl's local OAuth server reads at most 1024 bytes of the HTTP
    # request. Leave room for the request line and headers.
    if len(parsed.query) > 900 or not parsed.query.isascii():
        raise ValueError("Callback query is invalid or too long")
    return "http://localhost:6414/?" + parsed.query
