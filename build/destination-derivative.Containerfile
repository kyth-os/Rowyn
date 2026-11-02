ARG ROWYN_BASE_IMAGE
FROM ${ROWYN_BASE_IMAGE}

# This file is intentionally new and destination-only. bootc switch rejects a
# target whose composefs fs-verity digest is already deployed; adding this
# rootfs marker keeps the destination deployment distinct from the old-channel
# bridge and every pre-migration image.
COPY build/registry-migration-destination-v1 /usr/share/rowyn-registry-migration-destination-v1
