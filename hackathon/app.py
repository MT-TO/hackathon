from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

VENV_SITE_PACKAGES = Path("/Users/mt/venv/lib/python3.14/site-packages")
if VENV_SITE_PACKAGES.exists():
    sys.path.insert(0, str(VENV_SITE_PACKAGES))

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from PIL import ExifTags, Image


BASE_DIR = Path(__file__).resolve().parent
IMAGES_ROOT = BASE_DIR / "Images"
CACHE_ROOT = BASE_DIR / ".cache"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
PREVIEW_SIZE = (800, 600)
THUMB_SIZE = (160, 120)
JPEG_QUALITY = 88
CACHE_TTL_SECONDS = 2
SIZE_LIMITS = (60, 4000)
QUALITY_LIMITS = (20, 100)
AUTO_TAG_MIN_CONFIDENCE = 0.35
AUTO_TAG_MAX_RESULTS = 5


@dataclass(frozen=True)
class ImageRecord:
    relative_path: str
    directory: str
    filename: str
    tags: tuple[str, ...]
    is_favorite: bool = False
    rotation_turns: int = 0

    @property
    def rotation_degrees(self) -> int:
        return self.rotation_turns * 90


@dataclass(frozen=True)
class ImageMetadata:
    tags: tuple[str, ...] = ()
    is_favorite: bool = False
    rotation_turns: int = 0

    @property
    def rotation_degrees(self) -> int:
        return self.rotation_turns * 90


@dataclass(frozen=True)
class VariantSettings:
    thumb_width: int = THUMB_SIZE[0]
    thumb_height: int = THUMB_SIZE[1]
    preview_width: int = PREVIEW_SIZE[0]
    preview_height: int = PREVIEW_SIZE[1]
    quality: int = JPEG_QUALITY

    @property
    def thumb_size(self) -> tuple[int, int]:
        return (self.thumb_width, self.thumb_height)

    @property
    def preview_size(self) -> tuple[int, int]:
        return (self.preview_width, self.preview_height)

    @property
    def thumb_cache_key(self) -> str:
        return self._cache_key(self.thumb_size)

    @property
    def preview_cache_key(self) -> str:
        return self._cache_key(self.preview_size)

    def cache_key_for(self, variant: str) -> str:
        return self.preview_cache_key if variant == "preview" else self.thumb_cache_key

    def to_session_payload(self) -> dict[str, int]:
        return {
            "thumb_width": self.thumb_width,
            "thumb_height": self.thumb_height,
            "preview_width": self.preview_width,
            "preview_height": self.preview_height,
            "quality": self.quality,
        }

    def _cache_key(self, size: tuple[int, int]) -> str:
        return f"{size[0]}x{size[1]}_q{self.quality}"


