"""
重置 dataset 29 下 10 张图回 pending, 用于 exclude_id 验证
"""
import pymysql

conn = pymysql.connect(
    host='127.0.0.1', port=3310,
    user='root', password='root',
    database='image_annotation',
)
cur = conn.cursor()
cur.execute("SELECT id FROM image WHERE dataset_id=29 AND status='ai_labeled' LIMIT 10")
ids = [r[0] for r in cur.fetchall()]
print(f"Found {len(ids)} ai_labeled images:", ids)
if ids:
    placeholders = ','.join(['%s'] * len(ids))
    cur.execute(
        f"UPDATE image SET status='pending' WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()
    print(f"Reset {cur.rowcount} images to pending")
cur.close()
conn.close()
