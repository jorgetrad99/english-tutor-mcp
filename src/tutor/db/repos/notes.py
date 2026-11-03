"""Sources, notes, passages, concepts, links and the search statement on Postgres (subjects
spec 4, 5.1, 8.1). Templates and instances live in tutor.db.repos.templates.

Every statement filters by the unit of work's user on top of row-level security, and every
write that references another row checks ownership first (foreign keys ignore RLS). Learner
text travels as bound parameters only.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, exists, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping

from tutor.db.repos._guards import require_note, require_subject
from tutor.db.tables import (
    concept_links,
    concepts,
    note_concepts,
    note_passages,
    notes,
    sources,
)
from tutor.domain.notes import (
    CLEAN_VERSION,
    ConceptName,
    Origin,
    ValidNote,
    ValidSource,
    idempotency_key,
    name_norm,
)
from tutor.services.ports import (
    ConceptRow,
    NoteListRow,
    NoteRow,
    PassageRow,
    SearchHit,
    SourceRow,
)

# Search (spec 5.1). Fixed numbers: the in-memory model in tutor.services.memory uses the same.
TRIGRAM_THRESHOLD = 0.3
MAX_SEEDS = 5
MAX_DEPTH = 2
FUZZY_MAX_TOKENS = 3
MAX_EXCERPTS = 2
_WORD = re.compile(r"\w+")

# Spanish and English stems of the query, OR-ed, against the passage tsvector that holds both.
_TSQ = "(websearch_to_tsquery('tutor_es', :q) || websearch_to_tsquery('tutor_en', :q))"
_HEADLINE = "MaxFragments=2, MaxWords=25, MinWords=8, StartSel=[[, StopSel=]]"

# One statement, bound parameters only (:uid, :sid, :q, :q_norm, :fuzzy_ok, :k):
#   scope          the user's notes, one subject's when :sid is given
#   passage_hits   passages whose tsv matches the query, counted per note
#   seeds          up to 5 concepts named like the normalised query (exact first, then by
#                  trigram word similarity >= 0.3)
#   walk/hops      the concept graph from the seeds, `related` both ways and `prerequisite`
#                  outgoing, at most 2 hops, each concept at its first depth, remembering
#                  whether an exactly named seed reaches it
#   concept_hits   notes attached to a reached concept, with the count, min depth and names
#   united         both hit sets ordered by passages + concepts desc, depth asc (none last),
#                  updated_at desc, id; `fuzzy` when only similarity reached the note
#   fuzzy          when nothing matched and the query has <= 3 tokens: titles within trigram
#                  word similarity of the normalised query (`fuzzy` as well)
#   excerpts       ts_headline of the first two matched passages of each returned note, the
#                  Spanish headline when it marks a match and the English one otherwise
SEARCH_SQL = f"""
WITH RECURSIVE scope AS (
    SELECT n.id, n.updated_at, n.title_norm
    FROM notes n
    WHERE n.user_id = :uid
      AND (CAST(:sid AS uuid) IS NULL OR n.subject_id = CAST(:sid AS uuid))
), passage_hits AS (
    SELECT p.note_id, count(*)::int AS passages
    FROM note_passages p
    JOIN scope s ON s.id = p.note_id
    WHERE p.user_id = :uid AND p.tsv @@ {_TSQ}
    GROUP BY p.note_id
), seeds AS (
    SELECT c.id, (c.name_norm = :q_norm) AS exact
    FROM concepts c
    WHERE c.user_id = :uid
      AND (CAST(:sid AS uuid) IS NULL OR c.subject_id = CAST(:sid AS uuid))
      AND (c.name_norm = :q_norm
           OR word_similarity(:q_norm, c.name_norm) >= {TRIGRAM_THRESHOLD})
    ORDER BY (c.name_norm = :q_norm) DESC, word_similarity(:q_norm, c.name_norm) DESC, c.id
    LIMIT {MAX_SEEDS}
), walk (id, depth, exact) AS (
    SELECT id, 0, exact FROM seeds
    UNION
    SELECT CASE WHEN l.from_id = w.id THEN l.to_id ELSE l.from_id END, w.depth + 1, w.exact
    FROM walk w
    JOIN concept_links l
      ON l.user_id = :uid
     AND (l.from_id = w.id OR (l.kind = 'related' AND l.to_id = w.id))
    WHERE w.depth < {MAX_DEPTH}
), hops AS (
    SELECT id, min(depth) AS depth, bool_or(exact) AS exact FROM walk GROUP BY id
), concept_hits AS (
    SELECT nc.note_id,
           count(*)::int AS concepts,
           min(h.depth)::int AS depth,
           bool_or(h.exact) AS exact,
           array_agg(c.name ORDER BY h.depth, c.name_norm COLLATE "C", c.name COLLATE "C")
               AS names
    FROM note_concepts nc
    JOIN scope s ON s.id = nc.note_id
    JOIN hops h ON h.id = nc.concept_id
    JOIN concepts c ON c.id = nc.concept_id AND c.user_id = :uid
    WHERE nc.user_id = :uid
    GROUP BY nc.note_id
), united AS (
    SELECT s.id AS note_id,
           s.updated_at,
           coalesce(ph.passages, 0) AS passages,
           coalesce(ch.concepts, 0) AS concepts,
           ch.depth,
           coalesce(ch.names, '{{}}'::text[]) AS names,
           (ph.note_id IS NULL AND NOT coalesce(ch.exact, false)) AS fuzzy
    FROM scope s
    LEFT JOIN passage_hits ph ON ph.note_id = s.id
    LEFT JOIN concept_hits ch ON ch.note_id = s.id
    WHERE ph.note_id IS NOT NULL OR ch.note_id IS NOT NULL
), fuzzy AS (
    SELECT s.id AS note_id,
           s.updated_at,
           0 AS passages,
           0 AS concepts,
           NULL::int AS depth,
           '{{}}'::text[] AS names,
           true AS fuzzy
    FROM scope s
    WHERE CAST(:fuzzy_ok AS boolean)
      AND NOT EXISTS (SELECT 1 FROM united)
      AND word_similarity(:q_norm, s.title_norm) >= {TRIGRAM_THRESHOLD}
), ranked AS (
    SELECT * FROM (SELECT * FROM united UNION ALL SELECT * FROM fuzzy) u
    ORDER BY passages + concepts DESC, depth ASC NULLS LAST, updated_at DESC, note_id
    LIMIT :k
)
SELECT r.note_id, r.passages, r.concepts, r.depth, r.names, r.fuzzy,
       coalesce((
           SELECT array_agg(CASE WHEN position('[[' IN es.h) > 0 THEN es.h ELSE en.h END
                            ORDER BY m.seq)
           FROM (
               SELECT p.seq, p.text
               FROM note_passages p
               WHERE p.user_id = :uid AND p.note_id = r.note_id AND p.tsv @@ {_TSQ}
               ORDER BY p.seq
               LIMIT {MAX_EXCERPTS}
           ) m
           CROSS JOIN LATERAL (
               SELECT ts_headline('tutor_es', m.text, {_TSQ}, '{_HEADLINE}') AS h
           ) es
           CROSS JOIN LATERAL (
               SELECT ts_headline('tutor_en', m.text, {_TSQ}, '{_HEADLINE}') AS h
           ) en
       ), '{{}}'::text[]) AS excerpts
