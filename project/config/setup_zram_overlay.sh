#!/bin/bash
# ZRAM compressed in-memory block device for OCI E2.1.Micro (1 GB RAM)
# =============================================================================
# v6.0+: This script is OPTIONAL for E2.1.Micro. With only 1 GB RAM,
# ZRAM is NOT recommended (it adds CPU overhead for compression/decompression
# and competes for the limited memory). We keep this script for users
# who want to experiment, but recommend using tmpfs only for the
# default E2.1.Micro setup.
#
# If you have a larger instance (e.g., 4 GB or more), ZRAM can provide
# a small performance boost for write-heavy workloads. The default
# 8G ZRAM is appropriate for 8+ GB instances, not 1 GB.
# =============================================================================

MOUNT_DIR="/mnt/compressed_ramdisk"

# 1. Check for teardown flag
if [ "$1" == "--teardown" ]; then
    echo "Initiating safe unmount and teardown of ZRAM memory workspaces..."
    if mountpoint -q "$MOUNT_DIR"; then
        sudo umount -l "$MOUNT_DIR"
    fi
    if [ -b /dev/zram0 ]; then
        sudo zramctl --reset /dev/zram0
    fi
    echo "Teardown completed successfully. ZRAM block device released."
    exit 0
fi

# 2. Idempotency Check
if mountpoint -q "$MOUNT_DIR"; then
    echo "ZRAM compressed RAM disk is already mounted at $MOUNT_DIR. Skipping mount setup."
    exit 0
fi

# v6.0+: Check available memory before allocating ZRAM.
# We don't want to allocate 8 GB ZRAM on a 1 GB system.
TOTAL_MEM_MB=$(grep MemTotal /proc/meminfo | awk '{print int($2/1024)}')
if [ "$TOTAL_MEM_MB" -lt 2048 ]; then
    echo "WARNING: System has ${TOTAL_MEM_MB} MB RAM. ZRAM is not recommended for <2 GB systems."
    echo "         Recommend using tmpfs only (already configured in docker-compose.yml)."
    echo "         Skipping ZRAM setup."
    exit 0
fi

echo "Initializing compressed ZRAM device (ZSTD algorithm) on OCI Host..."

# 3. Initialize compressed ZRAM device
sudo modprobe zram
# v6.0+: ZRAM size is now 25% of total RAM (max 2 GB), not fixed 8G.
ZRAM_SIZE_MB=$((TOTAL_MEM_MB / 4))
if [ "$ZRAM_SIZE_MB" -gt 2048 ]; then
    ZRAM_SIZE_MB=2048
fi
if ! zramctl /dev/zram0 &>/dev/null; then
    sudo zramctl --algorithm zstd --size "${ZRAM_SIZE_MB}M" /dev/zram0
fi

# 4. Format and mount ZRAM compressed space
sudo mkfs.ext4 -F -O ^has_journal -b 4096 -L RAM_DISK /dev/zram0
sudo mkdir -p "$MOUNT_DIR"
sudo mount -o noatime,nodiratime,nosuid,nodev,commit=60 /dev/zram0 "$MOUNT_DIR"

# 5. Create workspace directories
sudo mkdir -p "$MOUNT_DIR/base_profile"
sudo mkdir -p "$MOUNT_DIR/worker_workspaces"
sudo mkdir -p "$MOUNT_DIR/overlay_workdir"

logger -t ZRAM_SETUP "Dynamic compressed memory workspaces initialized (${ZRAM_SIZE_MB}MB)."
echo "ZRAM RAM Disk mounted successfully at $MOUNT_DIR (${ZRAM_SIZE_MB}MB)."
