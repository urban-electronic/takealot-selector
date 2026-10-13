"""Durable quote browser profile. Login/location state stays on the collector host."""
import asyncio
import os
import threading
from contextlib import asynccontextmanager
from database import DB_DIR

_lock = threading.Lock()

@asynccontextmanager
async def quote_context():
    from playwright.async_api import async_playwright
    await asyncio.to_thread(_lock.acquire)
    try:
        profile = os.environ.get('BUYBOX_BROWSER_PROFILE', os.path.join(DB_DIR, 'buybox_browser_profile'))
        os.makedirs(profile, exist_ok=True)
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile, headless=True, args=['--no-sandbox'],
                locale='en-ZA', timezone_id='Africa/Johannesburg',
                viewport={'width':1366,'height':768})
            try:
                yield context
            finally:
                await context.close()
    finally:
        _lock.release()
