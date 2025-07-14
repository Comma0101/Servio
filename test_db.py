import asyncio
import asyncpg
import os

async def test_db_connection():
    try:
        print("Attempting to connect to the database...")
        conn = await asyncpg.connect(
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        print("Connection successful!")
        await conn.close()
    except Exception as e:
        with open("/app/db_error.log", "w") as f:
            f.write(str(e))

if __name__ == "__main__":
    asyncio.run(test_db_connection())
