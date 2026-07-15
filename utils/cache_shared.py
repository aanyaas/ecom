"""
utils/cache_shared.py

Shared flask-caching instance.

By default uses SimpleCache (in-process, no Redis required).
To switch to Redis, set these environment variables:
    CACHE_TYPE=RedisCache
    CACHE_REDIS_URL=redis://localhost:6379/0
"""
import os
from flask_caching import Cache

cache = Cache(config={
    'CACHE_TYPE': os.getenv('CACHE_TYPE', 'SimpleCache'),
    'CACHE_DEFAULT_TIMEOUT': int(os.getenv('CACHE_DEFAULT_TIMEOUT', 300)),
    'CACHE_REDIS_URL': os.getenv('CACHE_REDIS_URL', 'redis://localhost:6379/0'),
})
