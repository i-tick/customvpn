#!/usr/bin/env python3
# server.py

import os
import fcntl
import struct
import subprocess
import socket
import ssl
import select
import signal
import logging
import sys
from datetime import datetime

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
SOCKET_TIMEOUT = 30  # seconds

# Global state for cleanup
active_connection = False
tun_fd = None
tls_conn = None
public_interface = None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_default_interface():
    """
    Auto-detect the default network interface by parsing 'ip route'.
    Returns the interface name (e.g., 'eth0', 'ens3').
    """
    try:
        result = subprocess.run(
            "ip route | grep ^default",
            shell=True, capture_output=True, text=True, check=True
        )
        # Output format: "default via <gw> dev <interface> ..."
        parts = result.stdout.split()
        if 'dev' in parts:
            idx = parts.index('dev')
            if idx + 1 < len(parts):
                return parts[idx + 1]
    except Exception as e:
        logging.warning(f"Failed to auto-detect interface: {e}. Falling back to 'eth0'")
    return "eth0"


def create_tun_interface(ifname: str):
    """
    Opens /dev/net/tun and configures a TUN device with the given ifname.
    Returns the file descriptor for the TUN device.
    """
    try:
        tun_fd = os.open(TUN_DEVICE, os.O_RDWR)
        # ioctl to create TUN and name it ifname
        ifr = struct.pack("16sH", ifname.encode(), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(tun_fd, 0x400454ca, ifr)  # TUNSETIFF
        return tun_fd
    except Exception as e:
        logging.error(f"Failed to create TUN interface: {e}")
        sys.exit(1)


def setup_server_tun():
    """
    Configures the TUN interface on the server:
    - Assign IP 10.8.0.1/24 to tun0
    - Bring interface up
    - Enable IP forwarding & NAT (iptables)
    """
    global public_interface
    
    try:
        # 1. Create TUN
        tun_fd = create_tun_interface(VPN_INTERFACE)
        logging.info(f"TUN device created (fd={tun_fd})")
        
        # 2. Assign IP and bring up
        subprocess.run(f"ip addr add {SERVER_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)
        logging.info(f"Assigned IP {SERVER_TUN_IP}/24 to {VPN_INTERFACE}")
        
        subprocess.run(f"ip link set dev {VPN_INTERFACE} up".split(), check=True)
        logging.info(f"Brought up {VPN_INTERFACE}")

        # 3. Enable IP forwarding
        subprocess.run("sysctl -w net.ipv4.ip_forward=1".split(), check=True)
        logging.info("Enabled IP forwarding")

        # 4. Auto-detect public interface and configure NAT
        public_interface = get_default_interface()
        subprocess.run(f"iptables -t nat -A POSTROUTING -o {public_interface} -j MASQUERADE".split(), check=True)
        logging.info(f"Configured NAT (MASQUERADE) on {public_interface}")

        return tun_fd
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to configure TUN: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error in setup_server_tun: {e}")
        sys.exit(1)


def start_tls_server_and_accept():
    """
    Creates a TCP socket on (0.0.0.0, 8443), wraps it in TLS, and accepts one client.
    Returns the SSL-wrapped client socket.
    """
    try:
        # Verify certificate files exist
        if not os.path.exists(CERT_FILE):
            raise FileNotFoundError(f"Certificate file not found: {CERT_FILE}")
        if not os.path.exists(KEY_FILE):
            raise FileNotFoundError(f"Key file not found: {KEY_FILE}")
        
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
        logging.info(f"Loaded TLS certificates from {CERT_FILE} and {KEY_FILE}")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LISTEN_ADDR, LISTEN_PORT))
        sock.listen(1)
        logging.info(f"Server listening on {LISTEN_ADDR}:{LISTEN_PORT} (TLS) ...")

        client_sock, addr = sock.accept()
        logging.info(f"TCP connection from {addr}. Wrapping in TLS ...")
        client_sock.settimeout(SOCKET_TIMEOUT)
        
        tls_conn = context.wrap_socket(client_sock, server_side=True)
        tls_conn.settimeout(SOCKET_TIMEOUT)
        logging.info(f"TLS handshake completed with {addr}")
        return tls_conn
    except FileNotFoundError as e:
        logging.error(f"Certificate error: {e}")
        sys.exit(1)
    except socket.error as e:
        logging.error(f"Socket error: {e}")
        sys.exit(1)
    except ssl.SSLError as e:
        logging.error(f"TLS error: {e}")
        sys.exit(1)


