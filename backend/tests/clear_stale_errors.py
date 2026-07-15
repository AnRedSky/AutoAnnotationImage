"""Clear stale train:error:* keys from Redis (pre-fix error messages still cached)."""
import redis
r1 = redis.Redis(host='127.0.0.1', port=9770, db=1)
for k in ['celery', 'unacked', 'unacked_index']:
    n = r1.delete(k)
    print(f'  cleared db1.{k}={n}')

r2 = redis.Redis(host='127.0.0.1', port=9770, db=2)
err_keys = [k.decode() for k in r2.keys('train:error:*')]
for k in err_keys:
    r2.delete(k)
print(f'  cleared db2 train:error:* -> {len(err_keys)} keys')

meta_keys = [k.decode() for k in r2.keys('celery-task-meta-*')]
for k in meta_keys:
    r2.delete(k)
print(f'  cleared db2 celery-task-meta:* -> {len(meta_keys)} keys')
