"""
Django Cache Configuration

This module provides cache configuration for the Django application.
Supports multiple cache backends including Redis, Memcached, and local memory.
"""
import os
from urllib.parse import urlparse

# ============================
# Cache Configuration
# ============================

# Cache backend selection from environment variable
CACHE_BACKEND = os.getenv('CACHE_BACKEND', 'redis')

# Cache path configuration
# Use Case: Local file system cache directory for temporary files
# Default: '/opt/backend/cache' (container internal path)
# Note: Ensure directory exists and has proper permissions
CACHE_PATH = os.getenv(
    'BACKEND_CACHE_PATH',
    '/opt/backend/cache'
)

def get_redis_cache_url():
    """
    Get Redis cache URL from environment variables.
    Prefer CACHE_REDIS_URL, then derive database 1 from the legacy broker
    override or the shared REDIS_URL while preserving connection options.
    """
    # First try to get dedicated cache Redis URL
    cache_redis_url = os.getenv('CACHE_REDIS_URL')
    if cache_redis_url:
        return cache_redis_url

    # Fallback to Celery broker URL and modify database number
    celery_broker_url = os.getenv('CELERY_BROKER_URL') or os.getenv('REDIS_URL', 'redis://redis:6379/0')
    parsed_url = urlparse(celery_broker_url)
    return parsed_url._replace(path='/1').geturl()

if CACHE_BACKEND == 'redis':
    # Redis cache configuration
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': get_redis_cache_url(),
            'OPTIONS': {},
            'KEY_PREFIX': 'backend',
            'VERSION': 1,
            'TIMEOUT': 300,  # 5 minutes default timeout
        }
    }

elif CACHE_BACKEND == 'memcached':
    # Memcached cache configuration
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
            'LOCATION': os.getenv('CACHE_MEMCACHED_URL', '127.0.0.1:11211'),
            'OPTIONS': {
                'no_delay': True,
                'ignore_exc': True,
            },
            'KEY_PREFIX': 'backend',
            'VERSION': 1,
            'TIMEOUT': 300,
        }
    }

elif CACHE_BACKEND == 'database':
    # Database cache configuration
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'django_cache_table',
            'OPTIONS': {
                'MAX_ENTRIES': 10000,
                'CULL_FREQUENCY': 3,
            },
            'KEY_PREFIX': 'backend',
            'VERSION': 1,
            'TIMEOUT': 300,
        }
    }

else:
    # Fallback to local memory cache (development)
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'backend-cache',
            'OPTIONS': {
                'MAX_ENTRIES': 1000,
                'CULL_FREQUENCY': 3,
            },
            'KEY_PREFIX': 'backend',
            'VERSION': 1,
            'TIMEOUT': 300,
        }
    }

# ============================
# Cache Session Configuration
# ============================

# Use cache for session storage (optional)
# Uncomment the following lines to use cache for sessions
# SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
# SESSION_CACHE_ALIAS = 'default'

# ============================
# Cache Middleware Configuration
# ============================

# Cache middleware settings
CACHE_MIDDLEWARE_ALIAS = 'default'
CACHE_MIDDLEWARE_SECONDS = 300  # 5 minutes
CACHE_MIDDLEWARE_KEY_PREFIX = 'backend'

# ============================
# Cache Key Configuration
# ============================

# Custom cache key functions
def get_cache_key(prefix: str, *args) -> str:
    """
    Generate a consistent cache key

    Args:
        prefix: Key prefix
        *args: Additional key components

    Returns:
        Formatted cache key
    """
    return f"{prefix}:{':'.join(str(arg) for arg in args)}"

# ============================
# Cache Health Check
# ============================

def check_cache_health():
    """
    Check if cache backend is working properly

    Returns:
        bool: True if cache is working, False otherwise
    """
    try:
        from django.core.cache import cache

        # Test basic operations
        test_key = 'cache_health_check'
        test_value = 'test_value'

        # Test set
        cache.set(test_key, test_value, 60)

        # Test get
        retrieved_value = cache.get(test_key)

        # Test delete
        cache.delete(test_key)

        return retrieved_value == test_value

    except Exception as e:
        print(f"Cache health check failed: {e}")
        return False
