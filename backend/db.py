"""Database connection pool and schema initialization."""

import asyncpg
import os

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=2,
            max_size=10,
        )
    return _pool


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id   TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                country       TEXT NOT NULL,
                province      TEXT,
                public_key_hex TEXT NOT NULL,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS attestations (
                id            TEXT PRIMARY KEY,
                payload       JSONB NOT NULL,
                signature     TEXT NOT NULL,
                signer_id     TEXT NOT NULL,
                sig_valid     BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_attestations_signer
                ON attestations(signer_id);

            CREATE TABLE IF NOT EXISTS attestation_inputs (
                attestation_id  TEXT NOT NULL REFERENCES attestations(id),
                input_id        TEXT NOT NULL,
                PRIMARY KEY (attestation_id, input_id)
            );
        """)
