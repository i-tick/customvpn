#!/usr/bin/env python3
# server.py

import os
import fcntl
import struct
import subprocess
import socket
import ssl
import select
import threading

# TUN/TAP constants
TUN_DEVICE = "/dev/net/tun"
IFF_TUN   = 0x0001
IFF_NO_PI = 0x1000

# VPN network config
SERVER_TUN_IP   = "10.8.0.1"
SERVER_TUN_NETMASK = "255.255.255.0"
CLIENT_TUN_IP   = "10.8.0.2"
VPN_INTERFACE   = "tun0"

# TLS config
LISTEN_ADDR = "0.0.0.0"
LISTEN_PORT = 8443
CERT_FILE   = "./certs/server.crt"
KEY_FILE    = "./certs/server.key"


def create_tun_interface(ifname: str):
    """
    Opens /dev/net/tun and configures a TUN device with the given ifname.
    Returns the file descriptor for the TUN device.
    """
    tun_fd = os.open(TUN_DEVICE, os.O_RDWR)
    # ioctl to create TUN and name it ifname
    ifr = struct.pack("16sH", ifname.encode(), IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(tun_fd, 0x400454ca, ifr)  # TUNSETIFF
    return tun_fd


def setup_server_tun():
    """
    Configures the TUN interface on the server:
    - Assign IP 10.8.0.1/24 to tun0
    - Bring interface up
    - Enable IP forwarding & NAT (iptables)
    """
    # 1. Create TUN
    tun_fd = create_tun_interface(VPN_INTERFACE)
    # 2. Assign IP and bring up
    subprocess.run(f"ip addr add {SERVER_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)
    subprocess.run(f"ip link set dev {VPN_INTERFACE} up".split(), check=True)

    # 3. Enable IP forwarding
    subprocess.run("sysctl -w net.ipv4.ip_forward=1".split(), check=True)

    # 4. Configure NAT: MASQUERADE traffic from VPN out of default interface (e.g. eth0)
    # Replace 'eth0' with your actual public-facing interface if needed
    subprocess.run("iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE".split(), check=True)

    return tun_fd


def start_tls_server_and_accept():
    """
    Creates a TCP socket on (0.0.0.0, 8443), wraps it in TLS, and accepts one client.
    Returns the SSL-wrapped client socket.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LISTEN_ADDR, LISTEN_PORT))
    sock.listen(1)
    print(f"[+] Server listening on {LISTEN_ADDR}:{LISTEN_PORT} (TLS) ...")

    client_sock, addr = sock.accept()
    print(f"[+] TCP connection from {addr}. Wrapping in TLS ...")
    tls_conn = context.wrap_socket(client_sock, server_side=True)
    print("[+] TLS handshake completed.")
    return tls_conn


def forward_traffic(tun_fd, tls_conn):
    """
    Main I/O loop: multiplexes between the TUN interface and TLS socket.
    - Any packet read from TUN → send over tls_conn.
    - Any packet read from tls_conn → write into TUN.
    """
    while True:
        # Wait until either tun_fd or tls_conn is ready for reading
        rlist, _, _ = select.select([tun_fd, tls_conn], [], [])

        if tun_fd in rlist:
            # Packet from TUN (this is a raw IP packet destined to go out)
            packet = os.read(tun_fd, 4096)
            if not packet:
                break
            # Send via TLS to the client
            try:
                tls_conn.sendall(packet)
            except Exception as e:
                print(f"[!] Error sending to client: {e}")
                break

        if tls_conn in rlist:
            # Packet coming from client (encrypted IP packet)
            try:
                data = tls_conn.recv(4096)
            except Exception as e:
                print(f"[!] Error reading from client: {e}")
                break

            if not data:
                # Client closed
                break

            # Write raw IP packet into server's TUN
            os.write(tun_fd, data)


def main():
    if os.geteuid() != 0:
        print("[-] This script must be run as root.")
        return

    # 1. Set up the server TUN interface
    tun_fd = setup_server_tun()
    print(f"[+] TUN interface {VPN_INTERFACE} configured with {SERVER_TUN_IP}/24")

    # 2. Wait for a client to connect over TLS
    tls_conn = start_tls_server_and_accept()

    # 3. Forward traffic between TUN and TLS socket
    print("[+] Starting packet forwarding ...")
    forward_traffic(tun_fd, tls_conn)

    # 4. Cleanup on exit
    print("[*] Connection closed. Shutting down.")
    tls_conn.close()
    os.close(tun_fd)
    # (Optionally) remove NAT rules or bring down the interface


if __name__ == "__main__":
    main()
