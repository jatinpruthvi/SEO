# ==============================================================================
# Browser-Runtime Dockerfile v6.0+ (1-persona, MAX_INVISIBILITY, x86_64)
# ==============================================================================
# v6.0+ changes:
# - AMD64 base image (not ARM64) for CloakBrowser compatibility
# - UID 1000 user "orchestrator" created for privilege drop in entrypoint.sh
# - /app and /opt/venv owned by UID 1000
# - libnss3, libdrm, libgbm, libasound2 added for Chromium headless audio/video
# - chrony installed for NTP time-sync (prevents container clock drift
#   detection; Date.now() skew is a fingerprint)
# - sudo, util-linux (for mount/umount if needed by panic handler)
# - iproute2, iptables, procps for runtime introspection
# - Memory-constrained (1 GB total RAM on E2.1.Micro)
# ==============================================================================

# v6.0+: AMD64 image (not ARM64) for CloakBrowser compatibility.
# CloakBrowser is Chrome-based and only has x86_64 builds.
# R7-DOK-001: Explicitly pin to linux/amd64. On Apple Silicon Mac
# build hosts, `docker buildx build` may pull the ARM64 variant
# of this image unless --platform is set. The v6.0 changelog
# claimed this was fixed, but the FROM line itself didn't include
# the platform flag.
FROM --platform=linux/amd64 mcr.microsoft.com/playwright:v1.44.0-jammy

ENV DEBIAN_FRONTEND=noninteractive

# R7-DOK-002: ARG declared AFTER FROM is the correct pattern for
# build-arg toggles. The arg is only available in subsequent RUN
# instructions (not in the FROM line). This is the standard Docker
# pattern for conditional installs. Build with:
#   docker build --build-arg INSTALL_PLAYWRIGHT_CHROMIUM=1 .
# to include the Chromium download, or omit the arg (defaults to 0)
# to use CloakBrowser at runtime instead.
ARG INSTALL_PLAYWRIGHT_CHROMIUM=0

# Update system and install runtime dependencies
# R7-DOK-005: Pre-accept mscorefonts EULA. This must run before
# the install block because debconf selections are not persistent
# across RUN commands.
RUN echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | debconf-set-selections

