"""Versioned release notes, kept with the application rather than in business data."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
VERSION_PATTERN = re.compile(r'^\d+\.\d+\.\d+$')


def load_release_notes(path=None):
    records = json.loads((Path(path) if path else ROOT / 'release_notes.json').read_text(encoding='utf-8-sig'))
    versions = set()
    for record in records:
        version = record['version']
        if not VERSION_PATTERN.fullmatch(version) or version in versions:
            raise ValueError('Invalid or duplicate release version: ' + version)
        if not record.get('reason', '').strip() or not record.get('changes'):
            raise ValueError('Release reason and changes are required: ' + version)
        versions.add(version)
    return sorted(records, key=lambda row: tuple(map(int, row['version'].split('.'))), reverse=True)


def release_notes_context(records, query='', page=1, per_page=25):
    query = query.strip()
    if query:
        needle = query.casefold()
        records = [row for row in records if needle in ' '.join(
            [row['version'], row['date'], row['reason'], *row['changes']]).casefold()]
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1
    pages = max(1, (len(records) + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    return dict(releases=records[(page - 1) * per_page:page * per_page], q=query,
                page=page, pages=pages, total=len(records))
