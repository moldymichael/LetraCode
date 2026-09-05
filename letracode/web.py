"""Public, bounded HTTP retrieval with DNS pinning and no ambient credentials."""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

MAX_RESPONSE = 2 * 1024 * 1024


def validate_url(url: str):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise ValueError('Invalid URL; use a public HTTP or HTTPS URL without spaces or control characters.')
    parts = urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('Only public HTTP/HTTPS URLs without credentials are supported.')
    if parts.port not in (None, 80, 443):
        raise ValueError('Web research is limited to standard ports 80 and 443.')
    host = parts.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost','.local','.internal','.lan')) or '.' not in host and ':' not in host:
        raise ValueError('Local and private network addresses are not web research targets.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('Local and private network addresses are not web research targets.')
    return parts


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('This hostname resolves to a private or reserved address.')
    return addresses


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure):
        super().__init__(host, port, timeout=15)
        self.address, self.secure = address, secure

    def connect(self):
        # Pin the validated address rather than letting HTTPConnection resolve again.
        family, socktype, proto, _, address = self.address
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(self.timeout)
        try:
            sock.connect(address)
            if self.secure:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


def fetch_public(url: str, cancel: threading.Event, approve_redirect, max_bytes=MAX_RESPONSE):
    for _ in range(6):
        if cancel.is_set():
            raise ValueError('Cancelled')
        parts = validate_url(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        addresses = public_addresses(parts.hostname, port)
        conn = PinnedHTTP(parts.hostname, port, addresses[0], parts.scheme == 'https')
        try:
            path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
            conn.request('GET', path, headers={'User-Agent':'LetraCode/0.1 (local desktop research)', 'Accept':'text/html, text/plain, application/json, application/xml;q=0.8', 'Accept-Encoding':'identity'})
            response = conn.getresponse()
            if response.status in (301,302,303,307,308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Redirect did not provide a destination.')
                target = urljoin(url, location)
                validate_url(target)
                # Every changed destination requires approval; query leakage remains visible.
                if not approve_redirect(target):
                    raise ValueError('Redirect denied by user.')
                url = target
                continue
            if response.status >= 400:
                raise ValueError(f'Website returned HTTP {response.status}. It may require a browser, sign-in, or retry later.')
            content_type = response.getheader('Content-Type', '').lower()
            if not any(x in content_type for x in ('text/','json','xml')):
                raise ValueError('This URL is not a text page. Download documents yourself and link them locally.')
            if response.getheader('Content-Encoding', 'identity') not in ('identity',''):
                raise ValueError('Website returned unsupported compressed content.')
            chunks, size = [], 0
            deadline = time.monotonic() + 30
            while size <= max_bytes:
                if cancel.is_set() or time.monotonic() > deadline:
                    raise ValueError('Web request cancelled or timed out.')
                chunk = response.read1(min(32768, max_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk); chunks.append(chunk)
            if size > max_bytes:
                raise ValueError('Page exceeds the 2 MiB retrieval limit.')
            raw = b''.join(chunks)
            match = re.search(r'charset=([\w-]+)', content_type)
            try:
                text = raw.decode(match.group(1) if match else 'utf-8', errors='replace')
            except LookupError:
                text = raw.decode('utf-8', errors='replace')
            return {'url':url, 'text':html_to_text(text, url) if 'html' in content_type else text[:50000], 'raw':text}
        finally:
            conn.close()
    raise ValueError('Too many redirects.')


class TextExtractor(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.hidden, self.links = base, [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript','svg','template'):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ('p','div','br','li','h1','h2','h3','tr','pre','section'):
            self.parts.append('\n')
        if tag == 'a':
            href = dict(attrs).get('href','')
            link = urljoin(self.base, href)
            self.links.append(link if link.startswith(('http://','https://')) else '')

    def handle_endtag(self, tag):
        if tag in ('script','style','noscript','svg','template'):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == 'a' and self.links:
            link = self.links.pop()
            if link:
                self.parts.append(f' [{link}]')
        if tag in ('p','div','li','h1','h2','h3','tr','pre','section'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(html, base):
    parser = TextExtractor(base)
    parser.feed(html)
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())[:50000]


class SearchParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.current, self.capture = [], None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class','').split()
        if tag == 'a' and ('result__a' in classes or 'result-link' in classes):
            href = urljoin('https://duckduckgo.com', attrs.get('href',''))
            parsed = urlsplit(href)
            target = parse_qs(parsed.query).get('uddg', [href])[0]
            if target.startswith(('http://','https://')):
                self.current = {'title':'','url':target,'snippet':''}
                self.results.append(self.current)
                self.capture = 'title'
        elif 'result__snippet' in classes or 'result-snippet' in classes:
            self.capture = 'snippet'

    def handle_endtag(self, tag):
        if tag in ('a','td'):
            self.capture = None

    def handle_data(self, text):
        if self.current and self.capture:
            self.current[self.capture] += text


def search_results(html):
    parser = SearchParser()
    parser.feed(html)
    return [{k:v.strip() for k,v in r.items()} for r in parser.results[:8]]


def search_url(query):
    return 'https://html.duckduckgo.com/html/?' + urlencode({'q':query})
