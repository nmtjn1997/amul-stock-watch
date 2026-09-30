from amul_watch.cookies import extract_cookie


def test_cookie_from_header_form_stops_at_the_quote() -> None:
    raw = "curl 'https://shop.amul.com/api/x' -H 'Cookie: jsessionid=abc; tid=1' -H 'ms-ga: xyz'"
    assert extract_cookie(raw) == "jsessionid=abc; tid=1"


def test_cookie_from_b_flag() -> None:
    assert extract_cookie("curl 'u' -b 'jsessionid=x; a=b' -H 'x: y'") == "jsessionid=x; a=b"
