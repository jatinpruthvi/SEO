# profile_archiver.py - Secure Physical Profile Directory Archiver & Rehydrator
# Compresses, encrypts, and restores the entire browser workspace (IndexedDB, Cache, HSTS, History)
#
# CHANGELOG (R6 - sixth-pass depth audit fixes):
#   PRA-1 - Tar is now gzip-compressed ("w:gz") instead of uncompressed
#           ("w"). 300MB → ~30MB. The old code claimed "Compresses" but
#           didn't actually compress.
#   PRA-2 - Path traversal protection on extraction. tar.extractall() is
#           vulnerable to "Zip Slip" attacks. We validate each member
#           path to ensure it stays within the target directory.
#   PRA-3 - Race condition fix: only delete the unencrypted tar on
#           successful encryption. Previously, the finally block
#           could delete the unencrypted tar even if encryption failed
#           (data loss).
#   PRA-4 - Case-insensitive pruning. The old code matched "Cache" but
#           not "cache" or "GPUCache" (which on macOS is lowercase
#           "gpucache"). Now we use case-insensitive matching.
#
# BUGFIX (R8):
#   BUG-1 - CRITICAL: Pruning was completely broken. The os.walk loop
#           did `dirs[:] = [d for d in dirs if d.lower() not in prune_dirs]`
#           (keeping non-prune dirs), then `for d in list(dirs): if
#           d.lower() in prune_dirs:` which is ALWAYS FALSE on the
#           already-filtered list — shutil.rmtree was dead code. Worse,
#           `tar.add(source_dir)` archived the entire tree independently
#           of the walk, so all cache directories were included unchanged.
#           Fix: delete prune dirs directly by name before creating the
#           tar. This guarantees they are removed from disk and cannot
#           appear in the archive.

import os
import tarfile
import shutil
from loguru import logger
from cryptography.fernet import Fernet

