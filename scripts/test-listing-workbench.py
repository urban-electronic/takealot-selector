"""Browser lifecycle against an isolated in-memory API; never contacts seller services."""
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]


def serve(port):
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from api.listing_routes import router
    from api.store_routes import router as stores
    from database import Base, get_db
    from models import Store
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([Store(id='new', name='Fixture New'), Store(id='old', name='Fixture Old')]); db.commit()
    def database():
        with sessions() as db:
            yield db
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
    app.include_router(router); app.include_router(stores)
    app.dependency_overrides[get_db] = database
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


def run():
    from playwright.sync_api import sync_playwright, expect
    from _audit_server import free_port, vite_server
    from test_listing_workbench import table, complete
    api_base = f'http://127.0.0.1:{free_port()}'
    env = {k: v for k, v in os.environ.items() if k not in ('API_KEY', 'DATABASE_URL', 'RAILWAY_VOLUME_MOUNT_PATH')}
    server = subprocess.Popen([sys.executable, __file__, '--serve-fixture', api_base.rsplit(':', 1)[1]], cwd=ROOT, env=env)
    try:
        opener = build_opener(ProxyHandler({}))
        for _ in range(100):
            try:
                opener.open(api_base + '/api/stores', timeout=1).close(); break
            except OSError:
                if server.poll() is not None: raise RuntimeError('Fixture API failed to start')
                time.sleep(.1)
        else: raise RuntimeError('Fixture API readiness timeout')
        with vite_server(ROOT) as base, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            context.add_init_script(f"localStorage.setItem('api_base_url', '{api_base}'); localStorage.setItem('active_store_id', 'new');")
            external, errors = [], []
            def route(request):
                if request.request.url.startswith((base + '/', api_base + '/')):
                    request.continue_()
                else:
                    external.append(request.request.url); request.abort()
            context.route('**/*', route)
            page = context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base + '/listing')
            expect(page.get_by_role('heading', name='上架工作台', exact=True)).to_be_visible()
            page.locator('input[type=file]').set_input_files({'name': 'fixture.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': table()})
            expect(page.get_by_role('status')).to_contain_text('新增1款')
            page.get_by_role('button', name='核对资料', exact=True).click()
            expect(page.locator('#listing-packaged_weight_kg')).to_have_value('1.2')
            expect(page.locator('#listing-major_colour')).to_have_value('')
            for field, val in complete().items():
                target = page.locator('#listing-' + field)
                if field == 'brand_mode': target.select_option(val)
                else: target.fill('\n'.join(val) if field == 'image_urls' else ' / '.join(val) if isinstance(val, list) else str(val))
            page.get_by_role('button', name='保存资料', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('资料已保存')
            expect(page.locator('#listing-major_colour')).to_have_value('Black')
            expect(page.locator('#listing-other_colours')).to_have_value('Red')
            page.get_by_role('button', name='确认本款已人工审核', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('尚未向平台提交')
            page.get_by_role('checkbox', name='选择JB-005').check()
            with page.expect_download() as download:
                page.get_by_role('button', name='下载已审核资料（1）', exact=True).click()
            assert download.value.suggested_filename.endswith('.xlsx')
            page.locator('#listing-other_colours').fill('Blue')
            page.get_by_role('button', name='保存资料', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('资料已保存')
            expect(page.get_by_role('checkbox', name='选择JB-005')).to_be_disabled()
            expect(page.get_by_role('checkbox', name='选择JB-005')).not_to_be_checked()
            artifact = ROOT / '.tmp-audit/listing'; artifact.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(artifact / 'desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Page overflows mobile viewport'
            page.screenshot(path=str(artifact / 'mobile.png'), full_page=True)
            page.locator('.store-switcher select').select_option('old')
            expect(page.get_by_text('暂无匹配草稿。', exact=False)).to_be_visible()
            expect(page.locator('.listing-editor')).to_have_count(0)
            assert not external, f'Unexpected external requests: {external}'
            assert not errors, f'Browser errors: {errors}'
            browser.close()
            print('PASS listing browser: import, colours, review, download, edit invalidation, store isolation, mobile layout')
    finally:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=10)


if __name__ == '__main__':
    if '--serve-fixture' in sys.argv: serve(int(sys.argv[-1]))
    else: run()
