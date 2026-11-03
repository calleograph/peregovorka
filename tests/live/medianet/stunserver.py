"""Минимальный STUN-сервер (RFC 5389, Binding) для испытательной сети: отвечает адресом, с которого пришёл запрос (XOR-MAPPED-ADDRESS)."""
import socket
import struct
import sys

COOKIE = 0x2112A442
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 3478))
while True:
    data, (ip, port) = s.recvfrom(2048)
    if len(data) < 20 or struct.unpack("!HHI", data[:8])[0] != 0x0001 or struct.unpack("!I", data[4:8])[0] != COOKIE:
        continue
    tid = data[8:20]
    xip = struct.unpack("!I", socket.inet_aton(ip))[0] ^ COOKIE
    attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, port ^ (COOKIE >> 16), xip)
    s.sendto(struct.pack("!HHI", 0x0101, len(attr), COOKIE) + tid + attr, (ip, port))
    print(f"stun {ip}:{port}", flush=True)
