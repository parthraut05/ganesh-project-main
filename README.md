# Distributed Pong Game - Complete Implementation

## 📦 Files Included

1. **server.py** - Game server with leader election and room management
2. **client.py** - Pygame-based game client with pause/resume UI
3. **common.py** - Shared utilities and message types
4. **game_state.py** - Game physics and state management
5. **discovery.py** - UDP broadcast-based server discovery

---

## ✨ Features Implemented

### ✅ Core Requirements
1. **Client-Server Architecture** - Multiple servers, multiple clients
2. **Dynamic Discovery** - UDP broadcast with multi-strategy approach
3. **Leader Election** - Bully algorithm (highest UUID wins)
4. **Fault Tolerance** - Heartbeat monitoring, automatic re-election
5. **Multiple Game Rooms** - Smart player pairing, independent game sessions

### ✅ Advanced Features
1. **Pause/Resume System**
   - Immediate game pause on disconnect (3 seconds detection)
   - 60-second reconnection window
   - 3-2-1 countdown before resume
   - Connected player stays connected during pause

2. **Game Mechanics**
   - First to 3 points wins
   - Automatic game restart after win
   - Score preservation during pause

3. **Reconnection**
   - State preservation (score, ball position)
   - Automatic room rejoin
   - Forfeit after 60 seconds

4. **Security**
   - Duplicate player ID prevention
   - Session validation
   - Explicit disconnect messages

### 🔧 Bug Fixes Applied
1. **Self-discovery bug** - Server no longer discovers itself
2. **Reconnection bug** - Keeps player_to_room mapping during pause
3. **Timeout bug** - Paused players excluded from inactivity checks
4. **Connection drops** - Continuous updates prevent client timeout

---

## 🚀 Quick Start

### Prerequisites
```bash
pip install pygame
```

### Run Server
```bash
python server.py
```

### Run Clients
```bash
# Terminal 1
python client.py --player 1

# Terminal 2
python client.py --player 2

# Terminal 3
python client.py --player 5
```

---

## 🎮 How to Play

### Controls
- **UP Arrow** - Move paddle up
- **DOWN Arrow** - Move paddle down
- **Close Window** - Disconnect cleanly

### Gameplay
- First player to **3 points** wins
- Players are paired automatically (any available player)
- If opponent disconnects, game pauses for **60 seconds**
- Opponent can reconnect to resume game

---

## 🌐 Network Setup

### Firewall Configuration

**Windows:**
```powershell
netsh advfirewall firewall add rule name="Pong Discovery" protocol=UDP dir=in localport=50000 action=allow
netsh advfirewall firewall add rule name="Pong Server" protocol=UDP dir=in localport=50010 action=allow
netsh advfirewall firewall add rule name="Pong Client" protocol=UDP dir=in localport=50020 action=allow
```

**Linux (ufw):**
```bash
sudo ufw allow 50000/udp
sudo ufw allow 50010/udp
sudo ufw allow 50020/udp
```

### Ports Used
- **50000** - Discovery (UDP broadcast)
- **50010** - Server control (leader election, heartbeat, sync)
- **50020** - Client communication (game input/updates)

---

## 🏗️ Architecture

### Server Architecture
```
┌─────────────────────────────────────────┐
│         PongServer                      │
├─────────────────────────────────────────┤
│ Threads:                                │
│  - discovery_thread   (UDP listener)    │
│  - control_thread     (peer messages)   │
│  - client_thread      (player messages) │
│  - heartbeat_thread   (leader monitor)  │
│  - game_thread        (physics + state) │
│  - timeout_checker    (disconnect/forfeit)│
└─────────────────────────────────────────┘
```

### Room States
- **WAITING_FOR_PLAYERS** - 1 player waiting
- **ACTIVE** - 2 players, game running
- **PAUSED** - 1 player disconnected, waiting for reconnect
- **FINISHED** - Game over (score = 3 or forfeit)

