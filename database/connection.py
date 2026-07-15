import os
import time
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool
from flask import g
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3309)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', ''),
    'ssl_disabled': True,
    'use_pure': True,
    'connection_timeout': 10,
    'autocommit': True,
}

class DatabaseConnectionError(Exception):
    """Raised when database connection fails after retries."""

# Initialize connection pool with standard config
db_pool = None
is_pythonanywhere = 'PYTHONANYWHERE_SITE' in os.environ
pool_size = 1 if is_pythonanywhere else 16

try:
    db_pool = MySQLConnectionPool(
        pool_name="ecom_pool",
        pool_size=pool_size,
        pool_reset_session=True,
        **DB_CONFIG
    )
    print("Database Connection Pool initialized successfully.")
except Exception as pool_err:
    print(f"Failed to initialize Connection Pool: {pool_err}")
    db_pool = None

def get_db_connection():
    conn = None
    if db_pool:
        for attempt in range(3):
            try:
                conn = db_pool.get_connection()
                break
            except Exception as e:
                print(f"Failed to fetch connection from pool (attempt {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(0.5)
    
    if not conn:
        # Fallback to direct connection if pool fails
        for attempt in range(3):
            try:
                conn = mysql.connector.connect(**DB_CONFIG)
                break
            except Exception as e:
                print(f"DB Connection fallback attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(0.5)
        if not conn:
            raise DatabaseConnectionError("Could not connect to database after multiple attempts")
            
    if 'db_conns' not in g:
        g.db_conns = []
    g.db_conns.append(conn)
    return conn

def close_db_connection(exception=None):
    conns = g.pop('db_conns', [])
    for db in conns:
        try:
            if db and hasattr(db, 'is_connected') and db.is_connected():
                db.close()
        except Exception as e:
            print(f"Error returning connection to pool: {e}")