FROM ranked r
ORDER BY r.passages + r.concepts DESC, r.depth ASC NULLS LAST, r.updated_at DESC, r.note_id
"""  # noqa: S608 - only fixed fragments and numbers are interpolated; text is bound
_SEARCH = text(SEARCH_SQL)


def search_params(
    user_id: UUID, query: str, subject_id: UUID | None, limit: int
) -> dict[str, Any] | None:
    """The bound parameters of SEARCH_SQL, or None when the query has no word tokens or the
    limit is below 1 (the in-memory model returns nothing for those too)."""
    tokens = _WORD.findall(name_norm(query))
    if not tokens or limit < 1:
        return None
    return {
        "uid": user_id,
        "sid": subject_id,
        "q": query,
        "q_norm": name_norm(query),
        "fuzzy_ok": len(tokens) <= FUZZY_MAX_TOKENS,
        "k": limit,
    }


def _source_row(m: RowMapping) -> SourceRow:
    return SourceRow(
        id=m["id"],
        subject_id=m["subject_id"],
        kind=m["kind"],
        title=m["title"],
        url=m["url"],
        created_at=m["created_at"],
    )


def _note_row(m: RowMapping) -> NoteRow:
    return NoteRow(
        id=m["id"],
        subject_id=m["subject_id"],
        source_id=m["source_id"],
        title=m["title"],
        lesson_ref=m["lesson_ref"],
        position=m["position"],
        raw_text=m["raw_text"],
        raw_had_nul=m["raw_had_nul"],
        clean_text=m["clean_text"],
        clean_version=m["clean_version"],
        idempotency_key=m["idempotency_key"],
        origin=m["origin"],
        supersedes_note_id=m["supersedes_note_id"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def upsert_concept(conn: Connection, user_id: UUID, subject_id: UUID, concept: ConceptName) -> UUID:
    """The subject's concept with this name_norm, created with this spelling if new."""
    inserted = conn.execute(
        pg_insert(concepts)
        .values(
            id=uuid4(),
            user_id=user_id,
            subject_id=subject_id,
            name=concept.name,
            name_norm=concept.name_norm,
        )
        .on_conflict_do_nothing(constraint="concepts_user_subject_name_norm_key")
        .returning(concepts.c.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    return conn.execute(
        select(concepts.c.id).where(
            concepts.c.user_id == user_id,
            concepts.c.subject_id == subject_id,
            concepts.c.name_norm == concept.name_norm,
        )
    ).scalar_one()


class PgNoteRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    # --- sources -----------------------------------------------------------------------------

    def upsert_source(self, subject_id: UUID, source: ValidSource, now: datetime) -> SourceRow:
        require_subject(self._conn, self._user_id, subject_id)
        stmt = pg_insert(sources).values(
            id=uuid4(),
            user_id=self._user_id,
            subject_id=subject_id,
            kind=source.kind,
            title=source.title,
            url=source.url,
            created_at=now,
        )
        m = (
            self._conn.execute(
                stmt.on_conflict_do_update(
                    constraint="sources_user_subject_kind_title_key",
                    # An existing row keeps its url unless the call brings one.
                    set_={"url": func.coalesce(stmt.excluded.url, sources.c.url)},
                ).returning(sources)
            )
            .mappings()
            .one()
        )
        return _source_row(m)

    def source(self, source_id: UUID) -> SourceRow | None:
        m = (
            self._conn.execute(
                select(sources).where(sources.c.id == source_id, sources.c.user_id == self._user_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _source_row(m)

    # --- notes -------------------------------------------------------------------------------

    def by_idempotency_key(self, key: str) -> NoteRow | None:
        m = (
            self._conn.execute(
                select(notes).where(
                    notes.c.user_id == self._user_id, notes.c.idempotency_key == key
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _note_row(m)

    def latest_by_lesson(
        self, subject_id: UUID, source_id: UUID | None, lesson_ref: str
    ) -> NoteRow | None:
        same_source = (
            notes.c.source_id.is_(None) if source_id is None else notes.c.source_id == source_id
        )
        m = (
            self._conn.execute(
                select(notes)
                .where(
                    notes.c.user_id == self._user_id,
                    notes.c.subject_id == subject_id,
                    same_source,
                    notes.c.lesson_ref == lesson_ref,
                )
                .order_by(notes.c.created_at.desc(), notes.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _note_row(m)

    def insert(
        self,
        note: ValidNote,
        *,
        subject_id: UUID,
        source_id: UUID | None,
        origin: Origin,
        supersedes: UUID | None,
        now: datetime,
    ) -> NoteRow:
        require_subject(self._conn, self._user_id, subject_id)
        if source_id is not None:
            source = self.source(source_id)
            if source is None or source.subject_id != subject_id:
                raise LookupError("source not found")
        if supersedes is not None:
            require_note(self._conn, self._user_id, supersedes)
        key = idempotency_key(subject_id, source_id, note.lesson_ref, note.raw_text)
        m = (
            self._conn.execute(
                pg_insert(notes)
                .values(
                    id=uuid4(),
                    user_id=self._user_id,
                    subject_id=subject_id,
                    source_id=source_id,
                    title=note.title,
                    title_norm=name_norm(note.title),
                    lesson_ref=note.lesson_ref,
                    position=note.position,
                    raw_text=note.raw_text,
                    raw_sha256=note.raw_sha256,
                    raw_had_nul=note.raw_had_nul,
                    clean_text=note.clean_text,
                    clean_version=CLEAN_VERSION,
                    idempotency_key=key,
                    origin=origin,
                    supersedes_note_id=supersedes,
                    created_at=now,
                    updated_at=now,
                )
                # A concurrent identical call wins the unique key: no violation, no row.
                .on_conflict_do_nothing(constraint="notes_user_idempotency_key_key")
                .returning(notes)
            )
            .mappings()
            .one_or_none()
        )
        if m is None:
            raise ValueError("duplicate idempotency key for this user")
        row = _note_row(m)
        self._write_children(row.id, subject_id, note)
        return row

    def _write_children(self, note_id: UUID, subject_id: UUID, note: ValidNote) -> None:
        if note.passages:
            self._conn.execute(
                insert(note_passages),
                [
                    {"note_id": note_id, "seq": seq, "user_id": self._user_id, "text": passage}
                    for seq, passage in enumerate(note.passages, start=1)
                ],
            )
        concept_ids: dict[str, UUID] = {}

        def concept_id(concept: ConceptName) -> UUID:
            if concept.name_norm not in concept_ids:
                concept_ids[concept.name_norm] = self._concept_id(subject_id, concept)
            return concept_ids[concept.name_norm]

        for concept in note.concepts:
            self._conn.execute(
                pg_insert(note_concepts)
                .values(note_id=note_id, concept_id=concept_id(concept), user_id=self._user_id)
                .on_conflict_do_nothing()
            )
        for link in note.links:
            self._conn.execute(
                pg_insert(concept_links)
                .values(
                    from_id=concept_id(link.from_name),
                    to_id=concept_id(link.to_name),
                    kind=link.kind,
                    user_id=self._user_id,
                )
                .on_conflict_do_nothing()
            )

    def _concept_id(self, subject_id: UUID, concept: ConceptName) -> UUID:
        return upsert_concept(self._conn, self._user_id, subject_id, concept)

    def get(self, note_id: UUID) -> NoteRow | None:
        m = (
            self._conn.execute(
                select(notes).where(notes.c.id == note_id, notes.c.user_id == self._user_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _note_row(m)

    def list_for_subject(self, subject_id: UUID) -> tuple[NoteListRow, ...]:
        newer = notes.alias("newer")
        superseded = exists().where(
            newer.c.user_id == self._user_id, newer.c.supersedes_note_id == notes.c.id
        )
        rows = self._conn.execute(
            select(
                notes.c.id,
                notes.c.source_id,
                sources.c.title.label("source_title"),
                sources.c.kind.label("source_kind"),
                notes.c.title,
                notes.c.lesson_ref,
                notes.c.position,
                notes.c.origin,
                notes.c.created_at,
                notes.c.supersedes_note_id,
                superseded.label("superseded"),
            )
            .select_from(
                notes.outerjoin(
                    sources,
                    (sources.c.id == notes.c.source_id) & (sources.c.user_id == self._user_id),
                )
            )
            .where(notes.c.user_id == self._user_id, notes.c.subject_id == subject_id)
            .order_by(
                sources.c.title.collate("C").asc().nulls_last(),
                notes.c.position.asc().nulls_last(),
                notes.c.created_at,
                notes.c.id,
            )
        ).mappings()
        return tuple(
            NoteListRow(
                id=m["id"],
                source_id=m["source_id"],
                source_title=m["source_title"],
                source_kind=m["source_kind"],
                title=m["title"],
                lesson_ref=m["lesson_ref"],
                position=m["position"],
                origin=m["origin"],
                created_at=m["created_at"],
                supersedes_note_id=m["supersedes_note_id"],
                superseded=bool(m["superseded"]),
            )
            for m in rows
        )

    # --- passages and concepts ---------------------------------------------------------------

    def passages(self, note_id: UUID, start: int, stop: int) -> tuple[PassageRow, ...]:
        rows = self._conn.execute(
            select(note_passages.c.seq, note_passages.c.text)
            .where(
                note_passages.c.note_id == note_id,
                note_passages.c.user_id == self._user_id,
                note_passages.c.seq.between(start, stop),
            )
            .order_by(note_passages.c.seq)
        ).all()
        return tuple(PassageRow(seq=r.seq, text=r.text) for r in rows)

    def passage_count(self, note_id: UUID) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(note_passages)
            .where(note_passages.c.note_id == note_id, note_passages.c.user_id == self._user_id)
        ).scalar_one()
        return int(count)

    def concept_by_norm(self, subject_id: UUID, name_norm: str) -> ConceptRow | None:
        m = (
            self._conn.execute(
                select(concepts).where(
                    concepts.c.user_id == self._user_id,
                    concepts.c.subject_id == subject_id,
                    concepts.c.name_norm == name_norm,
                )
            )
            .mappings()
            .one_or_none()
        )
        if m is None:
            return None
        return ConceptRow(
            id=m["id"], subject_id=m["subject_id"], name=m["name"], name_norm=m["name_norm"]
        )

    def concepts(self, note_id: UUID) -> tuple[ConceptRow, ...]:
        rows = self._conn.execute(
            select(concepts)
            .join(note_concepts, note_concepts.c.concept_id == concepts.c.id)
            .where(
                note_concepts.c.note_id == note_id,
                note_concepts.c.user_id == self._user_id,
                concepts.c.user_id == self._user_id,
            )
            .order_by(concepts.c.name_norm.collate("C"), concepts.c.id)
        ).mappings()
        return tuple(
            ConceptRow(
                id=m["id"], subject_id=m["subject_id"], name=m["name"], name_norm=m["name_norm"]
            )
            for m in rows
        )

    # --- search ------------------------------------------------------------------------------

    def search(self, query: str, subject_id: UUID | None, limit: int) -> tuple[SearchHit, ...]:
        params = search_params(self._user_id, query, subject_id, limit)
        if params is None:
            return ()
        rows = self._conn.execute(_SEARCH, params).mappings()
        return tuple(
            SearchHit(
                note_id=m["note_id"],
                passages=m["passages"],
                concepts=tuple(m["names"]),
                depth=m["depth"],
                fuzzy=m["fuzzy"],
                excerpts=tuple(m["excerpts"]),
            )
            for m in rows
        )
