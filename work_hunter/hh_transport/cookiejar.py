from __future__ import annotations

from http.cookiejar import Cookie, CookieJar


HH_TLDS = ("ru", "kz", "uz", "by", "net", "com")


def is_hh_domain(domain: str) -> bool:
    normalized = domain.strip().lower().rstrip(".").lstrip(".")
    if normalized.startswith("israel."):
        return False
    return any(normalized == f"hh.{tld}" or normalized.endswith(f".hh.{tld}") for tld in HH_TLDS)


class HHOnlyCookieJar(CookieJar):
    def set_cookie(self, cookie: Cookie) -> None:
        if not is_hh_domain(cookie.domain):
            return
        super().set_cookie(cookie)

    def as_playwright_cookies(self) -> list[dict[str, object]]:
        cookies: list[dict[str, object]] = []
        for cookie in self:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires or -1,
                    "httpOnly": bool(cookie.has_nonstandard_attr("HttpOnly")),
                    "secure": bool(cookie.secure),
                    "sameSite": "Lax",
                }
            )
        return cookies
