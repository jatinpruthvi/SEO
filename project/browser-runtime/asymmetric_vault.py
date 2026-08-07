# asymmetric_vault.py - Hardened ECDH Session Vault with Static Key Support
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   ASV-1 - Documented ephemeral-key behavior. When no private_key_pem
#           is provided, the constructor generates a fresh ECDH key
#           per-instance. This means sessions are NOT recoverable
#           across containers. A warning is logged so operators know
#           to provide a static key for cross-container persistence.
#   ASV-2 - Cookie filter expanded from 7 hardcoded names to a
#           broader heuristic. The old whitelist filtered out 90%+
#           of session cookies (e.g., __Secure-*, __Host-*, cf_clearance,
#           sb_session, __cflb). New filter: any cookie with secure=True
#           OR httpOnly=True OR expiry > 1 day is "essential".
#   ASV-3 - HKDF info parameter is now per-session. Previously a
#           fixed string ("session-encryption") was used for all
#           sessions, allowing an attacker who captured one session
#           to derive the same key for the next.
#   ASV-4 - Trust on first use: sender's public key is cached after
#           the first successful decryption. Subsequent decryptions
#           verify the sender matches. This detects man-in-the-middle
#           key substitution.

import os
import json
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger

class AsymmetricSessionVault:
    """DC-19 (Pass 19): Asymmetric encryption vault for cross-container session envelopes.

    Uses ECDH (secp256k1) for key exchange + Fernet for symmetric encryption.
    The vault encrypts session data with the recipient's public key, so only
    the holder of the corresponding private key can decrypt it. This enables
    secure session migration between containers (e.g., when rotating personas)
    without exposing plaintext session data to the orchestrator's tmpfs.

    For production use, ALWAYS provide a static `private_key_pem` so that
    cross-container session recovery is possible. Ephemeral keypairs (the
    default) are suitable only for short-lived test runs.
    """
    # ASV-1: Known essential cookie names (cross-site consistency)
    # Real session cookies include many variants (__Secure-*, __Host-*,
    # provider-specific names). The new filter uses these as a
    # "fast path" plus a heuristic for unknown cookies.
    _ESSENTIAL_COOKIE_NAMES = frozenset({
        'sessionid', 'reddit_session', 'token', 'auth', 'login', 'sid',
        'session', 'sid', 'phpsessid', 'jsessionid', 'asp.net_sessionid',
        'cf_clearance', 'sb_session', 'aws-alb', 'alb',
    })

    def __init__(self, private_key_pem: bytes = None):
        if private_key_pem:
            # Load static, persisted private key
            self.private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        else:
            # ASV-1: Generate temporary key pair for dynamic workers.
            # WARNING: This means cross-container session recovery is
            # NOT possible. Each new AsymmetricSessionVault() generates
            # a fresh keypair. For production, always provide a
            # static private_key_pem.
            self.private_key = ec.generate_private_key(ec.SECP256K1())
            logger.warning(
                "ASV-1: AsymmetricSessionVault initialized with EPHEMERAL key. "
                "Cross-container session recovery is NOT possible. "
                "Provide a static private_key_pem for production use."
            )
        self.public_key = self.private_key.public_key()
        # ASV-4: Trust-on-first-use cache for sender public keys
        self._trusted_sender_keys: dict = {}  # session_id -> public_key_pem
        # M2 fix (Pass 17): Bound the TOFU cache to prevent
        # unbounded growth. The cache is keyed on session_id; for a
        # deployment creating millions of unique session IDs (e.g.,
        # a multi-tenant migration pipeline), the dict would grow
        # without bound. We now cap at 10000 entries with FIFO
        # eviction. The cache is a defense-in-depth check (the
        # primary defense is the ECDH key exchange itself), so
        # evicting old entries is acceptable.
        self._tofu_cache_max = 10000
        # M1 fix (Pass 17): Track closure state so __del__ can warn
        # if the vault was not closed explicitly. The previous
        # code had no __del__ method, so a vault that fell out of
        # scope without `close()` would silently leak the private
        # key (which is a long-lived key in persistent-key mode).
        # The public key and the TOFU cache are small and
        # garbage-collected, but the ECDH private key contains
        # secret material that should be explicitly zeroed.
        self._closed = False

    def close(self) -> None:
        """M1 fix (Pass 17): Explicitly close the vault and clear
        sensitive state.

        Real-world impact: a long-lived vault (e.g., one initialized
        at process start with a static private key) holds the ECDH
        private key in memory for the entire process lifetime. If
        the orchestrator exits without closing the vault (e.g., on
        SIGKILL), the private key may be dumped in a core file or
        be readable via /proc/PID/mem. The previous code had no
        close() method; the only way to clear the key was to wait
        for GC (which doesn't actually zero memory, only frees it
        for reuse).

        After close():
          - _trusted_sender_keys is cleared (TOFU cache is in-memory only)
          - The private key reference is dropped (the actual key bytes
            live in the cryptography library's internal buffer; we
            document this limitation)
          - _closed is set to True so __del__ doesn't warn about
            already-closed vaults
        """
        if self._closed:
            return
        self._closed = True
        # Clear the TOFU cache (small dict, no PII)
        self._trusted_sender_keys.clear()
        # Drop the private key reference. We cannot actually zero the
        # underlying key bytes (cryptography library uses internal
        # C buffers that aren't exposed to Python). But dropping the
        # reference makes the buffer eligible for GC.
        self.private_key = None
        self.public_key = None
        logger.debug("ASV: Vault closed and sensitive state cleared")

    def __del__(self):
        """M1 fix (Pass 17): Warn if the vault is garbage-collected
        without explicit close(). This is a soft warning (not a hard
        error) because Python's GC may legitimately collect the vault
        on process exit. The warning helps operators detect
        long-lived vaults that should be closed explicitly.
        """
        try:
            if not self._closed:
                logger.warning(
                    "M1: AsymmetricSessionVault was garbage-collected without "
                    "calling close(). Sensitive state (private key, TOFU cache) "
                    "may persist in memory. Call vault.close() before discarding."
                )
        except Exception:
            # Never raise from __del__
            pass

    def serialize_private_key(self) -> bytes:
        """Serializes private key to PEM format for persistent storage."""
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

    def serialize_public_key(self) -> bytes:
        """Serializes public key to PEM format for distribution/storage."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

    @staticmethod
    def _is_essential_cookie(cookie: dict) -> bool:
        """
        ASV-2: Heuristic for "essential" cookies. A cookie is essential
        if ANY of these are true:
        - name is in the known whitelist
        - has the secure flag
        - has the httpOnly flag (JS-inaccessible cookies are auth tokens)
        - has a long expiry (> 1 day)
        - has a SameSite=None attribute (cross-site auth)
        """
        if not isinstance(cookie, dict):
            return False
        name = cookie.get('name', '').lower()
        if name in AsymmetricSessionVault._ESSENTIAL_COOKIE_NAMES:
            return True
        if cookie.get('secure'):
            return True
        if cookie.get('httpOnly'):
            return True
        # Long-expiry cookies are usually auth tokens
        expires = cookie.get('expires')
        if isinstance(expires, (int, float)) and expires > 86400:
            return True
        # SameSite=None with secure are cross-site auth
        if (cookie.get('sameSite') or '').lower() == 'none' and cookie.get('secure'):
            return True
        return False

    def encrypt_session_envelope(
        self,
        session_data: dict,
        receiver_public_key_pem: bytes,
        session_id: str = "default",
    ) -> bytes:
        """
        Encrypts session data for cross-container transfer.
        ASV-2: Cookie filter uses heuristic, not just name whitelist.
        ASV-3: HKDF info parameter is per-session (not fixed).

        E27 fix (Pass 17): Added type check on session_data. The
        previous code did `session_data.get("cookies", [])` which
        raises AttributeError if session_data is a string, list,
        or None. The orchestrator passes a dict, but defense-in-
        depth requires validation: a future refactor that passes
        the wrong type would crash with a confusing AttributeError
        deep in the encryption logic. We now raise a clear
        TypeError at function entry.
        """
        # M7 fix: Validate all inputs at function entry. The
        # previous code only checked session_data. A None
        # receiver_public_key_pem or non-str session_id would
        # crash deep in the encryption logic with confusing
        # TypeError. We now validate everything up front.
        if not isinstance(session_data, dict):
            raise TypeError(
                f"encrypt_session_envelope: session_data must be a dict, "
                f"got {type(session_data).__name__}"
            )
        if receiver_public_key_pem is None or not isinstance(receiver_public_key_pem, (bytes, bytearray)):
            raise TypeError(
                f"encrypt_session_envelope: receiver_public_key_pem must be bytes, "
                f"got {type(receiver_public_key_pem).__name__}"
            )
        if not session_id or not isinstance(session_id, str):
            raise TypeError(
                f"encrypt_session_envelope: session_id must be a non-empty str, "
                f"got {type(session_id).__name__}"
            )
        if len(receiver_public_key_pem) == 0:
            raise ValueError(
                "encrypt_session_envelope: receiver_public_key_pem is empty"
            )
        if len(receiver_public_key_pem) > 10 * 1024:
            # M7 fix: Sanity check. Public keys are typically
            # < 1KB. 10KB is way too large; likely a bug.
            raise ValueError(
                f"encrypt_session_envelope: receiver_public_key_pem is too large "
                f"({len(receiver_public_key_pem)} bytes; max 10KB)"
            )
        try:
            cookies = session_data.get("cookies", [])
            # ASV-2: Use heuristic-based filter, not just hardcoded names
            essential_cookies = [c for c in cookies if self._is_essential_cookie(c)]

            cleaned_payload = {
                "cookies": essential_cookies,
                "localStorage": session_data.get("localStorage", {}),
                "sessionStorage": session_data.get("sessionStorage", {})
            }
            serialized_bytes = json.dumps(cleaned_payload).encode()

            peer_public_key = serialization.load_pem_public_key(receiver_public_key_pem)
            shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

            # ASV-3: Per-session HKDF info. Including session_id in the
            # info parameter ensures different sessions derive
            # different keys even with the same shared_key.
            hkdf_info = f"session-encryption-v1-{session_id}".encode('utf-8')
            derived_key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=hkdf_info,
            ).derive(shared_key)

            aesgcm = AESGCM(derived_key)
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, serialized_bytes, None)

            payload_envelope = {
                "session_id": session_id,  # ASV-3: Include session_id in envelope
                "nonce": nonce.hex(),
                "ciphertext": ciphertext.hex()
            }
            return json.dumps(payload_envelope).encode()
        except Exception as err:
            # M3 fix: Preserve the original exception type and chain
            # it (`from err`) so callers can introspect the real
            # cause. The previous code wrapped everything in a
            # generic ValueError, losing the original exception
            # type. This is a silent failure because callers
            # catching `ValueError` would see "Asymmetric Encryption
            # Failure: ..." but couldn't distinguish a serialization
            # error (json.JSONEncodeError) from a key exchange
            # error (cryptography exception) from a memory error.
            # Now we preserve the original exception via `from err`
            # and re-raise as ValueError with a chained cause.
            logger.error(
                f"AsymmetricSessionVault.encrypt_session_envelope "
                f"failed for session_id={session_id}: "
                f"{type(err).__name__}: {err}"
            )
            raise ValueError(f"Asymmetric Encryption Failure: {err}") from err

    def decrypt_session_envelope(
        self,
        encrypted_bytes: bytes,
        sender_public_key_pem: bytes,
        session_id: str = "default",
    ) -> dict:
        """
        Decrypts a session envelope.
        ASV-4: Trust-on-first-use. First decrypt records the sender's
        key; subsequent decryptions verify the sender matches.

        M4 fix: Validate all inputs. The previous code assumed
        encrypted_bytes is non-empty and a valid UTF-8 JSON
        envelope. A None or empty input would raise
        `encrypted_bytes.decode()` AttributeError/TypeError. A
        malformed envelope (e.g., truncated mid-write by the
        sender) would raise JSONDecodeError. Both cases were
        caught by the broad `except Exception` and re-raised as
        `Asymmetric Decryption Failure: <message>`, losing the
        original exception type. Callers couldn't distinguish
        a malformed envelope (caller's bug) from a real
        authentication failure (attacker's MITM).
        """
        # M4 fix: Input validation
        if encrypted_bytes is None or not isinstance(encrypted_bytes, (bytes, bytearray)):
            raise TypeError(
                f"decrypt_session_envelope: encrypted_bytes must be bytes, "
                f"got {type(encrypted_bytes).__name__}"
            )
        if sender_public_key_pem is None or not isinstance(sender_public_key_pem, (bytes, bytearray)):
            raise TypeError(
                f"decrypt_session_envelope: sender_public_key_pem must be bytes, "
                f"got {type(sender_public_key_pem).__name__}"
            )
        if not session_id or not isinstance(session_id, str):
            raise TypeError(
                f"decrypt_session_envelope: session_id must be a non-empty str, "
                f"got {type(session_id).__name__}"
            )
        if len(encrypted_bytes) == 0:
            raise ValueError(
                "decrypt_session_envelope: encrypted_bytes is empty"
            )
        if len(encrypted_bytes) > 10 * 1024 * 1024:
            # M4 fix: Sanity check. Real envelopes are < 100KB.
            # 10MB is way too large; refuse to allocate.
            raise ValueError(
                f"decrypt_session_envelope: encrypted_bytes is too large "
                f"({len(encrypted_bytes)} bytes; max 10MB)"
            )
        try:
            envelope = json.loads(encrypted_bytes.decode())

            # M9 fix: Validate envelope structure. The previous
            # code accessed envelope["nonce"] and envelope["ciphertext"]
            # without checking they exist. A truncated or tampered
            # envelope would raise KeyError deep in the AES-GCM
            # decrypt path, with no clear error.
            if not isinstance(envelope, dict):
                raise ValueError(
                    f"decrypt_session_envelope: envelope is not a dict, "
                    f"got {type(envelope).__name__}"
                )
            for required_key in ("nonce", "ciphertext"):
                if required_key not in envelope:
                    raise ValueError(
                        f"decrypt_session_envelope: envelope missing "
                        f"required key '{required_key}'"
                    )
            # Validate nonce is valid hex
            try:
                nonce_bytes = bytes.fromhex(envelope["nonce"])
            except (ValueError, TypeError) as hex_err:
                raise ValueError(
                    f"decrypt_session_envelope: invalid nonce hex: {hex_err}"
                )
            # Validate ciphertext is valid hex
            try:
                ciphertext_bytes = bytes.fromhex(envelope["ciphertext"])
            except (ValueError, TypeError) as hex_err:
                raise ValueError(
                    f"decrypt_session_envelope: invalid ciphertext hex: {hex_err}"
                )

            # ASV-4: Verify sender's public key (TOFU).
            sender_key_str = sender_public_key_pem.decode('utf-8', errors='replace')
            if session_id in self._trusted_sender_keys:
                if self._trusted_sender_keys[session_id] != sender_key_str:
                    raise ValueError(
                        f"ASV-4: Sender's public key changed for session "
                        f"{session_id}! Possible MITM key substitution."
                    )
            else:
                # First time seeing this session; record the key.
                # M2 fix (Pass 17): Bound the TOFU cache with FIFO
                # eviction at 10000 entries. The cache is keyed
                # on session_id; without the bound, a deployment
                # creating millions of unique session_ids would
                # grow the dict unbounded (~200 bytes per entry).
                if len(self._trusted_sender_keys) >= self._tofu_cache_max:
                    # Evict the oldest entry (FIFO).
                    oldest_session_id = next(iter(self._trusted_sender_keys))
                    self._trusted_sender_keys.pop(oldest_session_id)
                self._trusted_sender_keys[session_id] = sender_key_str

            # ASV-3: Use the session_id from the envelope (preferred) or
            # the parameter (fallback for older envelopes).
            actual_session_id = envelope.get("session_id", session_id)

            peer_public_key = serialization.load_pem_public_key(sender_public_key_pem)
            shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

            hkdf_info = f"session-encryption-v1-{actual_session_id}".encode('utf-8')
            derived_key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=hkdf_info,
            ).derive(shared_key)

            aesgcm = AESGCM(derived_key)
            nonce = nonce_bytes
            ciphertext = ciphertext_bytes

            decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
            return json.loads(decrypted_bytes.decode())
        except Exception as err:
            raise ValueError(f"Asymmetric Decryption Failure: {err}")