### Message Flow
```
Client → MSG_GAME_INPUT → Server (Leader)
Server → MSG_GAME_UPDATE → Clients
Server → MSG_ROOM_PAUSED → Client (on disconnect)
Client → MSG_DISCONNECT → Server (on close)
Server → MSG_ROOM_RESUMED → Clients (countdown)
Server → MSG_GAME_FORFEIT → Client (60s timeout)
```

---

## 🗳️ Leader Election (Bully Algorithm)

### How it Works
1. **Startup:** Server with highest UUID becomes leader
2. **Heartbeat:** Leader sends heartbeat every 1 second
3. **Timeout:** Followers wait 3 seconds before declaring leader dead
4. **Election:** On timeout, servers with lower UUIDs send ELECTION to higher peers
5. **Takeover:** Highest peer becomes leader, broadcasts COORDINATOR

### Example
```
Server A (111...) - Follower
Server B (999...) - Leader ⭐
Server C (555...) - Follower

B crashes →
C detects timeout →
C sends ELECTION to none (highest) →
C becomes leader ⭐
C broadcasts COORDINATOR →
A updates leader = C
```

---

## 🧪 Testing Scenarios

### Test 1: Basic Gameplay
```bash
# Terminal 1
python server.py

# Terminal 2
python client.py --player 1

# Terminal 3
python client.py --player 2

# Play until one player reaches 3 points
```

### Test 2: Pause/Resume
```bash
# Start server and 2 clients (player 1 and player 2)
# Play for a bit, then:

# Close Player 2 window
# Player 1 should see: "GAME PAUSED - 60s countdown"

# Restart Player 2 within 60 seconds:
python client.py --player 2

# Both players see: "OPPONENT RECONNECTED! 3... 2... 1..."
# Game resumes from same score
```

### Test 3: Forfeit
```bash
# Start game between Player 1 and Player 2
# Close Player 2
# Wait 60 seconds without reconnecting
# Player 1 sees: "OPPONENT FORFEITED - You win!"
```

### Test 4: Multiple Rooms
```bash
# Terminal 1: Server
python server.py

# Terminal 2-3: Room 0
python client.py --player 1
python client.py --player 2

# Terminal 4-5: Room 1
python client.py --player 3
python client.py --player 4

# Both games run independently
# Closing player 2 only pauses Room 0
```

### Test 5: Leader Election
```bash
# Terminal 1: Server A
python server.py

# Terminal 2: Server B
python server.py

# Note which has higher UUID (becomes leader)
# Close leader server
# Follower automatically takes over
# Clients continue playing seamlessly
```

### Test 6: Smart Pairing
```bash
# Start Player 1 (waits)
python client.py --player 1

# Start Player 5 (pairs with Player 1)
python client.py --player 5

# They play in same room despite different IDs
```

---

## 📊 Configuration Constants

### Server (`server.py`)
```python
HEARTBEAT_INTERVAL = 1.0      # Leader sends heartbeat every 1s
HEARTBEAT_TIMEOUT = 3.0       # Declare leader dead after 3s
TICK_INTERVAL = 0.05          # Game physics update rate (20 Hz)
WINNING_SCORE = 3             # Points needed to win
RECONNECT_TIMEOUT = 60.0      # Seconds to wait for reconnection
PLAYER_TIMEOUT = 3.0          # Inactivity timeout (active games only)
RESUME_COUNTDOWN = 3          # Countdown before resume (3-2-1)
```

### Client (`client.py`)
```python
SERVER_TIMEOUT = 5.0          # Declare server dead after 5s
                              # (10s during pause)
```

---

## 🐛 Known Issues & Limitations

### Not Implemented (from requirements)
1. **Logical Time** - No Lamport timestamps or vector clocks
2. **Reliable Ordered Multicast** - No sequence numbers or FIFO guarantees

