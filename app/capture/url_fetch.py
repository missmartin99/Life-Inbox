"""Safe HTTP(S) URL fetching with SSRF protections."""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests


MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 10
ALLOWED_SCHEMES = {"http", "https"}


class _HTMLMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts: list[str] = []
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "meta":
            name = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or ""
            ).lower()
            content = attrs_dict.get("content", "").strip()
            if name in {"og:title", "twitter:title", "description", "og:description"}:
                if content:
                    self.meta[name] = content

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif self._skip_depth == 0:
            text = " ".join(data.split())
            if text:
                self.text_parts.append(text)


def _host_is_blocked(hostname: str) -> bool:
    if not hostname or hostname.lower() in {"localhost"}:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve host: {hostname}") from exc

    for info in infos:
        ip_text = info[4][0]
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def validate_public_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError("Only http and https URLs are supported")
    if not parsed.netloc:
        raise ValueError("URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")
    hostname = parsed.hostname
    if hostname is None or _host_is_blocked(hostname):
        raise ValueError("URL host is not allowed")
    return parsed.geturl()


def _validate_redirect_target(url: str) -> str:
    return validate_public_http_url(url)


def extract_article(html: str, source_url: str) -> dict:
    parser = _HTMLMetaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed HTML still yields whatever was parsed.
        pass
    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or "".join(parser.title_parts).strip()
        or source_url
    )
    description = parser.meta.get("og:description") or parser.meta.get(
        "description"
    )
    body = " ".join(parser.text_parts)
    if description and description not in body:
        body = f"{description}\n\n{body}".strip()
    body = body[:50000]
    return {
        "title": title[:500],
        "raw_text": body or title,
        "mime_type": "text/html",
    }


def fetch_url(url: str) -> dict:
    """Fetch a public URL and return extracted title/text metadata."""
    current = validate_public_http_url(url)
    session = requests.Session()
    session.max_redirects = 0

    for _ in range(MAX_REDIRECTS + 1):
        _validate_redirect_target(current)
        response = session.get(
            current,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
            headers={
                "User-Agent": "LifeInboxCapture/1.0 (+local educational project)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                raise ValueError("Redirect missing Location header")
            current = urljoin(current, location)
            response.close()
            continue

        if response.status_code >= 400:
            raise ValueError(f"Upstream returned HTTP {response.status_code}")

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                response.close()
                raise ValueError("Response exceeded size limit")
            chunks.append(chunk)
        response.close()
        raw = b"".join(chunks)
        charset = "utf-8"
        if "charset=" in (response.headers.get("Content-Type") or "").lower():
            charset = (
                response.headers.get("Content-Type", "")
                .lower()
                .split("charset=")[-1]
                .split(";")[0]
                .strip()
                or "utf-8"
            )
        text = raw.decode(charset, errors="replace")
        if content_type and content_type not in {
            "text/html",
            "application/xhtml+xml",
            "text/plain",
        }:
            # Still keep a truncated textual representation.
            extracted = {
                "title": current,
                "raw_text": text[:20000],
                "mime_type": content_type,
            }
        elif content_type == "text/plain":
            extracted = {
                "title": current,
                "raw_text": text[:50000],
                "mime_type": content_type,
            }
        else:
            extracted = extract_article(text, current)
        return {
            "final_url": current,
            "status_code": response.status_code,
            **extracted,
        }

    raise ValueError("Too many redirects")