# R7-DOK-006: The install block is intentionally a single RUN
# command. Splitting into multiple RUN commands would create
# separate Docker layers, each requiring apt-get update and
# increasing the image size by ~50MB per layer. A single RUN
# command is the standard pattern for minimal image size.
# Update system and install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-venv \
    xvfb \
    x11-xkb-utils \
    x11-apps \
    # v6.0.3: X11 utilities required by entrypoint.sh
    x11-xserver-utils \
    x11-utils \
    # v6.0.3: DBus daemon and tools (required for ChromeBrowser)
    dbus \
    dbus-x11 \
    # v6.0.3: PulseAudio and ALSA (required for AudioContext)
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    libasound2 \
    libpulse0 \
    # v6.0.3: Additional fonts for Windows persona fidelity
    fonts-liberation \
    fonts-noto-core \
    fonts-dejavu-core \
    fonts-dejavu-extra \
    fonts-freefont-ttf \
    # R7-DOK-005: Pre-accept the Microsoft EULA for msttcorefonts
    # to avoid the interactive prompt during apt-get install.
    # This is set BEFORE the install line so the package
    # installation doesn't hang waiting for input.
    # (debconf-set-selections must be in a separate RUN before the
    # install, since RUN commands don't share state.)
    ttf-mscorefonts-installer \
    # R7-DOK-008: cabextract is required by ttf-mscorefonts-installer
    # to extract the .exe font files from Microsoft's CAB archives.
    # Without cabextract, mscorefonts install fails.
    cabextract \
    iptables \
    iproute2 \
    tzdata \
    locales \
    # v5.0+: chrony for NTP sync (Date.now() drift detection)
    chrony \
    # v5.0+: libnss3, libdrm, libgbm, libasound2 for Chromium audio/video
    libnss3 \
    libdrm2 \
    libgbm1 \
    # v5.0+: util-linux for mount/umount (panic-button handler)
    util-linux \
    # v5.0+: procps for ps/pkill (zombie reaping)
    procps \
    # v5.0+: kmod for tc (netem cleanup)
    kmod \
    # v5.0+: sudo (used by entrypoint.sh for privilege drop)
    sudo \
    # v6.0.1: tini for proper signal handling (PID 1 wrapper)
    # tini reaps zombies and forwards signals (SIGTERM, SIGUSR1 panic) to the
    # actual entrypoint.sh process. The --grace-period=90000 flag (90s) gives
    # the orchestrator time to wipe tmpfs and clean tc qdisc before SIGKILL.
    tini \
    # NET-1: curl for the network pre-flight audit (network_audit.sh).
    curl \
    && rm -rf /var/lib/apt/lists/*

# R7-NEW-007: Resolve tini path at BUILD time, not runtime.
# The previous Dockerfile used `$(command -v tini || echo /usr/bin/tini)` in
# the ENTRYPOINT, which means every container start had to spawn a shell,
# run `command -v tini`, and resolve the path. This added ~5ms to startup
# (negligible on its own, but noticeable in tight CI loops running
# hundreds of containers). We now resolve the path at image build time
# and bake it into the ENTRYPOINT, saving the runtime shell fork.
# If tini's location changes, the image must be rebuilt.
RUN TINI_PATH="$(command -v tini || echo /usr/bin/tini)" && \
    echo "R7-NEW-007: tini resolved at build time: ${TINI_PATH}" && \
    printf '#!/bin/sh\nexec %s --grace-period=90000 -- /entrypoint.sh "$@"\n' "${TINI_PATH}" > /usr/local/bin/sre_entrypoint && \
    chmod +x /usr/local/bin/sre_entrypoint

# Set standard UTF-8 locale parameters
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

WORKDIR /app

# v5.0+: Create UID 1000 user for privilege drop.
# The entrypoint.sh runs as root (for iptables) and execs the orchestrator
# as UID 1000 via `su -s /bin/bash -c "exec \"\$@\"" 1000 -- "$@"`.
RUN groupadd -g 1000 orchestrator 2>/dev/null || true \
    && useradd -u 1000 -g 1000 -m -s /bin/bash orchestrator 2>/dev/null || true

# TrueType font directories copying setup
RUN mkdir -p /usr/share/fonts/truetype/msttcorefonts/
RUN fc-cache -f -v

# Initialize secure python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install production libraries
# R7-DOK-009: The Playwright base image includes test fixtures
# (browsers, test files) that are visible in the filesystem.
# A sibling container in a shared pod can see these artifacts.
# Mitigation in scope: the test fixtures are at /root/.cache/ms-playwright/
# which is owned by root and not readable by UID 1000 (the
# orchestrator user). The orchestrator uses CloakBrowser at
# /opt/cloakbrowser instead, not Playwright's bundled Chromium.
# Full mitigation (switching to a minimal base like python:3.11-slim)
# is a Phase C architectural change.
RUN pip install --no-cache-dir \
    playwright \
    browserforge \
    loguru \
    asyncpg \
    redis \
    cryptography \
    beautifulsoup4 \
    numpy \
    # v5.0+: httpx for proxy integrity checks
    httpx \
    # v5.0+: aiofiles for async file I/O (encrypted session archive read)
    aiofiles

# Firefox telemetry disabling
RUN mkdir -p /etc/firefox/
RUN echo 'lockPref("toolkit.telemetry.enabled", false);' > /etc/firefox/syspref.js \
    && echo 'lockPref("datareporting.healthreport.uploadEnabled", false);' >> /etc/firefox/syspref.js \
    && echo 'lockPref("media.peerconnection.enabled", false);' >> /etc/firefox/syspref.js \
    && echo 'lockPref("media.peerconnection.ice.no_host", true);' >> /etc/firefox/syspref.js

# v5.0+: chrony hardened config (minimal, just time sync)
COPY config/chrony_hardened.conf /etc/chrony/chrony.conf
RUN chmod 644 /etc/chrony/chrony.conf

# R7-DOK-013: Use COPY --chown instead of COPY + chown. The previous
# pattern was slow because chown -R on /opt/venv (1GB+) runs at
# build time, even though the files were just copied. With
# COPY --chown, the ownership is set during the COPY, eliminating
# the separate chown step.
COPY --chown=1000:1000 hardened_orchestrator.py /app/hardened_orchestrator.py
COPY --chown=1000:1000 prototype_hardener.js /app/prototype_hardener.js
COPY --chown=1000:1000 session_serializer.py /app/session_serializer.py
COPY --chown=1000:1000 humanized_movement.py /app/humanized_movement.py
COPY --chown=1000:1000 paste_emulator.py /app/paste_emulator.py
COPY --chown=1000:1000 ebpf_sentinel.py /app/ebpf_sentinel.py
COPY --chown=1000:1000 dynamic_pacing.py /app/dynamic_pacing.py
COPY --chown=1000:1000 curl_cffi_handover.py /app/curl_cffi_handover.py
COPY --chown=1000:1000 circadian_scheduler.py /app/circadian_scheduler.py
COPY --chown=1000:1000 sticky_proxy_resolver.py /app/sticky_proxy_resolver.py
COPY --chown=1000:1000 profile_archiver.py /app/profile_archiver.py
COPY --chown=1000:1000 behavioral_entropy.py /app/behavioral_entropy.py
COPY --chown=1000:1000 hci_reading_model.py /app/hci_reading_model.py
COPY --chown=1000:1000 keyboard_emulator.py /app/keyboard_emulator.py
COPY --chown=1000:1000 asymmetric_vault.py /app/asymmetric_vault.py
COPY --chown=1000:1000 identity_health.py /app/identity_health.py
COPY --chown=1000:1000 input_sanitizer.py /app/input_sanitizer.py
COPY --chown=1000:1000 prompt_guard.py /app/prompt_guard.py

# v5.0+: chown /opt/venv to UID 1000 (the COPY --chown above handles /app).
# For /opt/venv we still need a chown because the venv was created by
# `python3 -m venv` as root.
RUN chown -R 1000:1000 /opt/venv

# v5.0+: ensure /mnt/ramdisk is owned by UID 1000 (writeable by the
# orchestrator process after privilege drop)
RUN mkdir -p /mnt/ramdisk && chown 1000:1000 /mnt/ramdisk && chmod 755 /mnt/ramdisk

# v6.0+: Install Playwright's bundled Chromium for system utilities only.
# The actual browser is CloakBrowser (Chrome-based), mounted at runtime
# from the host at /opt/cloakbrowser/chrome. Playwright's Chromium is
# installed as a fallback for direct headless tasks that don't need
# full Chrome fidelity (proprietary codecs, Widevine, etc.).
# DKF-1: gated on INSTALL_PLAYWRIGHT_CHROMIUM build arg so the image
# can be built without the 300 MB Chromium download and without the
# outbound build-time egress to cdn.playwright.dev.
# DKF-4: drop the `|| true`; let the install fail the build if it
# actually fails, so a broken base image doesn't silently ship.
RUN if [ "$INSTALL_PLAYWRIGHT_CHROMIUM" = "1" ]; then \
      playwright install chromium 2>&1 | tail -5; \
    else \
      echo "Skipping Playwright Chromium install (INSTALL_PLAYWRIGHT_CHROMIUM=$INSTALL_PLAYWRIGHT_CHROMIUM)"; \
    fi

# v6.0+: Verify CloakBrowser mount point will be available at runtime
RUN mkdir -p /opt/cloakbrowser && \
    echo "CloakBrowser will be mounted at runtime from host:/opt/cloakbrowser"

# v5.0+: COPY the entrypoint script (was missing in v4.0 Dockerfile)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# v6.0.1: explicit CMD so container knows what to run.
# The CMD is the python orchestrator. The ENTRYPOINT (tini) wraps
# this in a proper PID 1 signal handler with 90s grace period.
# entrypoint.sh runs INSIDE tini (still as root, for iptables).
CMD ["/opt/venv/bin/python3", "hardened_orchestrator.py"]

# v6.0.1: tini as PID 1 wraps the entire command chain. It:
# - Reaps zombie processes
# - Forwards SIGTERM/SIGINT to the actual process tree
# - Honors the --grace-period (90s) for clean shutdown
# - The full command is: tini -- /entrypoint.sh python3 hardened_orchestrator.py
#   (tini is invoked directly; entrypoint.sh is then exec'd)
# v5.0+: ENTRYPOINT must be last. entrypoint.sh drops to UID 1000
# before exec'ing the CMD.
# R7-DOK-003: The /etc/profile.d/tini_path.sh RUN block was
# dead code. Nothing in the runtime reads TINI_PATH from that file.
# R7-NEW-007: tini path is resolved at BUILD time (see RUN block above)
# and baked into /usr/local/bin/sre_entrypoint. This eliminates the
# per-startup shell fork + `command -v tini` resolution (~5ms savings).
ENTRYPOINT ["/usr/local/bin/sre_entrypoint"]
