from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from tutor.content import load_track
from tutor.services.ports import UowFactory

pytestmark = pytest.mark.integration


def test_seed_loaded_24_items_and_120_chunks(engine: Engine) -> None:
    with engine.connect() as conn:
        items = conn.execute(text("SELECT count(*) FROM track_items WHERE domain = 'it'"))
        chunks = conn.execute(
            text(
                "SELECT count(*) FROM track_chunks c JOIN track_items i ON i.id = c.track_item_id"
                " WHERE i.domain = 'it'"
            )
        )
        assert (items.scalar_one(), chunks.scalar_one()) == (24, 120)


def test_track_repo_returns_the_seeded_track_in_order(
    uow_factory: UowFactory, user_id: UUID
) -> None:
    it_items = [i for i in load_track() if i.domain == "it"]
    expected = tuple(sorted(it_items, key=lambda i: i.order_no))
    with uow_factory(user_id) as uow:
        assert uow.track.items("it") == expected


def test_tutor_app_cannot_write_the_track(uow_factory: UowFactory, user_id: UUID) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):  # noqa: SIM117
        with uow_factory(user_id) as uow:
            uow.conn.execute(text("INSERT INTO track_items (id) VALUES ('x')"))  # type: ignore[attr-defined]
