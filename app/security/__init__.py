"""Secret storage. Secrets (1C password, ZZap API keys) are encrypted at rest.

Never store these in plaintext (PROMPT.md / ROADMAP.md cross-cutting requirement).
"""
from .secrets import Cipher, DpapiCipher, decrypt, encrypt

__all__ = ["Cipher", "DpapiCipher", "encrypt", "decrypt"]
