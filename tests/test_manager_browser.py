import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard_png"))
from app import create_app
from catalog import Store, select_dashboard, validate_settings
from manager import Manager
from renderer import png_size


@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', 'Set RUN_BROWSER_TESTS=1 for Chromium tests')
class ManagerBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_select_edit_duplicate_rotate_and_schedule_through_ui(self):
        from playwright.async_api import async_playwright, expect
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            async def bootstrap(request):
                return web.Response(status=404, text='Origin established')
            origin_app = web.Application()
            origin_app.router.add_route('*', '/{path:.*}', bootstrap)
            origin = TestServer(origin_app)
            await origin.start_server()
            self.addAsyncCleanup(origin.close)
            base_url = str(origin.make_url('/')).rstrip('/')
            for ident, color in [('a', '#e2eafe'), ('b', '#e0f4e8')]:
                (root / f'{ident}.html').write_text(f'<body data-ready="true" style="background:{color};font:22px sans-serif;padding:16px"><h1 style="font-size:32px">Dashboard {ident.upper()}</h1><p>Live display</p></body>')
            now = [datetime.fromisoformat('2026-11-19T08:00:00-05:00').timestamp()]
            rows = [{'id': ident, 'name': name, 'config': {
                'source': str(root / f'{ident}.html'), 'base_url': base_url,
                'width': width, 'height': height, 'filename': f'{ident}.png',
                'interval_seconds': 10, 'render_delay_ms': 0, 'ready_selector': 'body[data-ready="true"]'
            }} for ident, name, width, height in [('a', 'Office', 320, 240), ('b', 'Family', 640, 360)]]
            settings = validate_settings({'dashboards': rows, 'outputs': [{
                'id': 'display', 'name': 'Kitchen display', 'filename': 'display.png', 'mode': 'rotation',
                'anchor': now[0], 'rotation': [{'dashboard_id': 'a', 'duration_seconds': 10},
                                             {'dashboard_id': 'b', 'duration_seconds': 20}]
            }]}, root)
            store = Store(root / 'data/dashboards.json', root)
            executable = os.environ.get('BROWSER_EXECUTABLE')
            manager = Manager(store, settings, executable, clock=lambda: now[0])
            proxy = web.Application()
            proxy.add_subapp('/api/hassio_ingress/test-session/', create_app(manager, development=True, start_worker=False))
            server = TestServer(proxy)
            await server.start_server()
            self.addAsyncCleanup(server.close)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, executable_path=executable)
                self.addAsyncCleanup(browser.close)
                page = await browser.new_page(viewport={'width': 1320, 'height': 1050})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(str(server.make_url('/api/hassio_ingress/test-session/index.html')))
                await expect(page.get_by_label('Select dashboard')).to_have_value('a')
                await page.get_by_label('Select dashboard').select_option('b')
                await expect(page.get_by_label('Width (pixels)')).to_have_value('640')
                await page.get_by_role('button', name='Duplicate', exact=True).click()
                await page.get_by_label('Name', exact=True).fill('Evening')
                await page.get_by_label('Width (pixels)').fill('480')

                async def save():
                    async with page.expect_response(lambda response: response.url.endswith('/api/settings') and response.request.method == 'PUT') as response_info:
                        await page.get_by_role('button', name='Save changes').click()
                    response = await response_info.value
                    self.assertEqual(response.status, 200, await response.text())
                    await expect(page.get_by_role('button', name='Save changes')).to_be_enabled()

                await save()
                self.assertEqual(len(manager.settings['dashboards']), 3)
                await page.get_by_label('Select dashboard').select_option('a')
                await expect(page.get_by_label('Width (pixels)')).to_have_value('320')
                for _ in range(4):
                    await manager.step()
                self.assertEqual(png_size((root / 'www/a.png').read_bytes()), (320, 240))
                self.assertEqual(png_size((root / 'www/b.png').read_bytes()), (640, 360))
                self.assertEqual(png_size((root / 'www/display.png').read_bytes()), (320, 240))
                first = (root / 'www/display.png').read_bytes()
                now[0] += 10
                for _ in range(4):
                    await manager.step()
                second = (root / 'www/display.png').read_bytes()
                self.assertNotEqual(first, second)
                self.assertEqual(png_size(second), (640, 360))
                loaded = store.load(root / 'unused-options.json')
                self.assertEqual(select_dashboard(loaded['outputs'][0], now[0]).dashboard_id, 'b')

                screenshots = os.environ.get('UI_SCREENSHOT_DIR')
                if screenshots:
                    Path(screenshots).mkdir(parents=True, exist_ok=True)
                    await page.reload()
                    await expect(page.locator('#preview')).to_be_visible()
                    await page.screenshot(path=str(Path(screenshots) / 'dashboards.png'), full_page=True)

                await page.get_by_role('button', name='Shared outputs', exact=True).click()
                await expect(page.get_by_label('Select shared output')).to_have_value('display')
                await page.get_by_label('Switch dashboards using').select_option('schedule')
                await page.get_by_role('button', name='Add entry', exact=True).click()
                steps = page.locator('.step')
                await steps.nth(1).get_by_label('Dashboard', exact=True).select_option('b')
                await steps.nth(1).get_by_label('Switch at', exact=True).fill('19:00')
                await save()
                self.assertEqual(manager.settings['outputs'][0]['mode'], 'schedule')
                self.assertEqual(select_dashboard(manager.settings['outputs'][0], now[0]).dashboard_id, 'a')
                for _ in range(4):
                    await manager.step()
                self.assertEqual(png_size((root / 'www/display.png').read_bytes()), (320, 240))
                if screenshots:
                    # Refresh status without replacing the editor's current selection.
                    await expect(page.locator('#status')).to_contain_text('Showing: Office')
                    await page.screenshot(path=str(Path(screenshots) / 'schedules.png'), full_page=True)
                await page.set_viewport_size({'width': 390, 'height': 844})
                self.assertTrue(await page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