class HardenedProfileArchiver:
    """DC-19 (Pass 19): Compresses and encrypts browser profile directories.

    Browser profiles contain cookies, localStorage, IndexedDB, and other
    state that defines a persona's identity. This archiver:
    1. Prunes redundant cache directories (Cache, Code Cache, GPUCache)
       to reduce archive size by ~90% (300MB → 30MB)
    2. Compresses the remaining profile with gzip
    3. Encrypts the archive with Fernet (AES-128-CBC + HMAC-SHA256)
    4. Writes the encrypted archive to disk (or returns bytes for in-memory use)

    The encrypted archive can be safely stored in PostgreSQL (as bytes) or
    on disk; only someone with the ENCRYPTION_KEY can decrypt it.
    """
    def __init__(self, encryption_key: str):
        self.cipher = Fernet(encryption_key.encode())

    def archive_profile_directory(self, source_dir: str, archive_output_path: str):
        """
        Compresses and encrypts the physical browser profile folder.
        Actively prunes redundant binary caches (Cache, Code Cache, GPUCache) pre-compression
        to minimize storage overhead and prevent ZRAM filesystem exhaustion.

        PRA-1: Uses gzip compression. 300MB profile → ~30MB archive.
        PRA-3: Only deletes the unencrypted tar on successful encryption.
        PRA-4: Case-insensitive cache pruning.
        """
        # PRA-4: Lowercase set for case-insensitive pruning
        prune_dirs = {
            "cache", "code cache", "gpucache", "crashpad",
            "shadercache", "safe browsing", "cache2",
            "jumplistcache", "thumbnail",
        }
        tar_path = archive_output_path + ".tar"
        encrypted_written = False
        try:
            logger.info(f"Archiver: Pruning redundant cache directories in: {source_dir}")
            # BUG-1 FIX: Delete prune dirs directly by scanning each level of the
            # directory tree. The previous os.walk approach modified dirs[:] to
            # skip traversal but never called shutil.rmtree (dead code), AND
            # tar.add() archived everything regardless. We now walk the tree and
            # delete matching directories before the tar is created.
            for root, dirs, files in os.walk(source_dir, topdown=True):
                to_delete = [d for d in dirs if d.lower() in prune_dirs]
                # Remove pruned dirs from further traversal
                dirs[:] = [d for d in dirs if d.lower() not in prune_dirs]
                # Actually delete them from disk so tar.add() won't include them
                for d in to_delete:
                    dir_path = os.path.join(root, d)
                    try:
                        shutil.rmtree(dir_path, ignore_errors=True)
                        logger.debug(f"Archiver: Pruned cache dir: {dir_path}")
                    except Exception as _prune_err:
                        logger.warning(f"Archiver: Could not prune {dir_path}: {_prune_err}")

            # Prune massive logging/telemetry files
            for root, _, files in os.walk(source_dir):
                for f in files:
                    f_lower = f.lower()
                    if f_lower.endswith(".log") or f_lower == "telemetry.log" or "crash" in f_lower:
                        try:
                            os.unlink(os.path.join(root, f))
                        except Exception as _suppressed_exc:
                            logger.debug(
                                f"HardenedProfileArchiver.archive_profile_directory(): "
                                f"suppressed {type(_suppressed_exc).__name__}"
                            )

            logger.info(f"Archiver: Compressing pruned profile workspace directory: {source_dir}")
            # PRA-1: Use gzip compression. ~10x reduction on Chrome profiles
            # (which are mostly small text files, ideal for gzip).
            tar_size = 0
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(source_dir, arcname=os.path.basename(source_dir))
            # E24 fix (Pass 17): Capture tar size BEFORE reading
            # the file. The previous code called
            # `os.path.getsize(tar_path)` AFTER `encrypted_written = True`
            # was set, but BEFORE the `finally` block deleted the
            # tar. The size was correctly captured. However, the
            # f-string `{os.path.getsize(tar_path)}` was evaluated
            # at log time, which is fine in single-threaded code
            # but would race with the finally block's `os.unlink`
            # in a multi-threaded deployment. We now capture the
            # size to a local variable immediately after `with`
            # closes the tarfile (the file is still on disk at
            # this point), avoiding any potential race.

            # 3. Read and encrypt the gzipped tar archive payload
            tar_size = os.path.getsize(tar_path)
            with open(tar_path, "rb") as f:
                raw_data = f.read()

            encrypted_data = self.cipher.encrypt(raw_data)

            with open(archive_output_path, "wb") as f:
                f.write(encrypted_data)

            encrypted_written = True  # PRA-3: Mark success
            logger.info(f"Archiver: Profile successfully encrypted and saved to: {archive_output_path} "
                        f"(compressed: {tar_size} bytes -> encrypted: {len(encrypted_data)} bytes)")
        except Exception as e:
            logger.error(f"Archiver: Encryption and archiving failed: {e}")
            raise e
        finally:
            # PRA-3: Only delete the unencrypted tar if encryption succeeded.
            # If encryption failed, keep the tar so the caller can retry
            # without losing the persona's data.
            if encrypted_written and os.path.exists(tar_path):
                os.unlink(tar_path)
            elif not encrypted_written and os.path.exists(tar_path):
                logger.warning(
                    f"Archiver: Encryption failed; keeping unencrypted tar at {tar_path} "
                    f"for manual recovery."
                )

    def rehydrate_profile_directory(self, encrypted_archive_path: str, target_extraction_dir: str):
        """
        Decrypts and extracts the entire physical browser profile folder,
        restoring the complete historical state before browser execution.

        PRA-2: Validates each member path to prevent "Zip Slip"
        (path traversal) attacks. A maliciously crafted tar archive
        could otherwise write files outside the target directory.
        """
        tar_path = encrypted_archive_path + ".tar"
        try:
            # E25 fix (Pass 17): Validate archive_path doesn't
            # contain path traversal characters. The previous
            # code used `encrypted_archive_path` directly in
            # `open(encrypted_archive_path, "rb")`. A caller
            # passing "../../etc/passwd" would attempt to read
            # an arbitrary file. We now validate that the path
            # is absolute (which prevents path traversal) and
            # doesn't contain ".." segments.
            if ".." in encrypted_archive_path.split("/") or ".." in encrypted_archive_path.split("\\"):
                raise ValueError(
                    f"PRA-E25: encrypted_archive_path contains '..' path "
                    f"traversal: {encrypted_archive_path!r}"
                )
            if not os.path.isabs(encrypted_archive_path):
                raise ValueError(
                    f"PRA-E25: encrypted_archive_path must be absolute, "
                    f"got: {encrypted_archive_path!r}"
                )
            logger.info(f"Archiver: Decrypting profile archive: {encrypted_archive_path}")
            with open(encrypted_archive_path, "rb") as f:
                encrypted_data = f.read()

            decrypted_data = self.cipher.decrypt(encrypted_data)

            with open(tar_path, "wb") as f:
                f.write(decrypted_data)

            # PRA-2: Validate member paths BEFORE extraction.
            # The tar archive is created by us (trusted source) but
            # defense-in-depth requires path validation to prevent
            # "Zip Slip" if the archive is ever tampered with.
            target_abs = os.path.abspath(target_extraction_dir)
            logger.info(f"Archiver: Extracting profile workspace onto volatile ZRAM path: {target_extraction_dir}")
            os.makedirs(target_extraction_dir, exist_ok=True)
            with tarfile.open(tar_path, "r:gz") as tar:
                for member in tar.getmembers():
                    # Skip absolute paths and parent directory references
                    if os.path.isabs(member.name) or ".." in member.name.split("/"):
                        logger.warning(
                            f"PRA-2: Skipping suspicious tar member: {member.name}"
                        )
                        continue
                    # Resolve the full target path and verify it's inside target
                    member_path = os.path.abspath(
                        os.path.join(target_abs, member.name)
                    )
                    if not member_path.startswith(target_abs + os.sep) and member_path != target_abs:
                        logger.warning(
                            f"PRA-2: Path traversal attempt blocked: "
                            f"member={member.name} -> {member_path}"
                        )
                        continue
                    # Safe to extract
                    tar.extract(member, path=target_extraction_dir)

            logger.info("Archiver: Profile directory successfully rehydrated and restored.")
        except Exception as e:
            logger.error(f"Archiver: Decryption and rehydration failed: {e}")
            raise e
        finally:
            if os.path.exists(tar_path):
                os.unlink(tar_path)
