"""Check Redis stale task-meta entries."""
import redis
r2 = redis.Redis(host='127.0.0.1', port=9770, db=2)
keys = [k.decode() for k in r2.keys('celery-task-meta-*')]
print(f'  db2 stale task-meta count: {len(keys)}')
for k in keys[:8]:
    v = r2.get(k)
    if v:
        snippet = v[:300].decode('utf-8', errors='replace')
        print(f'\n  {k}\n    {snippet}')
