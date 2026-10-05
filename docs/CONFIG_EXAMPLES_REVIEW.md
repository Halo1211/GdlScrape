# Starter configuration reference

Config Maker includes 15 small presets adapted from the official gallery-dl
examples, documentation, and upstream discussions. They contain no login data.
Each preset exposes its purpose and source in the interface.

Open **Config → Start here**, choose an example, and load it into the draft. Choose
your own folder and login, review the helper, then use **Save As...**. Loading an
example replaces the draft and does not start downloads.

## Included presets

Files are stored in
[`gallery_dl_app/assets/config-examples`](../gallery_dl_app/assets/config-examples).
Preset behavior is covered by regression tests against gallery-dl 1.32.14.

| Preset | Purpose | Source |
| --- | --- | --- |
| `pixiv-animation-archive` | Keep original Ugoira frames and `animation.json` in ZIP, without FFmpeg. | [Ugoira discussion](https://github.com/mikf/gallery-dl/discussions/6147) |
| `instagram-selected-media` | Profile posts, Reels, and Highlights with request pauses. | [Instagram discussion](https://github.com/mikf/gallery-dl/discussions/5586) |
| `reddit-connected-media` | Keep linked Imgur and Redgifs media inside the Reddit post folder. | [Parent/child extraction](https://github.com/mikf/gallery-dl/issues/7721) |
| `windows-short-paths` | Shorten ArtStation titles and use project/asset IDs. | [Windows path discussion](https://github.com/mikf/gallery-dl/discussions/5307) |
| `tumblr-original-posts` | Photo/video posts and inline media, excluding reblogs and external sites. | [Official example](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf) |
| `manga-cbz-information` | MangaDex CBZ with `info.json` and retained originals. | [CBZ metadata discussion](https://github.com/mikf/gallery-dl/discussions/2872) |
| `pixiv-consistent-history` | Share archive IDs between Pixiv profiles and searches. | [Pixiv archive discussion](https://github.com/mikf/gallery-dl/discussions/7036) |
| `metadata-per-site` | JSON for Pixiv/Instagram and JSON plus post text for Twitter. | [Official example](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf) |
| `booru-tags` | One tag per line beside images from selected booru sites. | [Official example](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf) |
| `manga-cbz` | English MangaDex chapters as CBZ while keeping originals. | [Official example](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf) |
| `social-images` | Image filters, separate history, and pauses for Instagram, Twitter, and Bluesky. | [File-filter manual](https://gdl-org.github.io/docs/configuration.html#extractor-file-filter) |
| `pixiv-animation` | Convert Ugoira to MP4 while keeping source frames. | [Ugoira discussion](https://github.com/mikf/gallery-dl/discussions/6147) |
| `imgur-folders` | Route posts into folders using a text condition and fallback. | [Conditional folders](https://github.com/mikf/gallery-dl/discussions/4703) |
| `separate-information` | Separate Danbooru media and JSON directories, with tab indentation. | [Metadata location discussion](https://github.com/mikf/gallery-dl/discussions/6094) |
| `social-post-text` | Twitter and Tumblr text once per post, in separate folders. | [Post-text discussion](https://github.com/mikf/gallery-dl/discussions/5628) |

## Adaptation rules

Examples use current gallery-dl options rather than copying historical external
commands. Site-specific metadata fields remain scoped to their website. Presets
with archive output retain originals; conversion presets describe their output
explicitly.

Named actions and unrelated imported options are preserved when a form is opened.
Shared and site processing actions accumulate according to gallery-dl's
configuration rules.

Request pauses do not guarantee access or prevent rate limits. Presets cannot
provide authentication, make inaccessible posts available, or migrate existing
archive records. Direct URLs may select content excluded from profile traversal.

## Upstream references

- [Annotated example, pinned to 1.32.14](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf)
- [Reference config, pinned to 1.32.14](https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl.conf)
- [Configuration file outline](https://github.com/mikf/gallery-dl/wiki/Config-File-Outline)
- [Configuration manual](https://gdl-org.github.io/docs/configuration.html)

See the [configuration guide](CONFIG_MAKER_GUIDE.md) for editing these presets.
