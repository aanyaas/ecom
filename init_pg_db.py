import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def get_connection():
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('postgres://'):
        db_url = 'postgresql://' + db_url[11:]
    return psycopg2.connect(db_url)

def run_migration():
    conn = get_connection()
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    # Check if tables already exist
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='admin_menus'")
    count = cur.fetchone()[0]
    if count > 0:
        print('Tables already exist, skipping migration.')
        cur.close()
        conn.close()
        return

    print('Running PostgreSQL migration...')
    sql = open('init_pg_schema.sql', 'r').read()
    cur.execute(sql)
    print('Migration complete!')
    cur.close()
    conn.close()

if __name__ == '__main__':
    run_migration()
