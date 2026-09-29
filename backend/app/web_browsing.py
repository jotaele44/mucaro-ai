from __future__ import annotations

import base64
import functools
import hashlib
import http.client
from io import BytesIO
import ipaddress
import re
import socket
import urllib.error
import urllib.robotparser
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urlparse

from .config import Settings, settings
from .utils import read_json, stable_id, utc_now, write_json

WEB_SNAPSHOT_SCHEMA_VERSION = "mucaro_web_snapshot_v1"
WEB_BROWSE_SESSION_SCHEMA_VERSION = "mucaro_web_browse_session_v1"
WEB_SNAPSHOT_DIR = "web_snapshots"
WEB_BROWSE_SESSION_DIR = "web_browse_sessions"
WEB_SNAPSHOT_LEDGER_NAME = "web_snapshot_ledger.json"
DEFAULT_WEB_TIMEOUT_SECONDS = 15
DEFAULT_MAX_WEB_BYTES = 1_000_000
DEFAULT_ROBOTS_MAX_BYTES = 100_000
MAX_URL_LENGTH = 2048
TEXTUAL_CONTENT_MARKERS = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-javascript",
    "application/rss+xml",
    "application/atom+xml",
)


class WebBrowsingError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class WebFetchPolicy:
    timeout_seconds: int = DEFAULT_WEB_TIMEOUT_SECONDS
    max_bytes: int = DEFAULT_MAX_WEB_BYTES
    allow_private_hosts: bool = False
    respect_robots: bool = True
    snapshot_http_errors: bool = False
    cite_http_error_pages: bool = False
    cache_mode: str = "immutable_snapshot_only"


@dataclass(frozen=True)
class WebBrowseSessionPolicy:
    max_pages: int = 5
    require_same_host: bool = False
    discovery_mode: str = "operator_supplied_urls"
    search_enabled: bool = False
    automated_link_following: bool = False


