"""Focused tests for API key creation from the NiceGUI page."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openscientist.api.auth import hash_secret
from openscientist.database.models import APIKey, User

api_keys_page = import_module("openscientist.webapp_components.pages.api_keys")


def _use_test_session(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Route page database work through the transaction-scoped test session."""

    @asynccontextmanager
    async def test_session_ctx() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(api_keys_page, "get_session_ctx", test_session_ctx)


@pytest.mark.asyncio
async def test_page_creation_limit_counts_only_active_keys(
    db_session: AsyncSession,
    webapp_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoked credentials do not exhaust the page's ten-key allowance."""
    db_session.add_all(
        [
            APIKey(
                user_id=webapp_user.id,
                name=f"revoked-page-{index}",
                key_hash=hash_secret(f"revoked-page-secret-{index}"),
                is_active=False,
            )
            for index in range(api_keys_page.MAX_KEYS_PER_USER)
        ]
    )
    await db_session.commit()
    _use_test_session(monkeypatch, db_session)

    full_key = await api_keys_page._create_key_for_user(
        str(webapp_user.id),
        "new-page-key",
    )

    assert full_key.startswith("new-page-key:")
    result = await db_session.execute(
        select(APIKey).where(
            APIKey.user_id == webapp_user.id,
            APIKey.name == "new-page-key",
        )
    )
    assert result.scalar_one().is_active is True


@pytest.mark.asyncio
async def test_page_creation_reuses_revoked_name(
    db_session: AsyncSession,
    webapp_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page replaces an inactive same-name record with a fresh key."""
    revoked_key = APIKey(
        user_id=webapp_user.id,
        name="reusable-page-name",
        key_hash=hash_secret("old-page-secret"),
        is_active=False,
    )
    db_session.add(revoked_key)
    await db_session.commit()
    await db_session.refresh(revoked_key)
    revoked_key_id = revoked_key.id
    _use_test_session(monkeypatch, db_session)

    full_key = await api_keys_page._create_key_for_user(
        str(webapp_user.id),
        "reusable-page-name",
    )

    assert full_key.startswith("reusable-page-name:")
    result = await db_session.execute(
        select(APIKey).where(
            APIKey.user_id == webapp_user.id,
            APIKey.name == "reusable-page-name",
        )
    )
    replacement = result.scalar_one()
    assert replacement.id != revoked_key_id
    assert replacement.is_active is True
