"""Perform a small, read-only check of the configured Douban session."""

import sys

import requests

from douban.runtime_config import (
    get_douban_cookie,
    get_douban_user_agent,
    parse_cookie_header,
)


HOME_URL = 'https://movie.douban.com/'


def main():
    raw_cookie = get_douban_cookie()
    cookies = parse_cookie_header(raw_cookie)
    if not cookies:
        print('Cookie configuration: missing or invalid')
        return 2

    session = requests.Session()
    session.headers['User-Agent'] = get_douban_user_agent()
    session.cookies.update(cookies)

    try:
        home_response = session.get(HOME_URL, timeout=20)
    except requests.RequestException as exc:
        print('Connectivity check failed: {}'.format(type(exc).__name__))
        return 3

    response_text = home_response.text
    session_valid = (
        'nav-user-account' in response_text
        and 'accounts.douban.com/passport/login' not in response_text
    )

    print('Homepage HTTP status: {}'.format(home_response.status_code))
    print('Authenticated session: {}'.format('valid' if session_valid else 'not confirmed'))
    if home_response.status_code == 403:
        print('Hint: set DOUBAN_USER_AGENT to the browser User-Agent used to copy the Cookie.')
    return 0 if home_response.ok and session_valid else 1


if __name__ == '__main__':
    sys.exit(main())
