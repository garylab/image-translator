# Image Translate Service (Google Translate UI)

Translate text in images using Google Translate's image UI via headless Chromium.
The service accepts image bytes or base64 and returns a downloadable translated image.

## Installation

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies (uv)
uv sync

# Install Chromium for Playwright
playwright install chromium
```

## FastAPI Service

Run the API:

```bash
uv run uvicorn imagetest.api:app --host 0.0.0.0 --port 8000
```

### Multipart upload

```bash
curl -X POST "http://localhost:8000/translate" \
  -F "file=@input.jpg" \
  -F "target_lang=en" \
  -o translated.png
```

### Base64 JSON

```bash
curl -X POST "http://localhost:8000/translate/base64" \
  -H "Content-Type: application/json" \
  -d '{"image_base64":"...","target_lang":"en"}' \
  -o translated.png
```

### Optional proxy/Tor

```bash
curl -X POST "http://localhost:8000/translate" \
  -F "file=@input.jpg" \
  -F "tor=true" \
  -o translated.png
```

Notes:
- `tor=true` uses `socks5://127.0.0.1:9050` by default. Override with `TOR_SOCKS_PROXY`.
- Tor Browser typically exposes `socks5://127.0.0.1:9150`.

## Endpoint Fields

`/translate` (multipart form):
- `file` (required unless `image_base64` provided)
- `image_base64` (optional)
- `source_lang` (default `auto`)
- `target_lang` (default `en`)
- `tor` (default `false`)
- `proxy` (optional, e.g. `socks5://127.0.0.1:9050`)
- `timeout_ms` (default `90000`)

`/translate/base64` (JSON):
- `image_base64` (required)
- `source_lang`, `target_lang`, `tor`, `proxy`, `timeout_ms`
