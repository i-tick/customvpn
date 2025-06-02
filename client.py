#!/usr/bin/env python3
# client.py

import os
import fcntl
import struct
import subprocess
import socket
import ssl
import select

# TUN/TAP constants
TUN_DEVICE = "/dev/net/tun"
IFF_TUN   = 0x0001
IFF_NO_PI = 0x1000

# VPN network config
CLIENT_TUN_IP   = "10.8.0.2"
CLIENT_TUN_NETMASK = "255.255.255.0"
SERVER_TUN_IP   = "10.8.0.1"
VPN_INTERFACE   = "tun0"

# TLS config
SERVER_ADDR = "SERVER_PUBLIC_IP_OR_HOSTNAME"
SERVER_PORT = 8443
# CLIENT_VERIFY = False                # for POC, disable server cert verification
CLIENT_VERIFY = True                   # to verify, copy server.crt as ca.crt
CA_CERT_FILE = "./certs/server.crt"    # Path to the server's certificate (or CA bundle)


def create_tun_interface(ifname: str):
    """
    Opens /dev/net/tun and configures a TUN device with the given ifname.
    Returns the file descriptor for the TUN device.
    """
    tun_fd = os.open(TUN_DEVICE, os.O_RDWR)
    ifr = struct.pack("16sH", ifname.encode(), IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(tun_fd, 0x400454ca, ifr)  # TUNSETIFF
    return tun_fd


def setup_client_tun():
    """
    Configures the TUN interface on the client:
    - Assign IP 10.8.0.2/24 to tun0
    - Bring interface up
    - Add a route so that traffic to VPN network goes via tun0
    """
    tun_fd = create_tun_interface(VPN_INTERFACE)
    # Assign IP and bring up
    subprocess.run(f"ip addr add {CLIENT_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)
    subprocess.run(f"ip link set dev {VPN_INTERFACE} up".split(), check=True)

    # Route all traffic destined for 10.8.0.0/24 into tun0 (optional if default route is changed)
    # subprocess.run(f"ip route add {SERVER_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)

    return tun_fd


def connect_to_server_tls():
    """
    Creates a TCP socket, wraps it in TLS (client side), and connects to the server.
    Returns the TLS-wrapped socket.
    """
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if CLIENT_VERIFY:
        context.load_verify_locations(cafile=CA_CERT_FILE)
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.connect((SERVER_ADDR, SERVER_PORT))
    tls_conn = context.wrap_socket(raw_sock, server_hostname=SERVER_ADDR)
    print("[+] TLS handshake with server completed.")
    return tls_conn


def forward_traffic(tun_fd, tls_conn):
    """
    Main I/O loop: multiplexes between the TUN interface and TLS socket.
    """
    while True:
        rlist, _, _ = select.select([tun_fd, tls_conn], [], [])

        if tun_fd in rlist:
            # Packet from TUN (raw IP packet to send to server)
            packet = os.read(tun_fd, 4096)
            if not packet:
                break
            try:
                tls_conn.sendall(packet)
            except Exception as e:
                print(f"[!] Error sending packet to server: {e}")
                break

        if tls_conn in rlist:
            # Packet coming from server → raw IP packet to write into TUN
            try:
                data = tls_conn.recv(4096)
            except Exception as e:
                print(f"[!] Error reading from server: {e}")
                break

            if not data:
                # Server closed
                break

            os.write(tun_fd, data)


def main():
    if os.geteuid() != 0:
        print("[-] This script must be run as root.")
        return

    # 1. Set up the client TUN interface
    tun_fd = setup_client_tun()
    print(f"[+] TUN interface {VPN_INTERFACE} configured with {CLIENT_TUN_IP}/24")

    # 2. Connect to server over TLS
    tls_conn = connect_to_server_tls()

    # 3. Forward traffic between TUN and TLS socket
    print("[+] Starting packet forwarding ...")
    forward_traffic(tun_fd, tls_conn)

    # 4. Cleanup
    print("[*] Connection closed. Shutting down.")
    tls_conn.close()
    os.close(tun_fd)


if __name__ == "__main__":
    main()
