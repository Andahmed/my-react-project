#!/usr/bin/env python3
"""Static regression checks for React features that do not require npm/browser dependencies."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
store = (ROOT / 'frontend/src/adminStore.tsx').read_text(encoding='utf-8')
html = (ROOT / 'app/pos_istakoza.html').read_text(encoding='utf-8')
checks = []
def check(name, condition):
    checks.append(bool(condition))
    print(('PASS' if condition else 'FAIL') + ' ' + name)

check('React defines saveTemplate used by UI', bool(re.search(r'const\s+saveTemplate\s*=\s*async\s*\(', store)) and 'onClick={saveTemplate}' in store)
check('React defines useTemplate used by UI', bool(re.search(r'const\s+useTemplate\s*=\s*\(', store)) and 'onClick={() => useTemplate(t)}' in store)
check('React defines deleteTemplate used by UI', bool(re.search(r'const\s+deleteTemplate\s*=\s*async\s*\(', store)) and 'onClick={() => deleteTemplate(t)}' in store)
check('React template save/load/delete use existing API endpoint', store.count("'/api/store/template'") >= 2 and 'api(' in store)
check('React template save validates name and item quantities', "if (!name)" in store and "if (!items.length)" in store and 'Number(line.qty) > 0' in store)
check('HTML legacy supports save/load/delete template for comparison', all(x in html for x in ('function poLoadTpl()', 'async function poSaveTpl()', 'async function tplDelPo(')))
check('React keeps database/API rather than localStorage for templates', "api('/api/store/template'" in store and 'localStorage' not in store)
print(f'TOTAL {sum(checks)} / {len(checks)}')
raise SystemExit(0 if all(checks) else 1)
