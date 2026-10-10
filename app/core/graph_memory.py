from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def graph_memory(uri):
    # Schema names are fixed, never interpolated from request data. setup is bootstrap-only.
    saver_pool = AsyncConnectionPool(
        make_conninfo(uri, options="-c search_path=graph_checkpoints"),
        min_size=1,
        max_size=3,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    store_pool = AsyncConnectionPool(
        make_conninfo(uri, options="-c search_path=graph_store"),
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    async with saver_pool, store_pool:
        await saver_pool.wait()
        await store_pool.wait()
        yield AsyncPostgresSaver(saver_pool), AsyncPostgresStore(store_pool)
