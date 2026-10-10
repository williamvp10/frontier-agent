from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def database_pool(url: str, *, pool_size: int = 5) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        echo=False,
        hide_parameters=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
