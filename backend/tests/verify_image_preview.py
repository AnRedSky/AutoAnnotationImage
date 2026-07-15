"""端到端验证：图片预览 401 修复"""
import urllib.request, urllib.error, json, random, string, sys

BASE = 'http://127.0.0.1:5000'

# 1. 注册 + 登录
uname = 'img_test_' + ''.join(random.choices(string.ascii_lowercase, k=6))
data = json.dumps({'username': uname, 'password': 'Test1234!', 'email': uname + '@x.com'}).encode()
req = urllib.request.Request(f'{BASE}/api/auth/register', data=data, headers={'Content-Type': 'application/json'})
try:
    code = urllib.request.urlopen(req).status
    print(f'register: {code}')
except urllib.error.HTTPError as e:
    print(f'register: {e.code} {e.reason}')

data = ('username=' + uname + '&password=Test1234!').encode()
req = urllib.request.Request(f'{BASE}/api/auth/login', data=data)
tok = json.loads(urllib.request.urlopen(req).read())['access_token']
print(f'login OK, token len = {len(tok)}')

# 2. 找 dataset + image
req = urllib.request.Request(f'{BASE}/api/datasets', headers={'Authorization': 'Bearer ' + tok})
ds = json.loads(urllib.request.urlopen(req).read())
ds = ds if isinstance(ds, list) else ds.get('items', ds)
if not ds:
    print('NO dataset; creating one...')
    data = json.dumps({'name': 'preview_test', 'category_names': ['cat', 'dog']}).encode()
    req = urllib.request.Request(f'{BASE}/api/datasets', data=data,
                                  headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok})
    new_ds = json.loads(urllib.request.urlopen(req).read())
    ds_id = new_ds['id']
else:
    ds_id = ds[0]['id']
print('dataset id =', ds_id)

# 3. 找一张图（没有就上传一个 fixture）
req = urllib.request.Request(f'{BASE}/api/images/list/{ds_id}', headers={'Authorization': 'Bearer ' + tok})
imgs_data = json.loads(urllib.request.urlopen(req).read())
imgs = imgs_data if isinstance(imgs_data, list) else imgs_data.get('items', [])
if not imgs:
    print('NO image; uploading fixture_01.png ...')
    from pathlib import Path
    fix = Path(__file__).parent / 'tests' / 'fixtures' / 'images' / 'fixture_01.png'
    if not fix.exists():
        fix = Path('backend/tests/fixtures/images/fixture_01.png')
    if not fix.exists():
        print('no fixture file, skip')
        sys.exit(0)
    import mimetypes
    boundary = '----' + ''.join(random.choices(string.ascii_letters, k=16))
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="files"; filename="{fix.name}"\r\n'
        f'Content-Type: {mimetypes.guess_type(fix.name)[0] or "image/png"}\r\n\r\n'
    ).encode() + fix.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(
        f'{BASE}/api/images/upload/{ds_id}', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                 'Authorization': 'Bearer ' + tok},
    )
    res = json.loads(urllib.request.urlopen(req).read())
    imgs = res.get('items') or res.get('uploaded') or [res]
    img_id = imgs[0]['id'] if isinstance(imgs, list) and imgs else res['id']
    print(f'uploaded image id = {img_id}')
else:
    img_id = imgs[0]['id']
    print(f'using image id = {img_id}')

# 4. 模拟 <img> 不带 Authorization header
print()
print('=== Test 1: <img src=...> no Authorization header (旧行为, 期望 401) ===')
try:
    r = urllib.request.urlopen(f'{BASE}/api/files/{img_id}', timeout=3)
    print(f'  PASS (unexpected): {r.status}')
except urllib.error.HTTPError as e:
    print(f'  {e.code} {e.reason}')

# 5. 模拟 <img> 用 ?token=xxx (修复后方式)
print()
print('=== Test 2: <img src=.../?token=xxx> (修复后) ===')
try:
    r = urllib.request.urlopen(f'{BASE}/api/files/{img_id}?token={tok}', timeout=5)
    data = r.read()
    ct = r.headers.get('Content-Type')
    print(f'  {r.status} | {ct} | {len(data)} bytes')
except urllib.error.HTTPError as e:
    print(f'  {e.code} {e.reason}')
    print(f'  body: {e.read()[:200]}')

# 6. 缩略图
print()
print('=== Test 3: thumbnail ?size=240&token=xxx ===')
try:
    r = urllib.request.urlopen(f'{BASE}/api/files/{img_id}/thumbnail?size=240&token={tok}', timeout=5)
    data = r.read()
    ct = r.headers.get('Content-Type')
    print(f'  {r.status} | {ct} | {len(data)} bytes')
except urllib.error.HTTPError as e:
    print(f'  {e.code} {e.reason}')

# 7. axios 走 Authorization header
print()
print('=== Test 4: Authorization header (axios 默认方式) ===')
req = urllib.request.Request(
    f'{BASE}/api/files/{img_id}',
    headers={'Authorization': 'Bearer ' + tok},
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    data = r.read()
    print(f'  {r.status} | {r.headers.get("Content-Type")} | {len(data)} bytes')
except urllib.error.HTTPError as e:
    print(f'  {e.code} {e.reason}')

# 8. 无效 token
print()
print('=== Test 5: ?token=invalid (期望 401) ===')
try:
    r = urllib.request.urlopen(f'{BASE}/api/files/{img_id}?token=invalid_xxx', timeout=3)
    print(f'  unexpected pass: {r.status}')
except urllib.error.HTTPError as e:
    print(f'  {e.code} {e.reason}')

print()
print('DONE')
