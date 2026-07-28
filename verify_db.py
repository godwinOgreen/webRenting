# verify_db.py
from sqlalchemy import create_engine, text

# Uses the standard sync URL string style matching your local environment
DATABASE_URL = "postgresql+psycopg://postgres:g0dw1ndb@localhost:5432/real_estate_db"

engine = create_engine(DATABASE_URL)

print("─" * 40)
print("🔍 POSTGRESQL LIVE SCHEMA DISCOVERY")
print("─" * 40)

with engine.connect() as conn:
    # 1. Physical Table Extraction
    table_count = conn.execute(
        text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    ).scalar()
    print(f"🏭 Total Tables: {table_count}")

    tables = (
        conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"
            )
        )
        .scalars()
        .all()
    )
    for table in tables:
        print(f"  ├── {table}")

    # 2. Native Enum Custom Boundaries
    enums = (
        conn.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e' ORDER BY typname"))
        .scalars()
        .all()
    )
    print(f"\n🏷️ Custom Enum Types: {len(enums)}")
    for enum in enums:
        print(f"  ├── {enum}")

    # 3. Active Architectural Extensions
    extensions = conn.execute(
        text(
            "SELECT extname, extversion FROM pg_extension WHERE extname IN ('postgis', 'uuid-ossp')"
        )
    ).all()
    print("\n🛠️ Activated Extensions:")
    for ext in extensions:
        print(f"  ├── {ext.extname} (v{ext.extversion})")

    # 4. Trigger Orchestration Audit
    # NOTE: Updated to look for 'trigger_%_updated_at' matching your migration conventions
    triggers = (
        conn.execute(
            text(
                """
        SELECT event_object_table FROM information_schema.triggers
        WHERE trigger_name LIKE '%updated_at'
        ORDER BY event_object_table
        """
            )
        )
        .scalars()
        .all()
    )
    print(f"\n🔄 Automated updated_at Triggers: {len(triggers)}")
    for trigger_table in triggers:
        print(f"  ├── {trigger_table}")

print("─" * 40)
