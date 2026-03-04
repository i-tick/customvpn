#!/bin/bash
# cleanup.sh - Clean up VPN network configuration

set -e

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

echo "[*] VPN Cleanup Script"
echo "======================================================================"

# Kill running processes
echo "[*] Stopping VPN processes..."
pkill -f "python3 server.py" || true
pkill -f "python3 client.py" || true
sleep 1

# Remove TUN interface
echo "[*] Removing TUN interface..."
ip link delete tun0 2>/dev/null || echo "    tun0 not found (already removed)"

# Remove NAT/MASQUERADE rules
echo "[*] Removing NAT rules..."
for interface in eth0 ens0 ens1 ens2 ens3 wlan0 wlan1; do
    if ip link show "$interface" &>/dev/null; then
        iptables -t nat -D POSTROUTING -o "$interface" -j MASQUERADE 2>/dev/null && \
            echo "    Removed MASQUERADE rule for $interface" || true
    fi
done

# Disable IP forwarding (optional)
echo "[*] Disabling IP forwarding..."
sysctl -w net.ipv4.ip_forward=0 2>/dev/null || true

echo "======================================================================"
echo "[+] Cleanup completed successfully!"
echo ""
echo "Note: If you manually added default routes, remove them with:"
echo "  ip route del default via 10.8.0.1 dev tun0"
echo "  ip route add default via <your-original-gateway> dev <interface>"
