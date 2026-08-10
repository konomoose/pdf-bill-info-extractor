from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .pdf_preparation import (
    PDFPreparationError,
    normalize_account_key,
)
from .pdf_redaction import normalize_redaction_terms
from .profile_loader import PROJECT_ROOT


DEFAULT_REDACTION_TERMS_PATH = (
    PROJECT_ROOT
    / "config"
    / "redaction_terms.local.json"
)

ACCOUNTS_KEY = "accounts"
INSTITUTIONS_KEY = "institutions"
TERMS_KEY = "terms"
ETRANSFER_KEEP_FIRST_NAME_ONLY_KEY = "etransfer_keep_first_name_only"


@dataclass(frozen=True)
class RedactionAccountSettings:
    terms: tuple[str, ...] = ()
    etransfer_keep_first_name_only: bool = False


def _settings_path(
    settings_path: Path | None,
) -> Path:
    return (
        Path(settings_path)
        if settings_path is not None
        else DEFAULT_REDACTION_TERMS_PATH
    ).resolve()


def _account_key_text(
    account_key: str | Path,
) -> str:
    return normalize_account_key(account_key).as_posix()


def _normalized_terms(
    terms: Iterable[str],
) -> tuple[str, ...]:
    return normalize_redaction_terms(tuple(terms))


def _load_settings_data(
    *,
    settings_path: Path | None = None,
) -> dict:
    path = _settings_path(settings_path)

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def _unique_terms(
    values: Iterable[str],
) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()

    for term in values:
        key = term.casefold()
        if key in seen:
            continue

        seen.add(key)
        terms.append(term)

    return tuple(terms)


def _load_account_settings(
    *,
    settings_path: Path | None = None,
) -> dict[str, RedactionAccountSettings]:
    data = _load_settings_data(
        settings_path=settings_path,
    )
    accounts = data.get(ACCOUNTS_KEY, {})

    if not isinstance(accounts, dict):
        return {}

    normalized: dict[str, RedactionAccountSettings] = {}

    for account_key, value in accounts.items():
        if not isinstance(account_key, str):
            continue

        if isinstance(value, list):
            raw_terms = value
            etransfer_keep_first_name_only = False
        elif isinstance(value, dict):
            raw_terms = value.get(TERMS_KEY, [])
            if not isinstance(raw_terms, list):
                continue

            raw_option = value.get(
                ETRANSFER_KEEP_FIRST_NAME_ONLY_KEY,
                False,
            )
            if not isinstance(raw_option, bool):
                continue

            etransfer_keep_first_name_only = raw_option
        else:
            continue

        if any(not isinstance(term, str) for term in raw_terms):
            continue

        try:
            normalized_key = _account_key_text(account_key)
            normalized_terms = _normalized_terms(raw_terms)
        except Exception:
            continue

        normalized[normalized_key] = RedactionAccountSettings(
            terms=normalized_terms,
            etransfer_keep_first_name_only=etransfer_keep_first_name_only,
        )

    return {
        account_key: normalized[account_key]
        for account_key in sorted(
            normalized,
            key=str.casefold,
        )
    }