class PhotoLibrary:
    def __init__(self, images_root: Path, cache_root: Path) -> None:
        self.images_root = images_root
        self.cache_root = cache_root
        self.previews_root = cache_root / "miniatures"
        self.thumbs_root = cache_root / "vignettes"
        self.metadata_file = cache_root / "metadata.json"
        self.images_root.mkdir(parents=True, exist_ok=True)
        self.previews_root.mkdir(parents=True, exist_ok=True)
        self.thumbs_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._records_cache: list[ImageRecord] = []
        self._directory_cache: list[str] = [""]
        self._last_scan_at = 0.0

    def list_images(self) -> list[ImageRecord]:
        if time.time() - self._last_scan_at < CACHE_TTL_SECONDS:
            return self._records_cache

        metadata = self._load_metadata()
        records: list[ImageRecord] = []
        directories = {""}

        for current_root, dirnames, filenames in os.walk(self.images_root):
            current_path = Path(current_root)
            relative_dir = current_path.relative_to(self.images_root)
            depth = len(relative_dir.parts)

            dirnames[:] = sorted(
                directory
                for directory in dirnames
                if not directory.startswith(".")
            )
            if depth >= 2:
                dirnames[:] = []

            normalized_dir = self._normalize_relative_dir(relative_dir)
            directories.add(normalized_dir)

            for filename in sorted(filenames):
                path = current_path / filename
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                relative_path = self._normalize_relative_path(path.relative_to(self.images_root))
                entry = metadata.get(relative_path, ImageMetadata())
                records.append(
                    ImageRecord(
                        relative_path=relative_path,
                        directory=normalized_dir,
                        filename=filename,
                        tags=entry.tags,
                        is_favorite=entry.is_favorite,
                        rotation_turns=entry.rotation_turns,
                    )
                )

        records.sort(key=lambda record: (record.directory, record.filename.lower()))
        self._records_cache = records
        self._directory_cache = sorted(directories)
        self._last_scan_at = time.time()
        return records

    def list_directories(self) -> list[str]:
        self.list_images()
        return self._directory_cache

    def filter_images(
        self,
        directory: str = "",
        tag: str = "",
        only_untagged: bool = False,
        favorites_only: bool = False,
    ) -> list[ImageRecord]:
        normalized_directory = self._clean_directory(directory)
        normalized_tag = tag.strip().lower()
        filtered: list[ImageRecord] = []

        for record in self.list_images():
            if normalized_directory and not self._is_in_directory_scope(record.relative_path, normalized_directory):
                continue
            if only_untagged and record.tags:
                continue
            if favorites_only and not record.is_favorite:
                continue
            if normalized_tag and normalized_tag not in {current.lower() for current in record.tags}:
                continue
            filtered.append(record)

        return filtered

    def tag_summary(self, directory: str = "") -> list[tuple[str, int]]:
        summary: dict[str, int] = {}
        for record in self.filter_images(directory=directory):
            for tag in record.tags:
                summary[tag] = summary.get(tag, 0) + 1
        return sorted(summary.items(), key=lambda item: (-item[1], item[0].lower()))

    def count_untagged(self, directory: str = "") -> int:
        return sum(1 for record in self.filter_images(directory=directory) if not record.tags)

    def count_favorites(self, directory: str = "") -> int:
        return sum(1 for record in self.filter_images(directory=directory) if record.is_favorite)

    def ensure_variant(self, relative_path: str, variant: str, settings: VariantSettings) -> Path:
        clean_relative_path = self._clean_relative_path(relative_path)
        source = self.images_root / clean_relative_path
        if not source.exists():
            raise FileNotFoundError(relative_path)
        entry = self._load_metadata().get(clean_relative_path, ImageMetadata())

        target_root, size = (
            (self.previews_root, settings.preview_size)
            if variant == "preview"
            else (self.thumbs_root, settings.thumb_size)
        )
        variant_key = f"{settings.cache_key_for(variant)}_rot{entry.rotation_degrees}"
        target = (target_root / variant_key / clean_relative_path).with_suffix(".jpg")
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return target

        with Image.open(source) as image:
            converted = image.convert("RGB")
            converted = self._apply_rotation(converted, entry.rotation_turns)
            converted.thumbnail(size)
            converted.save(target, format="JPEG", quality=settings.quality, optimize=True)
        return target

    def suggest_tags_from_image(self, relative_path: str, limit: int = AUTO_TAG_MAX_RESULTS) -> list[tuple[str, float]]:
        clean_relative_path = self._clean_relative_path(relative_path)
        source = self.images_root / clean_relative_path
        if not source.exists():
            raise FileNotFoundError(relative_path)

        raw_suggestions = self._classify_with_macos_vision(source)
        suggestions: list[tuple[str, float]] = []
        seen: set[str] = set()

        for label, confidence in raw_suggestions:
            normalized = self._normalize_suggested_tag(label)
            if not normalized or normalized in seen:
                continue
            suggestions.append((normalized, confidence))
            seen.add(normalized)
            if len(suggestions) >= limit:
                break

        return suggestions

    def add_automatic_tag(self, relative_path: str) -> tuple[str, float, list[tuple[str, float]], bool]:
        suggestions = self.suggest_tags_from_image(relative_path)
        if not suggestions:
            raise ValueError("Aucun tag automatique exploitable n'a été trouvé.")

        tag, confidence = suggestions[0]
        if confidence < AUTO_TAG_MIN_CONFIDENCE:
            raise ValueError("La détection automatique n'est pas assez fiable pour proposer un tag.")

        updated = self.add_tags([relative_path], tag)
        return tag, confidence, suggestions, bool(updated)

    def remove_tag_from_image(self, relative_path: str, tag: str) -> bool:
        clean_relative_path = self._clean_relative_path(relative_path)
        if not tag.strip():
            raise ValueError("Tag invalide.")
        updated = self.remove_tags([clean_relative_path], tag)
        return bool(updated)

    def add_tags(self, relative_paths: Iterable[str], raw_tags: str) -> int:
        tags = self._parse_tags(raw_tags)
        if not tags:
            return 0

        metadata = self._load_metadata()
        updated_count = 0
        for relative_path in self._validated_existing_paths(relative_paths):
            entry = metadata.get(relative_path, ImageMetadata())
            current_tags = set(entry.tags)
            new_tags = current_tags | tags
            if new_tags != current_tags:
                metadata[relative_path] = ImageMetadata(
                    tags=tuple(sorted(new_tags)),
                    is_favorite=entry.is_favorite,
                    rotation_turns=entry.rotation_turns,
                )
                updated_count += 1

        if updated_count:
            self._save_metadata(metadata)
            self.invalidate_index()
        return updated_count

    def remove_tags(self, relative_paths: Iterable[str], raw_tags: str) -> int:
        tags = self._parse_tags(raw_tags)
        if not tags:
            return 0

        metadata = self._load_metadata()
        updated_count = 0
        for relative_path in self._validated_existing_paths(relative_paths):
            entry = metadata.get(relative_path, ImageMetadata())
            current_tags = set(entry.tags)
            new_tags = current_tags - tags
            if new_tags != current_tags:
                if new_tags or entry.is_favorite or entry.rotation_turns:
                    metadata[relative_path] = ImageMetadata(
                        tags=tuple(sorted(new_tags)),
                        is_favorite=entry.is_favorite,
                        rotation_turns=entry.rotation_turns,
                    )
                else:
                    metadata.pop(relative_path, None)
                updated_count += 1

        if updated_count:
            self._save_metadata(metadata)
            self.invalidate_index()
        return updated_count

    def delete_image(self, relative_path: str) -> str:
        valid_paths = self._validated_existing_paths([relative_path])
        if not valid_paths:
            raise ValueError("Image introuvable.")

        clean_relative_path = valid_paths[0]
        source = self.images_root / clean_relative_path
        metadata = self._load_metadata()

        source.unlink()
        metadata.pop(clean_relative_path, None)
        self._save_metadata(metadata)
        self._remove_cached_variants(clean_relative_path)
        self.invalidate_index()
        return clean_relative_path

    def create_directory(self, parent_directory: str, name: str) -> str:
        clean_parent = self._clean_directory(parent_directory)
        clean_name = self._sanitize_directory_name(name)
        if not clean_name:
            raise ValueError("Le nom du dossier est vide.")

        target = self.images_root / clean_parent / clean_name
        relative_target = self._normalize_relative_dir(target.relative_to(self.images_root))
        if len(Path(relative_target).parts) > 2:
            raise ValueError("La profondeur maximale est de deux niveaux sous Images/.")

        target.mkdir(parents=True, exist_ok=True)
        self.invalidate_index()
        return relative_target

    def move_images(self, relative_paths: Iterable[str], target_directory: str) -> int:
        clean_target_directory = self._clean_directory(target_directory)
        target_folder = self.images_root / clean_target_directory
        if not target_folder.exists() or not target_folder.is_dir():
            raise ValueError("Le dossier cible n'existe pas.")
        if len(Path(clean_target_directory).parts) > 2:
            raise ValueError("Le dossier cible dépasse la profondeur autorisée.")

        metadata = self._load_metadata()
        moved = 0

        for relative_path in self._validated_existing_paths(relative_paths):
            source = self.images_root / relative_path
            if source.parent == target_folder:
                continue

            destination = self._unique_destination(target_folder / source.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)

            new_relative_path = self._normalize_relative_path(destination.relative_to(self.images_root))
            if relative_path in metadata:
                metadata[new_relative_path] = metadata.pop(relative_path)

            self._remove_cached_variants(relative_path)
            moved += 1

        if moved:
            self._save_metadata(metadata)
            self.invalidate_index()
        return moved

    def set_favorite(self, relative_path: str, is_favorite: bool) -> bool:
        valid_paths = self._validated_existing_paths([relative_path])
        if not valid_paths:
            raise ValueError("Image introuvable.")

        clean_relative_path = valid_paths[0]
        metadata = self._load_metadata()
        entry = metadata.get(clean_relative_path, ImageMetadata())

        if entry.is_favorite == is_favorite:
            return is_favorite

        if entry.tags or is_favorite or entry.rotation_turns:
            metadata[clean_relative_path] = ImageMetadata(
                tags=entry.tags,
                is_favorite=is_favorite,
                rotation_turns=entry.rotation_turns,
            )
        else:
            metadata.pop(clean_relative_path, None)

        self._save_metadata(metadata)
        self.invalidate_index()
        return is_favorite

    def set_rotation(self, relative_path: str, mode: str) -> int:
        valid_paths = self._validated_existing_paths([relative_path])
        if not valid_paths:
            raise ValueError("Image introuvable.")

        clean_relative_path = valid_paths[0]
        metadata = self._load_metadata()
        entry = metadata.get(clean_relative_path, ImageMetadata())
        current_turns = entry.rotation_turns

        if mode == "cw":
            rotation_turns = (current_turns + 1) % 4
        elif mode == "ccw":
            rotation_turns = (current_turns - 1) % 4
        elif mode == "reset":
            rotation_turns = 0
        else:
            raise ValueError("Action rotation inconnue.")

        if rotation_turns == current_turns:
            return rotation_turns

        if entry.tags or entry.is_favorite or rotation_turns:
            metadata[clean_relative_path] = ImageMetadata(
                tags=entry.tags,
                is_favorite=entry.is_favorite,
                rotation_turns=rotation_turns,
            )
        else:
            metadata.pop(clean_relative_path, None)

        self._save_metadata(metadata)
        self._remove_cached_variants(clean_relative_path)
        self.invalidate_index()
        return rotation_turns

    def import_uploaded_files(self, uploaded_files: Iterable, target_directory: str) -> int:
        clean_target_directory = self._clean_directory(target_directory)
        target_root = self.images_root / clean_target_directory
        target_root.mkdir(parents=True, exist_ok=True)

        imported = 0
        for uploaded_file in uploaded_files:
            filename = (getattr(uploaded_file, "filename", "") or "").strip()
            if not filename:
                continue

            normalized_filename = filename.replace("\\", "/")
            if Path(normalized_filename).suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            relative_destination = self._build_import_path(normalized_filename, clean_target_directory)
            destination = self._unique_destination(self.images_root / relative_destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            uploaded_file.save(destination)
            imported += 1

        if imported:
            self.invalidate_index()
        return imported

    def get_record(self, relative_path: str) -> ImageRecord | None:
        clean_relative_path = self._clean_relative_path(relative_path)
        for record in self.list_images():
            if record.relative_path == clean_relative_path:
                return record
        return None

    def get_exif_data(self, relative_path: str) -> list[tuple[str, str]]:
        clean_relative_path = self._clean_relative_path(relative_path)
        source = self.images_root / clean_relative_path
        if not source.exists():
            raise FileNotFoundError(relative_path)

        with Image.open(source) as image:
            exif = image.getexif()
            if not exif:
                return []

            entries: list[tuple[str, str]] = []
            for tag_id, raw_value in sorted(
                exif.items(),
                key=lambda item: str(ExifTags.TAGS.get(item[0], item[0])).lower(),
            ):
                label = str(ExifTags.TAGS.get(tag_id, f"Tag {tag_id}"))
                if label == "MakerNote":
                    continue
                formatted_value = self._format_exif_value(raw_value)
                if formatted_value:
                    entries.append((label, formatted_value))

            gps_entries = self._extract_gps_exif(exif)
            if gps_entries:
                entries.extend(gps_entries)
            return entries

    def invalidate_index(self) -> None:
        self._last_scan_at = 0.0

    def _load_metadata(self) -> dict[str, ImageMetadata]:
        if not self.metadata_file.exists():
            return {}
        with self.metadata_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        metadata: dict[str, ImageMetadata] = {}
        for key, value in payload.items():
            clean_key = self._clean_relative_path(key)
            tags, is_favorite, rotation_turns = self._parse_metadata_entry(value)
            metadata[clean_key] = ImageMetadata(
                tags=tags,
                is_favorite=is_favorite,
                rotation_turns=rotation_turns,
            )
        return metadata

    def _save_metadata(self, metadata: dict[str, ImageMetadata]) -> None:
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
        cleaned = {
            key: self._serialize_metadata_entry(value)
            for key, value in sorted(metadata.items())
            if value.tags or value.is_favorite or value.rotation_turns
        }
        with self.metadata_file.open("w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, indent=2, ensure_ascii=False)

    def _validated_existing_paths(self, relative_paths: Iterable[str]) -> list[str]:
        valid_paths: list[str] = []
        for relative_path in relative_paths:
            clean_path = self._clean_relative_path(relative_path)
            if (self.images_root / clean_path).exists():
                valid_paths.append(clean_path)
        return valid_paths

    def _remove_cached_variants(self, relative_path: str) -> None:
        clean_relative_path = self._clean_relative_path(relative_path)
        relative_variant = Path(clean_relative_path).with_suffix(".jpg")
        for root in (self.previews_root, self.thumbs_root):
            legacy_candidate = root / relative_variant
            if legacy_candidate.exists():
                legacy_candidate.unlink()
            for settings_dir in root.iterdir():
                if not settings_dir.is_dir():
                    continue
                candidate = settings_dir / relative_variant
                if candidate.exists():
                    candidate.unlink()

    @staticmethod
    def _classify_with_macos_vision(source: Path) -> list[tuple[str, float]]:
        swift_script = """
import CoreGraphics
import Foundation
import ImageIO
import Vision

let path = {path}
let limit = {limit}
let url = URL(fileURLWithPath: path) as CFURL
guard let source = CGImageSourceCreateWithURL(url, nil) else {{
    fputs("image-source-error\\n", stderr)
    exit(3)
}}
guard let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else {{
    fputs("image-decode-error\\n", stderr)
    exit(4)
}}

func classify(_ image: CGImage) throws -> [[String: Any]] {{
    let request = VNClassifyImageRequest()
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    return request.results?.prefix(limit).map {{ observation in
        ["identifier": observation.identifier, "confidence": observation.confidence]
    }} ?? []
}}

var results = try classify(cgImage)

if #available(macOS 10.15, *) {{
    let saliencyRequest = VNGenerateAttentionBasedSaliencyImageRequest()
    let saliencyHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? saliencyHandler.perform([saliencyRequest])
    if let observation = saliencyRequest.results?.first,
       let salientObject = observation.salientObjects?.max(by: {{ $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }}) {{
        let imageRect = CGRect(x: 0, y: 0, width: cgImage.width, height: cgImage.height)
        let box = VNImageRectForNormalizedRect(salientObject.boundingBox, cgImage.width, cgImage.height)
        let expanded = box.insetBy(dx: -box.width * 0.12, dy: -box.height * 0.12).intersection(imageRect)
        if expanded.width > 0, expanded.height > 0, let cropped = cgImage.cropping(to: expanded) {{
            let foregroundResults = try classify(cropped)
            if !foregroundResults.isEmpty {{
                results = foregroundResults
            }}
        }}
    }}
}}

let data = try JSONSerialization.data(withJSONObject: results, options: [])
print(String(data: data, encoding: .utf8) ?? "[]")
""".format(path=json.dumps(str(source)), limit=AUTO_TAG_MAX_RESULTS)
        module_cache_root = CACHE_ROOT / "vision-module-cache"
        module_cache_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache_root)
        environment["SWIFT_MODULE_CACHE_PATH"] = str(module_cache_root)
        environment.setdefault("HOME", str(Path.home()))

        try:
            result = subprocess.run(
                ["swift", "-e", swift_script],
                check=True,
                capture_output=True,
                env=environment,
                text=True,
            )
        except FileNotFoundError as error:
            raise RuntimeError("Swift n'est pas disponible sur cette machine.") from error
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").strip()
            raise RuntimeError(
                f"Analyse automatique impossible via Vision macOS{f' ({stderr})' if stderr else '.'}"
            ) from error

        try:
            payload = json.loads((result.stdout or "").strip() or "[]")
        except json.JSONDecodeError as error:
            raise RuntimeError("Réponse invalide du moteur de classification locale.") from error

        suggestions: list[tuple[str, float]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            label = str(item.get("identifier", "")).strip()
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if label:
                suggestions.append((label, confidence))
        return suggestions

    @staticmethod
    def _normalize_suggested_tag(label: str) -> str:
        cleaned = " ".join(label.replace("_", " ").replace("-", " ").split())
        return cleaned.strip().lower()

    @staticmethod
    def _extract_gps_exif(exif) -> list[tuple[str, str]]:
        gps_ifd = getattr(ExifTags, "IFD", None)
        gps_tag_id = getattr(gps_ifd, "GPSInfo", None)
        if gps_tag_id is None:
            return []

        try:
            gps_info = exif.get_ifd(gps_tag_id)
        except Exception:
            return []

        if not gps_info:
            return []

        entries: list[tuple[str, str]] = []
        for tag_id, raw_value in sorted(gps_info.items(), key=lambda item: item[0]):
            label = str(ExifTags.GPSTAGS.get(tag_id, f"GPS {tag_id}"))
            formatted_value = PhotoLibrary._format_exif_value(raw_value)
            if formatted_value:
                entries.append((f"GPS {label}", formatted_value))
        return entries

    @staticmethod
    def _format_exif_value(value: object) -> str:
        if value is None:
            return ""

        if isinstance(value, bytes):
            return "" if not value else f"{len(value)} octets"

        if isinstance(value, str):
            return " ".join(value.split())

        if isinstance(value, (list, tuple)):
            parts = [PhotoLibrary._format_exif_value(item) for item in value]
            cleaned = [part for part in parts if part]
            return ", ".join(cleaned)

        numerator = getattr(value, "numerator", None)
        denominator = getattr(value, "denominator", None)
        if numerator is not None and denominator not in (None, 0):
            if denominator == 1:
                return str(numerator)
            decimal = numerator / denominator
            if abs(decimal) >= 1:
                return f"{decimal:.2f}".rstrip("0").rstrip(".")
            return f"{numerator}/{denominator}"

        if isinstance(value, float):
            return f"{value:.4f}".rstrip("0").rstrip(".")

        return str(value)

    @staticmethod
    def _parse_metadata_entry(value: object) -> tuple[tuple[str, ...], bool, int]:
        if isinstance(value, dict):
            raw_tags = value.get("tags", [])
            raw_favorite = value.get("favorite", False)
            raw_rotation = value.get("rotation", 0)
        else:
            raw_tags = value
            raw_favorite = False
            raw_rotation = 0

        tags = tuple(sorted({str(tag).strip() for tag in raw_tags if str(tag).strip()}))
        return tags, bool(raw_favorite), PhotoLibrary._normalize_rotation_turns(raw_rotation)

    @staticmethod
    def _serialize_metadata_entry(entry: ImageMetadata) -> dict[str, object]:
        payload: dict[str, object] = {"tags": list(entry.tags)}
        if entry.is_favorite:
            payload["favorite"] = True
        if entry.rotation_turns:
            payload["rotation"] = entry.rotation_turns
        return payload

    @staticmethod
    def _normalize_rotation_turns(value: object) -> int:
        try:
            normalized = int(value) % 4
        except (TypeError, ValueError):
            return 0
        return normalized

    @staticmethod
    def _apply_rotation(image: Image.Image, rotation_turns: int) -> Image.Image:
        turns = PhotoLibrary._normalize_rotation_turns(rotation_turns)
        if turns == 1:
            return image.transpose(Image.Transpose.ROTATE_270)
        if turns == 2:
            return image.transpose(Image.Transpose.ROTATE_180)
        if turns == 3:
            return image.transpose(Image.Transpose.ROTATE_90)
        return image

    @staticmethod
    def _parse_tags(raw_tags: str) -> set[str]:
        tags: set[str] = set()
        for item in raw_tags.replace(";", ",").split(","):
            tag = item.strip()
            if tag:
                tags.add(tag)
        return tags

    @staticmethod
    def _sanitize_directory_name(name: str) -> str:
        cleaned = name.strip().replace("\\", " ").replace("/", " ")
        return " ".join(cleaned.split())

    @classmethod
    def _build_import_path(cls, upload_name: str, target_directory: str) -> Path:
        normalized = Path(upload_name)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("Chemin d'import invalide.")

        clean_parts = [
            cls._sanitize_directory_name(part)
            for part in normalized.parts[:-1]
            if cls._sanitize_directory_name(part)
        ]
        filename = normalized.name.strip()
        if not filename:
            raise ValueError("Nom de fichier invalide.")

        relative_destination = Path(target_directory, *clean_parts, filename)
        clean_destination = Path(cls._clean_relative_path(relative_destination.as_posix()))
        if len(clean_destination.parts[:-1]) > 2:
            raise ValueError("La profondeur maximale est de deux niveaux sous Images/.")
        return clean_destination

    @staticmethod
    def _normalize_relative_dir(path: Path) -> str:
        if str(path) == ".":
            return ""
        return path.as_posix()

    @staticmethod
    def _normalize_relative_path(path: Path) -> str:
        return path.as_posix()

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        counter = 1
        while True:
            candidate = destination.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _is_in_directory_scope(relative_path: str, directory: str) -> bool:
        path = Path(relative_path)
        directory_path = Path(directory)
        return directory_path == path.parent or directory_path in path.parent.parents

    @staticmethod
    def _clean_relative_path(relative_path: str) -> str:
        clean_path = Path(relative_path)
        if clean_path.is_absolute() or ".." in clean_path.parts:
            raise ValueError("Chemin invalide.")
        return clean_path.as_posix()

    @staticmethod
    def _clean_directory(directory: str) -> str:
        if not directory:
            return ""
        clean_path = Path(directory)
        if clean_path.is_absolute() or ".." in clean_path.parts:
            raise ValueError("Dossier invalide.")
        return "" if str(clean_path) == "." else clean_path.as_posix()


app = Flask(__name__)
app.config["SECRET_KEY"] = "hackathon-photo-secret"
library = PhotoLibrary(IMAGES_ROOT, CACHE_ROOT)


@app.context_processor
def inject_helpers() -> dict[str, object]:
    return {
        "breadcrumb_parts": breadcrumb_parts,
        "directory_label": directory_label,
        "gallery_query": gallery_query,
        "gallery_url": gallery_url,
        "variant_settings": current_variant_settings(),
    }


@app.get("/")
def index() -> str:
    current_directory = request.args.get("dir", "").strip()
    tag = request.args.get("tag", "").strip()
    only_untagged = request.args.get("untagged", "") == "1"
    favorites_only = request.args.get("favorites", "") == "1"

    try:
        clean_directory = library._clean_directory(current_directory)
    except ValueError:
        abort(400)

    records = library.filter_images(
        directory=clean_directory,
        tag=tag,
        only_untagged=only_untagged,
        favorites_only=favorites_only,
    )
    return render_template(
        "index.html",
        records=records,
        directories=library.list_directories(),
        current_directory=clean_directory,
        tag=tag,
        only_untagged=only_untagged,
        favorites_only=favorites_only,
        total_images=len(library.list_images()),
        total_visible=len(records),
        favorite_count=library.count_favorites(clean_directory),
        tag_summary=library.tag_summary(clean_directory),
        untagged_count=library.count_untagged(clean_directory),
        return_query=request.query_string.decode("utf-8"),
    )


@app.get("/image/<path:relative_path>")
def image_detail(relative_path: str) -> str:
    record = library.get_record(relative_path)
    if record is None:
        abort(404)
    return render_template(
        "detail.html",
        record=record,
        exif_entries=library.get_exif_data(relative_path),
        selected_query=request.args.get("from", ""),
    )


@app.get("/assets/<variant>/<path:relative_path>")
def image_asset(variant: str, relative_path: str):
    if variant not in {"preview", "thumb"}:
        abort(404)
    try:
        path = library.ensure_variant(relative_path, variant, current_variant_settings())
    except (FileNotFoundError, ValueError):
        abort(404)
    return send_file(path, mimetype="image/jpeg", conditional=True)


@app.get("/original/<path:relative_path>")
def original_asset(relative_path: str):
    try:
        clean_relative_path = library._clean_relative_path(relative_path)
    except ValueError:
        abort(404)
    source = IMAGES_ROOT / clean_relative_path
    if not source.exists():
        abort(404)
    return send_file(source, conditional=True)


@app.post("/actions/import")
def import_images() -> object:
    uploaded_files = request.files.getlist("images") + request.files.getlist("folder_images")
    if not uploaded_files:
        flash("Sélectionnez au moins une image ou un dossier.", "error")
        return redirect(_redirect_target())

    try:
        imported = library.import_uploaded_files(
            uploaded_files,
            request.form.get("target_directory", ""),
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    if not imported:
        flash("Aucune image valide importée.", "error")
    else:
        flash(f"{imported} image(s) importée(s) depuis votre ordinateur.", "success")
    return redirect(_redirect_target())


@app.post("/actions/render-settings")
def update_render_settings() -> object:
    if request.form.get("mode") == "reset":
        session.pop("variant_settings", None)
        flash("Réglages d'image réinitialisés.", "success")
        return redirect(_redirect_target())

    try:
        settings = parse_variant_settings(request.form)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    session["variant_settings"] = settings.to_session_payload()
    flash("Réglages d'image mis à jour.", "success")
    return redirect(_redirect_target())


@app.post("/actions/favorite")
def update_favorite() -> object:
    relative_path = request.form.get("relative_path", "").strip()
    mode = request.form.get("mode", "").strip()
    if mode not in {"on", "off"}:
        flash("Action favori inconnue.", "error")
        return redirect(_redirect_target())

    try:
        is_favorite = library.set_favorite(relative_path, is_favorite=mode == "on")
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    flash("Image ajoutée aux favoris." if is_favorite else "Image retirée des favoris.", "success")
    return redirect(_redirect_target())


@app.post("/actions/rotation")
def update_rotation() -> object:
    relative_path = request.form.get("relative_path", "").strip()
    mode = request.form.get("mode", "").strip()

    try:
        rotation_turns = library.set_rotation(relative_path, mode)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    flash(f"Rotation appliquée : {rotation_turns * 90}°.", "success")
    return redirect(_redirect_target())


@app.post("/actions/auto-tag")
def auto_tag_image() -> object:
    relative_path = request.form.get("relative_path", "").strip()

    try:
        tag, confidence, suggestions, created = library.add_automatic_tag(relative_path)
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    alternatives = ", ".join(name for name, _ in suggestions[1:3])
    message = (
        f"Tag automatique {'ajouté' if created else 'déjà présent'} : {tag} "
        f"({confidence * 100:.0f}% de confiance)."
    )
    if alternatives:
        message += f" Alternatives : {alternatives}."
    flash(message, "success")
    return redirect(_redirect_target())


@app.post("/actions/remove-tag")
def remove_single_tag() -> object:
    relative_path = request.form.get("relative_path", "").strip()
    tag = request.form.get("tag", "").strip()

    try:
        removed = library.remove_tag_from_image(relative_path, tag)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    if removed:
        flash(f"Tag retiré : {tag}.", "success")
    else:
        flash(f"Le tag {tag} n'était pas présent.", "error")
    return redirect(_redirect_target())


@app.post("/actions/delete-image")
def delete_image() -> object:
    relative_path = request.form.get("relative_path", "").strip()

    try:
        deleted_path = library.delete_image(relative_path)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())
    except OSError as error:
        flash(f"Suppression impossible : {error}", "error")
        return redirect(_redirect_target())

    flash(f"Image supprimée : {Path(deleted_path).name}.", "success")
    return redirect(_redirect_target())


@app.post("/actions/create-directory")
def create_directory() -> object:
    parent_directory = request.form.get("parent_directory", "")
    name = request.form.get("name", "")
    try:
        new_directory = library.create_directory(parent_directory, name)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(_redirect_target())

    flash(f"Dossier créé : {new_directory or 'Images'}", "success")
    return redirect(url_for("index", dir=new_directory))


@app.post("/actions/batch")
def batch_action() -> object:
    action = request.form.get("action", "")
    selected = request.form.getlist("selected")

    if not selected:
        flash("Sélectionnez au moins une image.", "error")
        return redirect(_redirect_target())

    try:
        if action == "add_tag":
            updated = library.add_tags(selected, request.form.get("tag_value", ""))
            flash(f"{updated} image(s) taguée(s).", "success" if updated else "error")
        elif action == "remove_tag":
            updated = library.remove_tags(selected, request.form.get("tag_value", ""))
            flash(f"{updated} image(s) mises à jour.", "success" if updated else "error")
        elif action == "move":
            moved = library.move_images(selected, request.form.get("target_directory", ""))
            flash(f"{moved} image(s) déplacée(s).", "success" if moved else "error")
        else:
            flash("Action inconnue.", "error")
    except ValueError as error:
        flash(str(error), "error")

    return redirect(_redirect_target())


def _redirect_target() -> str:
    fallback = url_for("index")
    next_value = request.form.get("next", "").strip()
    if not next_value:
        return fallback
    if next_value.startswith("/"):
        return next_value
    return fallback


def breadcrumb_parts(directory: str) -> list[tuple[str, str]]:
    clean_directory = library._clean_directory(directory)
    if not clean_directory:
        return []
    breadcrumbs: list[tuple[str, str]] = []
    current = Path()
    for part in Path(clean_directory).parts:
        current /= part
        breadcrumbs.append((part, current.as_posix()))
    return breadcrumbs


def directory_label(directory: str) -> str:
    return directory or "Images"


def gallery_query(
    directory: str = "",
    tag: str = "",
    only_untagged: bool = False,
    favorites_only: bool = False,
) -> dict[str, str]:
    query: dict[str, str] = {}
    if directory:
        query["dir"] = directory
    if tag:
        query["tag"] = tag
    if only_untagged:
        query["untagged"] = "1"
    if favorites_only:
        query["favorites"] = "1"
    return query


def gallery_url(
    directory: str = "",
    tag: str = "",
    only_untagged: bool = False,
    favorites_only: bool = False,
) -> str:
    return url_for(
        "index",
        **gallery_query(
            directory=directory,
            tag=tag,
            only_untagged=only_untagged,
            favorites_only=favorites_only,
        ),
    )


def current_variant_settings() -> VariantSettings:
    stored = session.get("variant_settings", {})
    if not isinstance(stored, dict):
        return VariantSettings()

    return VariantSettings(
        thumb_width=coerce_int_setting(stored, "thumb_width", THUMB_SIZE[0]),
        thumb_height=coerce_int_setting(stored, "thumb_height", THUMB_SIZE[1]),
        preview_width=coerce_int_setting(stored, "preview_width", PREVIEW_SIZE[0]),
        preview_height=coerce_int_setting(stored, "preview_height", PREVIEW_SIZE[1]),
        quality=coerce_int_setting(
            stored,
            "quality",
            JPEG_QUALITY,
            minimum=QUALITY_LIMITS[0],
            maximum=QUALITY_LIMITS[1],
        ),
    )


def parse_variant_settings(payload: Mapping[str, object]) -> VariantSettings:
    return VariantSettings(
        thumb_width=parse_required_int_setting(payload, "thumb_width", "largeur vignette"),
        thumb_height=parse_required_int_setting(payload, "thumb_height", "hauteur vignette"),
        preview_width=parse_required_int_setting(payload, "preview_width", "largeur aperçu"),
        preview_height=parse_required_int_setting(payload, "preview_height", "hauteur aperçu"),
        quality=parse_required_int_setting(
            payload,
            "quality",
            "qualité JPEG",
            minimum=QUALITY_LIMITS[0],
            maximum=QUALITY_LIMITS[1],
        ),
    )


def parse_required_int_setting(
    payload: Mapping[str, object],
    key: str,
    label: str,
    minimum: int = SIZE_LIMITS[0],
    maximum: int = SIZE_LIMITS[1],
) -> int:
    raw_value = str(payload.get(key, "")).strip()
    if not raw_value:
        raise ValueError(f"Le champ {label} est obligatoire.")

    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"Le champ {label} doit être un entier.") from error

    if not minimum <= value <= maximum:
        raise ValueError(f"Le champ {label} doit être compris entre {minimum} et {maximum}.")
    return value


def coerce_int_setting(
    payload: Mapping[str, object],
    key: str,
    default: int,
    minimum: int = SIZE_LIMITS[0],
    maximum: int = SIZE_LIMITS[1],
) -> int:
    raw_value = payload.get(key, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    if not minimum <= value <= maximum:
        return default
    return value


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
