# Components Documentation

Comprehensive documentation of all components in the Custom VPN application (Version 2.0)

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Server Components](#server-components)
3. [Client Components](#client-components)
4. [Shared Utilities](#shared-utilities)
5. [Support Scripts](#support-scripts)
6. [Data Flow](#data-flow)
7. [Module Dependencies](#module-dependencies)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Custom VPN Architecture v2.0                 │
└─────────────────────────────────────────────────────────────────┘

CLIENT MACHINE                          SERVER MACHINE
┌──────────────────────┐                ┌──────────────────────┐
│ Application Layer    │                │ Internet/Gateway     │
└──────────────────────┘                └──────────────────────┘
           │                                        │
           ▼                                        ▼
    ┌─────────────┐                          ┌──────────────┐
    │   tun0      │  (10.8.0.2/24)           │   eth0/ens3  │
    │ (TUN Dev)   │                          │ (Public IP)  │
    └──────┬──────┘                          └──────┬───────┘
           │                                        │
           │ IP Packets                             │ NAT
           ▼                                        ▼
    ┌─────────────────┐       TLS/TCP      ┌─────────────────┐
    │  client.py      │◄────Port 8443────► │  server.py      │
    │ (VPN Client)    │  (Encrypted)       │ (VPN Server)    │
    └─────────────────┘                    └─────────────────┘
           │                                        │
           │ Multiplexed I/O                        │ Multiplexed I/O
           │ (select+signal handlers)               │ (select+signal handlers)
           │                                        │
           ▼                                        ▼
    ┌──────────────────┐                   ┌──────────────────┐
    │ Logging & Stats  │                   │ Logging & Stats  │
    │ Error Handling   │                   │ Error Handling   │
    │ Graceful Shutdown│                   │ Graceful Shutdown│
    └──────────────────┘                   └──────────────────┘
```

---

## Server Components

### 1. **server.py** - Main VPN Server Application

#### Purpose
Acts as a VPN gateway, bridging client TUN traffic to the internet via TLS encryption.

#### Key Classes/Functions

##### Global Variables
```python
# Configuration constants
SERVER_TUN_IP = "10.8.0.1"           # VPN network server IP
SERVER_TUN_NETMASK = "255.255.255.0"  # VPN subnet mask
LISTEN_ADDR = "0.0.0.0"               # Server binding address
LISTEN_PORT = 8443                    # TLS listening port
CERT_FILE = "./certs/server.crt"      # Server certificate
KEY_FILE = "./certs/server.key"       # Server private key
SOCKET_TIMEOUT = 30                   # Socket timeout seconds
VPN_INTERFACE = "tun0"                # Virtual interface name

# Global state for cleanup
active_connection = False              # Connection status
tun_fd = None                         # TUN device file descriptor
tls_conn = None                       # TLS socket connection
public_interface = None               # Auto-detected network interface
```

##### `get_default_interface()`
**Purpose:** Auto-detect the server's public network interface

**Returns:** Interface name (string) e.g., "eth0", "ens3", "wlan0"

**Implementation:**
- Parses `ip route | grep ^default` output
- Extracts interface name from routing table
- Falls back to "eth0" if detection fails
- Logs warnings on failure with informative messages

**Code Flow:**
```
1. Run: ip route | grep ^default
2. Parse output for 'dev' keyword
3. Extract interface name after 'dev'
4. Return interface or fallback
```

##### `create_tun_interface(ifname: str) -> int`
**Purpose:** Create and configure a TUN (Tunnel) device

**Parameters:**
- `ifname` (str): Name of interface to create (e.g., "tun0")

**Returns:** File descriptor (int) for TUN device

**Operations:**
1. Opens `/dev/net/tun` character device
2. Issues `TUNSETIFF` ioctl to create TUN device
3. Returns file descriptor for reading/writing raw packets

**Error Handling:**
- Catches OSError and ioctl errors
- Logs detailed error messages
- Exits gracefully with `sys.exit(1)`

**TUN Device Details:**
- **IFF_TUN**: Tunnel interface flag (layer 3 - IP packets)
- **IFF_NO_PI**: No packet info flag (disables protocol info in packets)

##### `setup_server_tun() -> int`
**Purpose:** Complete TUN interface initialization and NAT configuration

**Returns:** TUN device file descriptor

**Steps:**
1. **Create TUN device** using `create_tun_interface()`
2. **Assign IP address**
   - Command: `ip addr add 10.8.0.1/24 dev tun0`
   - Subnet: 10.8.0.0/24 for VPN traffic
3. **Bring interface up**
   - Command: `ip link set dev tun0 up`
4. **Enable IP forwarding**
   - Command: `sysctl -w net.ipv4.ip_forward=1`
   - Allows packets to be routed between interfaces
5. **Configure NAT (Network Address Translation)**
   - Auto-detects public interface
   - Command: `iptables -t nat -A POSTROUTING -o {interface} -j MASQUERADE`
   - Masquerades VPN traffic to appear from server's public IP

**Error Handling:**
- Catches subprocess.CalledProcessError
- Precise error logging for each step
- Graceful exit with detailed error context

##### `start_tls_server_and_accept() -> ssl.SSLSocket`
**Purpose:** Create TLS server and accept one client connection

**Returns:** Encrypted SSL socket connection

**Process:**
1. **Validate certificates exist**
   - Checks `./certs/server.crt` and `./certs/server.key`
   - Raises FileNotFoundError if missing
2. **Create SSL context**
   - Protocol: `PROTOCOL_TLS_SERVER` (TLS 1.2+)
   - Loads certificate chain from files
3. **Create socket**
   - Creates TCP socket (AF_INET, SOCK_STREAM)
   - Sets SO_REUSEADDR to avoid "Address already in use" errors
4. **Bind and listen**
   - Binds to `0.0.0.0:8443`
   - Listens for one connection (backlog=1)
5. **Accept connection**
   - Blocks until client connects
   - Wraps accepted socket in TLS
6. **Set timeouts**
   - Both raw socket and TLS socket get 30-second timeout
   - Prevents indefinite waits on failed connections

**TLS Configuration:**
- **Server-side TLS**: Server authenticates to client
- **Certificate authority**: Self-signed for POC
- **Cipher suites**: Default system OpenSSL configuration

**Error Handling:**
- FileNotFoundError: Missing certificates
- socket.error: Socket binding/listening failures
- ssl.SSLError: TLS handshake errors
- All errors logged with context

##### `forward_traffic(tun_fd: int, tls_conn: ssl.SSLSocket) -> None`
**Purpose:** Bidirectional packet forwarding between TUN and TLS socket

**Parameters:**
- `tun_fd` (int): TUN device file descriptor
- `tls_conn` (ssl.SSLSocket): Encrypted client connection

**Algorithm:**
```
Loop while connection active:
    Use select() to multiplex between TUN and TLS
    Set 1-second timeout for graceful shutdown
    
    If TUN has data:
        Read up to 4096 bytes (raw IP packet)
        Send packet over TLS socket to client
        Increment packet counter
    
    If TLS has data:
        Read up to 4096 bytes (encrypted packet)
        Write packet to TUN device (kernel inject)
        Increment packet counter
    
    Handle timeouts and disconnections
```

**Multiplexing Strategy:**
- Uses `select.select([tun_fd, tls_conn], [], [], 1.0)`
- Monitors both TUN device and socket simultaneously
- 1-second timeout allows signal handling (graceful shutdown)
- Non-blocking approach prevents CPU spinning

**Packet Flow:**
1. **TUN → Client**: Application sends IP packet to TUN interface
   - Kernel places packet in TUN device queue
   - `select()` detects readable TUN
   - Read packet, send encrypted over TLS to client
2. **Client → TUN**: Client sends encrypted packet
   - `select()` detects readable TLS socket
   - Read encrypted packet, decrypt via TLS layer
   - Write decrypted packet to TUN device
   - Kernel delivers to application

**Error Handling:**
- OSError on TUN read: Log and break
- socket.error on TLS send/recv: Log and break
- socket.timeout: Log warning and continue
- Graceful exit with statistics

**Statistics:**
- Tracks total packets forwarded in both directions
- Logs statistics on shutdown: "total packets: {count}"

##### `cleanup() -> None`
**Purpose:** Graceful resource cleanup on shutdown

**Cleanup Steps:**
1. **Close TLS connection**
   - Calls `tls_conn.close()` with error handling
   - Logs success or warning
2. **Close TUN device**
   - Calls `os.close(tun_fd)` to release file descriptor
   - Logs success or warning
3. **Remove TUN interface**
   - Command: `ip link delete tun0`
   - Physically removes the interface from system
4. **Remove NAT rules**
   - Command: `iptables -t nat -D POSTROUTING -o {interface} -j MASQUERADE`
   - Reverses NAT configuration
   - Only if `public_interface` was detected

**Exception Handling:**
- Each step wrapped in try-except
- Logs warnings, never raises exceptions
- Ensures cleanup completes even if steps fail

##### `signal_handler(signum: int, frame) -> None`
**Purpose:** Handle system signals (Ctrl+C, SIGTERM)

**Parameters:**
- `signum` (int): Signal number (SIGINT=2, SIGTERM=15)
- `frame`: Stack frame (unused)

**Behavior:**
1. Log signal received
2. Call `cleanup()`
3. Exit with code 0

**Signal Registration:**
```python
signal.signal(signal.SIGINT, signal_handler)    # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)   # Kill signal
```

##### `main() -> None`
**Purpose:** Main entry point and orchestration

**Sequence:**
1. **Check root privileges**
   - Validates `os.geteuid() == 0`
   - Exits with error if not root
2. **Register signal handlers**
   - Ctrl+C and SIGTERM
3. **Setup phase**
   - Initialize TUN interface
   - Enable IP forwarding and NAT
4. **Accept phase**
   - Create TLS server
   - Wait for client connection
5. **Forward phase**
   - Bidirectional packet forwarding
6. **Cleanup phase**
   - Always executes via finally block
   - Ensures resources freed regardless of exit reason

**Exception Handling:**
- KeyboardInterrupt: Catches manual interruption
- Generic Exception: Logs with stack trace
- Finally block: Guarantees cleanup runs

#### Dependencies
```python
import os              # File descriptor operations
import fcntl          # ioctl calls for device control
import struct         # Binary data packing
import subprocess     # Shell command execution
import socket         # Network sockets
import ssl            # TLS/SSL encryption
import select         # I/O multiplexing
import signal         # Signal handling
import logging        # Structured logging
import sys            # System operations
```

#### Logging Output
```
[2026-03-04 12:30:45] [INFO] Custom VPN Server Starting
[2026-03-04 12:30:45] [INFO] TUN device created (fd=5)
[2026-03-04 12:30:45] [INFO] Assigned IP 10.8.0.1/24 to tun0
[2026-03-04 12:30:45] [INFO] Brought up tun0
[2026-03-04 12:30:45] [INFO] Enabled IP forwarding
[2026-03-04 12:30:45] [INFO] Configured NAT (MASQUERADE) on eth0
[2026-03-04 12:30:45] [INFO] Loaded TLS certificates
[2026-03-04 12:30:45] [INFO] Server listening on 0.0.0.0:8443 (TLS) ...
[2026-03-04 12:30:50] [INFO] TCP connection from 192.168.1.100:54321
[2026-03-04 12:30:51] [INFO] TLS handshake completed with 192.168.1.100:54321
[2026-03-04 12:30:51] [INFO] Starting packet forwarding ...
[2026-03-04 12:35:20] [INFO] Keyboard interrupt received
[2026-03-04 12:35:20] [INFO] Stopped forwarding (total packets: 1234)
[2026-03-04 12:35:20] [INFO] Cleanup completed
```

---

## Client Components

### 2. **client.py** - VPN Client Application

#### Purpose
Creates a VPN endpoint that encrypts all IP traffic and sends it to the server via TLS tunnel.

#### Key Classes/Functions

##### Global Variables
```python
# Configuration
CLIENT_TUN_IP = "10.8.0.2"                # VPN client IP
CLIENT_TUN_NETMASK = "255.255.255.0"      # VPN subnet mask
SERVER_ADDR = "SERVER_PUBLIC_IP_OR_HOSTNAME"  # Server address (config)
SERVER_PORT = 8443                        # TLS port
VPN_INTERFACE = "tun0"                    # Interface name
SOCKET_TIMEOUT = 30                       # Socket timeout
CONNECTION_RETRIES = 3                    # Retry attempts
CONNECTION_RETRY_DELAY = 5                # Delay between retries (seconds)
CLIENT_VERIFY = True                      # Verify server certificate
CA_CERT_FILE = "./certs/server.crt"       # CA certificate path

# Global state
active_connection = False                 # Connection status
tun_fd = None                            # TUN file descriptor
tls_conn = None                          # TLS connection
```

##### `create_tun_interface(ifname: str) -> int`
**Purpose:** Create TUN device on client side

**Identical to server version:**
- Opens `/dev/net/tun`
- Issues `TUNSETIFF` ioctl
- Returns file descriptor

##### `setup_client_tun() -> int`
**Purpose:** Initialize client TUN interface

**Steps:**
1. **Create TUN device**
2. **Assign IP**: `ip addr add 10.8.0.2/24 dev tun0`
3. **Bring up interface**: `ip link set dev tun0 up`
4. **Optional routing**: Comment shows how to add host routes

**Differences from server:**
- No NAT configuration needed on client
- No IP forwarding enabled
- Lower privilege requirements (still need to create TUN)

##### `connect_to_server_tls() -> ssl.SSLSocket`
**Purpose:** Connect to server with retries and certificate validation

**Returns:** TLS-wrapped socket connection

**Features:**
1. **Automatic Retry Logic**
   - Attempts up to `CONNECTION_RETRIES` (default 3)
   - Waits `CONNECTION_RETRY_DELAY` (default 5 seconds) between attempts
   - Helpful for servers that take time to start

2. **Certificate Validation**
   - If `CLIENT_VERIFY = True`:
     - Loads CA certificate from `CA_CERT_FILE`
     - Validates server certificate against CA
     - Verifies hostname matches
   - If `CLIENT_VERIFY = False`:
     - Skips all validation
     - Useful for POC with self-signed certs

3. **Timeouts**
   - Raw socket: 30-second timeout
   - TLS socket: 30-second timeout

4. **Error Handling**
   - socket.timeout: Retryable
   - socket.gaierror: DNS failure (fatal)
   - socket.error: Connection error (retryable)
   - FileNotFoundError: Missing CA cert (fatal)
   - ssl.SSLError: TLS error (fatal)

**Retry Exception Handling:**
```python
for attempt in range(CONNECTION_RETRIES):
    try:
        # Connection attempt
    except socket.timeout:
        # Retry with delay
    except socket.error:
        # Retry with delay
    except (FileNotFoundError, socket.gaierror, ssl.SSLError):
        # Exit immediately (fatal)
```

##### `forward_traffic(tun_fd: int, tls_conn: ssl.SSLSocket) -> None`
**Purpose:** Bidirectional forwarding (same as server)

**Identical implementation:**
- `select()` multiplexing
- Packet forwarding logic
- Statistics tracking
- Error handling

##### `cleanup() -> None`
**Purpose:** Clean up client resources

**Steps:**
1. Close TLS connection
2. Close TUN device file descriptor
3. Remove TUN interface: `ip link delete tun0`

**Differences from server:**
- No NAT rules to remove
- No IP forwarding to disable

##### `signal_handler(signum: int, frame) -> None`
**Purpose:** Handle signals on client

**Identical to server implementation**

##### `main() -> None`
**Purpose:** Main client entry point

**Sequence:**
1. Check root privileges
2. Register signal handlers
3. Setup TUN interface
4. Connect to server (with retries)
5. Forward traffic
6. Cleanup on exit

#### Retry Logic Example
```
Attempt 1 at 12:30:50 → Connection refused → Wait 5 seconds
Attempt 2 at 12:30:55 → Timeout → Wait 5 seconds
Attempt 3 at 12:31:00 → Success! Connected

[INFO] Connection attempt 1/3 to 203.0.113.5:8443
[ERROR] Connection failed on attempt 1: Connection refused
[INFO] Retrying in 5 seconds...
[INFO] Connection attempt 2/3 to 203.0.113.5:8443
[ERROR] Connection failed on attempt 2: Connection timed out
[INFO] Retrying in 5 seconds...
[INFO] Connection attempt 3/3 to 203.0.113.5:8443
[INFO] TCP connection to 203.0.113.5:8443 established
[INFO] TLS handshake with server completed
```

---

## Shared Utilities

### 3. TUN/TAP Device Interaction

#### TUN Device Details
**What is TUN?**
- TUN = Tunnel device (layer 3, IP packets)
- TAP = Tunnel device (layer 2, Ethernet frames)
- This implementation uses TUN (IP-level)

**Device File:** `/dev/net/tun`

**IOCTL Call:** `TUNSETIFF`
```
Purpose: Configure TUN device properties
Parameters:
  - Device: /dev/net/tun
  - Command: 0x400454ca (TUNSETIFF)
  - Data: struct ifreq
```

**Flags:**
```python
IFF_TUN   = 0x0001  # Tunnel interface (layer 3)
IFF_NO_PI = 0x1000  # Suppress packet info header
```

**Packet Format:**
- Without `IFF_NO_PI`: 4-byte protocol info prefix
- With `IFF_NO_PI`: Raw IP packets only
- Packet size: Up to 4096 bytes

#### Packet Flow Through TUN
```
Application Layer (e.g., ping)
        ↓
    Writes to 10.8.0.2
        ↓
Kernel Routing Table
    Routes to tun0
        ↓
TUN Device Buffer
    (Our process reads from fd)
        ↓
Our Application
    (send via TLS socket)
```

### 4. TLS/SSL Configuration

#### Server-Side TLS
```python
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
tls_conn = context.wrap_socket(sock, server_side=True)
```

#### Client-Side TLS (with verification)
```python
context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
context.load_verify_locations(cafile=CA_CERT_FILE)
tls_conn = context.wrap_socket(sock, server_hostname=SERVER_ADDR)
```

#### Client-Side TLS (without verification - POC)
```python
context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
tls_conn = context.wrap_socket(sock, server_hostname=SERVER_ADDR)
```

#### Certificate Format
- **Type**: X.509 self-signed
- **Key**: RSA 2048-bit
- **Validity**: 365 days
- **Subject**: `/C=US/ST=State/L=City/O=CustomVPN/OU=Dev/CN=server.local`

### 5. I/O Multiplexing with select()

#### Purpose
Simultaneously monitor multiple file descriptors without blocking

#### Syntax
```python
readable, writable, exceptional = select.select(rlist, wlist, xlist, timeout)
```

**Parameters:**
- `rlist`: Descriptors to check for readability
- `wlist`: Descriptors to check for writability (not used here)
- `xlist`: Descriptors to check for exceptions (not used here)
- `timeout`: Maximum wait time in seconds

#### Usage in VPN
```python
rlist, _, _ = select.select([tun_fd, tls_conn], [], [], 1.0)

if tun_fd in rlist:
    # TUN has packet(s) to read
    
if tls_conn in rlist:
    # TLS socket has data to read
```

**Timeout Strategy:**
- 1-second timeout allows graceful shutdown
- Prevents indefinite blocking
- Responsive to signal handlers

### 6. Signal Handling

#### Signals Captured

**SIGINT (Signal 2)**
- Sent by: User pressing Ctrl+C
- Default behavior: Terminate process
- Our behavior: Graceful cleanup then exit

**SIGTERM (Signal 15)**
- Sent by: `kill <pid>` or systemd stop
- Default behavior: Terminate process
- Our behavior: Graceful cleanup then exit

#### Registration
```python
def signal_handler(signum, frame):
    logging.info(f"Received signal {signum}. Shutting down...")
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
```

#### Why Needed
Without signal handlers:
- Process terminates immediately
- Resources not cleaned up
- TUN interface left in system
- NAT rules left in place
- Network connectivity broken

With signal handlers:
- Clean removal of TUN
- Clean removal of NAT rules
- Proper socket closure
- System returns to normal state

---

## Support Scripts

### 7. **cleanup.sh** - Automated Cleanup

#### Purpose
Remove all VPN configuration and resources

#### Script Contents
```bash
#!/bin/bash
# cleanup.sh - Clean up VPN network configuration

set -e  # Exit on error

# 1. Kill running processes
pkill -f "python3 server.py" || true
pkill -f "python3 client.py" || true
sleep 1

# 2. Remove TUN interface
ip link delete tun0 2>/dev/null || true

# 3. Remove NAT/MASQUERADE rules
# (Try all common interface names)
for interface in eth0 ens0 ens1 ens3 wlan0 wlan1; do
    if ip link show "$interface" &>/dev/null; then
        iptables -t nat -D POSTROUTING -o "$interface" -j MASQUERADE || true
    fi
done

# 4. Disable IP forwarding
sysctl -w net.ipv4.ip_forward=0 || true

echo "Cleanup completed successfully!"
```

#### Usage
```bash
# Emergency cleanup if VPN crashed
sudo ./cleanup.sh

# Scheduled cleanup (e.g., in cron)
0 2 * * * /home/user/customvpn/cleanup.sh  # Daily at 2 AM
```

#### Safety Features
- Uses `set -e` to exit on errors (fails closed)
- Uses `|| true` to continue on non-critical failures
- Multiple interface checks (eth0, ens3, wlan0, etc.)
- Removes VPN processes before network cleanup

### 8. **test_vpn.sh** - Connectivity Verification

#### Purpose
Verify VPN configuration and connectivity

#### Test Sequence
```bash
# 1. Check TUN interface exists and has configuration
ip addr show tun0
Expected: "inet 10.8.0.2/24" (client) or "inet 10.8.0.1/24" (server)

# 2. Ping VPN server (from client)
ping -c 3 10.8.0.1
Expected: "3 packets transmitted, 3 received, 0% packet loss"

# 3. Ping external IP (tests NAT)
ping -c 3 8.8.8.8
Expected: Replies with TTL decremented by NAT hops

# 4. Show interface statistics
ip -s link show tun0
Expected: Non-zero RX/TX bytes
```

#### Output Example
```
[+] TUN Interface Status:
2: tun0: <POINTOPOINT,UP,LOWER_UP> mtu 4096
    inet 10.8.0.2/24 scope global tun0
       valid_lft forever preferred_lft forever

[+] Ping VPN Server (10.8.0.1):
PING 10.8.0.1 (10.8.0.1) 56(84) bytes of data.
64 bytes from 10.8.0.1: icmp_seq=1 ttl=64 time=5.23 ms
---

[+] Ping External IP (8.8.8.8):
64 bytes from 8.8.8.8: icmp_seq=1 ttl=119 time=45.12 ms
---

[+] Interface Statistics:
RX packets 150  RX bytes 14500
TX packets 145  TX bytes 16800
```

---

## Data Flow

### Scenario: Client Ping to External Server

```
Step 1: Client Application
┌─────────────────────┐
│ ping 8.8.8.8        │
│ (user command)      │
└──────────┬──────────┘
           │
Step 2: Kernel Routing
           │ "Destination 8.8.8.8"
           │ "Routes to tun0"
           ▼
       ┌─────────────────┐
       │ Raw IP Packet   │ Source: 10.8.0.2
       │ (ICMP Echo Req) │ Dest:   8.8.8.8
       └────────┬────────┘ TTL: 64
                │
Step 3: TUN Device
                │ Packet in TUN queue
                │ (os.read(tun_fd))
                ▼
           ┌─────────────────┐
           │  client.py      │
           │ (read from TUN) │
           └────────┬────────┘
                    │
Step 4: TLS Encryption
                    │
       ┌────────────▼────────────┐
       │ ssl.SSLSocket           │
       │ .sendall(packet)        │
       │                         │
       │ TLS Record:             │
       │ - Fragment              │
       │ - Compress (disabled)   │
       │ - Encrypt (AES-256-GCM) │
       │ - HMAC authenticate     │
       └────────────┬────────────┘
                    │
Step 5: TCP Transport
                    │
       ┌────────────▼────────────┐
       │ TCP Socket              │
       │ Port: 8443              │
       │ Encrypted payload       │
       │ TCP Header              │
       │ IP Header (NAT)         │
       └────────────┬────────────┘
                    │
       ┌────────────▼────────────┐
       │ Internet / ISP / VPN    │
       │ 203.0.113.5:8443        │
       └────────────┬────────────┘
                    │
          ┌─────────▼─────────┐
          │  server.py        │
          │ (accept on port   │
          │  8443)            │
          └────────┬──────────┘
                   │
Step 6: TLS Decryption
                   │
       ┌───────────▼───────────┐
       │ ssl.SSLSocket         │
       │ .recv(4096)           │
       │                       │
       │ TLS Record Processing:│
       │ - Verify HMAC         │
       │ - Decrypt (AES-256)   │
       │ - Decompress          │
       │ - Defragment          │
       └───────────┬───────────┘
                   │
Step 7: Server TUN Write
                   │
       ┌───────────▼───────────┐
       │ os.write(tun_fd, data)│
       │ Inject to server TUN  │
       └───────────┬───────────┘
                   │
Step 8: Kernel Routing (Server)
                   │ "Destination 8.8.8.8"
                   │ "Normal routing"
                   ▼
           ┌────────────────┐
           │ IP Forwarding  │
           │ NAT/Masquerade │
           │ eth0/ens3      │
           │ Appears from:  │
           │ 203.0.113.5    │
           └────────┬───────┘
                    │
Step 9: Internet Response
                    │
       ┌────────────▼────────────┐
       │ 8.8.8.8 replies         │
       │ ICMP Echo Reply          │
       │ Destination: 203.0.113.5 │
       │ (masqueraded server IP)  │
       └────────────┬────────────┘
                    │
Step 10: Server NAT Reverse
                    │ "Destination 203.0.113.5:8443"
                    │ "Reverse NAT to 10.8.0.1"
                    ▼
           ┌─────────────────┐
           │ server.py       │
           │ (reads from TUN)│
           └────────┬────────┘
                    │
Step 11: TLS Encryption (Server)
                    │
       ┌────────────▼────────────┐
       │ Encrypt for client       │
       │ TLS Record Protocol      │
       └────────────┬────────────┘
                    │
Step 12: TCP Transport (Reverse)
                    │
       ┌────────────▼────────────┐
       │  TCP 203.0.113.5:8443   │
       │  → Client_IP:54321      │
       │  (encrypted packet)     │
       └────────────┬────────────┘
                    │
                    | Internet
                    │
       ┌────────────▼────────────┐
       │ client.py               │
       │ (tls_conn.recv())       │
       └────────────┬────────────┘
                    │
Step 13: TLS Decryption (Client)
                    │
       ┌────────────▼────────────┐
       │ Decrypt ICMP Echo Reply  │
       │ Source: 8.8.8.8          │
       │ Dest: 10.8.0.2           │
       │ TTL: 119 (decremented)   │
       └────────────┬────────────┘
                    │
Step 14: Client TUN Write
                    │
       ┌────────────▼────────────┐
       │ os.write(client_tun_fd) │
       │ Inject into TUN         │
       └────────────┬────────────┘
                    │
Step 15: Kernel Delivery
                    │
       ┌────────────▼────────────┐
       │ ping process receives   │
       │ ICMP Echo Reply         │
       │ (64 bytes from 8.8.8.8) │
       └────────────┬────────────┘
                    │
Step 16: User Display
                    │
       ┌────────────▼────────────┐
       │ ping output:            │
       │ 64 bytes from 8.8.8.8:  │
       │ icmp_seq=1 ttl=119      │
       │ time=45.12 ms           │
       └────────────────────────┘
```

---

## Module Dependencies

### Standard Library Imports
```
os              - File descriptor, TUN device operations
fcntl          - ioctl() system calls
struct         - Binary data packing
subprocess     - Execute shell commands for ip/iptables/sysctl
socket         - TCP/UDP network sockets
ssl            - TLS encryption and certificates
select         - I/O multiplexing
signal         - Signal handling (SIGINT, SIGTERM)
logging        - Structured logging
sys            - System operations (exit, etc.)
```

### No External Dependencies
- Uses only Python standard library
- No need for `pip install`
- Works with Python 3.8+

### System Commands Used
```bash
ip addr add      - Configure IP addresses
ip link set      - Bring interfaces up/down
ip link delete   - Remove interfaces
ip route         - Query routing table
sysctl           - Kernel parameter setting
iptables         - Packet filtering and NAT
openssl          - Certificate generation
```

### File System
```
/dev/net/tun     - TUN device file
./certs/         - Certificate directory
./cleanup.sh     - Utility script
./test_vpn.sh    - Test script
```

---

## Initialization Sequence

### Server Startup
```
main()
├─ Check root (geteuid)
├─ Register signal handlers (SIGINT, SIGTERM)
├─ setup_server_tun()
│  ├─ create_tun_interface("tun0")
│  ├─ ip addr add 10.8.0.1/24
│  ├─ ip link set tun0 up
│  ├─ sysctl ip_forward=1
│  ├─ get_default_interface()
│  └─ iptables MASQUERADE
├─ start_tls_server_and_accept()
│  ├─ Load certificate and key
│  ├─ Create SSL context
│  ├─ Bind to 0.0.0.0:8443
│  ├─ Listen for connection
│  └─ Accept and wrap in TLS
├─ forward_traffic()
│  └─ Loop: select() + packet forwarding
└─ cleanup() finally block
   ├─ Close TLS socket
   ├─ Close TUN fd
   ├─ ip link delete tun0
   └─ iptables remove NAT
```

### Client Startup
```
main()
├─ Check root (geteuid)
├─ Register signal handlers (SIGINT, SIGTERM)
├─ setup_client_tun()
│  ├─ create_tun_interface("tun0")
│  ├─ ip addr add 10.8.0.2/24
│  └─ ip link set tun0 up
├─ connect_to_server_tls()
│  └─ Retry loop (up to 3 times):
│     ├─ Create SSL context
│     ├─ Connect to server_ip:8443
│     └─ TLS handshake
├─ forward_traffic()
│  └─ Loop: select() + packet forwarding
└─ cleanup() finally block
   ├─ Close TLS socket
   ├─ Close TUN fd
   └─ ip link delete tun0
```

---

## Troubleshooting Guide

**Problem: "Permission denied" on TUN device**
- **Cause**: Not running as root
- **Solution**: `sudo python3 server.py`
- **Code Path**: `create_tun_interface()` → `os.open()`

**Problem: "Address already in use" on port 8443**
- **Cause**: Port in TIME_WAIT state or previous process still running
- **Solution**: Wait 60 seconds or kill process
- **Code Path**: `start_tls_server_and_accept()` → socket binding

**Problem: Client can't reach server**
- **Cause**: Server IP wrong, network unreachable, or firewall blocking
- **Solution**: Verify `SERVER_ADDR`, check network connectivity
- **Code Path**: `connect_to_server_tls()` → `raw_sock.connect()`

**Problem: Can ping VPN server but not external IPs**
- **Cause**: NAT rules not applied, IP forwarding disabled
- **Solution**: Check server logs, verify eth0 is correct interface
- **Code Path**: `setup_server_tun()` → iptables/sysctl commands

**Problem: Network interface not found on cleanup**
- **Cause**: Interface already deleted or wrong name detected
- **Solution**: Safe, logs warning and continues
- **Code Path**: `cleanup()` → try-except for each cleanup step

---

**Version**: 2.0 Enhanced Edition  
**Last Updated**: March 4, 2026  
**Status**: Production-Ready
