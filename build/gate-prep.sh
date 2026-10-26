#!/usr/bin/env bash
LOG=/home/hatch/workspace/rowyn/build/gate-prep.log
export http_proxy=http://hatch-egress-proxy:3128 https_proxy=http://hatch-egress-proxy:3128 HTTP_PROXY=http://hatch-egress-proxy:3128 HTTPS_PROXY=http://hatch-egress-proxy:3128
exec >>"$LOG" 2>&1
echo "=== gate prep $(date) ==="
for i in $(seq 1 100); do pgrep -x apt-get >/dev/null || pgrep -x dpkg >/dev/null || break; sleep 15; done
apt-get update -qq 2>&1 | tail -1
apt-get install -y -qq podman qemu-system-x86 qemu-utils ovmf 2>&1 | tail -2
mkdir -p /etc/containers
printf '[storage]\ndriver = "vfs"\ngraphroot = "/home/hatch/containers-storage"\nrunroot = "/home/hatch/containers-runroot"\n' > /etc/containers/storage.conf
printf '[engine]\ncgroup_manager = "cgroupfs"\n' > /etc/containers/containers.conf
for t in podman qemu-system-x86_64; do command -v $t >/dev/null && echo "$t OK" || echo "$t MISSING"; done
IMG=ghcr.io/kyth-os/rowyn:spike
ok=0
for a in 1 2 3; do podman pull "$IMG" && { ok=1; break; }; echo "anon pull attempt $a failed"; sleep 10; done
if [ $ok -eq 0 ]; then
  TOKEN=$(grep 'github\.com' ~/.git-credentials | sed -E 's|https://[^:]+:([^@]+)@github.com|\1|' | head -1)
  echo "$TOKEN" | podman login ghcr.io -u mrtrick37 --password-stdin >/dev/null 2>&1 && echo "ghcr login OK"
  for a in 1 2 3; do podman pull "$IMG" && { ok=1; break; }; echo "auth pull attempt $a failed"; sleep 10; done
fi
podman images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -i rowyn || true
[ $ok -eq 1 ] && echo "PREP_OK" || echo "PREP_FAIL"
