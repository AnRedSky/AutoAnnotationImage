"""清理残留的 stale training jobs (PENDING/PROGRESS 状态)"""
import pymysql
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='root', database='image_annotation')
cur = conn.cursor()
cur.execute("SELECT id, celery_task_id, state, model_name FROM training_jobs WHERE state IN ('PENDING', 'PROGRESS')")
rows = cur.fetchall()
print(f'will delete {len(rows)} stale jobs:')
for r in rows:
    print(' ', r)
cur.execute("DELETE FROM training_jobs WHERE state IN ('PENDING', 'PROGRESS')")
print(f'deleted: {cur.rowcount}')
conn.commit()
conn.close()