class _HTMLTraceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.headings: list[str] = []
        self.link_samples: list[str] = []
        self.meta_names: list[str] = []
        self.body_text_blocks: list[str] = []
        self.body_text_parts: list[str] = []
        self._in_title = False
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._in_body = False
        self._skip_depth = 0
        self.link_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value for name, value in attrs if value}
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag == "body":
            self._in_body = True
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
            self._heading_parts = []
        elif tag == "a" and attr_map.get("href"):
            self.link_count += 1
            if len(self.link_samples) < 5:
                self.link_samples.append(str(attr_map["href"]))
        elif tag == "meta":
            name = attr_map.get("name") or attr_map.get("property")
            if name and len(self.meta_names) < 12:
                self.meta_names.append(str(name))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        elif tag == "body":
            self._in_body = False
        elif self._heading_tag == tag:
            heading = " ".join(" ".join(self._heading_parts).split())
            if heading and len(self.headings) < 12:
                self.headings.append(heading)
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._heading_tag:
            self._heading_parts.append(text)
        if self._in_body and not self._skip_depth:
            self.body_text_parts.append(text)
            if len(self.body_text_blocks) < 24:
                self.body_text_blocks.append(text)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def body_text(self) -> str:
        return " ".join(self.body_text_parts).strip()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _parse_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_private_host(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "0.0.0.0"} or lowered.endswith(".localhost") or lowered.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolved_private_addresses(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebBrowsingError("ERROR_WEB_DNS_FAILED", f"DNS lookup failed for {hostname}: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if _is_private_host(address):
            addresses.append(address)
    return sorted(set(addresses))


def _pinned_create_connection(
    address: tuple[str, int],
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
    source_address: tuple[str, int] | None = None,
    *,
    allow_private_hosts: bool,
) -> socket.socket:
    """Resolve and vet addresses at connect time (closes the DNS-rebinding window, SD-011).

    validate_web_fetch_url checks DNS before the request; without this, urllib would
    resolve again when connecting and a rebinding resolver could return a private
    address the second time. Every address is re-checked here and the socket connects
    only to a vetted address.
    """
    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebBrowsingError("ERROR_WEB_DNS_FAILED", f"DNS lookup failed for {host}: {exc}") from exc
    if not allow_private_hosts:
        private = sorted({str(info[4][0]) for info in infos if _is_private_host(str(info[4][0]))})
        if private:
            raise WebBrowsingError(
                "ERROR_WEB_PRIVATE_HOST_BLOCKED",
                f"{host} resolved to private, local, loopback, or link-local addresses at connect time: {', '.join(private)}.",
            )
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise last_error or OSError(f"no usable address for {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, pin_dns: bool, allow_private_hosts: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if pin_dns:
            self._create_connection = functools.partial(_pinned_create_connection, allow_private_hosts=allow_private_hosts)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, pin_dns: bool, allow_private_hosts: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if pin_dns:
            self._create_connection = functools.partial(_pinned_create_connection, allow_private_hosts=allow_private_hosts)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, allow_private_hosts: bool) -> None:
        super().__init__()
        self.allow_private_hosts = allow_private_hosts

    def http_open(self, req):  # type: ignore[override]
        # Through a configured proxy the proxy resolves the target; pinning applies to direct connections.
        factory = functools.partial(
            _PinnedHTTPConnection, pin_dns=not req.has_proxy(), allow_private_hosts=self.allow_private_hosts
        )
        return self.do_open(factory, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, allow_private_hosts: bool) -> None:
        super().__init__()
        self.allow_private_hosts = allow_private_hosts

    def https_open(self, req):  # type: ignore[override]
        direct = not req.has_proxy() and not getattr(req, "_tunnel_host", None)
        factory = functools.partial(_PinnedHTTPSConnection, pin_dns=direct, allow_private_hosts=self.allow_private_hosts)
        return self.do_open(factory, req, context=self._context)


def _governed_opener(policy: "WebFetchPolicy", *handlers: Any) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _PinnedHTTPHandler(policy.allow_private_hosts),
        _PinnedHTTPSHandler(policy.allow_private_hosts),
        *handlers,
    )


def validate_web_fetch_url(url: str, *, allow_private_hosts: bool = False) -> dict[str, Any]:
    if not isinstance(url, str) or not url.strip():
        raise WebBrowsingError("ERROR_WEB_URL_REQUIRED", "A non-empty URL is required.")
    raw_url = url.strip()
    if len(raw_url) > MAX_URL_LENGTH:
        raise WebBrowsingError("ERROR_WEB_URL_TOO_LONG", f"URL exceeds {MAX_URL_LENGTH} characters.")
    defragged, fragment = urldefrag(raw_url)
    parsed = urlparse(defragged)
    if parsed.scheme not in {"http", "https"}:
        raise WebBrowsingError("ERROR_WEB_URL_SCHEME", "Only http and https URLs are supported.")
    if not parsed.hostname:
        raise WebBrowsingError("ERROR_WEB_URL_HOST_REQUIRED", "URL must include a hostname.")
    if parsed.username or parsed.password:
        raise WebBrowsingError("ERROR_WEB_URL_CREDENTIALS", "URL credentials are not allowed.")
    if _is_private_host(parsed.hostname) and not allow_private_hosts:
        raise WebBrowsingError("ERROR_WEB_PRIVATE_HOST_BLOCKED", "Private, local, loopback, and link-local hosts are blocked.")
    if not allow_private_hosts:
        private_addresses = _resolved_private_addresses(parsed.hostname)
        if private_addresses:
            raise WebBrowsingError(
                "ERROR_WEB_PRIVATE_HOST_BLOCKED",
                f"Host resolves to private, local, loopback, or link-local addresses: {', '.join(private_addresses)}.",
            )
    return {
        "requested_url": raw_url,
        "fetch_url": defragged,
        "fragment_removed": bool(fragment),
        "scheme": parsed.scheme,
        "hostname": parsed.hostname.lower(),
    }


def _decode_preview(content: bytes, content_type: str, *, limit: int = 4000) -> tuple[str, list[str], str]:
    warnings: list[str] = []
    lowered = content_type.lower()
    if not any(marker in lowered for marker in TEXTUAL_CONTENT_MARKERS):
        return "", ["WEB_CONTENT_NOT_TEXT_PREVIEW_SKIPPED"], ""
    if "charset=" in lowered:
        charset = lowered.split("charset=", 1)[1].split(";", 1)[0].strip()
    else:
        charset = "utf-8"
        warnings.append("WEB_CHARSET_DEFAULTED_UTF8")
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        charset = "utf-8"
        warnings.append("WEB_CHARSET_UNKNOWN_USED_UTF8")
        text = content.decode(charset, errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        warnings.append("WEB_TEXT_PREVIEW_TRUNCATED")
        text = text[:limit]
    return text, warnings, charset


def _parser_trace(content: bytes, content_type: str, text_preview: str, charset: str) -> dict[str, Any]:
    lowered = content_type.lower()
    trace: dict[str, Any] = {
        "parser_name": "web_snapshot_preview_v1",
        "content_kind": "textual" if any(marker in lowered for marker in TEXTUAL_CONTENT_MARKERS) else "non_text",
        "content_type": content_type,
        "text_preview_length": len(text_preview),
        "trace_warnings": [],
    }
    if "application/pdf" in lowered:
        trace["content_kind"] = "pdf"
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            trace["trace_warnings"].append("PDF_TRACE_PARSER_UNAVAILABLE_INSTALL_PYPDF")
            return trace
        try:
            reader = PdfReader(BytesIO(content))
        except Exception as exc:
            trace["trace_warnings"].append(f"PDF_TRACE_OPEN_FAILED:{exc.__class__.__name__}")
            return trace
        page_count = len(reader.pages)
        pages_with_text = 0
        pages_with_empty_text = 0
        failed_pages = 0
        for idx, page in enumerate(reader.pages):
            try:
                if (page.extract_text() or "").strip():
                    pages_with_text += 1
                else:
                    pages_with_empty_text += 1
                    if len(trace["trace_warnings"]) < 12:
                        trace["trace_warnings"].append(f"PDF_TRACE_PAGE_EMPTY:{idx + 1}")
            except Exception:
                failed_pages += 1
                if len(trace["trace_warnings"]) < 12:
                    trace["trace_warnings"].append(f"PDF_TRACE_PAGE_PARSE_FAILED:{idx + 1}")
        trace.update(
            {
                "pdf_page_count": page_count,
                "pdf_pages_with_text": pages_with_text,
                "pdf_pages_with_empty_text": pages_with_empty_text,
                "pdf_failed_pages": failed_pages,
            }
        )
        return trace
    if "html" not in lowered:
        return trace
    try:
        html_text = content.decode(charset or "utf-8", errors="replace")
    except LookupError:
        html_text = content.decode("utf-8", errors="replace")
        trace["trace_warnings"].append("HTML_TRACE_CHARSET_UNKNOWN_USED_UTF8")
    parser = _HTMLTraceParser()
    parser.feed(html_text)
    parser.close()
    trace.update(
        {
            "content_kind": "html",
            "html_title": parser.title,
            "html_heading_count": len(parser.headings),
            "html_headings": parser.headings,
            "html_link_count": parser.link_count,
            "html_link_samples": parser.link_samples,
            "html_meta_names": parser.meta_names,
            "html_body_text_preview": parser.body_text[:4000],
            "html_visible_text_length": len(parser.body_text),
            "html_text_block_count": len(parser.body_text_blocks),
            "html_text_blocks": parser.body_text_blocks,
        }
    )
    if not parser.title:
        trace["trace_warnings"].append("HTML_TRACE_TITLE_MISSING")
    if not parser.headings:
        trace["trace_warnings"].append("HTML_TRACE_HEADINGS_MISSING")
    if not parser.body_text:
        trace["trace_warnings"].append("HTML_TRACE_BODY_TEXT_MISSING")
    return trace


def _build_cache_policy(headers: dict[str, str], policy: WebFetchPolicy) -> dict[str, Any]:
    values = {
        "cache_control": headers.get("Cache-Control"),
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "expires": headers.get("Expires"),
        "vary": headers.get("Vary"),
    }
    header_present = any(value for value in values.values())
    return {
        **values,
        "cache_mode": policy.cache_mode,
        "header_status": "CACHE_HEADERS_PRESENT" if header_present else "CACHE_HEADERS_ABSENT",
        "reuse_allowed": False,
        "revalidation_required_before_reuse": True,
        "warnings": [] if header_present else ["CACHE_HEADERS_ABSENT_IMMUTABLE_SNAPSHOT_ONLY"],
    }


def _snapshot_ledger_path(cfg: Settings) -> Any:
    return cfg.export_dir / WEB_SNAPSHOT_DIR / WEB_SNAPSHOT_LEDGER_NAME


def _browse_session_path(cfg: Settings, session_id: str) -> Any:
    return cfg.export_dir / WEB_BROWSE_SESSION_DIR / f"{session_id}.json"


def _append_snapshot_ledger_entry(cfg: Settings, snapshot: dict[str, Any], snapshot_path: str, snapshot_sha256: str) -> dict[str, Any]:
    ledger_path = _snapshot_ledger_path(cfg)
    ledger = read_json(
        ledger_path,
        {
            "schema_version": "mucaro_web_snapshot_ledger_v1",
            "entries": [],
        },
    )
    entry = {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_path": snapshot_path,
        "snapshot_sha256": snapshot_sha256,
        "requested_url": snapshot["requested_url"],
        "final_url": snapshot["final_url"],
        "retrieval_utc": snapshot["retrieval_utc"],
        "http_status": snapshot["http_status"],
        "content_type": snapshot["content_type"],
        "content_sha256": snapshot["content_sha256"],
        "content_size_bytes": snapshot["content_size_bytes"],
    }
    entries = list(ledger.get("entries") or [])
    entries.append(entry)
    ledger = {
        "schema_version": "mucaro_web_snapshot_ledger_v1",
        "entry_count": len(entries),
        "updated_at": utc_now(),
        "entries": entries,
    }
    write_json(ledger_path, ledger)
    return {"path": str(ledger_path), "entry": entry, "entry_count": len(entries)}


def _robots_url(fetch_url: str) -> str:
    parsed = urlparse(fetch_url)
    netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}/robots.txt"


def _check_robots_policy(fetch_url: str, policy: WebFetchPolicy) -> dict[str, Any]:
    checked_at = utc_now()
    robots_url = _robots_url(fetch_url)
    if not policy.respect_robots:
        return {
            "status": "ROBOTS_SKIPPED_BY_POLICY",
            "robots_url": robots_url,
            "allowed": True,
            "checked_at": checked_at,
            "warnings": ["ROBOTS_POLICY_DISABLED"],
        }
    request = urllib.request.Request(
        robots_url,
        headers={"User-Agent": "MucaroAI/0.1 local-first governed-fetch"},
        method="GET",
    )
    opener = _governed_opener(policy, _GovernedRedirectHandler(policy, [], check_target_robots=False))
    try:
        with opener.open(request, timeout=policy.timeout_seconds) as response:
            content = response.read(DEFAULT_ROBOTS_MAX_BYTES + 1)
            if len(content) > DEFAULT_ROBOTS_MAX_BYTES:
                raise WebBrowsingError("ERROR_WEB_ROBOTS_TOO_LARGE", f"robots.txt exceeded {DEFAULT_ROBOTS_MAX_BYTES} bytes.")
            robots_status = int(getattr(response, "status", 200))
            robots_final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise WebBrowsingError("ERROR_WEB_ROBOTS_DISALLOWED", f"robots.txt returned HTTP {exc.code}.") from exc
        return {
            "status": "ROBOTS_UNAVAILABLE",
            "robots_url": robots_url,
            "allowed": True,
            "checked_at": checked_at,
            "http_status": exc.code,
            "warnings": [f"ROBOTS_HTTP_{exc.code}_ALLOW_BY_DEFAULT"],
        }
    except urllib.error.URLError as exc:
        return {
            "status": "ROBOTS_UNAVAILABLE",
            "robots_url": robots_url,
            "allowed": True,
            "checked_at": checked_at,
            "warnings": [f"ROBOTS_FETCH_FAILED_ALLOW_BY_DEFAULT:{exc.reason}"],
        }
    parser = urllib.robotparser.RobotFileParser(robots_url)
    parser.parse(content.decode("utf-8", errors="replace").splitlines())
    allowed = bool(parser.can_fetch("MucaroAI", fetch_url))
    result = {
        "status": "ROBOTS_ALLOWED" if allowed else "ROBOTS_DISALLOWED",
        "robots_url": robots_url,
        "robots_final_url": robots_final_url,
        "allowed": allowed,
        "checked_at": checked_at,
        "http_status": robots_status,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_size_bytes": len(content),
        "warnings": [],
    }
    if not allowed:
        raise WebBrowsingError("ERROR_WEB_ROBOTS_DISALLOWED", f"robots.txt disallows fetch: {fetch_url}")
    return result


def _read_bounded_response(response: Any, max_bytes: int) -> bytes:
    content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise WebBrowsingError("ERROR_WEB_RESPONSE_TOO_LARGE", f"Response exceeded {max_bytes} bytes.")
    return content


class _GovernedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        policy: WebFetchPolicy,
        redirect_chain: list[dict[str, Any]],
        *,
        check_target_robots: bool = True,
    ) -> None:
        self.policy = policy
        self.redirect_chain = redirect_chain
        self.check_target_robots = check_target_robots

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        checked = validate_web_fetch_url(
            redirected.full_url,
            allow_private_hosts=self.policy.allow_private_hosts,
        )
        target_robots_policy = (
            _check_robots_policy(str(checked["fetch_url"]), self.policy)
            if self.check_target_robots
            else {
                "status": "ROBOTS_NOT_CHECKED_FOR_INTERNAL_REQUEST",
                "allowed": True,
                "warnings": ["ROBOTS_TARGET_CHECK_SKIPPED_FOR_INTERNAL_REQUEST"],
            }
        )
        self.redirect_chain.append(
            {
                "from_url": req.full_url,
                "to_url": checked["fetch_url"],
                "http_status": int(code),
                "target_robots_policy": target_robots_policy,
            }
        )
        return redirected


