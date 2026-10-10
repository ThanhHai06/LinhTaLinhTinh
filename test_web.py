import asyncio
from dataclasses import replace
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from webcore import Config,ResearchEngine
from webapp import create_app


def config(directory):
    return Config(mode='demo',interval=90,pages=1,limit=10,ws_markets=2,
                  websocket=False,adverse_cents=__import__('decimal').Decimal('0.2'),
                  capitals=(__import__('decimal').Decimal('10'),__import__('decimal').Decimal('50')),
                  max_age=60,max_skew=10,data_dir=directory)

class WebTests(unittest.TestCase):
    def test_demo_engine_persists_without_internet(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=ResearchEngine(config(tmp))
            async def probe():
                await engine.start()
                for _ in range(50):
                    if engine.last_success_utc: break
                    await asyncio.sleep(.05)
                self.assertIsNotNone(engine.last_success_utc)
                self.assertEqual(engine.books_count,3)
                self.assertEqual(engine.overview()['mode'],'demo')
                self.assertFalse(engine.overview()['live_data_verified'])
                self.assertGreaterEqual(engine.last_scan_id,1)
                self.assertEqual(len(engine.history()),1)
                await engine.close()
            asyncio.run(probe())

    def test_api_auth_prod(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'APP_ENV':'production','APP_PASSWORD':'very-long-secret-password'}):
                app=create_app(config(tmp),enable_workers=False)
                with TestClient(app) as c:
                    self.assertEqual(c.get('/health').status_code,200)
                    self.assertEqual(c.get('/').status_code,401)
                    self.assertEqual(c.get('/api/state').status_code,401)
                    auth=('admin','very-long-secret-password')
                    self.assertEqual(c.get('/',auth=auth).status_code,200)
                    self.assertIn('POLYSCOPE',c.get('/',auth=auth).text)
                    self.assertIn('live_data_verified',c.get('/api/state',auth=auth).json())
                    self.assertEqual(c.post('/api/scan',auth=auth).status_code,403)
                    self.assertTrue(c.post('/api/scan',auth=auth,headers={'X-Scanner-Action':'manual-refresh'}).json()['queued'])
                    self.assertEqual(c.get('/api/export.csv',auth=auth).status_code,200)
                    self.assertEqual(c.get('/assets/app.js',auth=auth).status_code,200)
                    self.assertEqual(c.get('/assets/style.css',auth=auth).status_code,200)
                    self.assertIn('mode',c.get('/api/rewards',auth=auth).json())

    def test_prod_requires_secure_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'APP_ENV':'production','APP_PASSWORD':'short'}):
                with self.assertRaises(RuntimeError): create_app(config(tmp),enable_workers=False)

    def test_config_rejects_nonfinite_capital(self):
        with patch.dict(os.environ,{'APP_MODE':'demo','CAPITALS':'NaN'}):
            with self.assertRaises(ValueError): Config.from_env()

    def test_live_failure_is_visible_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=replace(config(tmp),mode='live')
            engine=ResearchEngine(cfg)
            with patch('webcore.collect_live',side_effect=RuntimeError('Network offline')):
                self.assertFalse(asyncio.run(engine.run_scan()))
            self.assertEqual(engine.results,[])
            self.assertFalse(engine.overview()['live_data_verified'])
            self.assertIn('Network offline',engine.overview()['last_error'])

if __name__=='__main__': unittest.main()
