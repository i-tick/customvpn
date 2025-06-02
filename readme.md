# Custom VPN (TUN/TAP + TLS)  

A minimal custom VPN implementation in Python using TUN/TAP interfaces and TLS over TCP. This guide explains how to set up, configure, and run both the server and client components.  

---

## Table of Contents

1. [Project Overview](#project-overview)  
2. [Prerequisites](#prerequisites)  
3. [TLS Certificate Generation](#tls-certificate-generation)  
4. [Folder Structure](#folder-structure)  
5. [Server Setup & Execution](#server-setup--execution)  
6. [Client Setup & Execution](#client-setup--execution)  
7. [Networking & Routing Configuration](#networking--routing-configuration)  
8. [Testing & Verification](#testing--verification)  
9. [Cleanup](#cleanup)  

---

## Project Overview

This project demonstrates a simple VPN built from scratch in Python. It consists of:

- **`server.py`**:  
  - Creates a TUN interface (`tun0`) with IP `10.8.0.1/24`.  
  - Listens on TCP port 8443 using TLS (server certificates).  
  - Forwards raw IP packets between the TUN interface and a connected client over the encrypted TLS tunnel.  
  - Enables IP forwarding and NAT (MASQUERADE) so that client traffic can reach the internet.

- **`client.py`**:  
  - Creates its own TUN interface (`tun0`) with IP `10.8.0.2/24`.  
  - Connects to the server’s TLS endpoint (port 8443).  
  - Forwards raw IP packets between the client’s TUN interface and the server over TLS.  

This README provides all installation steps, system requirements, and execution instructions required to get the VPN up and running.

---

## Prerequisites

> **Note**: Most commands below require **root** privileges (prefix with `sudo` or switch to root shell).

1. **Linux Machine**  
   - Ubuntu 20.04 or later is recommended.  
   - Must support TUN/TAP (i.e., `/dev/net/tun` exists and `tun` module can be loaded).

2. **Python 3 (≥ 3.8)**  
   - Ensure Python 3 is installed:  
     ```bash
     sudo apt update
     sudo apt install -y python3 python3-pip
     ```  
   - Verify:  
     ```bash
     python3 --version
     ```

3. **TUN/TAP Support**  
   - Install or verify availability of the TUN kernel module:  
     ```bash
     sudo apt install -y iproute2
     sudo modprobe tun
     ls /dev/net/tun    # Should output "/dev/net/tun"
     ```

4. **OpenSSL** (for certificate generation)  
   ```bash
   sudo apt install -y openssl
   openssl version

5. **iptables** (for NAT/forwarding on the server)

   ```bash
   sudo apt install -y iptables
   iptables --version
   ```

6. **Git** (optional, to clone the repository)

   ```bash
   sudo apt install -y git
   git --version
   ```

7. **Root Privileges**

   * Both `server.py` and `client.py` must be run as **root** (or via `sudo`) so they can:

     * Create and configure the `tun0` interface.
     * Modify IP routing and `iptables` rules.

---

## TLS Certificate Generation

Create a self-signed certificate for the server. The client can either disable verification (for POC) or trust this certificate.

1. **Create a `certs/` directory** (in your project root):

   ```bash
   mkdir -p ~/customvpn/certs
   cd ~/customvpn/certs
   ```

2. **Generate a new RSA private key and a self-signed certificate**:

   ```bash
   openssl req -newkey rsa:2048 \
     -nodes \
     -keyout server.key \
     -x509 \
     -days 365 \
     -out server.crt \
     -subj "/C=US/ST=State/L=City/O=CustomVPN/OU=Dev/CN=server.local"
   ```

   * **`server.key`** → RSA private key (keep this file secret, chmod 600).
   * **`server.crt`** → Self-signed certificate.

3. **Copy `server.crt` to the client machine** (if you want to verify the server’s certificate). For quick testing, you may set `CLIENT_VERIFY = False` in `client.py` to disable verification.

---

## Folder Structure

Below is the recommended project layout. Adjust paths if your project is located elsewhere.

```
customvpn/
├── certs/
│   ├── server.crt         # Server’s self-signed certificate
│   └── server.key         # Server’s private key
├── server.py               # VPN server implementation
└── client.py               # VPN client implementation
```

* **`certs/`**

  * Contains TLS certificate and key used by `server.py`.
  * If you enable verification on the client, place a copy of `server.crt` in a similarly named folder on the client side.

* **`server.py`**

  * Configures the TUN interface, enables routing/NAT, listens for a TLS‐wrapped TCP connection, and forwards packets bidirectionally between TUN and the TLS socket.

* **`client.py`**

  * Configures its own TUN interface, connects to the server over TLS, and forwards packets bidirectionally between TUN and the TLS socket.

---

## Server Setup & Execution

1. **Switch to project directory and ensure you are root** (or use `sudo`):

   ```bash
   cd ~/customvpn
   sudo -i
   ```

2. **Verify certificates exist** in `certs/`:

   ```bash
   ls certs/server.crt certs/server.key
   # Example output:
   # certs/server.crt  certs/server.key
   ```

3. **Edit `server.py` if necessary**:

   * Ensure `CERT_FILE` and `KEY_FILE` paths match the location of your certificate and key (`./certs/server.crt`, `./certs/server.key`).
   * Confirm `LISTEN_PORT` (default 8443) is open and not blocked by a firewall.

4. **Configure the TUN interface and NAT** (this is done automatically by `server.py`, but you can verify manually):

   * `server.py` will run:

     ```bash
     ip link set dev tun0 up
     ip addr add 10.8.0.1/24 dev tun0
     sysctl -w net.ipv4.ip_forward=1
     iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
     ```
   * If your public interface is not `eth0`, replace `eth0` with the correct device name (e.g., `ens3`, `ens160`).

5. **Run the server**:

   ```bash
   python3 server.py
   ```

   * Expected output:

     ```
     [+] TUN interface tun0 configured with 10.8.0.1/24
     [+] Server listening on 0.0.0.0:8443 (TLS) ...
     ```
   * The script will block at the `accept()` call, waiting for one client to connect.

---

## Client Setup & Execution

1. **On the client machine**, clone or copy the same `client.py` file and (optionally) the `certs/server.crt` file if you want to verify the server. Structure it as:

   ```
   ~/customvpn-client/
   ├── client.py
   └── certs/
       └── server.crt    # Copy of the server’s certificate (only if CLIENT_VERIFY = True)
   ```

2. **Switch to project directory and ensure you are root**:

   ```bash
   cd ~/customvpn-client
   sudo -i
   ```

3. **Edit `client.py`**:

   * Set `SERVER_ADDR` to the server’s public IP or hostname (e.g., `"203.0.113.5"` or `"vpn.example.com"`).
   * If you’d like the client to verify the server’s certificate, set:

     ```python
     CLIENT_VERIFY = True
     CA_CERT_FILE = "./certs/server.crt"
     ```

     and ensure `certs/server.crt` is present.
   * Otherwise, for quick testing, set:

     ```python
     CLIENT_VERIFY = False
     ```

4. **Configure the TUN interface** (handled automatically by `client.py`):

   * The script will run:

     ```bash
     ip link set dev tun0 up
     ip addr add 10.8.0.2/24 dev tun0
     ```
   * No additional NAT rules are required on the client side unless you want to forward its traffic through another gateway.

5. **Run the client**:

   ```bash
   python3 client.py
   ```

   * Expected output:

     ```
     [+] TUN interface tun0 configured with 10.8.0.2/24
     [+] TLS handshake with server completed.
     [+] Starting packet forwarding ...
     ```
   * The client will attempt a TLS‐wrapped TCP connection to `SERVER_ADDR:8443`. Once connected, it will begin relaying packets between the local `tun0` and the server.

---

## Networking & Routing Configuration

1. **Server IP forwarding & NAT** (done automatically in `server.py`):

   * Enables IP forwarding:

     ```bash
     sysctl -w net.ipv4.ip_forward=1
     ```
   * Adds an iptables rule to NAT VPN traffic out of the server’s public interface (e.g., `eth0`):

     ```bash
     iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
     ```
   * If your public interface is not `eth0`, replace accordingly.

2. **Client routing**:

   * By default, the system will send any packet destined for `10.8.0.0/24` into `tun0`.
   * To route **all internet traffic** through the VPN, you can change the default route after the VPN is up (manual step):

     ```bash
     ip route add default via 10.8.0.1 dev tun0
     ```

     * This forces **all** outgoing traffic from the client to go into `tun0`, encrypted, and then forwarded by the server.
     * To restore the original default route, you would remove this entry and re-add your original gateway.

> **Important**: Altering the default route will disconnect you from the network if SSH’ing into the client. Use a second terminal or set up a routing exception for the server’s IP.

---

## Testing & Verification

1. **Ping the server’s TUN IP** (from the client shell):

   ```bash
   ping -c 3 10.8.0.1
   ```

   * Successful replies indicate the VPN tunnel is functioning.

2. **Ping an external IP** (e.g., 8.8.8.8):

   ```bash
   ping -c 3 8.8.8.8
   ```

   * If NAT is correctly configured on the server, the client’s ping should succeed, proving that traffic is leaving via the server’s public interface.

3. **Monitor packet flow**:

   * On the **server**:

     ```bash
     tcpdump -i tun0 icmp
     ```

     You should see the client’s pings arriving on `tun0`.

   * On the **client**:

     ```bash
     tcpdump -i tun0 icmp
     ```

     You should see return traffic from the server or from external hosts.

4. **Verify TLS encryption**:

   * Use `ss` or `netstat` to confirm the client‐server connection is using TLS on port 8443.

     ```bash
     ss -tlnp | grep 8443
     ```

---

## Cleanup

When you’re done testing or shutting down:

1. **Stop the Python processes** (Ctrl+C or kill):

   * On the **client**:

     ```bash
     pkill -f client.py
     ip link set dev tun0 down
     ```
   * On the **server**:

     ```bash
     pkill -f server.py
     ip link set dev tun0 down
     sysctl -w net.ipv4.ip_forward=0
     iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
     ```

2. **Remove the TUN interface** (if still present):

   ```bash
   ip link delete tun0
   ```

3. **Revert any routing changes** on the client, if you altered the default route:

   ```bash
   ip route del default via 10.8.0.1 dev tun0
   # Re-add your original default route if needed, e.g.:
   # ip route add default via <original-gateway-ip> dev <interface>
   ```

4. **Secure or delete certificates** if they were only for testing:

   ```bash
   rm ~/customvpn/certs/server.key
   rm ~/customvpn-client/certs/server.crt   # if copied for verification
   ```

---

## Summary

You now have a working, minimal VPN:

* **Server**: Listens on TLS port 8443, routes traffic from the client’s TUN interface out to the internet, and returns replies back through the encrypted tunnel.
* **Client**: Creates its own TUN interface, sends raw IP packets over a TLS‐protected TCP connection to the server, and receives packets back for local injection.

This implementation is a **proof of concept**. For production usage, consider:

* Adding mutual TLS (client certificates).
* Supporting multiple clients (unique IP assignments, separate threads or a shared TUN with packet demultiplexing).
* Moving from TLS-over-TCP to DTLS-over-UDP for lower latency.
* Deploying a proper PKI or CA infrastructure rather than self-signed certificates.

Enjoy experimenting and learning about low-level networking, encryption, and virtual interfaces!
