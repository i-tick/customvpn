#!/bin/bash
# test_vpn.sh - Test VPN connectivity

if [[ $EUID -ne 0 ]]; then
   echo "[!] This script should be run as root for best results"
fi

VPN_SERVER_IP="10.8.0.1"
TEST_EXTERNAL_IP="8.8.8.8"

echo "======================================================================"
echo "VPN Connectivity Tests"
echo "======================================================================"
echo ""

# Check if tun0 exists
echo "[*] Checking TUN interface..."
if ip link show tun0 &>/dev/null; then
    echo "[+] TUN interface tun0 exists"
    ip addr show tun0 | grep "inet " || echo "    Warning: No IP assigned"
else
    echo "[-] TUN interface tun0 not found"
    exit 1
fi

echo ""

# Test ping to server
echo "[*] Testing ping to VPN server ($VPN_SERVER_IP)..."
if ping -c 2 -W 2 "$VPN_SERVER_IP" &>/dev/null; then
    echo "[+] Successfully pinged VPN server"
else
    echo "[-] Failed to ping VPN server"
fi

echo ""

# Test connection to external IP
echo "[*] Testing connectivity to external IP ($TEST_EXTERNAL_IP)..."
if ping -c 2 -W 3 "$TEST_EXTERNAL_IP" &>/dev/null; then
    echo "[+] Successfully pinged external IP (NAT working!)"
else
    echo "[-] Failed to ping external IP"
    echo "    This may indicate NAT is not properly configured"
fi

echo ""

# Show active connections
echo "[*] Active TUN interface status:"
ip -s link show tun0 2>/dev/null || echo "    TUN interface not found"

echo ""

# Show TUN traffic
echo "[*] Checking TUN traffic with tcpdump (5 seconds)..."
if command -v tcpdump &>/dev/null; then
    echo "    (Press Ctrl+C to stop)"
    timeout 5 tcpdump -i tun0 -n 2>/dev/null || true
else
    echo "    tcpdump not installed"
fi

echo ""
echo "======================================================================"
echo "Testing completed"
