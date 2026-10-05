"""Read-only config checks. Never evaluate filters or start postprocessors."""

from __future__ import annotations

import ast
import json
import shutil
from dataclasses import dataclass

from .core import safe_expand_path, validate_finite_numbers
from .postprocessor_config import postprocessor_options, resolved_postprocessor_action


def parse_config_json(text: str) -> dict:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result and not key.startswith("#"):
                raise ValueError("Duplicate settings keys in JSON. Merge repeated sections before importing or saving.")
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=object_pairs)
    validate_finite_numbers(data)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object.")
    return data


@dataclass(frozen=True)
class ConfigAdvice:
    path: str
    message: tuple[str, str]


def inspect_config(data: dict, *, dependencies: dict[str, bool] | None = None) -> list[ConfigAdvice]:
    """Report actionable structural mistakes without printing config values."""
    from gallery_dl.postprocessor import modules

    advice = []

    def add(path, en, id_text):
        advice.append(ConfigAdvice(path, (en, id_text)))

    if not isinstance(data, dict):
        add("config", "Use a JSON object as the config root.", "Gunakan object JSON sebagai akar config.")
        return advice
    for key in ("extractor", "downloader", "output", "postprocessor", "cache"):
        if key in data and not isinstance(data[key], dict):
            add(key, "This section must be a JSON object. Open All gallery-dl settings to correct it.",
                "Bagian ini harus berupa object JSON. Perbaiki melalui Semua pengaturan gallery-dl.")
    shared = data.get("extractor")
    shared = shared if isinstance(shared, dict) else {}
    for key in ("filename", "directory", "archive", "cookies", "file-filter", "postprocessors", "sleep-request", "timeout", "retries"):
        if key in data:
            add(key, "A root-level option overrides website settings. Move it into extractor for shared defaults, or into a website block.",
                "Opsi pada akar config mengalahkan pengaturan situs. Pindahkan ke extractor untuk default bersama, atau ke blok situs.")

    def check_filter(expression, path):
        if not expression:
            return
        try:
            if isinstance(expression, list):
                expression = "(" + ") and (".join(expression) + ")"
            if not isinstance(expression, str):
                raise ValueError("Filter must be text or a list of expressions")
            ast.parse(expression, mode="eval")
        except (SyntaxError, ValueError, TypeError, RecursionError):
            add(path, "The filter has invalid expression syntax. Correct it in File types, After downloading or All Settings.",
                "Sintaks ekspresi filter tidak valid. Perbaiki melalui Jenis file, Setelah mengunduh atau Semua pengaturan.")

    def check_overrides(block, path):
        value = block.get("postprocessor-options")
        if value and not isinstance(value, dict):
            add(path, "Additional action options must be a JSON object, or null to stop inheritance.",
                "Opsi tambahan tindakan harus berupa object JSON, atau null untuk menghentikan pewarisan.")

    def check_actions(value, path, scopes, enabled=True):
        if value is None:
            return
        actions = value if isinstance(value, list) else [value]
        for index, item in enumerate(actions):
            location = f"{path}[{index}]"
            options = resolved_postprocessor_action(data, item, overrides=postprocessor_options(data, scopes))
            if not isinstance(options.get("name"), str):
                add(location, "Choose an action or a named preset in After downloading.",
                    "Pilih tindakan atau preset bernama melalui Setelah mengunduh.")
                continue
            name = options["name"]
            check_filter(options.get("filter"), location + ".filter")
            if name not in modules:
                add(location, "The action or named preset is not defined. Check its spelling and the postprocessor section.",
                    "Tindakan atau preset bernama belum didefinisikan. Periksa ejaan dan bagian postprocessor.")
            if name == "ugoira" and enabled and dependencies is not None:
                mode = options.get("mode") or options.get("ffmpeg-demuxer")
                tool = "mkvmerge" if mode == "mkvmerge" else "FFmpeg"
                if mode in (None, "auto") and options.get("extension") in (None, "webm", "mkv") and (
                        options.get("mkvmerge-location") or dependencies.get("mkvmerge", False)):
                    tool = "mkvmerge"
                location_key = "mkvmerge-location" if tool == "mkvmerge" else "ffmpeg-location"
                custom_location = options.get(location_key)
                available = dependencies.get(tool, False)
                if isinstance(custom_location, str) and custom_location:
                    try:
                        available = bool(shutil.which(str(safe_expand_path(custom_location))))
                    except (ValueError, OSError):
                        available = False
                if mode != "archive" and not available:
                    add(location, f"{tool} is missing for animation conversion. Install it, or keep original animation files.",
                        f"{tool} belum ada untuk konversi animasi. Pasang tool tersebut, atau simpan animasi asli.")
            if name == "zip" and enabled and not options.get("keep-files", False):
                add(location, "This archive action removes original media. Enable Keep original files if you need both copies.",
                    "Tindakan arsip ini menghapus media asli. Aktifkan Simpan file asli jika memerlukan kedua salinan.")

    def visit(block, path, scopes, enabled=True):
        scopes = (*scopes, block)
        enabled = data["postprocess"] if "postprocess" in data else block.get("postprocess", enabled)
        check_overrides(block, path + ".postprocessor-options")
        if "postprocessors" in block:
            check_actions(block["postprocessors"], path + ".postprocessors", scopes, enabled)
        if block.get("archive") == "":
            add(path + ".archive", "Choose a download-history file, or remove this empty setting to inherit defaults.",
                "Pilih file riwayat unduhan, atau hapus pengaturan kosong untuk memakai bawaan.")
        for key in ("file-filter", "image-filter", "video-filter", "post-filter", "skip-filter"):
            check_filter(block.get(key), path + "." + key)
        for key, value in block.items():
            if isinstance(value, dict) and key not in {"keywords", "keywords-eval", "metadata", "directory", "filename", "cookies", "headers", "path-restrict", "extension-map", "postprocessor-options"}:
                visit(value, path + "." + key, scopes, enabled)

    visit(shared, "extractor", ())
    check_overrides(data, "postprocessor-options")
    if "postprocessors" in data:
        check_actions(data["postprocessors"], "postprocessors", (shared,), data.get("postprocess", shared.get("postprocess", True)))
    return advice


def config_helper_text(data: dict, *, indonesian: bool = False, dependencies: dict[str, bool] | None = None) -> str:
    advice = inspect_config(data, dependencies=dependencies)
    if not advice:
        return ("Tidak ditemukan masalah pada pemeriksaan struktur dan tindakan. Uji akses situs melalui Hanya simulasi; login, jaringan, dan field metadata belum diverifikasi."
                if indonesian else "No problems found in the structure and action checks. Test website access with Simulate only; login, network access and metadata fields have not been verified.")
    title = f"{len(advice)} hal perlu ditinjau:" if indonesian else f"{len(advice)} item(s) to review:"
    return title + "\n\n" + "\n\n".join(item.path + "\n" + item.message[int(indonesian)] for item in advice)
