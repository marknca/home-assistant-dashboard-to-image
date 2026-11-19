import copy
from pathlib import Path
import sys
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard_png"))
from app import create_app
from catalog import Store, validate_settings
from manager import Manager
from test_catalog import profiles


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data/dashboards.json", self.root)
        settings = validate_settings({"dashboards": profiles(), "outputs": []}, self.root)
        self.manager = Manager(self.store, settings)
        self.client = TestClient(TestServer(create_app(self.manager, development=True, start_worker=False)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_ui_uses_ingress_relative_assets_and_security_headers(self):
        response = await self.client.get('/index.html')
        html = await response.text()
        self.assertIn('src="static/app.js"', html)
        self.assertIn('Select dashboard', html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
        self.assertEqual((await self.client.get('/static/app.js')).status, 200)

    async def test_ingress_rejects_direct_access_and_spoofed_forwarded_headers(self):
        client = TestClient(TestServer(create_app(self.manager, start_worker=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.get('/api/settings', headers={'X-Forwarded-For': '172.30.32.2', 'X-Remote-User-Id': 'admin'})
        self.assertEqual(response.status, 403)

    async def test_save_is_atomic_validated_persistent_and_revision_checked(self):
        data = await (await self.client.get('/api/settings')).json()
        settings = data['settings']
        settings['dashboards'][1]['config']['width'] = 800
        settings['dashboards'][1]['config']['height'] = 600
        response = await self.client.put('/api/settings', json=settings, headers={'X-Dashboard-PNG': '1'})
        self.assertEqual(response.status, 200)
        saved = (await response.json())['settings']
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(self.store.load(self.root / 'not-needed'), saved)
        response = await self.client.put('/api/settings', json=settings, headers={'X-Dashboard-PNG': '1'})
        self.assertEqual(response.status, 409)
        bad = copy.deepcopy(saved); bad['dashboards'][1]['config']['filename'] = 'a.png'
        response = await self.client.put('/api/settings', json=bad, headers={'X-Dashboard-PNG': '1'})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.load(self.root / 'not-needed'), saved)

    async def test_writes_require_json_and_custom_header(self):
        self.assertEqual((await self.client.put('/api/settings', json=self.manager.settings)).status, 403)
        self.assertEqual((await self.client.put('/api/settings', data='{}', headers={'X-Dashboard-PNG': '1'})).status, 403)
        self.assertEqual((await self.client.put('/api/settings', json=[], headers={'X-Dashboard-PNG': '1'})).status, 400)

    async def test_render_queues_only_enabled_known_outputs(self):
        response = await self.client.post('/api/render', json={'key': 'dashboard:a'}, headers={'X-Dashboard-PNG': '1'})
        self.assertEqual(response.status, 202)
        self.assertIn('dashboard:a', self.manager.requested)
        response = await self.client.post('/api/render', json={'key': 'dashboard:missing'}, headers={'X-Dashboard-PNG': '1'})
        self.assertEqual(response.status, 400)

    async def test_preview_only_serves_configured_pngs_and_rejects_symlinks(self):
        (self.root / 'www').mkdir()
        (self.root / 'www/a.png').write_bytes(b'image bytes')
        response = await self.client.get('/api/preview?kind=dashboard&id=a')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b'image bytes')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((await self.client.get('/api/preview?kind=dashboard&id=../private')).status, 404)
        (self.root / 'private').write_text('secret')
        (self.root / 'www/a.png').unlink()
        (self.root / 'www/a.png').symlink_to(self.root / 'private')
        self.assertEqual((await self.client.get('/api/preview?kind=dashboard&id=a')).status, 404)


if __name__ == '__main__':
    unittest.main()
