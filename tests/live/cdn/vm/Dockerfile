FROM debian:13
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends qemu-system-x86 qemu-system-arm qemu-efi-aarch64 qemu-utils genisoimage ipxe-qemu seabios && rm -rf /var/lib/apt/lists/*
