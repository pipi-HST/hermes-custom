import sqlite3

db = sqlite3.connect('/Users/pipi/hermes-custom/data/captures.db')

# Check table schema
schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall()
for s in schema:
    print(f"TABLE: {s[0]}")

print("---")

# Check captures table columns
cols = db.execute("PRAGMA table_info(captures)").fetchall()
print("captures columns:")
for c in cols:
    print(f"  {c}")

# Count pages in vault
import os
vault_path = os.path.expanduser("~/Obsidian/hermes-vault")
if os.path.isdir(vault_path):
    md_files = []
    for root, dirs, files in os.walk(vault_path):
        for f in files:
            if f.endswith('.md'):
                md_files.append(os.path.join(root, f))
    print(f"\nvault .md files: {len(md_files)}")
    for f in sorted(md_files)[:10]:
        print(f"  {f}")
else:
    print(f"\nvault not found at {vault_path}")

db.close()
