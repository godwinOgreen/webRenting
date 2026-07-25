import asyncio
import asyncpg

async def test_connections():
    """Test PostgreSQL connection with provided credentials."""
    
    test_urls = [
        {
            "name": "postgres user with password",
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "g0dw1ndb",
        },
        {
            "name": "webrenting_test database",
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "password": "g0dw1ndb",
            "database": "webrenting_test",
        },
    ]
    
    for test in test_urls:
        try:
            print(f"\nTesting: {test['name']}")
            conn = await asyncpg.connect(
                host=test['host'],
                port=test['port'],
                user=test['user'],
                password=test['password'],
                database=test.get('database', 'postgres'),
            )
            version = await conn.fetchval("SELECT version();")
            print(f"  SUCCESS: Connected!")
            print(f"  Version: {version[:60]}...")
            
            # For postgres connection, list databases
            if test.get('database') is None or test.get('database') == 'postgres':
                databases = await conn.fetch("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;")
                db_names = [db['datname'] for db in databases]
                print(f"  Databases: {db_names}")
            else:
                # For webrenting_test, check if tables exist
                tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' LIMIT 10;")
                table_names = [t['table_name'] for t in tables]
                print(f"  Tables found: {table_names}")
            
            await conn.close()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {str(e)[:100]}")

asyncio.run(test_connections())
