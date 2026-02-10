import uuid
import threading
import time
import socket
from typing import Dict, Tuple, Set, Optional
from enum import Enum
from common import (
    make_udp_socket, send_message, recv_message,
    SERVER_CONTROL_PORT, CLIENT_PORT,
    MSG_GAME_INPUT, MSG_GAME_UPDATE, MSG_HEARTBEAT,
    MSG_ELECTION, MSG_ELECTION_OK, MSG_COORDINATOR, MSG_JOIN,
    MSG_DISCONNECT, MSG_ROOM_PAUSED, MSG_ROOM_RESUMED, MSG_GAME_FORFEIT
)
from game_state import GameState
from discovery import server_discovery_listener, client_discover_servers

HEARTBEAT_INTERVAL = 1.0
HEARTBEAT_TIMEOUT = 3.0
HEARTBEAT_GRACE_PERIOD = 5.0  # Grace period for new servers
TICK_INTERVAL = 0.05
WINNING_SCORE = 3
RECONNECT_TIMEOUT = 60.0
PLAYER_TIMEOUT = 3.0
RESUME_COUNTDOWN = 3

class RoomState(Enum):
    WAITING_FOR_PLAYERS = "waiting"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"

class Room:
    def __init__(self, room_id: int):
        self.room_id = room_id
        self.game_state = GameState()
        self.state = RoomState.WAITING_FOR_PLAYERS
        self.players: Set[int] = set()
        self.pause_time: Optional[float] = None
        self.disconnected_player: Optional[int] = None

