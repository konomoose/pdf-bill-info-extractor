from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES_FOLDER = PROJECT_ROOT / "config" / "profiles"
DEFAULT_PROFILE_PATH = DEFAULT_PROFILES_FOLDER / "cibc_credit_card.json"


class ProfileError(RuntimeError):
    """Raised when an extraction profile is missing or invalid."""


@dataclass(frozen=True)
class ExtractionProfile:
    profile_id: str
    display_name: str
    profile_version: int
    institution: str
    document_type: str
    parser: str
    input_folder: Path
    output_folder: Path
    file_pattern: str
    recursive: bool
    preserve_subfolders: bool
    required_headers: tuple[str, ...]
    excluded_page_phrases: tuple[str, ...]
    line_tolerance: float
    continuation_gap: float
    source_path: Path

    def resolve_input_folder(self) -> Path:
        return _resolve_project_path(self.input_folder)

    def resolve_output_folder(self) -> Path:
        return _resolve_project_path(self.output_folder)


def _resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"Profile field '{key}' must be a non-empty string.")
    return value.strip()


def _require_string_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ProfileError(f"Profile field '{key}' must be a non-empty list.")

    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProfileError(
                f"Every entry in profile field '{key}' must be a non-empty string."
            )
        cleaned.append(item.strip())

    return tuple(cleaned)


def load_profile(profile_path: str | Path | None = None) -> ExtractionProfile:
    path = Path(profile_path) if profile_path else DEFAULT_PROFILE_PATH
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

    if not path.is_file():
        raise ProfileError(f"Profile file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as profile_file:
            data = json.load(profile_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Could not read profile {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ProfileError(f"Profile {path} must contain one JSON object.")

    version = data.get("profile_version")
    if not isinstance(version, int) or version < 1:
        raise ProfileError("Profile field 'profile_version' must be an integer >= 1.")

    recursive = data.get("recursive", False)
    preserve_subfolders = data.get("preserve_subfolders", False)
    if not isinstance(recursive, bool):
        raise ProfileError("Profile field 'recursive' must be true or false.")
    if not isinstance(preserve_subfolders, bool):
        raise ProfileError(
            "Profile field 'preserve_subfolders' must be true or false."
        )
    if preserve_subfolders and not recursive:
        raise ProfileError(
            "'preserve_subfolders' cannot be true when 'recursive' is false."
        )

    try:
        line_tolerance = float(data.get("line_tolerance", 2.5))
        continuation_gap = float(data.get("continuation_gap", 18.0))
    except (TypeError, ValueError) as exc:
        raise ProfileError(
            "Profile fields 'line_tolerance' and 'continuation_gap' must be numbers."
        ) from exc

    if line_tolerance <= 0 or continuation_gap <= 0:
        raise ProfileError(
            "Profile fields 'line_tolerance' and 'continuation_gap' must be positive."
        )

    return ExtractionProfile(
        profile_id=_require_string(data, "profile_id"),
        display_name=_require_string(data, "display_name"),
        profile_version=version,
        institution=_require_string(data, "institution"),
        document_type=_require_string(data, "document_type"),
        parser=_require_string(data, "parser"),
        input_folder=Path(_require_string(data, "input_folder")),
        output_folder=Path(_require_string(data, "output_folder")),
        file_pattern=_require_string(data, "file_pattern"),
        recursive=recursive,
        preserve_subfolders=preserve_subfolders,
        required_headers=_require_string_list(data, "required_headers"),
        excluded_page_phrases=_require_string_list(data, "excluded_page_phrases"),
        line_tolerance=line_tolerance,
        continuation_gap=continuation_gap,
        source_path=path,
    )


def discover_profiles(
    profiles_folder: str | Path | None = None,
) -> tuple[ExtractionProfile, ...]:
    folder = Path(profiles_folder) if profiles_folder else DEFAULT_PROFILES_FOLDER
    if not folder.is_absolute():
        folder = (PROJECT_ROOT / folder).resolve()

    if not folder.is_dir():
        return ()

    profiles = [load_profile(path) for path in sorted(folder.glob("*.json"))]
    identifiers = [profile.profile_id for profile in profiles]
    if len(identifiers) != len(set(identifiers)):
        raise ProfileError(f"Duplicate profile_id values found in {folder}.")

    return tuple(profiles)
