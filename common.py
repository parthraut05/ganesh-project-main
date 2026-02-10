import json
import socket
from typing import Any, Dict, Tuple, Optional

# Port configuration
SERVER_CONTROL_PORT = 50010
CLIENT_PORT = 50020
DISCOVERY_PORT = 50000
DISCOVERY_BROADCAST_PORT = DISCOVERY_PORT

# Message types
MSG_GAME_INPUT = "GAME_INPUT"
MSG_GAME_UPDATE = "GAME_UPDATE"
MSG_HEARTBEAT = "HEARTBEAT"
MSG_ELECTION = "ELECTION"
MSG_ELECTION_OK = "ELECTION_OK"
MSG_COORDINATOR = "COORDINATOR"
MSG_JOIN = "JOIN"
MSG_DISCOVER_REQUEST = "DISCOVER_REQUEST"
MSG_DISCOVER_RESPONSE = "DISCOVER_RESPONSE"

# New message types for pause/resume/disconnect
MSG_DISCONNECT = "DISCONNECT"
MSG_ROOM_PAUSED = "ROOM_PAUSED"
MSG_ROOM_RESUMED = "ROOM_RESUMED"
MSG_GAME_FORFEIT = "GAME_FORFEIT"

def make_udp_socket(bind_ip: str = "0.0.0.0", bind_port: Optional[int] = 0, broadcast: bool = False) -> socket.socket:
    """
    Create a UDP socket with cross-platform compatibility

    Args:
        bind_ip: IP address to bind to (0.0.0.0 for all interfaces)
        bind_port: Port to bind to (0 for random port)
        broadcast: Enable broadcast support

    Returns:
        Configured UDP socket
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # SO_REUSEADDR: Allow rebinding to recently used address
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass

    # SO_REUSEPORT: Allow multiple sockets on same port (Linux/macOS only)
    try:
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except Exception:
        pass

    # SO_BROADCAST: Enable broadcast packets
    if broadcast:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass

    # Bind socket
    if bind_port is not None:
        try:
            s.bind((bind_ip, bind_port))
        except OSError as e:
            print(f"[WARNING] Failed to bind to {bind_ip}:{bind_port} - {e}")
            if bind_ip == "":
                s.bind(("0.0.0.0", bind_port))
            else:
                raise

    return s

def send_message(sock: socket.socket, addr: Tuple[str, int], payload: Dict[str, Any]) -> None:
    """
    Send a JSON message over UDP

    Args:
        sock: UDP socket
        addr: Destination (IP, port)
        payload: Dictionary to send as JSON
    """
    try:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        sock.sendto(data, addr)
    except Exception as e:
        pass

def recv_message(sock: socket.socket, bufsize: int = 65535) -> Tuple[Dict[str, Any], Tuple[str, int]]:
    """
    Receive a JSON message from UDP socket

    Args:
        sock: UDP socket
        bufsize: Maximum buffer size

    Returns:
        Tuple of (message dict, sender address)
    """
    data, addr = sock.recvfrom(bufsize)
    try:
        obj = json.loads(data.decode("utf-8"))
        return obj, addr
    except Exception:
        return {}, addr
