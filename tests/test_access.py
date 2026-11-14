import time

from ledpanels import access


def test_limiter_burst_then_refill():
    rl = access.RateLimiter()
    assert rl.allow(("a", "x"), 10, 2) and rl.allow(("a", "x"), 10, 2)
    assert not rl.allow(("a", "x"), 10, 2)
    assert rl.allow(("b", "x"), 10, 2)  # other clients are independent
    time.sleep(0.15)
    assert rl.allow(("a", "x"), 10, 2)


def test_login_and_lockout():
    a = access.Access(admin_pin="1234")
    assert a.login("9.9.9.9", "0000") is None
    token = a.login("9.9.9.9", "1234")
    assert token and a.role("9.9.9.9", token) == "admin"
    a.logout(token)
    assert a.role("9.9.9.9", token) is None
    for _ in range(access.MAX_FAILURES):
        a.login("8.8.8.8", "bad")
    try:
        a.login("8.8.8.8", "1234")
        assert False, "should be locked out"
    except ValueError:
        pass


def test_no_pin_means_no_remote_admin():
    a = access.Access(admin_pin="")
    assert a.login("1.2.3.4", "") is None


def test_roles_and_guest_window():
    a = access.Access(admin_pin="x")
    assert a.role("127.0.0.1", None) == "admin"
    assert a.role("10.0.0.5", None) is None
    a.open_guests(None)
    assert a.role("10.0.0.5", None) == "guest"
    a.close_guests()
    assert a.role("10.0.0.5", None) is None
    a.open_guests(0.001)  # 60 ms
    time.sleep(0.15)
    assert not a.guests_open()
    assert a.guest_state()["open"] is False
