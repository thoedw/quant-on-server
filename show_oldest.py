import sqlite3
import pandas as pd

conn = sqlite3.connect('data/securities_master.db')
query = """
SELECT s.symbol, n.published_at, n.title
FROM news_sentiment n
JOIN securities s ON n.security_id = s.security_id
WHERE s.symbol IN ('QNS', 'CDR')
ORDER BY n.published_at ASC
LIMIT 10;
"""
df = pd.read_sql_query(query, conn)
pd.set_option('display.max_columns', None)
pd.set_option('display.expand_frame_repr', False)
print("\n--- OLDEST NEWS IN DB ---")
print(df)