### Current Limitations
1. **UDP reliability** - Messages can be lost (mitigated by continuous updates)
2. **Network partitions** - Can cause split-brain scenarios
3. **No Byzantine fault tolerance** - Assumes honest servers
4. **Fixed subnet** - Discovery assumes /24 network (255.255.255.0)

---

## 🔍 Troubleshooting

### "No servers found"
- Ensure all machines on same subnet
- Check firewall settings
- Verify UDP ports 50000, 50010, 50020 are open
- Try disabling VPN

### "Connection Lost" during pause
- Should NOT happen - indicates bug
- Check server console for errors
- Verify server is sending pause updates

### "Duplicate player ID" error
- Another client already connected with that ID
- Use different player number
- Close previous client first

### Players not pairing
- Wait a few seconds
- Check server console for "MATCHMAKING" messages
- Restart clients if stuck

### Resume countdown not showing
- Ensure you're using same player ID when reconnecting
- Check server console for "RECONNECT SUCCESS" message
- Try waiting 1-2 seconds after restart

---

## 📚 Code Structure

### server.py
- `PongServer` - Main server class
- `Room` - Game room with state and players
- `RoomState` - Enum for room states
- `_assign_room()` - Smart matchmaking algorithm
- `_handle_player_disconnect()` - Pause mechanism
- `_handle_player_reconnect()` - Resume mechanism
- `_resume_countdown()` - 3-2-1 countdown thread
- `start_election()` - Bully algorithm implementation
- `become_leader()` - Leader takeover
- `game_loop()` - Physics and state broadcast
- `timeout_checker_loop()` - Disconnect/forfeit detection

### client.py
- `PongClient` - Main client class
- `_find_server()` - Server discovery
- `_reconnect()` - Reconnection on timeout
- `send_disconnect()` - Explicit disconnect (3x for reliability)
- `receive_updates()` - Message handler
- `draw()` - Pygame rendering with overlays
- `game_loop()` - Main game loop (60 FPS)

### common.py
- `make_udp_socket()` - Cross-platform socket creation
- `send_message()` - JSON serialization + UDP send
- `recv_message()` - UDP receive + JSON deserialization
- Message type constants

### game_state.py
- `GameState` - Dataclass for game physics
- `step()` - Update paddles, ball, collisions
- `_reset_ball()` - Reset after scoring
- `to_dict()` / `from_dict()` - Serialization

### discovery.py
- `server_discovery_listener()` - Respond to discovery requests
- `client_discover_servers()` - Multi-strategy broadcast
- `get_smart_broadcast_ip()` - Auto-detect subnet

---

## 🎯 Success Criteria

Your implementation is working correctly if:
- ✅ Server starts without discovering itself
- ✅ Multiple servers elect leader correctly
- ✅ Any player ID can pair with any other
- ✅ Game pauses immediately on disconnect (<3s)
- ✅ Connected player stays connected for 60s
- ✅ Reconnecting player sees 3-2-1 countdown
- ✅ Game resumes with same score
- ✅ Forfeit awarded after 60s timeout
- ✅ Multiple rooms work independently
- ✅ Leader failover is seamless

---

## 📞 Support

If you encounter issues:
1. Check server console for error messages
2. Verify network connectivity (ping between machines)
3. Review firewall settings
4. Ensure pygame is installed
5. Try restarting all components

---

## 🚀 Future Enhancements

To fully meet distributed systems requirements:
1. **Add Lamport Timestamps** - Track causality
2. **Implement FIFO Multicast** - Sequence numbers + hold-back queues
3. **Add Byzantine fault tolerance** - Handle malicious servers
4. **Improve discovery** - Support multiple subnets
5. **Add persistence** - Save game state to disk
6. **Implement matchmaking** - Skill-based pairing
7. **Add spectator mode** - Watch games in progress
8. **Add replay system** - Record and playback games

---

## 📜 License

This is an educational project for distributed systems coursework.

---

## 👨‍💻 Author

Created for MSc Computer Science - Service Technology and Engineering course
