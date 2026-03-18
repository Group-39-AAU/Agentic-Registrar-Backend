"""Drop and recreate all tables + enums in registrar_db."""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings

DATABASE_URL = str(settings.DATABASE_URL)

async def reset():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
    await engine.dispose()
    print("✅ Database reset complete — all tables dropped.")

if __name__ == "__main__":
    asyncio.run(reset())
