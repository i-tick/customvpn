# Quick Start Guide - Enhanced VPN Application

## What's New?

The Custom VPN application has been significantly enhanced with production-quality features:

✅ **Graceful Shutdown** - Proper cleanup on Ctrl+C  
✅ **Auto-Detect Network Interface** - Works on any Linux system  
✅ **Comprehensive Logging** - See everything that happens  
✅ **Better Error Handling** - Clear error messages  
✅ **Socket Timeouts** - Prevent hanging connections  
✅ **Connection Retry** - Client reconnects automatically  
✅ **Utility Scripts** - Easy cleanup and testing  

---

## Files Overview

| File | Purpose |
|------|---------|
| `server.py` | VPN server (enhanced) |
| `client.py` | VPN client (enhanced) |
| `cleanup.sh` | **NEW:** Automated cleanup script |
| `test_vpn.sh` | **NEW:** VPN connectivity tester |
| `certs/` | TLS certificates |

---

## Quick Start

### 1. **Start the VPN Server** (on server machine)

```bash
sudo python3 server.py
```

Expected output:
```
[2026-03-04 12:30:45] [INFO] ============================================================
[2026-03-04 12:30:45] [INFO] Custom VPN Server Starting
[2026-03-04 12:30:45] [INFO] ============================================================
[2026-03-04 12:30:45] [INFO] TUN device created (fd=5)
[2026-03-04 12:30:45] [INFO] Assigned IP 10.8.0.1/24 to tun0
[2026-03-04 12:30:45] [INFO] Brought up tun0
[2026-03-04 12:30:45] [INFO] Enabled IP forwarding
[2026-03-04 12:30:45] [INFO] Configured NAT (MASQUERADE) on eth0
[2026-03-04 12:30:45] [INFO] Loaded TLS certificates from ./certs/server.crt and ./certs/server.key
[2026-03-04 12:30:45] [INFO] Server listening on 0.0.0.0:8443 (TLS) ...
```

**Server is now waiting for a client connection.**

---

### 2. **Start the VPN Client** (on client machine)

First, edit `client.py` and set your server IP:

```python
SERVER_ADDR = "203.0.113.5"  # Change to your server's IP
```

Then run:

```bash
sudo python3 client.py
```

Expected output:
```
[2026-03-04 12:30:50] [INFO] ============================================================
[2026-03-04 12:30:50] [INFO] Custom VPN Client Starting
[2026-03-04 12:30:50] [INFO] ============================================================
[2026-03-04 12:30:50] [INFO] TUN device created (fd=4)
[2026-03-04 12:30:50] [INFO] Assigned IP 10.8.0.2/24 to tun0
[2026-03-04 12:30:50] [INFO] Brought up tun0
[2026-03-04 12:30:50] [INFO] Connection attempt 1/3 to 203.0.113.5:8443
[2026-03-04 12:30:51] [INFO] TCP connection to 203.0.113.5:8443 established
[2026-03-04 12:30:51] [INFO] TLS handshake with server completed
[2026-03-04 12:30:51] [INFO] Starting packet forwarding ...
```

**Client is now connected and forwarding packets!**

---

### 3. **Test the VPN** (on client machine, in another terminal)

```bash
sudo ./test_vpn.sh
```

This checks:
- ✓ TUN interface exists and has IP
- ✓ Can ping VPN server (10.8.0.1)
- ✓ Can reach external IP (8.8.8.8) through VPN
- ✓ Shows traffic statistics

---

### 4. **Graceful Shutdown** (any terminal)

**Method 1: Press Ctrl+C in VPN process terminal**
- Server and client detect Ctrl+C
- Automatically clean up TUN interface
- Remove NAT rules
- Close connections

```
^C
[2026-03-04 12:35:20] [INFO] Keyboard interrupt received
[2026-03-04 12:35:20] [INFO] Starting cleanup...
[2026-03-04 12:35:20] [INFO] Closed TLS connection
[2026-03-04 12:35:20] [INFO] Closed TUN device
[2026-03-04 12:35:20] [INFO] Removed tun0
[2026-03-04 12:35:20] [INFO] Removed NAT rule for eth0
[2026-03-04 12:35:20] [INFO] Cleanup completed
```

