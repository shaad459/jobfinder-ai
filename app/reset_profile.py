"""One-time cleanup: clears the profiles table so the next resume extraction runs fresh
against the new, richer schema (work_experience, certifications, projects, education, skills
split into hands_on/trained, plus the email field that was previously extracted but never
actually saved). The old cached profile row has the OLD flat schema and would otherwise keep
being returned as a cache hit forever, since caching is keyed on resume-text hash, not schema
version.

Does NOT touch jobs, matches, or gemini_calls - only profiles.
"""

from database import init_db, get_connection

init_db()
conn = get_connection()
cursor = conn.cursor()
cursor.execute("DELETE FROM profiles")
conn.commit()
conn.close()

print("Cleared the profiles table.")
print("Jobs, matches, and Gemini usage history were left untouched.")
print("The next resume extraction will run fresh (spends 1 real Gemini call) against the new schema.")