class PongServer:
    def __init__(self):
        self.server_id = str(uuid.uuid4())
        self.peers: Dict[str, Tuple[str, int]] = {}

        self.control_sock = make_udp_socket(bind_ip="0.0.0.0", bind_port=SERVER_CONTROL_PORT)
        self.client_sock = make_udp_socket(bind_ip="0.0.0.0", bind_port=CLIENT_PORT)

        self.leader_id = self.server_id

        # Room management
        self.rooms: Dict[int, Room] = {}
        self.player_to_room: Dict[int, int] = {}
        self.inputs: Dict[int, int] = {}
        self.player_to_addr: Dict[int, Tuple[str, int]] = {}
        self.player_sessions: Dict[int, str] = {}
        self.connected_players: Set[int] = set()

        self.player_last_input: Dict[int, float] = {}

        self.next_room_id = 0
        self.room_lock = threading.Lock()

        self.last_heartbeat_from_leader = time.time()
        self.running = True
        self.election_active = False
        self.election_lock = threading.Lock()  # Prevent concurrent elections
        self.discovery_stop = threading.Event()

        # Threads
        self.discovery_thread = threading.Thread(
            target=server_discovery_listener,
            args=(self.server_id, self.discovery_stop),
            daemon=True
        )
        self.control_thread = threading.Thread(target=self.control_loop, daemon=True)
        self.client_thread = threading.Thread(target=self.client_loop, daemon=True)
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.game_thread = threading.Thread(target=self.game_loop, daemon=True)
        self.timeout_checker_thread = threading.Thread(target=self.timeout_checker_loop, daemon=True)

    def _print_membership(self):
        print("\n===== MEMBERSHIP TABLE =====")
        print(f"Self   : {self.server_id[:8]}...")
        print(f"Leader : {self.leader_id[:8]}...")
        if not self.peers:
            print("Peers  : None")
        else:
            for sid, (ip, _) in self.peers.items():
                role = "LEADER" if sid == self.leader_id else "FOLLOWER"
                print(f"{sid[:8]}... -> {ip} ({role})")
        print("============================\n")

    def _assign_room(self, player_id: int) -> int:
        """Smart room assignment with reconnection support"""
        with self.room_lock:
            if player_id in self.player_to_room:
                room_id = self.player_to_room[player_id]
                if room_id in self.rooms:
                    room = self.rooms[room_id]
                    if room.state == RoomState.PAUSED and room.disconnected_player == player_id:
                        print(f"[RECONNECT] Player {player_id} detected as reconnecting to room {room_id}")
                        return room_id
                    elif room.state == RoomState.ACTIVE and player_id in room.players:
                        return room_id

            for room_id, room in self.rooms.items():
                if len(room.players) == 1 and room.state == RoomState.WAITING_FOR_PLAYERS:
                    print(f"[MATCHMAKING] Player {player_id} paired in room {room_id}")
                    self.player_to_room[player_id] = room_id
                    room.players.add(player_id)
                    room.state = RoomState.ACTIVE
                    return room_id

            new_room_id = self.next_room_id
            self.next_room_id += 1
            new_room = Room(new_room_id)
            new_room.players.add(player_id)
            self.rooms[new_room_id] = new_room
            self.player_to_room[player_id] = new_room_id
            print(f"[MATCHMAKING] Player {player_id} created room {new_room_id}, waiting...")
            return new_room_id

    def _handle_player_disconnect(self, player_id: int):
        """Handle player disconnection with pause mechanism"""
        with self.room_lock:
            if player_id not in self.player_to_room:
                return

            room_id = self.player_to_room[player_id]
            if room_id not in self.rooms:
                return

            room = self.rooms[room_id]

            print(f"[DISCONNECT] Player {player_id} disconnected from room {room_id}")

            if room.state == RoomState.ACTIVE and len(room.players) == 2:
                room.players.discard(player_id)
                room.state = RoomState.PAUSED
                room.pause_time = time.time()
                room.disconnected_player = player_id

                print(f"[PAUSE] Room {room_id} PAUSED, waiting for player {player_id}")

                other_players = room.players.copy()
                for other_pid in other_players:
                    target = self.player_to_addr.get(other_pid)
                    if target:
                        pause_msg = {
                            "type": MSG_ROOM_PAUSED,
                            "state": room.game_state.to_dict(),
                            "disconnected_player": player_id,
                            "time_remaining": int(RECONNECT_TIMEOUT)
                        }
                        send_message(self.client_sock, target, pause_msg)
                        print(f"[PAUSE] Sent pause notification to player {other_pid}")

            elif room.state == RoomState.WAITING_FOR_PLAYERS:
                room.players.discard(player_id)
                if len(room.players) == 0:
                    self.player_to_room.pop(player_id, None)
                    del self.rooms[room_id]
                    print(f"[CLEANUP] Empty room {room_id} deleted")

    def _handle_player_reconnect(self, player_id: int, room_id: int):
        """Handle player reconnection with resume countdown"""
        with self.room_lock:
            room = self.rooms.get(room_id)
            if not room:
                print(f"[RECONNECT ERROR] Room {room_id} not found")
                return

            if room.state != RoomState.PAUSED:
                print(f"[RECONNECT ERROR] Room {room_id} is not paused (state: {room.state})")
                return

            if room.disconnected_player != player_id:
                print(f"[RECONNECT ERROR] Expected player {room.disconnected_player}, got {player_id}")
                return

            print(f"[RECONNECT SUCCESS] Player {player_id} reconnected to room {room_id}")

            room.players.add(player_id)
            room.disconnected_player = None

            threading.Thread(
                target=self._resume_countdown,
                args=(room_id,),
                daemon=True
            ).start()

    def _resume_countdown(self, room_id: int):
        """Countdown before resuming game"""
        room = self.rooms.get(room_id)
        if not room:
            return

        print(f"[RESUME] Starting countdown for room {room_id}")

        for countdown in range(RESUME_COUNTDOWN, 0, -1):
            time.sleep(1)

            countdown_msg = {
                "type": MSG_ROOM_RESUMED,
                "countdown": countdown,
                "state": room.game_state.to_dict()
            }

            with self.room_lock:
                for pid in room.players:
                    target = self.player_to_addr.get(pid)
                    if target:
                        send_message(self.client_sock, target, countdown_msg)
                        print(f"[RESUME] Sent countdown {countdown} to player {pid}")

        with self.room_lock:
            room.state = RoomState.ACTIVE
            room.pause_time = None
            print(f"[RESUME] Room {room_id} ACTIVE again!")

            resume_msg = {
                "type": MSG_ROOM_RESUMED,
                "countdown": 0,
                "state": room.game_state.to_dict()
            }

            for pid in room.players:
                target = self.player_to_addr.get(pid)
                if target:
                    send_message(self.client_sock, target, resume_msg)
                    print(f"[RESUME] Sent final resume to player {pid}")

    def start(self):
        print(f"[{self.server_id[:8]}...] Starting server...")
        print(f"Server Control Port: {SERVER_CONTROL_PORT}")
        print(f"Client Port: {CLIENT_PORT}")

        self.discovery_thread.start()
        print("Scanning for peers...")

        found = client_discover_servers(timeout=2.0, my_server_id=self.server_id)

        if found:
            print(f"Found {len(found)} peer(s)")
            for sid, sip in found:
                self.peers[sid] = (sip, SERVER_CONTROL_PORT)
                join_msg = {"type": MSG_JOIN, "id": self.server_id}
                send_message(self.control_sock, (sip, SERVER_CONTROL_PORT), join_msg)
                print(f"Sent JOIN to {sid[:8]}... at {sip}")
        else:
            print("No peers found. I am the first server.")

        # Determine initial leader
        all_ids = list(self.peers.keys()) + [self.server_id]
        self.leader_id = max(all_ids)

        # CRITICAL FIX: Set heartbeat time properly
        if self.is_leader():
            print(f"Initial leader: {self.server_id[:8]}... (I am leader)")
            self.last_heartbeat_from_leader = time.time()
        else:
            print(f"Initial leader: {self.leader_id[:8]}... (peer is leader)")
            # Give grace period - expect heartbeat within this time
            self.last_heartbeat_from_leader = time.time() + HEARTBEAT_GRACE_PERIOD

        self.control_thread.start()
        self.client_thread.start()
        self.heartbeat_thread.start()
        self.game_thread.start()
        self.timeout_checker_thread.start()

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        print("\n[SHUTDOWN] Server stopping...")
        self.running = False
        self.discovery_stop.set()
        self.control_sock.close()
        self.client_sock.close()

    def higher_peers(self):
        return {sid: addr for sid, addr in self.peers.items() if sid > self.server_id}

    def _finalize_election(self):
        """Called after election timeout - become leader if no higher response"""
        with self.election_lock:
            if self.election_active and not self.is_leader():
                print(f"[ELECTION] Timeout reached, no response from higher peers")
                self.become_leader()
            self.election_active = False

    def start_election(self):
        """Initiate Bully Algorithm election"""
        with self.election_lock:
            if self.election_active:
                print(f"[ELECTION] Already in progress, skipping")
                return

            print(f"[ELECTION] Starting election...")
            self.election_active = True
            higher = self.higher_peers()

            if not higher:
                print(f"[ELECTION] No higher peers, becoming leader")
                self.become_leader()
                self.election_active = False
                return

            print(f"[ELECTION] Sending ELECTION to {len(higher)} higher peer(s)")
            msg = {"type": MSG_ELECTION, "candidate": self.server_id}
            for sid, addr in higher.items():
                send_message(self.control_sock, addr, msg)
                print(f"[ELECTION] Sent ELECTION to {sid[:8]}...")

        # Set timeout for election
        t = threading.Timer(2.5, self._finalize_election)
        t.daemon = True
        t.start()

    def become_leader(self):
        """Become the leader and announce to all peers"""
        self.leader_id = self.server_id
        self.last_heartbeat_from_leader = time.time()
        self.election_active = False
        print(f"\n*** I AM LEADER NOW ({self.server_id[:8]}...) ***\n")

        msg = {"type": MSG_COORDINATOR, "leader_id": self.server_id}
        for sid, addr in self.peers.items():
            send_message(self.control_sock, addr, msg)
            print(f"[COORDINATOR] Sent COORDINATOR to {sid[:8]}...")

        self._print_membership()

    def is_leader(self):
        return self.leader_id == self.server_id

    def control_loop(self):
        while self.running:
            try:
                msg, addr = recv_message(self.control_sock)
            except Exception:
                continue

            t = msg.get("type")

            if t == MSG_JOIN:
                new_id = msg.get("id")
                if new_id and new_id != self.server_id:
                    if new_id not in self.peers:
                        print(f"\n[JOIN] Peer joined: {new_id[:8]}... from {addr[0]}")
                        self.peers[new_id] = (addr[0], SERVER_CONTROL_PORT)

                        # Notify all existing peers about new peer
                        for pid, paddr in self.peers.items():
                            if pid != new_id:
                                send_message(self.control_sock, addr, {"type": MSG_JOIN, "id": pid})
                                send_message(self.control_sock, (paddr[0], SERVER_CONTROL_PORT), {"type": MSG_JOIN, "id": new_id})

                        # Check if new peer should be leader
                        if new_id > self.leader_id:
                            print(f"[ELECTION] Higher peer joined: {new_id[:8]}... (current leader: {self.leader_id[:8]}...)")

                            if self.is_leader():
                                # I'm current leader, yield to higher peer
                                print(f"[ELECTION] I am leader, yielding to higher peer")
                                self.leader_id = new_id
                                self.last_heartbeat_from_leader = time.time() + HEARTBEAT_GRACE_PERIOD
                                self._print_membership()
                            else:
                                # I'm follower, higher peer exists, update leader
                                self.leader_id = new_id
                                self.last_heartbeat_from_leader = time.time() + HEARTBEAT_GRACE_PERIOD
                                print(f"[ELECTION] Updated leader to {new_id[:8]}...")

            elif t == MSG_HEARTBEAT:
                sender_id = msg.get("server_id")

                if sender_id:
                    self.last_heartbeat_from_leader = time.time()

                    # Update leader if sender has higher ID
                    if sender_id >= self.leader_id:
                        if sender_id != self.leader_id:
                            print(f"[HEARTBEAT] Leader updated to {sender_id[:8]}...")
                        self.leader_id = sender_id

                    # Add sender to peers if unknown
                    if sender_id not in self.peers and sender_id != self.server_id:
                        self.peers[sender_id] = (addr[0], SERVER_CONTROL_PORT)
                        print(f"[HEARTBEAT] Discovered new peer {sender_id[:8]}... from heartbeat")

            elif t == MSG_ELECTION:
                candidate = msg.get("candidate")
                print(f"\n[ELECTION] Received ELECTION from {candidate[:8] if candidate else 'unknown'}...")

                if candidate and self.server_id > candidate:
                    print(f"[ELECTION] I have higher ID ({self.server_id[:8]}... > {candidate[:8]}...)")
                    print(f"[ELECTION] Sending ELECTION_OK and starting own election")
                    send_message(self.control_sock, addr, {"type": MSG_ELECTION_OK})
                    self.start_election()
                else:
                    print(f"[ELECTION] Candidate has higher or equal ID, ignoring")

            elif t == MSG_ELECTION_OK:
                print(f"[ELECTION] Received ELECTION_OK - higher peer is active")
                with self.election_lock:
                    self.election_active = False

            elif t == MSG_COORDINATOR:
                new_leader = msg.get("leader_id")
                if new_leader:
                    print(f"\n[COORDINATOR] New leader announced: {new_leader[:8]}...")
                    self.leader_id = new_leader
                    self.last_heartbeat_from_leader = time.time()

                    with self.election_lock:
                        self.election_active = False

                    self._print_membership()

            elif t == MSG_GAME_UPDATE and not self.is_leader():
                sync_data = msg.get("sync_data")
                if sync_data:
                    with self.room_lock:
                        self.rooms = {}
                        for rid_str, room_data in sync_data["rooms"].items():
                            rid = int(rid_str)
                            room = Room(rid)
                            room.game_state = GameState.from_dict(room_data["game_state"])
                            room.state = RoomState(room_data["state"])
                            room.players = set(room_data["players"])
                            room.pause_time = room_data.get("pause_time")
                            room.disconnected_player = room_data.get("disconnected_player")
                            self.rooms[rid] = room

                        self.player_to_room = {int(pid): int(rid) for pid, rid in sync_data["player_to_room"].items()}
                        self.connected_players = set(sync_data["connected_players"])
                        self.next_room_id = sync_data["next_room_id"]

            elif t == MSG_GAME_INPUT and self.is_leader():
                pid = int(msg.get("player", 0))
                if pid > 0:
                    self.inputs[pid] = int(msg.get("dir", 0))
                    self.player_last_input[pid] = time.time()

    def heartbeat_loop(self):
        """Monitor leader heartbeat and send heartbeat if leader"""
        # CRITICAL FIX: Grace period before starting monitoring
        print(f"[HEARTBEAT] Starting heartbeat monitor (grace period: {HEARTBEAT_GRACE_PERIOD}s)")
        time.sleep(HEARTBEAT_GRACE_PERIOD)
        print(f"[HEARTBEAT] Grace period over, active monitoring started")

        while self.running:
            time.sleep(HEARTBEAT_INTERVAL)

            if self.is_leader():
                # I'm leader, send heartbeat to all peers
                hb = {"type": MSG_HEARTBEAT, "server_id": self.server_id}
                for sid, addr in self.peers.items():
                    send_message(self.control_sock, addr, hb)
            else:
                # I'm follower, check if leader is alive
                elapsed = time.time() - self.last_heartbeat_from_leader
                if elapsed > HEARTBEAT_TIMEOUT:
                    print(f"\n[HEARTBEAT] Leader timeout! No heartbeat for {elapsed:.1f}s")
                    print(f"[HEARTBEAT] Last leader: {self.leader_id[:8]}...")
                    self.start_election()

    def timeout_checker_loop(self):
        """Check for player timeouts and room forfeits"""
        while self.running:
            time.sleep(0.5)
            if not self.is_leader():
                continue

            current_time = time.time()

            with self.room_lock:
                players_to_disconnect = []

                for pid in list(self.connected_players):
                    if pid not in self.player_to_room:
                        continue

                    room_id = self.player_to_room[pid]
                    room = self.rooms.get(room_id)

                    if not room:
                        continue

                    if room.state == RoomState.ACTIVE:
                        last_input_time = self.player_last_input.get(pid, current_time)
                        if current_time - last_input_time > PLAYER_TIMEOUT:
                            print(f"[TIMEOUT] Player {pid} inactive for {PLAYER_TIMEOUT}s")
                            players_to_disconnect.append(pid)

                for pid in players_to_disconnect:
                    self._handle_player_disconnect(pid)
                    self.connected_players.discard(pid)

                rooms_to_forfeit = []
                for room_id, room in self.rooms.items():
                    if room.state == RoomState.PAUSED and room.pause_time:
                        elapsed = current_time - room.pause_time
                        if elapsed > RECONNECT_TIMEOUT:
                            print(f"[FORFEIT] Room {room_id} timeout after {elapsed:.1f}s")
                            rooms_to_forfeit.append(room_id)

                for room_id in rooms_to_forfeit:
                    room = self.rooms[room_id]

                    for pid in room.players:
                        target = self.player_to_addr.get(pid)
                        if target:
                            forfeit_msg = {
                                "type": MSG_GAME_FORFEIT,
                                "winner": pid,
                                "reason": "opponent_timeout",
                                "state": room.game_state.to_dict()
                            }
                            send_message(self.client_sock, target, forfeit_msg)

                    for pid in list(room.players):
                        self.connected_players.discard(pid)
                        self.player_to_room.pop(pid, None)
                        self.player_to_addr.pop(pid, None)
                        self.player_last_input.pop(pid, None)

                    if room.disconnected_player:
                        self.player_to_room.pop(room.disconnected_player, None)

                    del self.rooms[room_id]

    def client_loop(self):
        while self.running:
            try:
                msg, addr = recv_message(self.client_sock)
                msg_type = msg.get("type")

                if msg_type == MSG_GAME_INPUT:
                    pid = int(msg.get("player", 0))
                    if pid == 0:
                        continue

                    self.player_to_addr[pid] = addr
                    current_time = time.time()
                    self.player_last_input[pid] = current_time

                    if self.is_leader():
                        if pid in self.connected_players:
                            existing_addr = self.player_to_addr.get(pid)
                            if existing_addr and existing_addr != addr:
                                error_msg = {
                                    "type": "ERROR",
                                    "message": f"Player ID {pid} already connected from {existing_addr[0]}"
                                }
                                send_message(self.client_sock, addr, error_msg)
                                print(f"[SECURITY] Rejected duplicate player {pid} from {addr[0]}")
                                continue

                        if pid in self.player_to_room:
                            room_id = self.player_to_room[pid]
                            room = self.rooms.get(room_id)

                            if room and room.state == RoomState.PAUSED and room.disconnected_player == pid:
                                print(f"[RECONNECT] Player {pid} is reconnecting to paused room {room_id}")
                                if pid not in self.connected_players:
                                    self.connected_players.add(pid)
                                self._handle_player_reconnect(pid, room_id)
                            elif pid not in self.connected_players:
                                self.connected_players.add(pid)
                        else:
                            if pid not in self.connected_players:
                                self.connected_players.add(pid)
                                room_id = self._assign_room(pid)

                        self.inputs[pid] = int(msg.get("dir", 0))
                    else:
                        leader_addr = self.peers.get(self.leader_id)
                        if leader_addr:
                            send_message(self.control_sock, leader_addr, msg)

                elif msg_type == MSG_DISCONNECT:
                    pid = int(msg.get("player", 0))
                    if pid > 0 and self.is_leader():
                        print(f"[DISCONNECT] Explicit disconnect from player {pid}")
                        self._handle_player_disconnect(pid)
                        self.connected_players.discard(pid)
                        self.player_to_addr.pop(pid, None)
                        self.player_last_input.pop(pid, None)

            except Exception as e:
                continue

    def game_loop(self):
        while self.running:
            time.sleep(TICK_INTERVAL)
            if not self.is_leader():
                continue

            with self.room_lock:
                for room_id, room in list(self.rooms.items()):
                    if room.state == RoomState.WAITING_FOR_PLAYERS:
                        waiting_msg = {
                            "type": MSG_GAME_UPDATE,
                            "state": room.game_state.to_dict(),
                            "room_status": "waiting"
                        }
                        for pid in room.players:
                            target = self.player_to_addr.get(pid)
                            if target:
                                send_message(self.client_sock, target, waiting_msg)

                    elif room.state == RoomState.PAUSED:
                        if room.pause_time:
                            time_left = RECONNECT_TIMEOUT - (time.time() - room.pause_time)
                            pause_msg = {
                                "type": MSG_ROOM_PAUSED,
                                "state": room.game_state.to_dict(),
                                "time_remaining": max(0, int(time_left))
                            }
                            for pid in room.players:
                                target = self.player_to_addr.get(pid)
                                if target:
                                    send_message(self.client_sock, target, pause_msg)

                    elif room.state == RoomState.ACTIVE:
                        if len(room.players) < 2:
                            continue

                        if room.game_state.score1 >= WINNING_SCORE or room.game_state.score2 >= WINNING_SCORE:
                            winner_idx = 1 if room.game_state.score1 >= WINNING_SCORE else 2

                            final_msg = {
                                "type": MSG_GAME_UPDATE,
                                "state": room.game_state.to_dict(),
                                "game_over": True,
                                "winner": winner_idx
                            }

                            for pid in room.players:
                                target = self.player_to_addr.get(pid)
                                if target:
                                    send_message(self.client_sock, target, final_msg)

                            print(f"[GAME OVER] Room {room_id}: Player {winner_idx} wins!")
                            room.game_state = GameState()
                            continue

                        player_list = sorted(list(room.players))
                        p1_input = self.inputs.get(player_list[0], 0)
                        p2_input = self.inputs.get(player_list[1], 0)

                        room.game_state.step(p1_input, p2_input)

                        update_msg = {
                            "type": MSG_GAME_UPDATE,
                            "state": room.game_state.to_dict(),
                            "room_status": "active"
                        }

                        for pid in room.players:
                            target = self.player_to_addr.get(pid)
                            if target:
                                send_message(self.client_sock, target, update_msg)

            if self.peers and int(time.time() * 20) % 10 == 0:
                with self.room_lock:
                    rooms_data = {}
                    for rid, room in self.rooms.items():
                        rooms_data[str(rid)] = {
                            "game_state": room.game_state.to_dict(),
                            "state": room.state.value,
                            "players": list(room.players),
                            "pause_time": room.pause_time,
                            "disconnected_player": room.disconnected_player
                        }

                    sync_msg = {
                        "type": MSG_GAME_UPDATE,
                        "sync_data": {
                            "rooms": rooms_data,
                            "player_to_room": self.player_to_room,
                            "connected_players": list(self.connected_players),
                            "next_room_id": self.next_room_id
                        }
                    }

                    for _, paddr in self.peers.items():
                        send_message(self.control_sock, paddr, sync_msg)

if __name__ == "__main__":
    PongServer().start()
