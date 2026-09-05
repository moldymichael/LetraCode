import pytest
from letracode.web import validate_url, html_to_text, search_results


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://127.0.0.1:11434','http://[::1]','http://169.254.169.254/latest','https://user:password@example.com','http://10.0.0.1','http://localhost','https://example.com:22','https://example.com/\nHost: x'])
def test_private_or_unsafe_web_target_is_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_html_extraction_removes_active_content_and_keeps_links():
    result = html_to_text('<h1>Documentation</h1><script>steal()</script><p>Read <a href="/guide">the guide</a>.</p>', 'https://example.com/docs')
    assert 'Documentation' in result
    assert 'steal()' not in result
    assert 'https://example.com/guide' in result


def test_search_preserves_destination_urls():
    html = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdoc.qt.io%2F">Qt docs</a><a class="result__snippet">Official documentation</a>'
    result = search_results(html)
    assert result[0]['url'] == 'https://doc.qt.io/'
    assert result[0]['title'] == 'Qt docs'


def test_mixed_public_private_dns_is_rejected(monkeypatch):
    import socket
    from letracode.web import public_addresses
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='private'):
        public_addresses('example.com',443)


def test_http_fetch_uses_pinned_address_and_denied_redirect_stops(monkeypatch):
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from letracode import web
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path,self.headers.get('Host'),self.headers.get('Authorization')))
            if self.path=='/redirect':
                self.send_response(302);self.send_header('Location','https://elsewhere.example/next');self.end_headers()
            else:
                body=b'<h1>Verified content</h1>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    peer=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=peer.serve_forever,daemon=True).start()
    # Only this test's resolver returns the local fixture. Production resolver is tested separately.
    monkeypatch.setattr(web,'public_addresses',lambda host,port:[(socket.AF_INET,socket.SOCK_STREAM,6,'',peer.server_address)])
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    try:
        result=web.fetch_public('http://source.example/page',threading.Event(),lambda _:False)
        assert result['text']=='Verified content'
        assert requests==[('/page','source.example',None)]
        destinations=[]
        def deny(url): destinations.append(url); return False
        with pytest.raises(ValueError,match='denied'):
            web.fetch_public('http://source.example/redirect',threading.Event(),deny)
        assert destinations==['https://elsewhere.example/next']
        assert len(requests)==2
    finally:
        peer.shutdown();peer.server_close()
