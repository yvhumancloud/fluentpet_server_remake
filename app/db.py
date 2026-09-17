from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.settings import settings

engine = create_async_engine(settings.database_url, pool_size=5, max_overflow=0)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request. `after_commit` tasks (push, webhooks) run only if it commits.

    FastAPI tears yield-dependencies down after the response is sent, so this still runs
    them after the client has its answer, but with the data visible to a fresh session.
    """
    after_commit = request.state.after_commit = BackgroundTasks()
    async with SessionLocal() as session:
        async with session.begin():
            yield session
        await after_commit()


def get_after_commit(
    request: Request, _: Annotated[AsyncSession, Depends(get_session)]
) -> BackgroundTasks:
    return request.state.after_commit


AfterCommit = Annotated[BackgroundTasks, Depends(get_after_commit)]
