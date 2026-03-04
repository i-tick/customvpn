#!/usr/bin/env python3
# client.py

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
SOCKET_TIMEOUT = 30  # seconds
CONNECTION_RETRIES = 3
CONNECTION_RETRY_DELAY = 5  # seconds

# Global state for cleanup
active_connection = False
tun_fd = None
tls_conn = None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def create_tun_interface(ifname: str):
    """
    Opens /dev/net/tun and configures a TUN device with the given ifname.
    Returns the file descriptor for the TUN device.
    """
    try:
        tun_fd = os.open(TUN_DEVICE, os.O_RDWR)
        ifr = struct.pack("16sH", ifname.encode(), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(tun_fd, 0x400454ca, ifr)  # TUNSETIFF
        return tun_fd
    except Exception as e:
        logging.error(f"Failed to create TUN interface: {e}")
        sys.exit(1)


def setup_client_tun():
    """
    Configures the TUN interface on the client:
    - Assign IP 10.8.0.2/24 to tun0
    - Bring interface up
    - Add a route so that traffic to VPN network goes via tun0
    """
    try:
        tun_fd = create_tun_interface(VPN_INTERFACE)
        logging.info(f"TUN device created (fd={tun_fd})")
        
        # Assign IP and bring up
        subprocess.run(f"ip addr add {CLIENT_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)
        logging.info(f"Assigned IP {CLIENT_TUN_IP}/24 to {VPN_INTERFACE}")
        
        subprocess.run(f"ip link set dev {VPN_INTERFACE} up".split(), check=True)
        logging.info(f"Brought up {VPN_INTERFACE}")

        # Route all traffic destined for 10.8.0.0/24 into tun0 (optional if default route is changed)
        # subprocess.run(f"ip route add {SERVER_TUN_IP}/24 dev {VPN_INTERFACE}".split(), check=True)

        return tun_fd
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to configure TUN: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error in setup_client_tun: {e}")
        sys.exit(1)


def connect_to_server_tls():
    """
    Creates a TCP socket, wraps it in TLS (client side), and connects to the server.
    Returns the TLS-wrapped socket.
    """
    for attempt in range(CONNECTION_RETRIES):
        try:
            logging.info(f"Connection attempt {attempt + 1}/{CONNECTION_RETRIES} to {SERVER_ADDR}:{SERVER_PORT}")
            
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            if CLIENT_VERIFY:
                if not os.path.exists(CA_CERT_FILE):
                    raise FileNotFoundError(f"CA certificate not found: {CA_CERT_FILE}")
                context.load_verify_locations(cafile=CA_CERT_FILE)
                logging.info(f"Loaded CA certificate from {CA_CERT_FILE}")
            else:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                logging.warning("Server certificate verification disabled!")

            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(SOCKET_TIMEOUT)
            raw_sock.connect((SERVER_ADDR, SERVER_PORT))
            logging.info(f"TCP connection to {SERVER_ADDR}:{SERVER_PORT} established")
            
            tls_conn = context.wrap_socket(raw_sock, server_hostname=SERVER_ADDR)
            tls_conn.settimeout(SOCKET_TIMEOUT)
            logging.info("TLS handshake with server completed")
            return tls_conn
        except socket.timeout:
            logging.warning(f"Connection timeout on attempt {attempt + 1}")
            if attempt < CONNECTION_RETRIES - 1:
                logging.info(f"Retrying in {CONNECTION_RETRY_DELAY} seconds...")
                import time
                time.sleep(CONNECTION_RETRY_DELAY)
        except socket.gaierror as e:
            logging.error(f"DNS resolution failed for {SERVER_ADDR}: {e}")
            sys.exit(1)
        except socket.error as e:
            logging.warning(f"Connection failed on attempt {attempt + 1}: {e}")
            if attempt < CONNECTION_RETRIES - 1:
                logging.info(f"Retrying in {CONNECTION_RETRY_DELAY} seconds...")
                import time
                time.sleep(CONNECTION_RETRY_DELAY)
        except FileNotFoundError as e:
            logging.error(f"Certificate error: {e}")
            sys.exit(1)
        except ssl.SSLError as e:
            logging.error(f"TLS error: {e}")
            sys.exit(1)
    
    logging.error(f"Failed to connect after {CONNECTION_RETRIES} attempts")
    sys.exit(1)


def forward_traffic(tun_fd, tls_conn):
    """
    Main I/O loop: multiplexes between the TUN interface and TLS socket.
    """
    global active_connection
    active_connection = True
    packets_forwarded = 0
    
    try:
        while active_connection:
            rlist, _, _ = select.select([tun_fd, tls_conn], [], [], 1.0)

            if tun_fd in rlist:
                try:
                    # Packet from TUN (raw IP packet to send to server)
                    packet = os.read(tun_fd, 4096)
                    if not packet:
                        logging.info("TUN interface closed")
                        break
                    tls_conn.sendall(packet)
                    packets_forwarded += 1
                except OSError as e:
                    logging.error(f"Error reading from TUN: {e}")
                    break
                except socket.error as e:
                    logging.error(f"Error sending to server: {e}")
                    break

            if tls_conn in rlist:
                try:
                    # Packet coming from server → raw IP packet to write into TUN
                    data = tls_conn.recv(4096)
                    if not data:
                        # Server closed
                        logging.info("Server closed connection")
                        break
                    os.write(tun_fd, data)
                    packets_forwarded += 1
                except socket.timeout:
                    logging.warning("Socket timeout - connection idle")
                    break
                except socket.error as e:
                    logging.error(f"Error reading from server: {e}")
                    break
    finally:
        active_connection = False
        logging.info(f"Stopped forwarding (total packets: {packets_forwarded})")


def cleanup():
    """
    Graceful cleanup: bring down TUN interface, close sockets.
    """
    global tun_fd, tls_conn
    
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
    logging.info("Custom VPN Client Starting")
    logging.info("="*60)
    
    try:
        # 1. Set up the client TUN interface
        tun_fd = setup_client_tun()
        logging.info(f"TUN interface {VPN_INTERFACE} configured with {CLIENT_TUN_IP}/24")

        # 2. Connect to server over TLS
        tls_conn = connect_to_server_tls()

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
