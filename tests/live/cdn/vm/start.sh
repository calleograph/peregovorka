#!/bin/sh
# start.sh — настоящая ВМ (QEMU без KVM) внутри обычного контейнера, без --privileged: испытательный «сервер» для установки узла CDN.
# Контейнер слушает порты 22/80/443 и пробрасывает их в ВМ. /base/$IMAGE — облачный образ (только чтение), состояние ВМ — в оверлее контейнера.
# Привилегированные контейнеры с systemd на общем сервере запрещены: их getty садятся на терминалы хоста (2026-11-04 это повесило PID 1 хоста).
set -eu
ARCH=${ARCH:-x86_64}; CPUS=${CPUS:-4}; MEM=${MEM:-3072}
: "${LAB_PASSWORD:?нужен пароль администратора ВМ}"
cd /run
qemu-img create -q -f qcow2 -F qcow2 -b "/base/$IMAGE" disk.qcow2 ${DISK:-24G}
cat > user-data <<UD
#cloud-config
ssh_pwauth: true
users:
  - name: cdnadmin
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: "$LAB_PASSWORD"
UD
printf 'instance-id: %s\nlocal-hostname: %s\n' "$(hostname)" "${VM_NAME:-pgcdnvm}" > meta-data
genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data
NET="user,id=n0,hostfwd=tcp::22-:22,hostfwd=tcp::80-:80,hostfwd=tcp::443-:443"
if [ "$ARCH" = aarch64 ]; then
  exec qemu-system-aarch64 -M virt -cpu max -accel tcg,thread=multi -smp "$CPUS" -m "$MEM" -nographic -bios /usr/share/qemu-efi-aarch64/QEMU_EFI.fd \
    -drive file=disk.qcow2,if=virtio -drive file=seed.iso,if=virtio,format=raw -netdev "$NET" -device virtio-net-pci,netdev=n0
fi
exec qemu-system-x86_64 -accel tcg,thread=multi -cpu max -smp "$CPUS" -m "$MEM" -nographic \
  -drive file=disk.qcow2,if=virtio -drive file=seed.iso,if=virtio,format=raw -netdev "$NET" -device virtio-net-pci,netdev=n0
