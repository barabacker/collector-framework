"""ca_bundle_with_extra_cert builds a certifi bundle plus a site's extra cert."""

from __future__ import annotations

from pathlib import Path

import certifi

from collector import ca_bundle_with_extra_cert


def test_bundle_contains_certifi_and_extra(tmp_path: Path):
    extra = tmp_path / 'extra.pem'
    extra.write_text('-----BEGIN CERTIFICATE-----\nEXTRA_MARKER\n-----END CERTIFICATE-----\n')

    bundle_path = ca_bundle_with_extra_cert(str(extra))
    text = Path(bundle_path).read_text(encoding='utf-8')

    assert 'EXTRA_MARKER' in text
    assert Path(certifi.where()).read_text(encoding='utf-8')[:200] in text


def test_bundle_is_cached_per_path(tmp_path: Path):
    extra = tmp_path / 'extra.pem'
    extra.write_text('-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n')

    assert ca_bundle_with_extra_cert(str(extra)) == ca_bundle_with_extra_cert(str(extra))
