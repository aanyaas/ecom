import os
import re

print('Running patch_render.py...')

with open('app.py', 'r') as f:
    content = f.read()

original = content
NL = chr(10)

# Fix 1: Wrap scheduler.start() in try/except
if 'scheduler.start()' in content and 'except Exception' not in content:
    content = content.replace(
        'scheduler.start()',
        'try:' + NL + '    scheduler.start()' + NL + 'except Exception as e:' + NL + '    print(f"Scheduler start failed: {e}")'
    )
    print('SUCCESS: scheduler.start() wrapped in try/except')

# Fix 2: Override database URI to use PostgreSQL from DATABASE_URL env var
db_patch_marker = 'Render PostgreSQL patch'
db_patch = (NL + '# Render PostgreSQL patch' + NL +
    '_render_db_url = os.environ.get("DATABASE_URL", "")' + NL +
    'if _render_db_url:' + NL +
    '    if _render_db_url.startswith("postgres://"):' + NL +
    '        _render_db_url = "postgresql://" + _render_db_url[11:]' + NL +
    '    app.config["SQLALCHEMY_DATABASE_URI"] = _render_db_url' + NL +
    '    print("DB: Using PostgreSQL from DATABASE_URL")' + NL)

if db_patch_marker not in content:
    lines = content.split(NL)
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and "app.config['SQLALCHEMY_DATABASE_URI']" in line and 'mysql' in line:
            new_lines.append(db_patch)
            inserted = True
    if inserted:
        content = NL.join(new_lines)
        print('SUCCESS: DATABASE_URL PostgreSQL override added')
    else:
        print('WARNING: Could not find MySQL URI line to patch')

if content != original:
    with open('app.py', 'w') as f:
        f.write(content)
    print('app.py written successfully')
else:
    print('No changes made to app.py')

print('patch_render.py complete!')
