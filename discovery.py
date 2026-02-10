import socket
import time
import json
from typing import List, Tuple, Optional
from common import (
    make_udp_socket, recv_message, send_message,
    DISCOVERY_BROADCAST_PORT, MSG_DISCOVER_REQUEST, MSG_DISCOVER_RESPONSE
)

def get_smart_broadcast_ip():
    """
    Automatically calculate the subnet broadcast address
    Works on Windows, Linux, and macOS

    Returns:
        Broadcast IP address (e.g., "192.168.1.255")
    """
    try:
        # Connect to public DNS to find active network interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
        s.close()

        # Assume /24 subnet (255.255.255.0)
        base_ip = my_ip.rsplit('.', 1)[0]
        broadcast_ip = f"{base_ip}.255"

        print(f"[DISCOVERY] Using broadcast IP: {broadcast_ip}")
        return broadcast_ip
    except Exception as e:
        print(f"[DISCOVERY] Failed to detect subnet, using 255.255.255.255")
        return "255.255.255.255"

def server_discovery_listener(server_id: str, stop_event=None):
    """
    Listen for discovery requests and respond with server ID

    Args:
        server_id: This server's unique ID
        stop_event: Threading event to stop listening
    """
    sock = make_udp_socket(bind_ip="0.0.0.0", bind_port=DISCOVERY_BROADCAST_PORT, broadcast=True)
    sock.settimeout(0.5)

    print(f"[DISCOVERY] Listening on port {DISCOVERY_BROADCAST_PORT}")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break

            try:
                msg, addr = recv_message(sock)
            except socket.timeout:
                continue
            except Exception:
                continue

            if msg.get("type") == MSG_DISCOVER_REQUEST:
                # Reply directly to sender
                reply = {"type": MSG_DISCOVER_RESPONSE, "id": server_id}
                try:
                    send_message(sock, addr, reply)
                except Exception:
                    pass
    finally:
        sock.close()

def client_discover_servers(timeout: float = 2.0, my_server_id: Optional[str] = None) -> List[Tuple[str, str]]:
    """
    Discover available servers on the local network

    Args:
        timeout: How long to listen for responses (seconds)
        my_server_id: Optional server ID to filter out (prevents self-discovery)

    Returns:
        List of (server_id, server_ip) tuples
    """
    results = []
    start = time.time()

    sock = make_udp_socket(bind_ip="0.0.0.0", bind_port=0, broadcast=True)
    sock.settimeout(0.3)

    request = {"type": MSG_DISCOVER_REQUEST}
    encoded_req = json.dumps(request).encode()

    # --- MULTI-STRATEGY BROADCAST ---

    # Strategy 1: Subnet broadcast
    subnet_bcast = get_smart_broadcast_ip()
    try:
        sock.sendto(encoded_req, (subnet_bcast, DISCOVERY_BROADCAST_PORT))
    except Exception as e:
        print(f"[DISCOVERY] Subnet broadcast failed: {e}")

    # Strategy 2: Limited broadcast
    try:
        sock.sendto(encoded_req, ("255.255.255.255", DISCOVERY_BROADCAST_PORT))
    except Exception as e:
        print(f"[DISCOVERY] Limited broadcast failed: {e}")

    # Strategy 3: Broadcast string
    try:
        sock.sendto(encoded_req, ("<broadcast>", DISCOVERY_BROADCAST_PORT))
    except Exception:
        pass

    # Listen for responses
    try:
        while time.time() - start < timeout:
            try:
                msg, addr = recv_message(sock)
            except socket.timeout:
                continue
            except Exception:
                continue

            if msg.get("type") == MSG_DISCOVER_RESPONSE:
                sid = msg.get("id")
                sip = addr[0]

                # FIXED: Skip if this is our own server
                if my_server_id and sid == my_server_id:
                    continue

                if sid and sip:
                    tup = (sid, sip)
                    if tup not in results:
                        results.append(tup)
                        print(f"[DISCOVERY] Found server {sid[:8]}... at {sip}")
    finally:
        sock.close()

    return results
