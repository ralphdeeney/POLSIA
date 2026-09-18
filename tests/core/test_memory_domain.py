"""Memory domain tests (pure entities — no DB, no services)."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from polsia.core.memory.entities import MemoryRecord
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.ids import CompanyId

COMPANY = CompanyId.generate()


def make_record(**overrides: object) -> MemoryRecord:
    kwargs: dict = {
        "company_id": COMPANY,
        "scope": "company",
        "type": "observation",
        "content": "Customer asked about invoicing.",
    }
    kwargs.update(overrides)
    return MemoryRecord.create(**kwargs)


class TestMemoryRecordCreate:
    def test_defaults(self) -> None:
        record = make_record()
        assert record.id.startswith("memr_")
        assert record.scope == "company"
        assert record.type == "observation"
        assert record.importance == 0.5
        assert record.metadata == {}
        assert record.created_at.tzinfo is not None
        assert record.updated_at.tzinfo is not None

    def test_content_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            make_record(content="")
        with pytest.raises(ValidationError):
            make_record(content="   ")

    def test_scope_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            make_record(scope="")
        with pytest.raises(ValidationError):
            make_record(scope="  ")

    def test_type_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            make_record(type="")
        with pytest.raises(ValidationError):
            make_record(type=" ")

    def test_importance_bounds(self) -> None:
        with pytest.raises(ValidationError):
            make_record(importance=-0.1)
        with pytest.raises(ValidationError):
            make_record(importance=1.1)
        assert make_record(importance=0.0).importance == 0.0
        assert make_record(importance=1.0).importance == 1.0

    def test_metadata_copied_from_input(self) -> None:
        meta = {"k": "v"}
        record = make_record(metadata=meta)
        meta["k2"] = "v2"
        assert record.metadata == {"k": "v"}

    def test_free_form_scope_values_allowed(self) -> None:
        for scope in ("company", "agent", "goal", "task", "anything-goes"):
            assert make_record(scope=scope).scope == scope


class TestMemoryRecordImmutability:
    def test_frozen_dataclass(self) -> None:
        record = make_record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.content = "changed"  # type: ignore[misc]

    def test_update_returns_copy_and_keeps_original(self) -> None:
        record = make_record()
        updated = record.update(content="changed", importance=0.9)
        assert updated is not record
        assert updated.content == "changed"
        assert updated.importance == 0.9
        assert record.content == "Customer asked about invoicing."
        assert record.importance == 0.5
        # Identity/scoping fields are preserved on update.
        assert updated.id == record.id
        assert updated.company_id == record.company_id
        assert updated.scope == record.scope
        assert updated.type == record.type

    def test_update_bumps_updated_at_only(self) -> None:
        ts = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        record = make_record(now=ts)
        updated = record.update(content="changed")
        assert updated.created_at == ts
        assert updated.updated_at > ts

    def test_update_validates_content(self) -> None:
        record = make_record()
        with pytest.raises(ValidationError):
            record.update(content="  ")

    def test_update_validates_importance(self) -> None:
        record = make_record()
        with pytest.raises(ValidationError):
            record.update(importance=2.0)

    def test_update_metadata_replaces(self) -> None:
        record = make_record(metadata={"a": 1})
        updated = record.update(metadata={"b": 2})
        assert updated.metadata == {"b": 2}
        assert record.metadata == {"a": 1}

    def test_update_with_no_changes_keeps_values(self) -> None:
        record = make_record(metadata={"a": 1}, importance=0.7)
        updated = record.update()
        assert updated.content == record.content
        assert updated.metadata == record.metadata
        assert updated.importance == record.importance
