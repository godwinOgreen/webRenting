# check_init.py
import os

versions_dir = os.path.join("alembic", "versions")

if os.path.exists(versions_dir):
    # Matches files starting with a date or 'init'
    files = [f for f in os.listdir(versions_dir) if f.endswith(".py") and "init" in f.lower()]
    if files:
        path = os.path.join(versions_dir, files[0])
        with open(path, encoding="utf-8") as f:
            content = f.read()

        print("─" * 40)
        print("📊 SCHEMA STRUCTURAL ANALYSIS")
        print("─" * 40)
        print(f"📁 Target File   : {path}")
        print(f"🏭 Tables Created: {content.count('op.create_table')}")
        print(f"🏷️  Enum Types    : {content.count('sa.Enum(')}")
        print(f"🛡️  CHECK Guards  : {content.count('CheckConstraint')}")
        print(f"⚡ Standard Idxs : {content.count('op.create_index')}")
        print("─" * 40)
    else:
        print("❌ No initial migration file matching 'init' was discovered.")
else:
    print("❌ Directory 'alembic/versions' could not be resolved.")