def fetch_web_snapshot(
    url: str,
    *,
    cfg: Settings = settings,
    policy: WebFetchPolicy | None = None,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    policy = policy or WebFetchPolicy()
    checked = validate_web_fetch_url(url, allow_private_hosts=policy.allow_private_hosts)
    requested_at = utc_now()
    redirect_chain: list[dict[str, Any]] = []
    opener = _governed_opener(policy, _GovernedRedirectHandler(policy, redirect_chain))
    robots_policy = _check_robots_policy(str(checked["fetch_url"]), policy)
    request = urllib.request.Request(
        str(checked["fetch_url"]),
        headers={"User-Agent": "MucaroAI/0.1 local-first governed-fetch"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=policy.timeout_seconds) as response:
            content = _read_bounded_response(response, policy.max_bytes)
            final_url = response.geturl()
            http_status = int(getattr(response, "status", 200))
            headers = dict(response.headers.items())
            http_error_policy = {
                "is_http_error": False,
                "snapshot_allowed": False,
                "citation_allowed": False,
            }
    except WebBrowsingError:
        raise
    except urllib.error.HTTPError as exc:
        if not policy.snapshot_http_errors:
            raise WebBrowsingError("ERROR_WEB_HTTP_STATUS", f"HTTP {exc.code}: {exc.reason}") from exc
        content = _read_bounded_response(exc, policy.max_bytes)
        final_url = exc.geturl()
        http_status = int(exc.code)
        headers = dict(exc.headers.items())
        http_error_policy = {
            "is_http_error": True,
            "snapshot_allowed": True,
            "citation_allowed": bool(policy.cite_http_error_pages),
            "reason": str(exc.reason),
        }
    except urllib.error.URLError as exc:
        raise WebBrowsingError("ERROR_WEB_FETCH_FAILED", str(exc.reason)) from exc
    retrieved_at = utc_now()
    content_type = headers.get("Content-Type", "application/octet-stream")
    cache_policy = _build_cache_policy(headers, policy)
    content_sha256 = hashlib.sha256(content).hexdigest()
    text_preview, parser_warnings, charset = _decode_preview(content, content_type)
    parser_trace = _parser_trace(content, content_type, text_preview, charset)
    if parser_trace.get("content_kind") == "html" and parser_trace.get("html_body_text_preview"):
        text_preview = str(parser_trace["html_body_text_preview"])
        parser_trace["text_preview_length"] = len(text_preview)
    snapshot_id = stable_id("websnap", str(checked["requested_url"]), final_url, content_sha256, retrieved_at)
    snapshot = {
        "schema_version": WEB_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_kind": "http_error_page" if http_error_policy["is_http_error"] else "web_page",
        "requested_url": checked["requested_url"],
        "fetch_url": checked["fetch_url"],
        "final_url": final_url,
        "redirect_chain": redirect_chain,
        "fragment_removed": checked["fragment_removed"],
        "retrieval_started_utc": requested_at,
        "retrieval_utc": retrieved_at,
        "http_status": http_status,
        "content_type": content_type,
        "cache_policy": cache_policy,
        "robots_policy": robots_policy,
        "http_error_policy": http_error_policy,
        "content_sha256": content_sha256,
        "content_size_bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "parser_name": "web_snapshot_preview_v1",
        "parser_trace": parser_trace,
        "parser_warnings": parser_warnings,
        "charset": charset,
        "text_preview": text_preview,
        "policy": {
            "timeout_seconds": policy.timeout_seconds,
            "max_bytes": policy.max_bytes,
            "allow_private_hosts": policy.allow_private_hosts,
            "respect_robots": policy.respect_robots,
            "snapshot_http_errors": policy.snapshot_http_errors,
            "cite_http_error_pages": policy.cite_http_error_pages,
            "cache_mode": policy.cache_mode,
        },
    }
    path = cfg.export_dir / WEB_SNAPSHOT_DIR / f"{snapshot_id}.json"
    write_json(path, snapshot)
    snapshot_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    ledger = _append_snapshot_ledger_entry(cfg, snapshot, str(path), snapshot_sha256)
    return {
        "status": "WEB_FETCH_SNAPSHOT_OK",
        "snapshot": snapshot,
        "snapshot_path": str(path),
        "snapshot_sha256": snapshot_sha256,
        "ledger_path": ledger["path"],
        "ledger_entry_count": ledger["entry_count"],
    }


def build_web_fetch_policy(arguments: dict[str, Any]) -> WebFetchPolicy:
    snapshot_http_errors = _parse_bool(arguments.get("snapshot_http_errors"))
    return WebFetchPolicy(
        timeout_seconds=_parse_positive_int(arguments.get("timeout_seconds"), DEFAULT_WEB_TIMEOUT_SECONDS),
        max_bytes=_parse_positive_int(arguments.get("max_bytes"), DEFAULT_MAX_WEB_BYTES),
        allow_private_hosts=_parse_bool(arguments.get("allow_private_hosts")),
        respect_robots=not _parse_bool(arguments.get("ignore_robots")),
        snapshot_http_errors=snapshot_http_errors,
        cite_http_error_pages=snapshot_http_errors and _parse_bool(arguments.get("cite_http_error_pages")),
        cache_mode="immutable_snapshot_only",
    )


def build_web_browse_session_policy(arguments: dict[str, Any]) -> WebBrowseSessionPolicy:
    return WebBrowseSessionPolicy(
        max_pages=_parse_positive_int(arguments.get("max_pages"), 5),
        require_same_host=_parse_bool(arguments.get("require_same_host")),
    )


def _select_session_urls(
    urls: list[str],
    *,
    fetch_policy: WebFetchPolicy,
    session_policy: WebBrowseSessionPolicy,
) -> tuple[list[str], list[dict[str, Any]]]:
    selected: list[str] = []
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    seed_host: str | None = None
    for index, url in enumerate(urls):
        try:
            checked = validate_web_fetch_url(url, allow_private_hosts=fetch_policy.allow_private_hosts)
        except WebBrowsingError as exc:
            decisions.append(
                {
                    "candidate_index": index,
                    "requested_url": url,
                    "decision": "REJECTED",
                    "reason": exc.status,
                    "detail": exc.detail,
                }
            )
            continue
        fetch_url = str(checked["fetch_url"])
        if fetch_url in seen:
            decisions.append(
                {
                    "candidate_index": index,
                    "requested_url": checked["requested_url"],
                    "fetch_url": fetch_url,
                    "decision": "REJECTED",
                    "reason": "DUPLICATE_URL",
                }
            )
            continue
        if seed_host is None:
            seed_host = str(checked["hostname"])
        if session_policy.require_same_host and checked["hostname"] != seed_host:
            decisions.append(
                {
                    "candidate_index": index,
                    "requested_url": checked["requested_url"],
                    "fetch_url": fetch_url,
                    "hostname": checked["hostname"],
                    "decision": "REJECTED",
                    "reason": "CROSS_HOST_REJECTED",
                    "required_hostname": seed_host,
                }
            )
            continue
        if len(selected) >= session_policy.max_pages:
            decisions.append(
                {
                    "candidate_index": index,
                    "requested_url": checked["requested_url"],
                    "fetch_url": fetch_url,
                    "hostname": checked["hostname"],
                    "decision": "REJECTED",
                    "reason": "MAX_PAGES_EXCEEDED",
                    "max_pages": session_policy.max_pages,
                }
            )
            continue
        seen.add(fetch_url)
        selected.append(fetch_url)
        decisions.append(
            {
                "candidate_index": index,
                "requested_url": checked["requested_url"],
                "fetch_url": fetch_url,
                "hostname": checked["hostname"],
                "decision": "SELECTED",
                "reason": "OPERATOR_SUPPLIED_URL_SELECTED",
            }
        )
    return selected, decisions


def fetch_web_browse_session(
    urls: list[str],
    *,
    purpose: str = "",
    cfg: Settings = settings,
    fetch_policy: WebFetchPolicy | None = None,
    session_policy: WebBrowseSessionPolicy | None = None,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    fetch_policy = fetch_policy or WebFetchPolicy()
    session_policy = session_policy or WebBrowseSessionPolicy()
    started_at = utc_now()
    selected_urls, selection_decisions = _select_session_urls(
        urls,
        fetch_policy=fetch_policy,
        session_policy=session_policy,
    )
    if not selected_urls:
        raise WebBrowsingError("ERROR_WEB_BROWSE_SESSION_NO_SELECTED_URLS", "No URLs passed the session selection policy.")
    session_id = stable_id("websession", purpose, *selected_urls, started_at)
    fetches: list[dict[str, Any]] = []
    for url in selected_urls:
        try:
            result = fetch_web_snapshot(url, cfg=cfg, policy=fetch_policy)
        except WebBrowsingError as exc:
            fetches.append(
                {
                    "url": url,
                    "status": exc.status,
                    "detail": exc.detail,
                    "snapshot_id": None,
                    "snapshot_path": None,
                    "snapshot_sha256": None,
                }
            )
            continue
        snapshot = result.get("snapshot", {})
        fetches.append(
            {
                "url": url,
                "status": result.get("status"),
                "snapshot_id": snapshot.get("snapshot_id"),
                "snapshot_path": result.get("snapshot_path"),
                "snapshot_sha256": result.get("snapshot_sha256"),
                "final_url": snapshot.get("final_url"),
                "http_status": snapshot.get("http_status"),
                "content_sha256": snapshot.get("content_sha256"),
            }
        )
    successful_fetches = [fetch for fetch in fetches if fetch.get("status") == "WEB_FETCH_SNAPSHOT_OK"]
    completed_at = utc_now()
    status = "WEB_BROWSE_SESSION_OK" if len(successful_fetches) == len(selected_urls) else "WEB_BROWSE_SESSION_PARTIAL"
    receipt = {
        "schema_version": WEB_BROWSE_SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "status": status,
        "purpose": purpose,
        "started_at": started_at,
        "completed_at": completed_at,
        "browser_discovery_policy": {
            "discovery_mode": session_policy.discovery_mode,
            "search_enabled": session_policy.search_enabled,
            "automated_link_following": session_policy.automated_link_following,
            "candidate_source": "operator_supplied_urls",
            "selection_basis": "validated_url_order",
        },
        "session_policy": {
            "max_pages": session_policy.max_pages,
            "require_same_host": session_policy.require_same_host,
        },
        "fetch_policy": {
            "timeout_seconds": fetch_policy.timeout_seconds,
            "max_bytes": fetch_policy.max_bytes,
            "allow_private_hosts": fetch_policy.allow_private_hosts,
            "respect_robots": fetch_policy.respect_robots,
            "snapshot_http_errors": fetch_policy.snapshot_http_errors,
            "cite_http_error_pages": fetch_policy.cite_http_error_pages,
            "cache_mode": fetch_policy.cache_mode,
        },
        "candidate_count": len(urls),
        "selected_count": len(selected_urls),
        "successful_fetch_count": len(successful_fetches),
        "selection_decisions": selection_decisions,
        "fetches": fetches,
        "replay": {
            "tool_name": "web.fetch_session",
            "arguments": {
                "urls": urls,
                "purpose": purpose,
                "max_pages": session_policy.max_pages,
                "require_same_host": session_policy.require_same_host,
                "timeout_seconds": fetch_policy.timeout_seconds,
                "max_bytes": fetch_policy.max_bytes,
                "allow_private_hosts": fetch_policy.allow_private_hosts,
                "ignore_robots": not fetch_policy.respect_robots,
                "snapshot_http_errors": fetch_policy.snapshot_http_errors,
                "cite_http_error_pages": fetch_policy.cite_http_error_pages,
            },
        },
        "warnings": [] if successful_fetches else ["WEB_BROWSE_SESSION_NO_SUCCESSFUL_FETCHES"],
    }
    path = _browse_session_path(cfg, session_id)
    write_json(path, receipt)
    receipt_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": status,
        "session_id": session_id,
        "receipt_path": str(path),
        "receipt_sha256": receipt_sha256,
        "candidate_count": len(urls),
        "selected_count": len(selected_urls),
        "successful_fetch_count": len(successful_fetches),
        "receipt": receipt,
    }