def _load_institution_terms(
    *,
    settings_path: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    data = _load_settings_data(
        settings_path=settings_path,
    )
    institutions = data.get(INSTITUTIONS_KEY, {})

    if not isinstance(institutions, dict):
        return {}

    normalized: dict[str, tuple[str, ...]] = {}

    for institution, value in institutions.items():
        if not isinstance(institution, str) or not institution.strip():
            continue

        if isinstance(value, list):
            raw_terms = value
        elif isinstance(value, dict):
            raw_terms = value.get(TERMS_KEY, [])
            if not isinstance(raw_terms, list):
                continue
        else:
            continue

        if any(not isinstance(term, str) for term in raw_terms):
            continue

        try:
            normalized[institution.strip()] = _normalized_terms(raw_terms)
        except Exception:
            continue

    return {
        institution: normalized[institution]
        for institution in sorted(
            normalized,
            key=str.casefold,
        )
    }


def _normalized_account_key_set(
    account_keys: Iterable[str | Path],
) -> tuple[str, ...]:
    normalized: set[str] = set()

    for account_key in account_keys:
        normalized.add(
            _account_key_text(account_key)
        )

    return tuple(
        sorted(
            normalized,
            key=str.casefold,
        )
    )


def _settings_payload(
    accounts: dict[str, RedactionAccountSettings],
    institutions: dict[str, tuple[str, ...]],
) -> dict:
    payload: dict[str, dict] = {}

    if institutions:
        payload[INSTITUTIONS_KEY] = {
            key: {
                TERMS_KEY: list(institutions[key]),
            }
            for key in sorted(
                institutions,
                key=str.casefold,
            )
        }

    payload[ACCOUNTS_KEY] = {
        key: {
            TERMS_KEY: list(accounts[key].terms),
            ETRANSFER_KEEP_FIRST_NAME_ONLY_KEY: (
                accounts[key].etransfer_keep_first_name_only
            ),
        }
        for key in sorted(
            accounts,
            key=str.casefold,
        )
    }

    return payload


def load_redaction_term_accounts(
    *,
    settings_path: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    accounts = _load_account_settings(
        settings_path=settings_path,
    )

    return {
        account_key: settings.terms
        for account_key, settings in accounts.items()
    }


def load_redaction_account_settings(
    account_key: str | Path,
    *,
    settings_path: Path | None = None,
) -> RedactionAccountSettings:
    normalized_key = _account_key_text(account_key)
    accounts = _load_account_settings(
        settings_path=settings_path,
    )

    return accounts.get(
        normalized_key,
        RedactionAccountSettings(),
    )


def load_redaction_account_settings_for_institution(
    account_key: str | Path,
    *,
    institution: str | None,
    institution_account_keys: Iterable[str | Path] = (),
    settings_path: Path | None = None,
) -> RedactionAccountSettings:
    normalized_key = _account_key_text(account_key)
    accounts = _load_account_settings(
        settings_path=settings_path,
    )
    account_settings = accounts.get(
        normalized_key,
        RedactionAccountSettings(),
    )

    if institution is None or not institution.strip():
        return account_settings

    try:
        known_account_keys = _normalized_account_key_set(
            (*tuple(institution_account_keys), normalized_key)
        )
    except Exception:
        return account_settings

    institutions = _load_institution_terms(
        settings_path=settings_path,
    )
    combined: list[str] = []
    combined.extend(
        institutions.get(
            institution.strip(),
            (),
        )
    )

    for known_key in known_account_keys:
        combined.extend(
            accounts.get(
                known_key,
                RedactionAccountSettings(),
            ).terms
        )

    return RedactionAccountSettings(
        terms=_unique_terms(combined),
        etransfer_keep_first_name_only=(
            account_settings.etransfer_keep_first_name_only
        ),
    )


def load_redaction_terms_for_account(
    account_key: str | Path,
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    return load_redaction_account_settings(
        account_key,
        settings_path=settings_path,
    ).terms


def save_redaction_account_settings(
    account_key: str | Path,
    terms: Iterable[str],
    *,
    etransfer_keep_first_name_only: bool = False,
    institution: str | None = None,
    institution_account_keys: Iterable[str | Path] = (),
    settings_path: Path | None = None,
) -> RedactionAccountSettings:
    path = _settings_path(settings_path)
    normalized_key = _account_key_text(account_key)
    normalized_terms = _normalized_terms(terms)
    accounts = _load_account_settings(
        settings_path=settings_path,
    )
    institutions = _load_institution_terms(
        settings_path=settings_path,
    )

    if institution is not None and institution.strip():
        institution_name = institution.strip()
        known_account_keys = _normalized_account_key_set(
            (*tuple(institution_account_keys), normalized_key)
        )
        institutions[institution_name] = normalized_terms

        for known_key in known_account_keys:
            existing = accounts.get(
                known_key,
                RedactionAccountSettings(),
            )
            accounts[known_key] = RedactionAccountSettings(
                terms=(),
                etransfer_keep_first_name_only=(
                    etransfer_keep_first_name_only
                    if known_key == normalized_key
                    else existing.etransfer_keep_first_name_only
                ),
            )
    else:
        accounts[normalized_key] = RedactionAccountSettings(
            terms=normalized_terms,
            etransfer_keep_first_name_only=etransfer_keep_first_name_only,
        )

    payload = _settings_payload(
        accounts,
        institutions,
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        f".{path.name}.tmp"
    )

    try:
        temporary.write_text(
            json.dumps(
                payload,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(
            temporary,
            path,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    if institution is not None and institution.strip():
        return RedactionAccountSettings(
            terms=normalized_terms,
            etransfer_keep_first_name_only=(
                accounts[normalized_key].etransfer_keep_first_name_only
            ),
        )

    return accounts[normalized_key]


def save_redaction_terms_for_account(
    account_key: str | Path,
    terms: Iterable[str],
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    return save_redaction_account_settings(
        account_key,
        terms,
        settings_path=settings_path,
    ).terms
