"""Durable event-trigger claims, replay suppression, and safety guards."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from typing import Any

import pytest
from polsia.adapters.persistence.event_store import SqlAlchemyEventStore
from polsia.adapters.persistence.repositories import (
    SqlAlchemyCompanyRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyUserRepository,
)
from polsia.adapters.persistence.trigger_models import TriggerDeliveryModel
from polsia.adapters.persistence.trigger_repositories import (
    SqlAlchemyEventTriggerPolicyRepository,
    SqlAlchemyTriggerDeliveryRepository,
)
from polsia.core.ceo.decisions import Decision, DecisionType
from polsia.core.ceo.triggers import (
    EventTriggerPolicy,
    SafetyGuard,
    SafetyLimits,
    TriggerDispatchContext,
)
from polsia.core.shared.errors import ValidationError
from polsia.core.shared.events import EventEnvelope, event_metadata_scope
from polsia.core.shared.ids import CompanyId
from polsia.core.tasks.entities import Task
from polsia.core.tasks.values import TaskStatus
from polsia.core.tenancy.entities import Company, Organization, OrgMembership, User
from polsia.core.tenancy.values import CompanyStatus, MembershipRole, UserStatus
from polsia.infra.db import create_all_metadata
from polsia.services.event_triggers import EventTriggerDispatcher, trigger_cycle_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
async def trigger_env():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        await create_all_metadata(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        user = User.register(
            email="trigger-owner@example.com", hashed_password="x", display_name="Trigger owner"
        )
        organization = Organization.create(
            name="Trigger Org", slug="trigger-org", owner_user_id=user.id
        )
        company = Company.create(
            organization_id=organization.id, name="Trigger Co", slug="trigger-co"
        ).transition_status(CompanyStatus.ACTIVE)
        async with factory() as session:
            await SqlAlchemyUserRepository(session).add(user)
            await SqlAlchemyOrganizationRepository(session).add(organization)
            await SqlAlchemyCompanyRepository(session).add(company)
            await SqlAlchemyMembershipRepository(session).add(
                OrgMembership.create(
                    organization_id=organization.id,
                    user_id=user.id,
                    role=MembershipRole.OWNER,
                )
            )
            await session.commit()
        yield {"factory": factory, "user": user, "organization": organization, "company": company}
    finally:
        await engine.dispose()


def _make_event(
    env: dict[str, Any], *, payload: dict[str, Any] | None = None, **kwargs: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="task.ready",
        organization_id=env["organization"].id,
        company_id=env["company"].id,
        payload=payload
        if payload is not None
        else {"task_id": "untrusted-task-reference", "runtime": "attacker-controlled"},
        **kwargs,
    )


async def _store_policy_event(env: dict[str, Any], event: EventEnvelope) -> EventTriggerPolicy:
    policy = EventTriggerPolicy.create(
        organization_id=env["organization"].id,
        company_id=env["company"].id,
        event_type=event.type,
        enabled=True,
    )
    async with env["factory"]() as session:
        await SqlAlchemyEventTriggerPolicyRepository(session).add(policy)
        await SqlAlchemyEventStore(session).append(event)
        await session.commit()
    return policy


class TestTriggerDelivery:
    async def test_duplicate_delivery_and_same_envelope_replay_are_idempotent(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        policy = await _store_policy_event(env, event)
        calls: list[tuple[str, str]] = []

        async def run_cycle(
            actor: User, matched_policy: EventTriggerPolicy, envelope: EventEnvelope, cycle: str
        ) -> dict[str, Any]:
            assert actor.id == env["user"].id
            assert matched_policy.id == policy.id
            assert envelope.payload["runtime"] == "attacker-controlled"
            calls.append((str(envelope.id), cycle))
            return {"status": "test_complete", "cycle_id": "untrusted-cycle-override"}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        assert await dispatcher.dispatch(event.id) == 1
        assert await dispatcher.dispatch(event.id) == 0
        assert await dispatcher.catch_up() == 0
        assert calls == [(str(event.id), trigger_cycle_id(policy.id, event.id))]

        async with env["factory"]() as session:
            deliveries = list((await session.scalars(select(TriggerDeliveryModel))).all())
        assert len(deliveries) == 1
        assert deliveries[0].status == "completed"
        assert deliveries[0].cycle_id == trigger_cycle_id(policy.id, event.id)
        assert deliveries[0].detail["cycle_id"] == deliveries[0].cycle_id

    async def test_atomic_unique_claim_grants_only_one_concurrent_lease(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        policy = await _store_policy_event(env, event)
        now = dt.datetime.now(dt.UTC)

        async def claim() -> bool:
            async with env["factory"]() as session:
                lease = await SqlAlchemyTriggerDeliveryRepository(session).claim(
                    policy,
                    event.id,
                    trigger_cycle_id(policy.id, event.id),
                    owner="concurrent",
                    lease_seconds=30,
                    max_attempts=3,
                    now=now,
                )
                if lease is not None:
                    await session.commit()
                return lease is not None

        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]

    async def test_recursive_lineage_is_recorded_and_suppressed(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        with event_metadata_scope(
            {
                "trigger_event_id": "evt_parent",
                "trigger_policy_id": "trp_parent",
                "trigger_cycle_id": "trg_parent",
            }
        ):
            event = _make_event(env)
        await _store_policy_event(env, event)
        invoked = False

        async def run_cycle(*_args: Any) -> dict[str, Any]:
            nonlocal invoked
            invoked = True
            return {}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        assert await dispatcher.dispatch(event.id) == 1
        assert not invoked
        async with env["factory"]() as session:
            delivery = await session.scalar(select(TriggerDeliveryModel))
        assert delivery is not None
        assert delivery.status == "recursion_suppressed"
        assert delivery.detail["trigger_event_id"] == "evt_parent"
        assert delivery.detail["trigger_policy_id"] == "trp_parent"
        assert delivery.detail["trigger_cycle_id"] == "trg_parent"

    async def test_untrusted_payload_does_not_choose_actor_or_cycle(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env, payload={"task_id": "tsk_forged", "actor_id": "usr_forged"})
        policy = await _store_policy_event(env, event)
        received: list[tuple[str, str]] = []

        async def run_cycle(
            actor: User, _policy: EventTriggerPolicy, _event: EventEnvelope, cycle: str
        ) -> dict[str, Any]:
            received.append((str(actor.id), cycle))
            return {}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        await dispatcher.dispatch(event.id)
        assert received == [(str(env["user"].id), trigger_cycle_id(policy.id, event.id))]

    async def test_catch_up_skips_live_leases_and_terminal_rows(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        clock = _Clock(dt.datetime.now(dt.UTC))
        live_event = _make_event(env)
        done_event = _make_event(env)
        policy = await _store_policy_event(env, live_event)
        async with env["factory"]() as session:
            await SqlAlchemyEventStore(session).append(done_event)
            deliveries = SqlAlchemyTriggerDeliveryRepository(session)
            await deliveries.claim(
                policy,
                live_event.id,
                trigger_cycle_id(policy.id, live_event.id),
                owner="live",
                lease_seconds=300,
                max_attempts=3,
                now=clock(),
            )
            done = await deliveries.claim(
                policy,
                done_event.id,
                trigger_cycle_id(policy.id, done_event.id),
                owner="done",
                lease_seconds=300,
                max_attempts=3,
                now=clock(),
            )
            assert done is not None
            await deliveries.finish(
                policy.id,
                done_event.id,
                "completed",
                {"ok": True},
                fencing_token=done.fencing_token,
                now=clock(),
            )
            await session.commit()

        calls: list[str] = []

        async def run_cycle(
            _actor: User, _policy: EventTriggerPolicy, event: EventEnvelope, _cycle: str
        ) -> dict[str, Any]:
            calls.append(str(event.id))
            return {}

        dispatcher = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=run_cycle, clock=clock
        )
        assert await dispatcher.dispatch(live_event.id) == 0
        assert await dispatcher.dispatch(done_event.id) == 0
        assert await dispatcher.catch_up() == 0
        assert calls == []

    async def test_bounded_catchup_skips_claimed_rows_and_reaches_older_events(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        first = _make_event(env, occurred_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2))
        second = _make_event(env, occurred_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
        policy = await _store_policy_event(env, first)
        async with env["factory"]() as session:
            await SqlAlchemyEventStore(session).append(second)
            await session.commit()
        calls: list[str] = []

        async def run_cycle(
            _actor: User, _policy: EventTriggerPolicy, event: EventEnvelope, _cycle: str
        ) -> dict[str, Any]:
            calls.append(str(event.id))
            return {}

        dispatcher = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=run_cycle, catchup_limit=1
        )
        assert await dispatcher.catch_up() == 1
        assert await dispatcher.catch_up() == 1
        assert calls == [str(first.id), str(second.id)]
        assert policy.enabled

    async def test_archived_organization_does_not_reach_callback(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        await _store_policy_event(env, event)
        async with env["factory"]() as session:
            await SqlAlchemyOrganizationRepository(session).update(env["organization"].archive())
            await session.commit()
        invoked = False

        async def run_cycle(*_args: Any) -> dict[str, Any]:
            nonlocal invoked
            invoked = True
            return {}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        assert await dispatcher.dispatch(event.id) == 1
        assert not invoked
        async with env["factory"]() as session:
            delivery = await session.scalar(select(TriggerDeliveryModel))
        assert delivery is not None
        assert delivery.status == "rejected"

    async def test_suspended_owner_does_not_reach_callback(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        await _store_policy_event(env, event)
        async with env["factory"]() as session:
            await SqlAlchemyUserRepository(session).update(
                dataclasses.replace(env["user"], status=UserStatus.SUSPENDED)
            )
            await session.commit()
        invoked = False

        async def run_cycle(*_args: Any) -> dict[str, Any]:
            nonlocal invoked
            invoked = True
            return {}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        await dispatcher.dispatch(event.id)
        assert not invoked

    async def test_domain_guard_rejection_is_recorded_as_rejected(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        await _store_policy_event(env, event)

        async def run_cycle(*_args: Any) -> dict[str, Any]:
            raise ValidationError("stale event")

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        await dispatcher.dispatch(event.id)
        async with env["factory"]() as session:
            delivery = await session.scalar(select(TriggerDeliveryModel))
        assert delivery is not None
        assert delivery.status == "rejected"

    async def test_unknown_event_type_is_rejected_at_policy_creation(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        with pytest.raises(ValidationError):
            EventTriggerPolicy.create(
                organization_id=env["organization"].id,
                company_id=env["company"].id,
                event_type="company.deleted",
                enabled=True,
            )


class TestSafetyGuard:
    def _context(
        self,
        env: dict[str, Any],
        task: Task,
        *,
        event_at: dt.datetime | None = None,
        limits: SafetyLimits | None = None,
        cycle_actions: int = 0,
        company_id: CompanyId | None = None,
    ) -> TriggerDispatchContext:
        company = env["company"]
        policy = EventTriggerPolicy.create(
            organization_id=env["organization"].id,
            company_id=company.id,
            event_type="task.ready",
            enabled=True,
            limits=limits,
        )
        event = EventEnvelope(
            event_type="task.ready",
            organization_id=env["organization"].id,
            company_id=company.id,
            occurred_at=event_at or dt.datetime.now(dt.UTC),
        )
        decision = Decision.create(
            company_id=company_id or company.id,
            decision_type=DecisionType.PRIORITIZE_TASK,
            payload={"task_id": str(task.id)},
            cycle_id=trigger_cycle_id(policy.id, event.id),
        )
        return TriggerDispatchContext(
            event=event,
            policy=policy,
            decision=decision,
            observation_digest="digest",
            cycle_actions=cycle_actions,
            cycle_tasks=cycle_actions,
            cycle_executions=cycle_actions,
            observed_task_status=task.status,
            observed_task_attempts=task.attempts,
        )

    def test_rejects_stale_and_future_events(self, trigger_env: dict[str, Any]) -> None:
        env = trigger_env
        task = Task.create(company_id=env["company"].id, title="Ready").transition_status(
            TaskStatus.READY
        )
        guard = SafetyGuard()
        old = self._context(env, task, event_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
        future = self._context(
            env, task, event_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=2)
        )
        with pytest.raises(ValidationError, match="stale or future"):
            guard.check_dispatch(company=env["company"], task=task, context=old)
        with pytest.raises(ValidationError, match="stale or future"):
            guard.check_dispatch(company=env["company"], task=task, context=future)

    def test_rejects_tenant_mismatch_and_cycle_cap(self, trigger_env: dict[str, Any]) -> None:
        env = trigger_env
        task = Task.create(company_id=env["company"].id, title="Ready").transition_status(
            TaskStatus.READY
        )
        guard = SafetyGuard()
        mismatch = self._context(env, task, company_id=CompanyId.generate())
        capped = self._context(env, task, limits=SafetyLimits(max_actions=1), cycle_actions=1)
        with pytest.raises(ValidationError, match="scope mismatch"):
            guard.check_dispatch(company=env["company"], task=task, context=mismatch)
        with pytest.raises(ValidationError, match="action cap"):
            guard.check_dispatch(company=env["company"], task=task, context=capped)

    def test_configured_retry_cap_above_two_is_enforced_without_hidden_lower_cap(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        task = dataclasses.replace(
            Task.create(company_id=env["company"].id, title="Retry", max_attempts=6),
            status=TaskStatus.FAILED,
            attempts=3,
        )
        event = EventEnvelope(
            event_type="task.failed",
            organization_id=env["organization"].id,
            company_id=env["company"].id,
        )

        def context(limit: int) -> TriggerDispatchContext:
            policy = EventTriggerPolicy.create(
                organization_id=env["organization"].id,
                company_id=env["company"].id,
                event_type="task.failed",
                enabled=True,
                limits=SafetyLimits(max_retries_per_task=limit),
            )
            decision = Decision.create(
                company_id=env["company"].id,
                decision_type=DecisionType.RETRY_TASK,
                payload={"task_id": str(task.id)},
                cycle_id=trigger_cycle_id(policy.id, event.id),
            )
            return TriggerDispatchContext(
                event=event,
                policy=policy,
                decision=decision,
                observation_digest="digest",
                observed_task_status=task.status,
                observed_task_attempts=task.attempts,
            )

        guard = SafetyGuard()
        guard.check_dispatch(company=env["company"], task=task, context=context(3))
        with pytest.raises(ValidationError, match="retry cap"):
            guard.check_dispatch(company=env["company"], task=task, context=context(2))

    def test_rejects_recursive_envelope_and_malformed_decision(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        task = Task.create(company_id=env["company"].id, title="Ready").transition_status(
            TaskStatus.READY
        )
        context = self._context(env, task)
        recursive_event = EventEnvelope(
            event_type="task.ready",
            organization_id=env["organization"].id,
            company_id=env["company"].id,
            metadata={"trigger_cycle_id": "trg_parent"},
        )
        recursive = dataclasses.replace(context, event=recursive_event)
        malformed_decision = dataclasses.replace(
            context.decision,
            payload={"task_id": "tsk_invalid"},
            task_id=Task.create(company_id=env["company"].id, title="Other").id,
        )
        malformed = dataclasses.replace(context, decision=malformed_decision)
        guard = SafetyGuard()
        with pytest.raises(ValidationError, match="recursive"):
            guard.check_dispatch(company=env["company"], task=task, context=recursive)
        with pytest.raises(ValidationError, match="task_id"):
            guard.check_dispatch(company=env["company"], task=task, context=malformed)


class _Clock:
    """Controllable clock so lease expiry is deterministic in tests."""

    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + dt.timedelta(seconds=seconds)


class TestTriggerLeaseRecovery:
    """Leased, fenced, bounded at-least-once recovery for trigger deliveries."""

    async def _claim(
        self,
        env: dict[str, Any],
        policy: EventTriggerPolicy,
        event: EventEnvelope,
        *,
        now: dt.datetime,
        owner: str = "worker",
        lease_seconds: int = 30,
        max_attempts: int = 3,
    ) -> Any:
        async with env["factory"]() as session:
            lease = await SqlAlchemyTriggerDeliveryRepository(session).claim(
                policy,
                event.id,
                trigger_cycle_id(policy.id, event.id),
                owner=owner,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
                now=now,
            )
            await session.commit()
            return lease

    async def _row(
        self, env: dict[str, Any], policy: EventTriggerPolicy, event: EventEnvelope
    ) -> tuple[str, int, int, str | None] | None:
        async with env["factory"]() as session:
            row = await SqlAlchemyTriggerDeliveryRepository(session).get_claim(policy.id, event.id)
        if row is None:
            return None
        return (row.status, row.attempts, row.fencing_token, row.lease_owner)

    async def test_expired_lease_is_recovered_with_higher_fencing_token(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        clock = _Clock(dt.datetime.now(dt.UTC))
        event = _make_event(env)
        policy = await _store_policy_event(env, event)

        first = await self._claim(env, policy, event, now=clock(), owner="w1", lease_seconds=10)
        assert first is not None and first.fencing_token == 1 and first.attempt == 1
        # A live lease blocks another worker.
        assert (
            await self._claim(env, policy, event, now=clock(), owner="w2", lease_seconds=10) is None
        )
        # After expiry a new worker takes over with a strictly greater token.
        clock.advance(11)
        second = await self._claim(env, policy, event, now=clock(), owner="w2", lease_seconds=10)
        assert second is not None and second.fencing_token == 2 and second.attempt == 2
        assert await self._row(env, policy, event) == ("claimed", 2, 2, "w2")

    async def test_stale_worker_cannot_overwrite_after_lease_steal(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        clock = _Clock(dt.datetime.now(dt.UTC))
        event = _make_event(env)
        policy = await _store_policy_event(env, event)

        stale = await self._claim(env, policy, event, now=clock(), owner="stale", lease_seconds=10)
        clock.advance(11)
        fresh = await self._claim(env, policy, event, now=clock(), owner="fresh", lease_seconds=600)
        assert stale is not None and fresh is not None

        async with env["factory"]() as session:
            deliveries = SqlAlchemyTriggerDeliveryRepository(session)
            assert not await deliveries.finish(
                policy.id,
                event.id,
                "completed",
                {"worker": "stale"},
                fencing_token=stale.fencing_token,
                now=clock(),
            )
            await session.commit()
        # The live owner's row is untouched by the stale completion.
        assert await self._row(env, policy, event) == ("claimed", 2, 2, "fresh")

        async with env["factory"]() as session:
            deliveries = SqlAlchemyTriggerDeliveryRepository(session)
            assert await deliveries.finish(
                policy.id,
                event.id,
                "completed",
                {"worker": "fresh"},
                fencing_token=fresh.fencing_token,
                now=clock(),
            )
            await session.commit()
        assert (await self._row(env, policy, event))[0] == "completed"  # type: ignore[index]

    async def test_worker_crash_without_completion_is_recovered_by_catch_up(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        clock = _Clock(dt.datetime.now(dt.UTC))
        event = _make_event(env)
        policy = await _store_policy_event(env, event)
        # Simulate a crash: claim, then never record an outcome.
        assert await self._claim(env, policy, event, now=clock(), owner="crashed", lease_seconds=10)
        calls: list[str] = []

        async def run_cycle(
            _actor: User, _policy: EventTriggerPolicy, envelope: EventEnvelope, _cycle: str
        ) -> dict[str, Any]:
            calls.append(str(envelope.id))
            return {"status": "recovered"}

        live = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=run_cycle, clock=clock
        )
        assert await live.catch_up() == 0
        assert calls == []

        clock.advance(11)
        recovered = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=run_cycle, clock=clock
        )
        assert await recovered.catch_up() == 1
        assert calls == [str(event.id)]
        assert (await self._row(env, policy, event))[0] == "completed"  # type: ignore[index]

    async def test_transient_failure_retries_up_to_bound_then_terminal(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        event = _make_event(env)
        policy = await _store_policy_event(env, event)

        async def failing(*_args: Any) -> dict[str, Any]:
            raise RuntimeError("transient worker fault")

        dispatcher = EventTriggerDispatcher(
            session_factory=env["factory"], run_cycle=failing, max_delivery_attempts=2
        )
        assert await dispatcher.dispatch(event.id) == 1
        assert (await self._row(env, policy, event))[0] == "interrupted"  # type: ignore[index]
        assert await dispatcher.dispatch(event.id) == 1
        assert (await self._row(env, policy, event))[0] == "failed"  # type: ignore[index]
        # Exhausted budget: no further claim and catch-up ignores it.
        assert await dispatcher.dispatch(event.id) == 0
        assert await dispatcher.catch_up() == 0
        row = await self._row(env, policy, event)
        assert row is not None and row[1] == 2

    async def test_dispatch_is_scoped_to_the_event_company(
        self, trigger_env: dict[str, Any]
    ) -> None:
        env = trigger_env
        other_company = Company.create(
            organization_id=env["organization"].id, name="Other Co", slug="other-co"
        ).transition_status(CompanyStatus.ACTIVE)
        async with env["factory"]() as session:
            await SqlAlchemyCompanyRepository(session).add(other_company)
            await session.commit()

        a_event = _make_event(env)  # belongs to company A and has a policy
        await _store_policy_event(env, a_event)
        b_event = EventEnvelope(
            event_type="task.ready",
            organization_id=env["organization"].id,
            company_id=other_company.id,
        )
        async with env["factory"]() as session:
            await SqlAlchemyEventStore(session).append(b_event)
            await session.commit()

        invoked: list[str] = []

        async def run_cycle(
            _actor: User, _policy: EventTriggerPolicy, envelope: EventEnvelope, _cycle: str
        ) -> dict[str, Any]:
            invoked.append(str(envelope.id))
            return {}

        dispatcher = EventTriggerDispatcher(session_factory=env["factory"], run_cycle=run_cycle)
        # Company A's policy never fires for a company B event, and no delivery
        # claim is created across the tenant boundary.
        assert await dispatcher.dispatch(b_event.id) == 0
        assert invoked == []
        assert await self._row(env, policy_for(env, b_event), b_event) is None


def policy_for(env: dict[str, Any], event: EventEnvelope) -> EventTriggerPolicy:
    """A throwaway policy handle used only to probe for a delivery row."""
    return EventTriggerPolicy.create(
        organization_id=env["organization"].id,
        company_id=event.company_id or env["company"].id,
        event_type=event.type,
        enabled=True,
    )
