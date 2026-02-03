"""
Translate images using Google Translate's image translation UI via a headless Chromium browser.

This wraps the web UI at:
https://translate.google.com/?sl=auto&tl=en&op=images
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import random
import re
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from src.config import settings

DEFAULT_TIMEOUT_MS = 90000
TRANSLATION_ERROR_MESSAGES = (
    "Can't detect text",
    "Cannot detect text",
    "This language may not be supported",
)

def _build_launch_options(headless: bool, proxy_server: Optional[str]) -> dict:
    launch_options = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--enable-webgl",
            "--enable-gpu",
            "--use-gl=angle",
        ],
    }
    if proxy_server:
        launch_options["proxy"] = {"server": proxy_server}
    return launch_options


class TranslationUiError(RuntimeError):
    pass


def _normalize_base64(data: str) -> bytes:
    if not data:
        raise ValueError("Base64 input is empty.")

    value = data.strip()
    if value.startswith("data:"):
        parts = value.split(",", 1)
        if len(parts) != 2:
            raise ValueError("Invalid data URL for base64 input.")
        value = parts[1]

    value = "".join(value.split())

    missing_padding = len(value) % 4
    if missing_padding:
        value += "=" * (4 - missing_padding)

    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error:
        return base64.b64decode(value)


def _infer_suffix(image_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            fmt = (img.format or "").lower()
    except Exception:
        fmt = ""

    if fmt == "jpeg":
        return ".jpg"
    if fmt:
        return f".{fmt}"
    return ".png"


def _write_temp_image(image_bytes: bytes) -> str:
    suffix = _infer_suffix(image_bytes)
    work_dir = Path(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="gt_image_", suffix=suffix, dir=str(work_dir))
    with os.fdopen(fd, "wb") as handle:
        handle.write(image_bytes)
    return path


def _resolve_work_path(path_value: str) -> str:
    path = Path(path_value)
    if path.is_absolute():
        resolved = path
    else:
        work_dir = Path(settings.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        resolved = work_dir / path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


async def _natural_delay() -> None:
    delay_min = max(0.0, settings.natural_delay_min_s)
    delay_max = max(delay_min, settings.natural_delay_max_s)
    if delay_max <= 0:
        return
    await asyncio.sleep(random.uniform(delay_min, delay_max))


async def _capture_error_screenshot(page, label: str) -> Optional[str]:
    work_dir = Path(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    filename = f"translate_error_{label}_{timestamp}.png"
    path = work_dir / filename
    try:
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


async def _has_image_file_input(page) -> bool:
    handles = await page.query_selector_all("input[type=file]")
    if not handles:
        return False

    preferred_tokens = ("image", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
    for handle in handles:
        try:
            accept_value = (await handle.get_attribute("accept")) or ""
        except Exception:
            accept_value = ""
        value = accept_value.lower()
        if any(token in value for token in preferred_tokens):
            return True

    return False


async def _open_image_translate(page, timeout_ms: int) -> None:
    await page.goto("https://translate.google.com/", wait_until="domcontentloaded")
    await _natural_delay()
    await _dismiss_consent(page)
    await _natural_delay()

    candidates = [
        page.get_by_role("tab", name=re.compile(r"images", re.I)),
        page.get_by_role("button", name=re.compile(r"(image translation|images)", re.I)),
        page.locator("button[aria-label='Image translation']"),
        page.locator("button:has-text('Images')"),
    ]

    for locator in candidates:
        try:
            if await locator.count() == 0:
                continue
            await locator.first.click(timeout=timeout_ms)
            try:
                await page.wait_for_url(
                    re.compile(r"[?&]op=images"), timeout=min(5000, timeout_ms)
                )
                await _natural_delay()
                return
            except PlaywrightTimeoutError:
                if await _has_image_file_input(page):
                    await _natural_delay()
                    return
        except Exception:
            continue

    try:
        if re.search(r"[?&]op=images", page.url):
            return
    except Exception:
        pass

    raise RuntimeError("Could not open image translate page.")


async def _dismiss_consent(page) -> None:
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Agree')",
        "button:has-text('Accept')",
        "#introAgreeButton",
        "button[aria-label='Accept all']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0 and await locator.first.is_visible():
                await locator.first.click(timeout=2000)
                return
        except Exception:
            continue

    for frame in page.frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    await locator.first.click(timeout=2000)
                    return
            except Exception:
                continue


async def _find_download_button(page):
    selectors = [
        "button:has-text('Download')",
        "a:has-text('Download')",
        "button[aria-label*='Download']",
        "a[aria-label*='Download']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0 and await locator.first.is_visible():
                return locator.first
        except Exception:
            continue

    try:
        locator = page.get_by_role("button", name=re.compile("download", re.I))
        if await locator.count() > 0 and await locator.first.is_visible():
            return locator.first
    except Exception:
        pass

    return None


async def _pick_file_input(page) -> int:
    handles = await page.query_selector_all("input[type=file]")
    if not handles:
        return -1

    preferred_tokens = ("image", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
    deprioritized_tokens = (".pdf", ".pptx", ".docx", ".xlsx", "application/pdf")

    def score(accept_value: str) -> int:
        value = (accept_value or "").lower()
        if any(token in value for token in preferred_tokens):
            return 2
        if any(token in value for token in deprioritized_tokens):
            return 0
        return 1

    best_idx = 0
    best_score = -1
    for idx, handle in enumerate(handles):
        try:
            accept_value = await handle.get_attribute("accept")
        except Exception:
            accept_value = ""
        value_score = score(accept_value or "")
        if value_score > best_score:
            best_score = value_score
            best_idx = idx

    return best_idx


async def _wait_for_translated_image_src(page, timeout_ms: int) -> str:
    deadline = time.time() + (timeout_ms / 1000.0)
    last_error = None

    while time.time() < deadline:
        try:
            src = await page.evaluate(
                """() => {
                const imgs = Array.from(document.querySelectorAll('img'))
                    .filter(img => img.src && img.naturalWidth > 50 && img.naturalHeight > 50)
                    .map(img => ({
                        src: img.src,
                        alt: img.alt || '',
                        aria: img.getAttribute('aria-label') || ''
                    }));

                if (!imgs.length) {
                    return null;
                }

                const preferred = imgs.find(item => /translated|translation/i.test(`${item.alt} ${item.aria}`));
                if (preferred) {
                    return preferred.src;
                }

                return imgs[imgs.length - 1].src;
            }"""
            )
            if src:
                return src
        except Exception as exc:
            last_error = exc

        time.sleep(0.5)

    raise RuntimeError("Timed out waiting for translated image.") from last_error


async def _wait_for_translation_ready(page, timeout_ms: int) -> None:
    deadline = time.time() + (timeout_ms / 1000.0)
    last_src = None
    stable_since = None
    last_error_text = None

    while time.time() < deadline:
        button = await _find_download_button(page)
        if button is not None:
            return

        try:
            translated_img = page.locator(
                "img[alt*='Translated' i], img[aria-label*='translation' i], img[aria-label*='translated' i]"
            )
            if await translated_img.count() > 0 and await translated_img.first.is_visible():
                return
        except Exception:
            pass

        error_text = await _detect_translation_error(page)
        if error_text:
            last_error_text = error_text

        try:
            result = await page.evaluate(
                """() => {
                const imgs = Array.from(document.querySelectorAll('img'))
                    .filter(img => img.src && img.naturalWidth > 50 && img.naturalHeight > 50)
                    .map(img => ({
                        src: img.src,
                        alt: img.alt || '',
                        aria: img.getAttribute('aria-label') || ''
                    }));

                if (!imgs.length) {
                    return { preferred: false, src: null };
                }

                const preferred = imgs.find(item => /translated|translation/i.test(`${item.alt} ${item.aria}`));
                if (preferred) {
                    return { preferred: true, src: preferred.src };
                }

                return { preferred: false, src: imgs[imgs.length - 1].src };
            }"""
            )
        except Exception:
            result = {"preferred": False, "src": None}

        src = result.get("src") if isinstance(result, dict) else None
        preferred = bool(result.get("preferred")) if isinstance(result, dict) else False

        if preferred and src:
            return

        if src:
            now = time.time()
            if src == last_src:
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= 1.5:
                    return
            else:
                last_src = src
                stable_since = None

        await asyncio.sleep(0.5)

    if last_error_text:
        screenshot_path = await _capture_error_screenshot(page, "detect_text")
        if screenshot_path:
            raise TranslationUiError(
                f"{last_error_text} (screenshot: {screenshot_path})"
            )
        raise TranslationUiError(last_error_text)

    screenshot_path = await _capture_error_screenshot(page, "timeout")
    if screenshot_path:
        raise RuntimeError(
            f"Timed out waiting for translation to finish. (screenshot: {screenshot_path})"
        )
    raise RuntimeError("Timed out waiting for translation to finish.")




async def _detect_translation_error(page) -> Optional[str]:
    try:
        body_text = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
        )
    except Exception:
        return None

    if not body_text:
        return None

    lower = body_text.lower()
    has_detect = "can't detect text" in lower or "cannot detect text" in lower
    has_lang = "language may not be supported" in lower

    if has_detect and has_lang:
        return "Can't detect text. This language may not be supported."

    for message in TRANSLATION_ERROR_MESSAGES:
        if message.lower() in lower:
            return message

    return None


async def _download_or_extract_image(
    page, timeout_ms: int, download_path: Optional[str]
) -> bytes:
    button = await _find_download_button(page)
    if button is not None:
        try:
            async with page.expect_download(timeout=timeout_ms) as download_info:
                await button.click()
            download = await download_info.value
            if download_path:
                await download.save_as(download_path)
                with open(download_path, "rb") as handle:
                    return handle.read()
            temp_path = await download.path()
            if temp_path:
                with open(temp_path, "rb") as handle:
                    return handle.read()
        except PlaywrightTimeoutError:
            pass

    src = await _wait_for_translated_image_src(page, timeout_ms)

    if src.startswith("data:"):
        _, encoded = src.split(",", 1)
        image_bytes = _normalize_base64(encoded)
    elif src.startswith("blob:"):
        encoded = await page.evaluate(
            """async (blobUrl) => {
            const response = await fetch(blobUrl);
            const buffer = await response.arrayBuffer();
            let binary = '';
            const bytes = new Uint8Array(buffer);
            for (let i = 0; i < bytes.length; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return btoa(binary);
        }""",
            src,
        )
        image_bytes = _normalize_base64(encoded)
    elif src.startswith("http"):
        response = await page.request.get(src, timeout=timeout_ms)
        image_bytes = await response.body()
    else:
        raise RuntimeError("Could not capture translated image output.")

    if download_path:
        with open(download_path, "wb") as handle:
            handle.write(image_bytes)

    return image_bytes


async def translate_image_google_async(
    *,
    image_bytes: Optional[bytes] = None,
    image_base64: Optional[str] = None,
    headless: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    download_path: Optional[str] = None,
) -> bytes:
    if bool(image_bytes) == bool(image_base64):
        raise ValueError("Provide exactly one of image_bytes or image_base64.")

    if image_base64 is not None:
        image_bytes = _normalize_base64(image_base64)

    if image_bytes is None:
        raise ValueError("Image bytes were not provided.")

    temp_path = _write_temp_image(image_bytes)
    if download_path:
        download_path = _resolve_work_path(download_path)
    use_tor = settings.tor_enabled
    proxy_server = None
    if use_tor:
        proxy_server = (settings.tor_socks_proxy or "").strip()
        if not proxy_server:
            raise ValueError(
                "Tor is enabled but TOR_SOCKS_PROXY is empty. "
                "Set TOR_SOCKS_PROXY."
            )

    try:
        async with async_playwright() as playwright:
            launch_options = _build_launch_options(headless, proxy_server)

            browser = await playwright.chromium.launch(**launch_options)
            context = await browser.new_context(accept_downloads=True, locale="en-US")
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            await _open_image_translate(page, timeout_ms)

            await page.wait_for_selector(
                "input[type=file]", state="attached", timeout=timeout_ms
            )
            await _natural_delay()
            file_input_index = await _pick_file_input(page)
            if file_input_index < 0:
                raise RuntimeError("No file input found on translate page.")
            await page.locator("input[type=file]").nth(file_input_index).set_input_files(
                temp_path,
                timeout=timeout_ms,
            )
            await _natural_delay()

            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
            await _natural_delay()

            await _wait_for_translation_ready(page, timeout_ms)
            await _natural_delay()

            return await _download_or_extract_image(page, timeout_ms, download_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def translate_image_google(*args, **kwargs) -> bytes:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise RuntimeError(
            "translate_image_google must be awaited in async contexts. "
            "Use translate_image_google_async instead."
        )

    return asyncio.run(translate_image_google_async(*args, **kwargs))
