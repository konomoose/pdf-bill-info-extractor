"""Optional analysis copies. Never rename or modify extraction outputs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import json
from tempfile import NamedTemporaryFile
from typing import Iterable

import pandas as pd

from .profile_loader import ExtractionProfile, PROJECT_ROOT
from .transaction_normalizer import NORMALIZED_COLUMNS, PROFILE_MAPPINGS
from .yearly_consolidator import _stage_yearly_csv, _commit_yearly_outputs
from .institution_collector import account_slug, _normalized_statement_paths

ANALYSIS_ROOT = PROJECT_ROOT / 'analysis_output'
ANALYSIS_COLUMNS = (
    'transaction_date', 'posting_date', 'effective_date', 'description',
    'category', 'money_in', 'money_out', 'net_amount', 'balance',
    'original_amount', 'withdrawal', 'deposit', 'payment',
    'interest_fees_insurance', 'institution', 'account_type',
    'profile_id', 'profile_name', 'source_file',
)


class AnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class AnalysisResult:
    csv_path: Path
    transaction_count: int
    yearly_file_count: int
    monthly_file_count: int
    profile_count: int
    money_in: str
    money_out: str
    net_amount: str


def _money(value: str, field: str) -> Decimal:
    try:
        number = Decimal(value or '0')
        if not number.is_finite() or number != number.quantize(Decimal('.01')):
            raise InvalidOperation
        return number
    except InvalidOperation as exc:
        raise AnalysisError(f'Invalid monetary value in {field}; source CSV was not changed.') from exc


def _date(value: str, field: str, required: bool = False) -> str:
    if not value and not required:
        return ''
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError as exc:
        raise AnalysisError(f'Invalid {field}; re-extract the source statement.') from exc
    return value


def standardize(frame: pd.DataFrame, profile: ExtractionProfile, source: str) -> pd.DataFrame:
    """Map normalized columns to explicit account inflows/outflows using Decimal."""
    if tuple(frame.columns) not in (
        tuple(NORMALIZED_COLUMNS), profile.normalized_output_columns,
    ):
        raise AnalysisError(f'Unexpected normalized headers for {profile.display_name}: {source}')
    mapping = PROFILE_MAPPINGS.get(profile.profile_id)
    if mapping is None:
        raise AnalysisError(f'No money mapping for {profile.display_name}.')
    rows = []
    for values in frame.to_dict('records'):
        v = {key: str(values.get(key, '')).strip() for key in NORMALIZED_COLUMNS}
        if v['profile_id'] and v['profile_id'] != profile.profile_id:
            raise AnalysisError(f'Profile mismatch in {source}; no summary was replaced.')
        for field in ('transaction_date', 'posting_date', 'effective_date'):
            v[field] = _date(v[field], field, field == 'transaction_date')
        if not v['description']:
            raise AnalysisError(f'Blank transaction description in {source}.')
        for field in ('amount', 'withdrawal', 'deposit', 'payment', 'interest_fees_insurance', 'balance'):
            if v[field]:
                v[field] = format(_money(v[field], field), '.2f')
        if 'amount' in mapping:
            if not v['amount']:
                raise AnalysisError(f'Missing card amount in {source}.')
            # The supplied card parsers use positive purchases and negative credits.
            out = _money(v['amount'], 'amount')
            incoming = Decimal(0)
        else:
            if not any(v[f] for f in ('withdrawal','deposit','payment','interest_fees_insurance')):
                raise AnalysisError(f'Missing transaction amounts in {source}.')
            incoming = sum((_money(v[f], f) for f in ('deposit','payment')), Decimal(0))
            out = sum((_money(v[f], f) for f in ('withdrawal','interest_fees_insurance')), Decimal(0))
        # Preserve reversals without introducing negative inflow/outflow columns.
        money_in = max(incoming, Decimal(0)) + max(-out, Decimal(0))
        money_out = max(out, Decimal(0)) + max(-incoming, Decimal(0))
        row = {field: v.get(field, '') for field in ANALYSIS_COLUMNS}
        row.update(
            money_in=format(money_in, '.2f'), money_out=format(money_out, '.2f'),
            net_amount=format(money_in-money_out, '.2f'), original_amount=v['amount'],
            institution=profile.institution, account_type=profile.document_type,
            profile_id=profile.profile_id, profile_name=profile.display_name,
            source_file=v['source_file'] or source,
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=ANALYSIS_COLUMNS)


def _read(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, ValueError) as exc:
        raise AnalysisError(f'Cannot read {path.name}. No analysis outputs were replaced.') from exc


def _sorted(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=ANALYSIS_COLUMNS)
    # Each canonical year is read once and each profile is included once.
    # Do NOT drop equal-looking purchases: they may be real separate transactions.
    return pd.concat(frames, ignore_index=True).sort_values(
        ['transaction_date','posting_date','effective_date','profile_id','source_file'],
        kind='mergesort',
    ).reset_index(drop=True)


def _profile_outputs(profile: ExtractionProfile, root: Path):
    source_root = profile.resolve_output_folder()
    slug = account_slug(profile)
    dest = root / profile.profile_id
    outputs, yearly_frames = {}, []
    yearly_paths = sorted(source_root.glob(f'????/{slug}_????_normalized_transactions.csv'))
    for path in yearly_paths:
        year = path.parent.name
        if not re.fullmatch(r'\d{4}', year) or path.name != f'{slug}_{year}_normalized_transactions.csv':
            continue
        normalized = _read(path)
        frame = standardize(normalized, profile, path.relative_to(source_root).as_posix())
        if not frame.empty and not frame['transaction_date'].str.startswith(year+'-').all():
            raise AnalysisError(f'Transaction year does not match {path.name}; collect the account CSVs again.')
        outputs[dest/'yearly'/f'{slug}_{year}_analysis.csv'] = frame
        yearly_frames.append(frame)
    if not yearly_frames:
        raise AnalysisError(f'No normalized yearly files for {profile.display_name}. Run Collect Account CSVs first.')
    monthly_count = 0
    for path in _normalized_statement_paths(source_root):
        relative = path.relative_to(source_root)
        frame = standardize(_read(path), profile, relative.as_posix())
        outputs[dest/'monthly'/relative] = frame
        monthly_count += 1
    combined = _sorted(yearly_frames)
    all_path = dest/f'{slug}_all_years_analysis.csv'
    outputs[all_path] = combined
    return outputs, combined, all_path, len(yearly_frames), monthly_count


def build_analysis(profiles: Iterable[ExtractionProfile], *, overall: bool = False,
                   output_root: Path | None = None) -> AnalysisResult:
    """Rebuild selected summaries; do not append, guess duplicates, or touch inputs."""
    chosen = {}
    for profile in profiles:
        previous = chosen.get(profile.profile_id)
        if previous is not None and previous != profile:
            raise AnalysisError('Conflicting definitions for one profile.')
        chosen[profile.profile_id] = profile
    if not chosen or (not overall and len(chosen) != 1):
        raise AnalysisError('Choose at least one profile (one for a profile summary).')
    root = Path(output_root) if output_root is not None else ANALYSIS_ROOT
    # Never allow summaries to be placed in an extraction input/output tree.
    for profile in chosen.values():
        for source in (profile.resolve_output_folder(), profile.resolve_input_folder()):
            a, b = root.resolve(), source.resolve()
            if a == b or a in b.parents or b in a.parents:
                raise AnalysisError('Analysis output must be separate from extraction folders.')
    outputs, frames = {}, []
    manifests, stale = {}, set()
    year_count = month_count = 0
    for profile in chosen.values():
        items, combined, all_path, years, months = _profile_outputs(profile, root)
        # Track only files created by this feature, so deleted source years
        # cannot leave misleading analysis copies behind on the next build.
        dest = root / profile.profile_id
        manifest = dest / '.analysis_manifest.json'
        current = {p.relative_to(dest).as_posix() for p in items}
        if manifest.exists():
            try:
                previous = json.loads(manifest.read_text(encoding='utf-8'))
                if not isinstance(previous, list) or not all(isinstance(v, str) for v in previous):
                    raise ValueError
                for relative in previous:
                    candidate = dest / relative
                    if (candidate.suffix != '.csv' or
                            not candidate.resolve().is_relative_to(dest.resolve())):
                        raise ValueError
                    if relative not in current:
                        stale.add(candidate)
            except (OSError, ValueError) as exc:
                raise AnalysisError('Invalid analysis file manifest; no outputs were replaced.') from exc
        manifests[manifest] = sorted(current)
        outputs.update(items)
        frames.append(combined)
        year_count += years
        month_count += months
    combined = _sorted(frames)
    final_path = root/'overall_summary.csv' if overall else all_path
    outputs[final_path] = combined
    # Selection is explicit. This companion records exactly which profiles were used.
    if overall:
        outputs[root/'overall_profiles.csv'] = pd.DataFrame([
            {'profile_id': p.profile_id, 'profile_name': p.display_name}
            for p in chosen.values()
        ])
    staged = {}
    try:
        for target, frame in outputs.items():
            staged[target] = _stage_yearly_csv(frame, target)
        for target, names in manifests.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', encoding='utf-8', delete=False,
                                    dir=target.parent, suffix='.tmp') as stream:
                staged[target] = Path(stream.name)
                json.dump(names, stream, indent=2)
        _commit_yearly_outputs(staged, stale)
    except Exception:
        for path in staged.values():
            path.unlink(missing_ok=True)
        raise
    incoming = sum((Decimal(v) for v in combined['money_in']), Decimal(0))
    outgoing = sum((Decimal(v) for v in combined['money_out']), Decimal(0))
    return AnalysisResult(final_path, len(combined), year_count, month_count,
                          len(chosen), format(incoming,'.2f'), format(outgoing,'.2f'),
                          format(incoming-outgoing,'.2f'))


def saved_selection() -> set[str]:
    path = ANALYSIS_ROOT/'overall_profiles.csv'
    if not path.exists():
        return set()
    try:
        return set(pd.read_csv(path, dtype=str, keep_default_na=False)['profile_id'])
    except (OSError, ValueError, KeyError):
        return set()
