from database import init_db, get_connection

init_db()

conn = get_connection()
cursor = conn.cursor()
cursor.execute("DELETE FROM matches")
cursor.execute("DELETE FROM jobs")
conn.commit()
conn.close()

print("Cleared the jobs and matches tables.")
print("Profiles (cached resume extraction) and Gemini usage history were left untouched.")
print("Next search will fetch fresh data with corrected Workday URLs and normalized posting dates.")