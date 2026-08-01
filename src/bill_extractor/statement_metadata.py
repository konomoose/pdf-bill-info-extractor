from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class StatementMetadata:
    """Statement-level context used when normalizing transactions."""

    source_file: Path
    profile_id: str
    institution: str
    document_type: str
    statement_start_date: date | None = None
    statement_end_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_file",
            Path(self.source_file),
        )

        for field_name in (
            "profile_id",
            "institution",
            "document_type",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{field_name} must be a non-empty string."
                )

            object.__setattr__(
                self,
                field_name,
                value.strip(),
            )

        if (
            self.statement_start_date is not None
            and self.statement_end_date is not None
            and self.statement_start_date
            > self.statement_end_date
        ):
            raise ValueError(
                "statement_start_date cannot be later than "
                "statement_end_date."
            )
