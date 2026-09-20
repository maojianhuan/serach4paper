"""Exercise the timed DBLP challenge without relying on the live website."""
import io
import unittest
from unittest.mock import Mock, patch
from code import fetch_openreview_accepted as c

URL = 'https://dblp.org/search/publ/api?q=test&format=json'
CHALLENGE = b'''<script id="anubis_challenge">{}</script>
<meta content='2; url=/.within.website/x/cmd/anubis/api/pass-challenge?id=test&amp;redir=%2Fsearch' http-equiv="refresh">'''


def response(body, url=URL):
    value = io.BytesIO(body)
    value.headers = {'Content-Type': 'text/html' if body.startswith(b'<') else 'application/json'}
    value.geturl = lambda: url
    return value


class DBLPAccessTests(unittest.TestCase):
    def test_timed_challenge_and_reused_session(self):
        opener = Mock()
        opener.open.side_effect = [response(CHALLENGE), response(b'{"ok":true}'), response(b'{"page":2}')]
        progress = Mock()
        with patch.object(c, 'build_opener', return_value=opener) as build, patch.object(c.time, 'sleep') as sleep:
            with c.dblp_access_session(progress):
                raw, headers = c.request_bytes(URL)
                self.assertEqual(raw, b'{"ok":true}')
                self.assertEqual(headers['url'], URL)
                c.request_bytes(URL + '&f=1')
            self.assertEqual(build.call_count, 1)
            sleep.assert_called_once_with(2)
        self.assertIn('&redir=', opener.open.call_args_list[1].args[0].full_url)
        self.assertEqual(progress.call_count, 2)

    def test_unhandled_challenge_does_not_retry_or_return_html(self):
        opener = Mock()
        opener.open.return_value = response(b'<script id="anubis_challenge">{}</script>')
        with patch.object(c, 'build_opener', return_value=opener), patch.object(c.time, 'sleep') as sleep:
            with c.dblp_access_session(), self.assertRaisesRegex(RuntimeError, '暂不支持'):
                c.request_bytes(URL)
            sleep.assert_not_called()
        self.assertEqual(opener.open.call_count, 1)

    def test_repeated_challenges_stop(self):
        opener = Mock()
        opener.open.side_effect = [response(CHALLENGE) for _ in range(3)]
        with patch.object(c, 'build_opener', return_value=opener), patch.object(c.time, 'sleep'):
            with c.dblp_access_session(), self.assertRaisesRegex(RuntimeError, '反复'):
                c.request_bytes(URL)
        self.assertEqual(opener.open.call_count, 3)

    def test_rejects_external_redirect_and_long_delay(self):
        for body in (CHALLENGE.replace(b'url=/', b'url=https://other.test/'),
                     CHALLENGE.replace(b'2; url', b'60; url')):
            with self.subTest(body=body), self.assertRaisesRegex(RuntimeError, '跳转地址'):
                c._dblp_challenge_refresh(body, URL)

    def test_other_sources_keep_existing_transport(self):
        with patch.object(c, 'urlopen', return_value=response(b'{"notes":[]}')) as direct, \
             patch.object(c, 'build_opener') as build:
            c.request_bytes('https://api2.openreview.net/notes')
        direct.assert_called_once()
        build.assert_not_called()

    def test_cookie_processor_receives_and_resends_cookie(self):
        # Real urllib cookie handling against a local HTTP server, no network dependency.
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append(self.headers.get('Cookie'))
                self.send_response(200)
                if len(observed) == 1:
                    self.send_header('Set-Cookie', 'verification=passed; Path=/')
                self.end_headers()
                self.wfile.write(CHALLENGE if len(observed) == 1 else b'{"verified":true}')
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(c, 'DBLP_HOSTS', {'127.0.0.1'}), patch.object(c.time, 'sleep'):
                with c.dblp_access_session():
                    raw, _ = c.request_bytes(f'http://127.0.0.1:{server.server_port}/search')
            self.assertEqual(raw, b'{"verified":true}')
            self.assertEqual(observed, [None, 'verification=passed'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
