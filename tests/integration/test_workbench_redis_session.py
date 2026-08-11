from datetime import UTC, datetime, timedelta

from lucking.repositories.redis_session import RedisSessionStore


class Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.actions = []

    def __getattr__(self, name):
        def queue(*args):
            self.actions.append((name, args))
            return self

        return queue

    def execute(self):
        for name, args in self.actions:
            getattr(self.redis, name)(*args)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.sets = {}

    def pipeline(self, transaction=True):
        return Pipeline(self)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)
        self.sets.pop(key, None)

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    def srem(self, key, value):
        self.sets.setdefault(key, set()).discard(value)

    def smembers(self, key):
        return self.sets.get(key, set())

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def ttl(self, key):
        return self.ttls.get(key, -1)


def test_redis_session_is_opaque_rotatable_and_revocable() -> None:
    redis = MemoryRedis()
    now = datetime(2026, 8, 8, 4, 0, tzinfo=UTC)
    store = RedisSessionStore(redis, now=lambda: now)  # type: ignore[arg-type]

    credentials = store.create("user-1")

    assert credentials.session_token not in "".join(redis.values)
    assert store.get(credentials.session_token).user_id == "user-1"  # type: ignore[union-attr]
    assert store.verify_csrf(credentials.session_token, credentials.csrf_token)
    rotated = store.rotate_csrf(credentials.session_token)
    assert rotated and rotated != credentials.csrf_token
    assert store.verify_csrf(credentials.session_token, rotated)

    store.revoke_user("user-1")
    assert store.get(credentials.session_token) is None


def test_absolute_expiry_removes_session() -> None:
    redis = MemoryRedis()
    current = [datetime(2026, 8, 8, 4, 0, tzinfo=UTC)]
    store = RedisSessionStore(
        redis,
        absolute_timeout_seconds=60,
        now=lambda: current[0],  # type: ignore[arg-type]
    )
    credentials = store.create("user-1")
    current[0] += timedelta(seconds=61)

    assert store.get(credentials.session_token) is None
