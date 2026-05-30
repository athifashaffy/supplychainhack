"""Supplier registry — lookup of verified identities and public keys."""

import asyncpg


async def get_supplier(pool: asyncpg.Pool, supplier_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM suppliers WHERE supplier_id = $1", supplier_id
        )
        return dict(row) if row else None


async def list_suppliers(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM suppliers ORDER BY name")
        return [dict(r) for r in rows]


async def register_supplier(pool: asyncpg.Pool, supplier_id: str, name: str,
                             country: str, province: str | None,
                             public_key_hex: str) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO suppliers (supplier_id, name, country, province, public_key_hex)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (supplier_id) DO UPDATE
                SET name = EXCLUDED.name,
                    country = EXCLUDED.country,
                    province = EXCLUDED.province,
                    public_key_hex = CASE
                        WHEN suppliers.public_key_hex = '' THEN EXCLUDED.public_key_hex
                        ELSE suppliers.public_key_hex
                    END
            RETURNING *
        """, supplier_id, name, country, province, public_key_hex)
        return dict(row)
