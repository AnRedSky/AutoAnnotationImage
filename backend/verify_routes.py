"""Quick OpenAPI/route inventory check (no import-time side effects)."""
import sys
sys.path.insert(0, '.')
from app.main import app
from collections import defaultdict

paths = {}
for r in app.routes:
    if hasattr(r, 'path') and hasattr(r, 'methods'):
        for method in r.methods - {'HEAD'}:
            paths.setdefault(r.path, []).append(method)

print(f'Total unique paths: {len(paths)}')
print(f'Total route operations: {sum(len(v) for v in paths.values())}')

by_prefix = defaultdict(int)
for p in paths:
    parts = p.strip('/').split('/')
    if len(parts) >= 2 and parts[0] == 'api':
        by_prefix['/api/' + parts[1]] += 1
    else:
        by_prefix[p] += 1

print('\n=== Routes by API prefix ===')
for prefix, count in sorted(by_prefix.items()):
    print(f'  {prefix:35s} {count:3d} ops')

spec = app.openapi()
print('\n=== OpenAPI ===')
print(f'  title: {spec["info"]["title"]}')
print(f'  version: {spec["info"]["version"]}')
print(f'  total paths in openapi: {len(spec["paths"])}')
