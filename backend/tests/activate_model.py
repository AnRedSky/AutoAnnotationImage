import httpx
r = httpx.post('http://127.0.0.1:5000/api/auth/login', data={'username':'admin','password':'admin123'})
token = r.json()['access_token']
H = {'Authorization': f'Bearer {token}'}
r = httpx.get('http://127.0.0.1:5000/api/models/', headers=H, timeout=10)
data = r.json()
items = data if isinstance(data, list) else data.get('items', [])
print('models count:', len(items))
for m in items:
    fp = m.get('file_path') or ''
    print(' ', m['id'], m['name'], 'ds=', m.get('dataset_id'), 'active=', m.get('is_active'), 'fp=', fp[:50] if fp else None)
for m in items:
    if m.get('file_path'):
        r = httpx.post(f"http://127.0.0.1:5000/api/models/{m['id']}/activate", headers=H, timeout=10)
        print(f"activate {m['id']}: {r.status_code} {r.text[:200]}")
        break
