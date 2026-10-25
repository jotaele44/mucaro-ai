"""Local model transport rejects HTTP redirects before following them."""
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from backend.app.model_router import _local_opener

class RedirectServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.2:9999/models")
        self.end_headers()
    def log_message(self, *args):
        pass

class TestLocalRedirects(unittest.TestCase):
    def test_local_endpoint_does_not_follow_redirect(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectServer)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                _local_opener().open(f"http://127.0.0.1:{server.server_port}/models", timeout=2)
            self.assertEqual(caught.exception.code, 302)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

if __name__ == "__main__":
    unittest.main()
