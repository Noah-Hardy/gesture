#!/usr/bin/env python3
"""
Shared Network Helpers

One place for the TLS trust store fix that both src.model_downloader and
src.updater need.

The frozen app is built with Homebrew's python@3.11 in CI, whose OpenSSL
looks for its default CA bundle at /opt/homebrew/etc/openssl@3/cert.pem - a
path that does not exist on an end user's Mac. Nothing else in the bundle
ships a CA file, so any bare urlopen('https://...') in the packaged app
raises SSLCertVerificationError. /etc/ssl/cert.pem is present and
Apple-maintained on every Mac, so it is tried first; the bundled certifi
package (a real dependency - see pyproject.toml) is the fallback for the
unlikely case that path is ever removed.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import ssl

_CONTEXT = None  # Cached after first successful build


# ============================================================================
# TLS CONTEXT
# ============================================================================
def ssl_context() -> ssl.SSLContext:
    """
    Build (and cache) an SSLContext that verifies against a CA bundle that
    actually exists on the running machine, regardless of how this
    interpreter's OpenSSL was configured at build time.
    """
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT

    for cafile in _candidate_ca_files():
        if cafile and os.path.exists(cafile):
            try:
                _CONTEXT = ssl.create_default_context(cafile=cafile)
                return _CONTEXT
            except (ssl.SSLError, OSError):
                continue

    # Last resort: the interpreter's own default. On the frozen app this is
    # the broken Homebrew path and will likely fail - but a context object is
    # still returned so callers get an SSL error, not a crash building one.
    _CONTEXT = ssl.create_default_context()
    return _CONTEXT


def _candidate_ca_files():
    """CA bundle paths to try, most trustworthy/current first"""
    yield '/etc/ssl/cert.pem'
    try:
        import certifi
        yield certifi.where()
    except ImportError:
        pass
