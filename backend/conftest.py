import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("CACHE_ENABLED", "0")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")


def pytest_configure(config):
    from django.conf import settings

    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    _install_fake_redis()


def _install_fake_redis():
    """Back apps.core.redis_client.get_redis with in-process fakeredis.

    Tests must never open a TCP connection to Redis: when the local server is
    down, every fail-open call blocks on a long connect timeout (or worse,
    hangs forever against a half-open proxy). A shared FakeServer keeps lock,
    cache and rate-limit semantics intact for their unit tests while making
    every Redis touch instant and hermetic.
    """
    import sys

    import fakeredis

    from apps.core import redis_client

    fake = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())

    def _fake_get_redis():
        return fake

    redis_client.get_redis = _fake_get_redis
    # Modules that bound the original symbol via ``from ... import get_redis``
    # keep working off the old reference - rebind any of them we can see.
    for mod in list(sys.modules.values()):
        namespace = getattr(mod, "__dict__", None)
        if not namespace:
            continue
        fn = namespace.get("get_redis")
        if getattr(fn, "__module__", "") == "apps.core.redis_client":
            namespace["get_redis"] = _fake_get_redis