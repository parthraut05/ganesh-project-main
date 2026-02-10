import argparse
import pygame
import time
import sys
import atexit
from discovery import client_discover_servers
from common import (
    make_udp_socket, send_message, recv_message,
    CLIENT_PORT, MSG_GAME_INPUT, MSG_GAME_UPDATE,
    MSG_DISCONNECT, MSG_ROOM_PAUSED, MSG_ROOM_RESUMED, MSG_GAME_FORFEIT
)
from game_state import GameState

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 400
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)
BLUE = (100, 149, 237)

SERVER_TIMEOUT = 5.0

class PongClient:
    def __init__(self, player: int):
        self.player = player
        self.sock = make_udp_socket(bind_ip="0.0.0.0", bind_port=0)
        self.sock.settimeout(0.001)
        self.server_addr = None
        self.state = None
        self.running = True
        self.font = None
        self.large_font = None
        self.small_font = None
        self.last_update_time = 0

        # Game states
        self.game_over = False
        self.winner = None
        self.game_over_time = 0
        self.duplicate_rejected = False

        # Pause/Resume states
        self.is_paused = False
        self.pause_time_remaining = 0
        self.is_resuming = False
        self.resume_countdown = 0

        # Forfeit state
        self.forfeited = False
        self.forfeit_reason = None
        self.forfeit_time = 0

        atexit.register(self._cleanup)

    def _cleanup(self):
        """Ensure disconnect is sent even on abnormal exit"""
        try:
            if self.server_addr and self.sock:
                self.send_disconnect()
                time.sleep(0.1)
        except:
            pass

    def _find_server(self):
        """Blocking search for a server"""
        print(f"[CLIENT {self.player}] Scanning for servers...")
        search_start = time.time()

        while True:
            if pygame.get_init():
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                        return None

            found = client_discover_servers(timeout=1.0)
            if found:
                sid, sip = found[0]
                print(f"[CLIENT] Found server {sid[:8]}... at {sip}")
                return (sip, CLIENT_PORT)

            if time.time() - search_start > 5:
                print(".", end="", flush=True)
                search_start = time.time()

    def _reconnect(self, screen=None):
        """Called when connection is lost"""
        print("\n[CLIENT] Connection lost! Reconnecting...")
        self.server_addr = None

        while self.server_addr is None and self.running:
            if screen and self.font:
                screen.fill(BLACK)
                text = self.large_font.render("Connection Lost", True, RED)
                text2 = self.font.render("Searching for new Server...", True, WHITE)
                screen.blit(text, (SCREEN_WIDTH//2 - 180, SCREEN_HEIGHT//2 - 30))
                screen.blit(text2, (SCREEN_WIDTH//2 - 200, SCREEN_HEIGHT//2 + 20))
                pygame.display.flip()

            self.server_addr = self._find_server()

            if self.server_addr:
                self.last_update_time = time.time()
                self._announce()
                self.is_paused = False
                self.is_resuming = False

    def start(self):
        self.server_addr = self._find_server()
        if self.server_addr:
            self.last_update_time = time.time()
            self._announce()
        self.game_loop()

    def _announce(self):
        """Announce presence to server"""
        if self.server_addr:
            send_message(self.sock, self.server_addr, {
                "type": MSG_GAME_INPUT,
                "player": self.player,
                "dir": 0
            })
            print(f"[CLIENT] Announced to server")

    def send_input(self, direction: int):
        """Send input to server - ALWAYS send even when paused (keeps connection alive)"""
        if self.server_addr:
            send_message(self.sock, self.server_addr, {
                "type": MSG_GAME_INPUT,
                "player": self.player,
                "dir": direction if not self.is_paused and not self.game_over else 0
            })

    def send_disconnect(self):
        """Reliably notify server of disconnect"""
        if self.server_addr:
            try:
                disconnect_msg = {
                    "type": MSG_DISCONNECT,
                    "player": self.player
                }
                # Send multiple times to ensure delivery
                for _ in range(3):
                    send_message(self.sock, self.server_addr, disconnect_msg)
                    time.sleep(0.05)
                print(f"[CLIENT] Sent disconnect notification")
            except:
                pass

    def receive_updates(self):
        """Receive and process messages from server"""
        try:
            msg, addr = recv_message(self.sock)
            msg_type = msg.get("type")

            if msg_type == "ERROR":
                print(f"\n[ERROR] {msg.get('message')}")
                self.duplicate_rejected = True
                self.running = False
                return

            elif msg_type == MSG_GAME_UPDATE:
                self.state = GameState.from_dict(msg["state"])
                self.last_update_time = time.time()

                if msg.get("game_over"):
                    self.game_over = True
                    self.winner = msg.get("winner")
                    self.game_over_time = time.time()
                    self.is_paused = False
                    self.is_resuming = False
                    print(f"\n[GAME OVER] Player {self.winner} wins!")

            elif msg_type == MSG_ROOM_PAUSED:
                self.state = GameState.from_dict(msg["state"])
                self.last_update_time = time.time()

                if not self.is_paused:
                    print(f"\n[PAUSED] Game paused!")

                self.is_paused = True
                self.is_resuming = False
                self.pause_time_remaining = msg.get("time_remaining", 60)

            elif msg_type == MSG_ROOM_RESUMED:
                self.state = GameState.from_dict(msg["state"])
                self.last_update_time = time.time()
                countdown = msg.get("countdown", 0)

                if countdown > 0:
                    if not self.is_resuming:
                        print(f"\n[RESUMING] Opponent reconnected!")
                    self.is_resuming = True
                    self.resume_countdown = countdown
                    print(f"[RESUMING] Countdown: {countdown}")
                else:
                    self.is_paused = False
                    self.is_resuming = False
                    print("[RESUMED] Game resumed!")

            elif msg_type == MSG_GAME_FORFEIT:
                self.state = GameState.from_dict(msg["state"])
                self.last_update_time = time.time()
                self.forfeited = True
                self.forfeit_reason = msg.get("reason", "unknown")
                self.forfeit_time = time.time()
                self.is_paused = False
                self.is_resuming = False
                print(f"\n[FORFEIT] Game ended: {self.forfeit_reason}")

        except:
            pass

    def draw(self, screen):
        """Draw game state"""

        if self.duplicate_rejected:
            screen.fill(BLACK)
            txt1 = self.large_font.render("CONNECTION REJECTED", True, RED)
            txt2 = self.font.render(f"Player ID {self.player} is already connected", True, WHITE)
            txt3 = self.font.render("Please choose a different player ID", True, YELLOW)
            screen.blit(txt1, (SCREEN_WIDTH//2 - 280, SCREEN_HEIGHT//2 - 60))
            screen.blit(txt2, (SCREEN_WIDTH//2 - 250, SCREEN_HEIGHT//2))
            screen.blit(txt3, (SCREEN_WIDTH//2 - 260, SCREEN_HEIGHT//2 + 40))
            pygame.display.flip()
            return

        if self.forfeited:
            screen.fill(BLACK)

            title = self.large_font.render("OPPONENT FORFEITED", True, YELLOW)
            subtitle = self.font.render("You win by forfeit!", True, GREEN)
            reason_map = {
                "opponent_timeout": "Opponent did not reconnect in time",
                "opponent_left": "Opponent left the game"
            }
            reason_text = self.small_font.render(
                reason_map.get(self.forfeit_reason, "Opponent disconnected"),
                True, WHITE
            )

            screen.blit(title, (SCREEN_WIDTH//2 - 260, SCREEN_HEIGHT//2 - 80))
            screen.blit(subtitle, (SCREEN_WIDTH//2 - 150, SCREEN_HEIGHT//2 - 20))
            screen.blit(reason_text, (SCREEN_WIDTH//2 - 200, SCREEN_HEIGHT//2 + 40))

            if time.time() - self.forfeit_time > 5:
                self.forfeited = False
                self.state = None

            pygame.display.flip()
            return

        if self.game_over and self.state:
            screen.fill(BLACK)

            is_left = (self.player % 2 == 1)
            my_score = self.state.score1 if is_left else self.state.score2
            their_score = self.state.score2 if is_left else self.state.score1
            i_won = (my_score >= 3)

            if i_won:
                title_text = self.large_font.render("YOU WON!", True, GREEN)
            else:
                title_text = self.large_font.render("YOU LOST!", True, RED)

            score_text = self.large_font.render(f"{my_score} - {their_score}", True, WHITE)
            restart_text = self.font.render("New game starting...", True, YELLOW)

            screen.blit(title_text, (SCREEN_WIDTH//2 - 140, SCREEN_HEIGHT//2 - 80))
            screen.blit(score_text, (SCREEN_WIDTH//2 - 60, SCREEN_HEIGHT//2 - 20))
            screen.blit(restart_text, (SCREEN_WIDTH//2 - 160, SCREEN_HEIGHT//2 + 60))
            pygame.display.flip()

            if time.time() - self.game_over_time > 3.0:
                self.game_over = False
                self.winner = None

            return

        if not self.state:
            screen.fill(BLACK)
            txt = self.font.render(f"Player {self.player}: Waiting for Match...", True, WHITE)
            dots = "." * (int(time.time() * 2) % 4)
            txt2 = self.small_font.render(f"Searching{dots}", True, GRAY)
            screen.blit(txt, (SCREEN_WIDTH//2 - 240, SCREEN_HEIGHT//2 - 20))
            screen.blit(txt2, (SCREEN_WIDTH//2 - 60, SCREEN_HEIGHT//2 + 20))
            pygame.display.flip()
            return

        # Normal game rendering
        screen.fill(BLACK)

        is_left_side = (self.player % 2 == 1)

        # Draw Left Paddle
        p1_color = RED if is_left_side else WHITE
        pygame.draw.rect(screen, p1_color, (
            self.state.paddle_x_offset,
            self.state.p1_y - self.state.paddle_height // 2,
            self.state.paddle_width,
            self.state.paddle_height
        ))

        # Draw Right Paddle
        p2_color = RED if not is_left_side else WHITE
        pygame.draw.rect(screen, p2_color, (
            SCREEN_WIDTH - self.state.paddle_x_offset - self.state.paddle_width,
            self.state.p2_y - self.state.paddle_height // 2,
            self.state.paddle_width,
            self.state.paddle_height
        ))

        # Draw Ball
        pygame.draw.rect(screen, WHITE, (
            self.state.ball_x - self.state.ball_size // 2,
            self.state.ball_y - self.state.ball_size // 2,
            self.state.ball_size,
            self.state.ball_size
        ))

        # Draw Scores
        s1 = self.large_font.render(str(self.state.score1), True, WHITE)
        s2 = self.large_font.render(str(self.state.score2), True, WHITE)
        screen.blit(s1, (SCREEN_WIDTH // 4, 20))
        screen.blit(s2, (3 * SCREEN_WIDTH // 4, 20))

        # Draw Player ID
        id_text = self.small_font.render(f"You: P{self.player}", True, YELLOW)
        screen.blit(id_text, (10, 10))

        # PAUSE OVERLAY
        if self.is_paused and not self.is_resuming:
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            overlay.set_alpha(180)
            overlay.fill(BLACK)
            screen.blit(overlay, (0, 0))

            pause_title = self.large_font.render("GAME PAUSED", True, YELLOW)
            pause_msg = self.font.render("Opponent disconnected", True, WHITE)

            if self.pause_time_remaining > 0:
                countdown_msg = self.font.render(
                    f"Waiting for reconnection: {self.pause_time_remaining}s",
                    True, BLUE
                )
            else:
                countdown_msg = self.font.render("Checking connection...", True, BLUE)

            info_text = self.small_font.render("Your connection is stable", True, GREEN)

            screen.blit(pause_title, (SCREEN_WIDTH//2 - 180, SCREEN_HEIGHT//2 - 100))
            screen.blit(pause_msg, (SCREEN_WIDTH//2 - 180, SCREEN_HEIGHT//2 - 40))
            screen.blit(countdown_msg, (SCREEN_WIDTH//2 - 240, SCREEN_HEIGHT//2 + 20))
            screen.blit(info_text, (SCREEN_WIDTH//2 - 160, SCREEN_HEIGHT//2 + 80))

        # RESUME COUNTDOWN OVERLAY
        elif self.is_resuming:
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            overlay.set_alpha(180)
            overlay.fill(BLACK)
            screen.blit(overlay, (0, 0))

            resume_title = self.large_font.render("OPPONENT RECONNECTED!", True, GREEN)
            countdown_text = self.large_font.render(str(self.resume_countdown), True, YELLOW)
            ready_text = self.font.render("Get Ready...", True, WHITE)

            screen.blit(resume_title, (SCREEN_WIDTH//2 - 300, SCREEN_HEIGHT//2 - 80))
            screen.blit(countdown_text, (SCREEN_WIDTH//2 - 30, SCREEN_HEIGHT//2))
            screen.blit(ready_text, (SCREEN_WIDTH//2 - 80, SCREEN_HEIGHT//2 + 80))

        pygame.display.flip()

    def game_loop(self):
        """Main game loop"""
        pygame.init()
        pygame.font.init()
        screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(f"Pong Player {self.player}")
        clock = pygame.time.Clock()

        self.font = pygame.font.Font(None, 36)
        self.large_font = pygame.font.Font(None, 64)
        self.small_font = pygame.font.Font(None, 28)

        while self.running:
            if self.duplicate_rejected:
                self.draw(screen)
                pygame.time.wait(3000)
                break

            timeout_threshold = SERVER_TIMEOUT if not self.is_paused else SERVER_TIMEOUT * 2
            if self.server_addr and time.time() - self.last_update_time > timeout_threshold:
                print(f"\n[TIMEOUT] No server response for {timeout_threshold}s")
                self._reconnect(screen)
                clock.tick(60)
                continue

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    print("\n[CLIENT] Window closed, sending disconnect...")
                    self.send_disconnect()
                    self.running = False

            keys = pygame.key.get_pressed()
            d = 0
            if not self.game_over and not self.is_paused:
                if keys[pygame.K_UP]:
                    d = -1
                elif keys[pygame.K_DOWN]:
                    d = 1

            self.send_input(d)
            self.receive_updates()
            self.draw(screen)
            clock.tick(60)

        print("[CLIENT] Cleaning up...")
        self.send_disconnect()
        time.sleep(0.2)
        pygame.quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", type=int, required=True, help="Player ID (must be unique)")
    args = parser.parse_args()

    if args.player < 1:
        print("Error: Player ID must be >= 1")
        sys.exit(1)

    print(f"\n╔══════════════════════════════════════╗")
    print(f"║   Distributed Pong - Player {args.player:2d}      ║")
    print(f"╚══════════════════════════════════════╝")
    print("Controls: UP/DOWN arrows to move paddle")
    print("Close window to disconnect cleanly\n")

    PongClient(args.player).start()