**Method 2: Use cleanup script (emergency cleanup)**
```bash
sudo ./cleanup.sh
```

This removes all VPN configuration even if processes crashed.

---

## Key Improvements Explained

### 🔄 **Auto-Detect Network Interface**
The server no longer needs you to edit the script for different systems. It automatically detects your public interface (eth0, ens3, wlan0, etc.) and configures NAT accordingly.

**Old Way:**
```python
subprocess.run("iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE")  # hardcoded!
```

**New Way:**
```python
public_interface = get_default_interface()  # auto-detected!
subprocess.run(f"iptables -t nat -A POSTROUTING -o {public_interface} -j MASQUERADE")
```

---

### 📝 **Structured Logging**
Instead of print statements, you now get professional logs with timestamps and severity levels:

```
[2026-03-04 12:30:45] [INFO] Server listening on 0.0.0.0:8443 (TLS) ...
[2026-03-04 12:30:50] [ERROR] Failed to create TUN interface: Permission denied
```

---

### 🛡️ **Graceful Shutdown**
Signal handlers catch Ctrl+C and SIGTERM to clean up properly:

```python
def signal_handler(signum, frame):
    logging.info(f"Received signal {signum}. Shutting down...")
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Kill signal
```

---

### ⏱️ **Socket Timeouts**
Connections won't hang indefinitely if the network fails:

```python
SOCKET_TIMEOUT = 30  # seconds
tls_conn.settimeout(SOCKET_TIMEOUT)
```

---

### 🔁 **Connection Retry (Client Only)**
Client automatically retries if server is unavailable:

```python
CONNECTION_RETRIES = 3
CONNECTION_RETRY_DELAY = 5  # seconds

for attempt in range(CONNECTION_RETRIES):
    try:
        # Connect
    except:
        # Retry after delay
```

---

## Troubleshooting

### "Failed to create TUN interface: Permission denied"
**Solution:** Run with `sudo`
```bash
sudo python3 server.py
```

### "Connection refused" on client
**Solution:** Make sure server is running and listening
```bash
# On server machine
sudo python3 server.py

# Should see:
# [INFO] Server listening on 0.0.0.0:8443 (TLS) ...
```

### "Cannot find server IP"
**Solution:** Update `SERVER_ADDR` in client.py to your server's IP
```python
SERVER_ADDR = "your.server.ip.address"
```

### "Certificate not found" error
**Solution:** Generate certificates first
```bash
cd certs
openssl req -newkey rsa:2048 -nodes -keyout server.key -x509 -days 365 -out server.crt -subj "/C=US/ST=State/L=City/O=CustomVPN/OU=Dev/CN=server.local"
```

### Network stuck after VPN crash
**Solution:** Run cleanup script
```bash
sudo ./cleanup.sh
```

---

## Monitoring & Debugging

### View Real-Time Logs
Both server and client output detailed logs. For better monitoring, redirect to a file:

```bash
sudo python3 server.py | tee server.log
```

### Monitor Network Traffic
In another terminal, watch VPN traffic:

```bash
sudo tcpdump -i tun0 -n
```

### Check VPN Statistics
```bash
sudo ./test_vpn.sh
```

---

## Configuration Options

### Server
```python
SERVER_TUN_IP = "10.8.0.1"        # VPN server IP
SERVER_TUN_NETMASK = "255.255.255.0"
LISTEN_PORT = 8443                 # TLS port
SOCKET_TIMEOUT = 30                # timeout in seconds
```

### Client
```python
CLIENT_TUN_IP = "10.8.0.2"        # VPN client IP
SERVER_ADDR = "your.server.ip"     # Server address
SERVER_PORT = 8443
SOCKET_TIMEOUT = 30
CONNECTION_RETRIES = 3
CONNECTION_RETRY_DELAY = 5
CLIENT_VERIFY = True               # Verify server cert
CA_CERT_FILE = "./certs/server.crt"
```

---