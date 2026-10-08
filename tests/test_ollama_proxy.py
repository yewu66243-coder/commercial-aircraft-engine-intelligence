import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from gpt_researcher.memory.embeddings import Memory


class OllamaProxyTests(unittest.TestCase):
    def test_local_sync_and_async_embeddings_bypass_a_broken_proxy(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body['input']))
                result = json.dumps({'embeddings': [[1.0, 2.0] for _ in body['input']]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(result)))
                self.end_headers()
                self.wfile.write(result)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.dict(os.environ, {
            'OLLAMA_BASE_URL': f'http://127.0.0.1:{server.server_port}',
            'HTTP_PROXY': 'http://127.0.0.1:1',
            'HTTPS_PROXY': 'http://127.0.0.1:1',
            'ALL_PROXY': 'http://127.0.0.1:1',
            'NO_PROXY': '',
            'http_proxy': 'http://127.0.0.1:1',
            'https_proxy': 'http://127.0.0.1:1',
            'all_proxy': 'http://127.0.0.1:1',
            'no_proxy': '',
        }):
            client = Memory('ollama', 'bge-m3', client_kwargs={'timeout': 2}).get_embeddings()
            try:
                self.assertEqual(client.embed_query('engine'), [1.0, 2.0])

                async def query_and_close():
                    try:
                        return await client.aembed_query('maintenance')
                    finally:
                        await client._async_client._client.aclose()

                self.assertEqual(asyncio.run(query_and_close()), [1.0, 2.0])
            finally:
                client._client._client.close()
        self.assertEqual(requests, [('/api/embed', ['engine']), ('/api/embed', ['maintenance'])])

    def test_loopback_overrides_preserve_other_options_and_caller_dicts(self):
        shared = {'timeout': 12, 'trust_env': True}
        sync = {'headers': {'X-Test': 'sync'}, 'trust_env': True}
        asynchronous = {'timeout': 8, 'trust_env': True}
        for url in ('http://localhost:11434', 'http://127.0.0.2:11434',
                    'http://[::1]:11434', 'localhost:11434'):
            with self.subTest(url=url), patch.dict(os.environ, {'OLLAMA_BASE_URL': url}), \
                    patch('langchain_ollama.OllamaEmbeddings') as constructor:
                Memory('ollama', 'bge-m3', client_kwargs=shared,
                       sync_client_kwargs=sync, async_client_kwargs=asynchronous)
                for key, original in [('client_kwargs', shared), ('sync_client_kwargs', sync),
                                      ('async_client_kwargs', asynchronous)]:
                    self.assertEqual(constructor.call_args.kwargs[key], {**original, 'trust_env': False})
                    self.assertTrue(original['trust_env'])

    def test_remote_ollama_keeps_proxy_configuration(self):
        options = {'timeout': 12, 'trust_env': True}
        for url in ('https://ollama.example.com', 'http://192.168.1.10:11434',
                    'https://localhost.example.com'):
            with self.subTest(url=url), patch.dict(os.environ, {'OLLAMA_BASE_URL': url}), \
                    patch('langchain_ollama.OllamaEmbeddings') as constructor:
                Memory('ollama', 'bge-m3', client_kwargs=options)
                constructor.assert_called_once_with(model='bge-m3', base_url=url, client_kwargs=options)


if __name__ == '__main__':
    unittest.main()
