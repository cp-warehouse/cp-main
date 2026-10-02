"""Bounded retries for transient HTTP failures; no credential-bearing errors."""
import time
import urllib.error
import urllib.request


def get(request, attempts=3):
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), response.url, response.headers.get_content_type(), response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            retryable = code in (500, 502, 503, 504)
            if not retryable or attempt == attempts-1:
                raise RuntimeError(f'HTTP {code}; request URL redacted') from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts-1:
                raise RuntimeError('Network request failed after bounded retries') from None
        time.sleep(2 ** attempt)
