"""TLS helper: build a certifi CA bundle augmented with a site's extra cert."""

from __future__ import annotations

import functools
import tempfile
from pathlib import Path

import certifi


@functools.cache
def ca_bundle_with_extra_cert(cert_path: str) -> str:
    """Build (and disk-cache) a certifi bundle plus a site's extra certificate.

    Some sites omit an intermediate certificate from their TLS chain;
    curl/BoringSSL, unlike browsers, will not fetch it, so we append it here.
    ``cert_path`` is an absolute path to the extra PEM file (the caller resolves
    it — see http.factory).
    """
    combined = Path(certifi.where()).read_text(encoding='utf-8')
    combined += '\n' + Path(cert_path).read_text(encoding='utf-8')
    # Deliberately not a context manager: the file must outlive this call —
    # curl reads it later, by path, for every request the session makes.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode='w', suffix='.pem', prefix='ca-bundle-', delete=False, encoding='utf-8'
    )
    tmp.write(combined)
    tmp.close()
    return tmp.name
