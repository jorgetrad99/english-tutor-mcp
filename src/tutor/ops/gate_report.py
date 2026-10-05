"""Validation-gate numbers (core loop v0 spec section 14), read only.

Run on the server with the owner's URL, never the app's:
    MIGRATION_DATABASE_URL=... python -m tutor.ops.gate_report --from 2026-11-16 [--to 2026-12-04]
        [--labels labels.json] [--author NAME] [--tz America/Mexico_City] [--database-url URL]
The URL comes from --database-url, else MIGRATION_DATABASE_URL. DATABASE_URL (the app's
NOSUPERUSER, NOBYPASSRLS login) is never used: every user table has FORCE ROW LEVEL SECURITY with
policies keyed on app.user_id, so that role, and equally a plain table owner, would read zero rows.
Only a SUPERUSER or a BYPASSRLS role sees all users (SET LOCAL row_security = off does not help a
role that is subject to RLS: it makes the query fail instead). The report checks the connected
role for one of the two and stops with a message otherwise.
Every transaction is READ ONLY. `labels.json` maps user hashes (first 12 hex of SHA-256 of the user
id, as in the logs) to names. The output holds counts, rates, those hashes and dates only: no
email, display name, Google sub or learner text is selected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.pool import NullPool

from tutor.db.engine import psycopg_url
from tutor.db.tables import audit_log, session_metrics, sessions
from tutor.mcp.observe import user_hash

DEFAULT_TZ = "America/Mexico_City"
OWNER_URL_KEY = "MIGRATION_DATABASE_URL"
_HASH = re.compile(r"^[0-9a-f]{12}$")
_LABEL = re.compile(r"^[^|\x00-\x1f\x7f]{1,40}$")
NOTE = "evidence_fidelity comes from `just eval-fidelity`, not from this report."
# Plan ruling 9: archived counts as confirmed (never produced in v0).
_CONFIRMED = frozenset({"confirmed", "archived"})
_PROPOSED = _CONFIRMED | {"provisional", "declined"}


@dataclass(frozen=True, slots=True)
class Ratio:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None


@dataclass(frozen=True, slots=True)
class UserLine:
    label: str
    closed: int
    low_trust: int
    incomplete: int
    voice: int


@dataclass(frozen=True, slots=True)
class GateReport:
    start: date
    end: date
    tz: str
    users: tuple[UserLine, ...]
    author: str | None
    author_closed: int | None
    median_wpm_voice: float | None
    median_wpm_text: float | None
    confirmation: Ratio
    activation: Ratio
    validity: Ratio
    rejected_errors: Ratio

    @property
    def closed(self) -> int:
        return sum(u.closed for u in self.users)

    @property
    def voice(self) -> int:
        return sum(u.voice for u in self.users)


def read_only_engine(database_url: str) -> Engine:
    """Every transaction on this engine is READ ONLY; timestamps come back in UTC."""
    return create_engine(
        psycopg_url(database_url),
        poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC -c default_transaction_read_only=on"},
    )


def require_unrestricted_role(conn: Connection) -> None:
    """Stop unless the connected role is a superuser or has BYPASSRLS (neither is inherited
    through membership, so only the login role's own attributes count). Any other role, the
    table owner included, is filtered by FORCE ROW LEVEL SECURITY and would report all zeros."""
    row = conn.execute(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = session_user")
    ).scalar_one()
    if not row:
        raise SystemExit(
            f"the {OWNER_URL_KEY} role must be a SUPERUSER or have BYPASSRLS: under FORCE ROW "
            "LEVEL SECURITY any other role, even the table owner, sees no users' rows"
        )


def window(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start 00:00, end + 1 day 00:00) in the given zone, as UTC instants."""
    low = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    high = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return low, high


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _glossary_final_statuses(conn: Connection, low: datetime, high: datetime) -> dict[str, str]:
    """Final status of each item first saved in [low, high) (spec 10.4), replaying the
    glossary_saved audit rows (ids and enums only) in time order. Audit rows outlive the
    provisional purge (spec 9.4); confirmed is sticky, as in the domain rules."""
    rows = conn.execute(
        select(audit_log.c.at, audit_log.c.meta)
        .where(audit_log.c.event == "glossary_saved", audit_log.c.at < high)
        .order_by(audit_log.c.at)
    ).all()
    first_at: dict[str, datetime] = {}
    final: dict[str, str] = {}
    for at, meta in rows:
        for item in meta.get("items", ()):
            item_id, status = str(item["id"]), str(item["status"])
            first_at.setdefault(item_id, at)
            if final.get(item_id) not in _CONFIRMED:
                final[item_id] = status
    return {item_id: s for item_id, s in final.items() if first_at[item_id] >= low}


def build_report(
    conn: Connection,
    start: date,
    end: date,
    tz_name: str,
    labels: Mapping[str, str],
    author: str | None,
) -> GateReport:
    low, high = window(start, end, ZoneInfo(tz_name))
    rows = conn.execute(
        select(
            sessions.c.user_id,
            sessions.c.status,
            sessions.c.low_trust,
            sessions.c.mode,
            session_metrics.c.user_words_per_min,
            session_metrics.c.chunks_offered,
            session_metrics.c.chunks_used,
            session_metrics.c.errors_total,
            session_metrics.c.errors_rejected,
        )
        .select_from(
            sessions.outerjoin(session_metrics, session_metrics.c.session_id == sessions.c.id)
        )
        .where(sessions.c.started_at >= low, sessions.c.started_at < high)
    ).all()
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    wpm: dict[str, list[float]] = {"voice": [], "text": []}
    offered = used = reported = rejected = 0
    for r in rows:
        line = counts[labels.get(user_hash(r.user_id), user_hash(r.user_id))]
        if r.errors_total is not None:
            reported += r.errors_total + r.errors_rejected
            rejected += r.errors_rejected
        if r.status == "incomplete":
            line[2] += 1
            continue
        if r.status != "closed":
            continue
        line[0] += 1
        line[1] += int(r.low_trust)
        line[3] += int(r.mode == "voice")
        if r.user_words_per_min is not None:
            wpm[r.mode].append(float(r.user_words_per_min))
            offered += r.chunks_offered
            used += r.chunks_used
    final = _glossary_final_statuses(conn, low, high)
    confirmed = sum(1 for s in final.values() if s in _CONFIRMED)
    proposed = sum(1 for s in final.values() if s in _PROPOSED)
    users = tuple(UserLine(label, *values) for label, values in sorted(counts.items()))
    closed = sum(u.closed for u in users)
    incomplete = sum(u.incomplete for u in users)
    author_closed = None
    if author is not None:
        author_closed = sum(u.closed for u in users if u.label == author)
    return GateReport(
        start=start,
        end=end,
        tz=tz_name,
        users=users,
        author=author,
        author_closed=author_closed,
        median_wpm_voice=_median(wpm["voice"]),
        median_wpm_text=_median(wpm["text"]),
        confirmation=Ratio(confirmed, proposed),
        activation=Ratio(used, offered),
        validity=Ratio(closed, closed + incomplete),
        rejected_errors=Ratio(rejected, reported),
    )


def _pct(ratio: Ratio, unit: str = "") -> str:
    value = ratio.value
    if value is None:
        return "n/a"
    return f"{value:.0%} ({ratio.hits} of {ratio.total}{unit})"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def render(report: GateReport) -> str:
    """Markdown for docs/v0/acceptance.md and the gate decision (ASCII only)."""
    lines = [
        f"# Gate report, {report.start.isoformat()} to {report.end.isoformat()} ({report.tz})",
        "",
        "## Sessions per user",
        "",
        "| User | Closed | Low trust | Incomplete | Voice |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for u in report.users:
        lines.append(f"| {u.label} | {u.closed} | {u.low_trust} | {u.incomplete} | {u.voice} |")
    total = (
        report.closed,
        sum(u.low_trust for u in report.users),
        sum(u.incomplete for u in report.users),
        report.voice,
    )
    lines.append("| All | {} | {} | {} | {} |".format(*total))
    author_label = (
        f"Author sessions ({report.author})" if report.author else "Author sessions (pass --author)"
    )
    author_value = "n/a" if report.author_closed is None else str(report.author_closed)
    lines += [
        "",
        "## Gate numbers",
        "",
        "| Metric | Value | Gate |",
        "| --- | --- | --- |",
        f"| Real sessions (closed, low trust included) | {report.closed} | >= 15 |",
        f"| Voice sessions (closed) | {report.voice} | >= 5 |",
        f"| {author_label} | {author_value} | >= 10 in 3 weeks |",
        f"| Median user_words_per_min, voice | {_number(report.median_wpm_voice)} | >= 20 |",
        f"| Median user_words_per_min, text | {_number(report.median_wpm_text)} | - |",
        f"| Glossary confirmation rate | {_pct(report.confirmation)} | >= 60% |",
        f"| Activation rate | {_pct(report.activation, ' phrases')} | - |",
        f"| end_session validity (closed vs incomplete) | {_pct(report.validity)} | - |",
        f"| Rejected-error share | {_pct(report.rejected_errors)} | - |",
        "",
        NOTE,
    ]
    return "\n".join(lines)


def load_labels(path: Path) -> dict[str, str]:
    """A JSON object of user hash -> name. Anything else stops the report."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read labels file {path}: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise SystemExit("labels file must be a JSON object of user hash -> name")
    for key, value in data.items():
        if not _HASH.match(str(key)) or not isinstance(value, str):
            raise SystemExit("labels keys must be 12 hex user hashes and values must be names")
        if not _LABEL.match(value):
            raise SystemExit("label names must be 1 to 40 characters without | or control codes")
    return {str(k): v for k, v in data.items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate_report", description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--author")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--database-url", dest="database_url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    url = args.database_url or os.environ.get(OWNER_URL_KEY, "")
    if not url:
        print(
            f"Set {OWNER_URL_KEY} (the owner's URL, not the app's DATABASE_URL) "
            "or pass --database-url",
            file=sys.stderr,
        )
        return 2
    labels = load_labels(args.labels) if args.labels else {}
    end = args.end or datetime.now(ZoneInfo(args.tz)).date()
    engine = read_only_engine(url)
    try:
        with engine.connect() as conn:
            require_unrestricted_role(conn)
            report = build_report(conn, args.start, end, args.tz, labels, args.author)
    finally:
        engine.dispose()
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
