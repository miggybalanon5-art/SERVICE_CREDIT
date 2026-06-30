"""
ONE-OFF DIAGNOSTIC - run this once to see exactly why a biometrics name isn't
matching an employee. Delete this file after you're done with it; it's not
part of the app.

Usage (from the app folder, with form6_tracker.sqlite3 in the same folder):
    python diagnose_biometrics_match.py

Edit SEARCH_NAME below if you want to check a different employee.
"""

from form6_store import connect, normalize_key

SEARCH_NAME = "DELA CRUZ, JUAN"

print(f"Looking for employees whose name is close to: {SEARCH_NAME!r}")
print(f"Normalized search key: {normalize_key(SEARCH_NAME)!r}")
print()

with connect() as conn:
    rows = conn.execute("SELECT id, grade, display_name, position FROM employees").fetchall()

if not rows:
    print("No employees found in the database at all. Did this script run against the right .sqlite3 file?")
else:
    print(f"{len(rows)} employee(s) in the database:\n")
    for row in rows:
        db_name = row["display_name"]
        db_key = normalize_key(db_name)
        search_key = normalize_key(SEARCH_NAME)
        match_marker = " <-- EXACT MATCH" if db_key == search_key else ""
        print(f"  id={row['id']:<4} grade={row['grade']!r:<10} display_name={db_name!r:<30} normalized={db_key!r}{match_marker}")

print()
print("If you don't see '<-- EXACT MATCH' above, the display_name stored for this")
print("employee does not normalize to the same thing as the biometrics file's name.")
print("Common causes: name typed in a different order (e.g. 'JUAN DELA CRUZ' instead")
print("of 'DELA CRUZ, JUAN'), an extra middle initial baked into the name, or a typo.")
