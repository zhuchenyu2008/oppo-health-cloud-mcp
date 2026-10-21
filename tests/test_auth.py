from oppo_health.auth import LoginRateLimiter, SessionStore, hash_password, verify_password


def test_password_and_session_lifecycle():
    encoded = hash_password("secret")
    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)
    store = SessionStore(ttl_seconds=60)
    session = store.create("admin")
    assert store.get(session.token).username == "admin"
    store.revoke(session.token)
    assert store.get(session.token) is None


def test_login_limiter_blocks_after_five_failures():
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900)
    for _ in range(5):
        assert limiter.allowed("127.0.0.1")
        limiter.failed("127.0.0.1")
    assert not limiter.allowed("127.0.0.1")
    assert limiter.retry_after("127.0.0.1") > 0
    limiter.succeeded("127.0.0.1")
    assert limiter.allowed("127.0.0.1")