def forward_traffic(tun_fd, tls_conn):
    """
    Main I/O loop: multiplexes between the TUN interface and TLS socket.
    - Any packet read from TUN → send over tls_conn.
    - Any packet read from tls_conn → write into TUN.
    """
    global active_connection
    active_connection = True
    packets_forwarded = 0
    
    try:
        while active_connection:
            # Wait until either tun_fd or tls_conn is ready for reading
            rlist, _, _ = select.select([tun_fd, tls_conn], [], [], 1.0)

            if tun_fd in rlist:
                try:
                    # Packet from TUN (this is a raw IP packet destined to go out)
                    packet = os.read(tun_fd, 4096)
                    if not packet:
                        logging.info("TUN interface closed")
                        break
                    # Send via TLS to the client
                    tls_conn.sendall(packet)
                    packets_forwarded += 1
                except OSError as e:
                    logging.error(f"Error reading from TUN: {e}")
                    break
                except socket.error as e:
                    logging.error(f"Error sending to client: {e}")
                    break

            if tls_conn in rlist:
                try:
                    # Packet coming from client (encrypted IP packet)
                    data = tls_conn.recv(4096)
                    if not data:
                        # Client closed
                        logging.info("Client closed connection")
                        break
                    # Write raw IP packet into server's TUN
                    os.write(tun_fd, data)
                    packets_forwarded += 1
                except socket.timeout:
                    logging.warning("Socket timeout - connection idle")
                    break
                except socket.error as e:
                    logging.error(f"Error reading from client: {e}")
                    break
    finally:
        active_connection = False
        logging.info(f"Stopped forwarding (total packets: {packets_forwarded})")


def cleanup():
    """
    Graceful cleanup: remove NAT rules, bring down TUN interface, close sockets.
    """
    global tun_fd, tls_conn, public_interface
    
    logging.info("Starting cleanup...")
    
    try:
        if tls_conn:
            tls_conn.close()
            logging.info("Closed TLS connection")
    except Exception as e:
        logging.warning(f"Error closing TLS connection: {e}")
    
    try:
        if tun_fd is not None:
            os.close(tun_fd)
            logging.info("Closed TUN device")
    except Exception as e:
        logging.warning(f"Error closing TUN device: {e}")
    
    # Remove TUN interface
    try:
        subprocess.run(f"ip link delete {VPN_INTERFACE}".split(), check=True)
        logging.info(f"Removed {VPN_INTERFACE}")
    except Exception as e:
        logging.warning(f"Error removing TUN interface: {e}")
    
    # Remove NAT rule
    if public_interface:
        try:
            subprocess.run(
                f"iptables -t nat -D POSTROUTING -o {public_interface} -j MASQUERADE".split(),
                check=True
            )
            logging.info(f"Removed NAT rule for {public_interface}")
        except Exception as e:
            logging.warning(f"Error removing NAT rule: {e}")
    
    logging.info("Cleanup completed")


def signal_handler(signum, frame):
    """
    Handle SIGINT and SIGTERM for graceful shutdown.
    """
    logging.info(f"Received signal {signum}. Shutting down...")
    cleanup()
    sys.exit(0)


def main():
    global tun_fd, tls_conn
    
    if os.geteuid() != 0:
        logging.error("This script must be run as root.")
        sys.exit(1)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logging.info("="*60)
    logging.info("Custom VPN Server Starting")
    logging.info("="*60)

    try:
        # 1. Set up the server TUN interface
        tun_fd = setup_server_tun()
        logging.info(f"TUN interface {VPN_INTERFACE} configured with {SERVER_TUN_IP}/24")

        # 2. Wait for a client to connect over TLS
        tls_conn = start_tls_server_and_accept()

        # 3. Forward traffic between TUN and TLS socket
        logging.info("Starting packet forwarding ...")
        forward_traffic(tun_fd, tls_conn)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received")
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
