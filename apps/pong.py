#!/usr/bin/env python3
"""
Cross-Device Multiplayer Pong Game Server
Serves a web-based Pong game with WebSocket multiplayer support
No external dependencies - single Python file creates complete game server
"""

import socket
import threading
import json
import time
import sys
import random
from dataclasses import dataclass, asdict
from typing import Optional, Dict
import argparse
import http.server
import socketserver
from urllib.parse import urlparse
import base64
import hashlib
import struct
import webbrowser

# Constants
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 400
PADDLE_WIDTH = 10
PADDLE_HEIGHT = 100
BALL_RADIUS = 8
PADDLE_SPEED = 7
INITIAL_BALL_SPEED = 5
BALL_SPEED_INCREMENT = 0.3
WINNING_SCORE = 10

@dataclass
class Ball:
    x: float = WINDOW_WIDTH // 2
    y: float = WINDOW_HEIGHT // 2
    dx: float = INITIAL_BALL_SPEED
    dy: float = 3
    radius: int = BALL_RADIUS
    speed: float = INITIAL_BALL_SPEED

@dataclass
class Paddle:
    x: float
    y: float = WINDOW_HEIGHT // 2 - PADDLE_HEIGHT // 2
    width: int = PADDLE_WIDTH
    height: int = PADDLE_HEIGHT

@dataclass
class GameState:
    ball: Ball
    paddle1: Paddle
    paddle2: Paddle
    score1: int = 0
    score2: int = 0
    game_started: bool = False

def parse_websocket_frame(data):
    """Parse a WebSocket frame"""
    if len(data) < 2:
        return None

    byte1, byte2 = data[0], data[1]
    opcode = byte1 & 0x0f
    masked = (byte2 >> 7) & 1
    payload_length = byte2 & 0x7f

    offset = 2

    if payload_length == 126:
        payload_length = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
    elif payload_length == 127:
        payload_length = struct.unpack('>Q', data[offset:offset+8])[0]
        offset += 8

    if masked:
        mask = data[offset:offset+4]
        offset += 4
        payload = bytearray(data[offset:offset+payload_length])
        for i in range(len(payload)):
            payload[i] ^= mask[i % 4]
    else:
        payload = data[offset:offset+payload_length]

    return payload.decode('utf-8') if opcode == 1 else None

def create_websocket_frame(payload):
    """Create a WebSocket frame"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')

    payload_length = len(payload)
    frame = bytearray()

    # FIN=1, opcode=1 (text)
    frame.append(0x81)

    if payload_length < 126:
        frame.append(payload_length)
    elif payload_length < 65536:
        frame.append(126)
        frame.extend(struct.pack('>H', payload_length))
    else:
        frame.append(127)
        frame.extend(struct.pack('>Q', payload_length))

    frame.extend(payload)
    return bytes(frame)

def websocket_handshake(request_headers):
    """Perform WebSocket handshake"""
    key = None
    for line in request_headers.split('\n'):
        if line.lower().startswith('sec-websocket-key:'):
            key = line.split(':')[1].strip()
            break

    if not key:
        return None

    # WebSocket magic string
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    accept_key = base64.b64encode(hashlib.sha1((key + magic).encode()).digest()).decode()

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key}\r\n"
        "\r\n"
    )

    return response.encode()

class GameRoom:
    def __init__(self, room_id):
        self.room_id = room_id
        self.players = {}  # socket -> player_info
        self.game_state = GameState(
            ball=Ball(),
            paddle1=Paddle(x=20),
            paddle2=Paddle(x=WINDOW_WIDTH - 30)
        )
        self.last_update = time.time()
        self.reset_ball()

    def reset_ball(self):
        """Reset ball to center with random direction"""
        self.game_state.ball.x = WINDOW_WIDTH // 2
        self.game_state.ball.y = WINDOW_HEIGHT // 2
        self.game_state.ball.speed = INITIAL_BALL_SPEED
        self.game_state.ball.dx = INITIAL_BALL_SPEED if random.random() > 0.5 else -INITIAL_BALL_SPEED
        self.game_state.ball.dy = random.uniform(-3, 3)

    def reset_game(self):
        """Reset the entire game"""
        self.game_state.score1 = 0
        self.game_state.score2 = 0
        self.reset_ball()

    def add_player(self, client_socket, player_info):
        """Add a player to the room"""
        if len(self.players) < 2:
            player_id = len(self.players) + 1
            player_info['id'] = player_id
            self.players[client_socket] = player_info

            if len(self.players) == 2:
                self.game_state.game_started = True
                self.reset_game()

            return player_id
        return None

    def remove_player(self, client_socket):
        """Remove a player from the room"""
        if client_socket in self.players:
            del self.players[client_socket]
            if len(self.players) < 2:
                self.game_state.game_started = False

    def update_paddle(self, player_id, y):
        """Update paddle position"""
        y = max(0, min(WINDOW_HEIGHT - PADDLE_HEIGHT, y))
        if player_id == 1:
            self.game_state.paddle1.y = y
        elif player_id == 2:
            self.game_state.paddle2.y = y

    def update_game(self):
        """Update game physics"""
        if not self.game_state.game_started or len(self.players) < 2:
            return None

        current_time = time.time()
        dt = current_time - self.last_update
        self.last_update = current_time

        # Update ball position
        self.game_state.ball.x += self.game_state.ball.dx * dt * 60
        self.game_state.ball.y += self.game_state.ball.dy * dt * 60

        # Ball collision with top/bottom walls
        if (self.game_state.ball.y <= self.game_state.ball.radius or
            self.game_state.ball.y >= WINDOW_HEIGHT - self.game_state.ball.radius):
            self.game_state.ball.dy = -self.game_state.ball.dy

        # Ball collision with paddles
        ball = self.game_state.ball
        p1 = self.game_state.paddle1
        p2 = self.game_state.paddle2

        # Left paddle collision
        if (ball.x - ball.radius <= p1.x + p1.width and
            ball.y >= p1.y and ball.y <= p1.y + p1.height and
            ball.dx < 0):
            ball.dx = -ball.dx
            ball.x = p1.x + p1.width + ball.radius
            # Increase ball speed
            ball.speed += BALL_SPEED_INCREMENT
            speed_multiplier = ball.speed / INITIAL_BALL_SPEED
            ball.dx = ball.speed
            ball.dy = ball.dy * speed_multiplier

        # Right paddle collision
        if (ball.x + ball.radius >= p2.x and
            ball.y >= p2.y and ball.y <= p2.y + p2.height and
            ball.dx > 0):
            ball.dx = -ball.dx
            ball.x = p2.x - ball.radius
            # Increase ball speed
            ball.speed += BALL_SPEED_INCREMENT
            speed_multiplier = ball.speed / INITIAL_BALL_SPEED
            ball.dx = -ball.speed
            ball.dy = ball.dy * speed_multiplier

        # Scoring
        scored = False
        if ball.x < 0:
            self.game_state.score2 += 1
            scored = True
        elif ball.x > WINDOW_WIDTH:
            self.game_state.score1 += 1
            scored = True

        if scored:
            self.reset_ball()

        # Check for game end
        if self.game_state.score1 >= WINNING_SCORE or self.game_state.score2 >= WINNING_SCORE:
            winner = 1 if self.game_state.score1 >= WINNING_SCORE else 2
            self.game_state.game_started = False
            return {'type': 'game_end', 'winner': winner}

        return None

    def get_state_dict(self):
        """Get game state as dictionary"""
        return {
            'ball': asdict(self.game_state.ball),
            'paddle1': asdict(self.game_state.paddle1),
            'paddle2': asdict(self.game_state.paddle2),
            'score1': self.game_state.score1,
            'score2': self.game_state.score2,
            'game_started': self.game_state.game_started,
            'players_count': len(self.players)
        }

    def broadcast(self, message, exclude=None):
        """Broadcast message to all players in the room"""
        frame = create_websocket_frame(json.dumps(message))
        for client_socket in list(self.players.keys()):
            if client_socket != exclude:
                try:
                    client_socket.send(frame)
                except:
                    self.remove_player(client_socket)

class PongHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, server=None, **kwargs):
        self.server_instance = server
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(self.server_instance.get_html_content().encode())
        else:
            super().do_GET()

class WebSocketServer:
    def __init__(self, host='0.0.0.0', port=8080):
        self.host = host
        self.port = port
        self.rooms = {}  # room_id -> GameRoom
        self.client_rooms = {}  # socket -> room_id
        self.running = False

    def start(self):
        """Start the WebSocket server"""
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen(5)

        print(f"🎮 WebSocket server listening on {self.host}:{self.port}")

        # Start game update thread
        threading.Thread(target=self.game_update_loop, daemon=True).start()

        while self.running:
            try:
                client_socket, addr = self.socket.accept()
                threading.Thread(target=self.handle_client, args=(client_socket, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    print(f"Error accepting connection: {e}")

    def handle_client(self, client_socket, addr):
        """Handle a WebSocket client connection"""
        try:
            # Read the HTTP request
            request = client_socket.recv(1024).decode()

            # Perform WebSocket handshake
            handshake_response = websocket_handshake(request)
            if not handshake_response:
                client_socket.close()
                return

            client_socket.send(handshake_response)
            print(f"🔗 WebSocket client connected from {addr}")

            # Handle WebSocket messages
            while self.running:
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break

                    message_text = parse_websocket_frame(data)
                    if message_text:
                        message = json.loads(message_text)
                        self.handle_message(client_socket, message)

                except Exception as e:
                    print(f"Error handling message: {e}")
                    break

        except Exception as e:
            print(f"Error with client {addr}: {e}")

        finally:
            self.cleanup_client(client_socket)
            client_socket.close()

    def handle_message(self, client_socket, message):
        """Handle a WebSocket message"""
        msg_type = message.get('type')

        if msg_type == 'create_room':
            room_id = message.get('room_id')
            if room_id not in self.rooms:
                self.rooms[room_id] = GameRoom(room_id)

            room = self.rooms[room_id]
            player_id = room.add_player(client_socket, {'socket': client_socket})

            if player_id:
                self.client_rooms[client_socket] = room_id
                response = {
                    'type': 'room_created',
                    'room_id': room_id,
                    'player_id': player_id
                }
                self.send_message(client_socket, response)

        elif msg_type == 'join_room':
            room_id = message.get('room_id')
            if room_id in self.rooms:
                room = self.rooms[room_id]
                player_id = room.add_player(client_socket, {'socket': client_socket})

                if player_id:
                    self.client_rooms[client_socket] = room_id
                    response = {
                        'type': 'room_joined',
                        'room_id': room_id,
                        'player_id': player_id
                    }
                    self.send_message(client_socket, response)
                else:
                    self.send_message(client_socket, {
                        'type': 'error',
                        'message': 'Room is full'
                    })
            else:
                self.send_message(client_socket, {
                    'type': 'error',
                    'message': 'Room not found'
                })

        elif msg_type == 'leave_room':
            self.cleanup_client(client_socket)

        elif msg_type == 'paddle_update':
            if client_socket in self.client_rooms:
                room_id = self.client_rooms[client_socket]
                room = self.rooms[room_id]
                player_id = message.get('player_id')
                y = message.get('y')
                room.update_paddle(player_id, y)

        elif msg_type == 'new_game':
            if client_socket in self.client_rooms:
                room_id = self.client_rooms[client_socket]
                room = self.rooms[room_id]
                room.reset_game()
                room.game_state.game_started = True

    def send_message(self, client_socket, message):
        """Send a message to a WebSocket client"""
        try:
            frame = create_websocket_frame(json.dumps(message))
            client_socket.send(frame)
        except:
            self.cleanup_client(client_socket)

    def cleanup_client(self, client_socket):
        """Clean up client connection"""
        if client_socket in self.client_rooms:
            room_id = self.client_rooms[client_socket]
            if room_id in self.rooms:
                self.rooms[room_id].remove_player(client_socket)
                # Remove empty rooms
                if len(self.rooms[room_id].players) == 0:
                    del self.rooms[room_id]
            del self.client_rooms[client_socket]

    def game_update_loop(self):
        """Main game update loop"""
        while self.running:
            try:
                for room_id, room in list(self.rooms.items()):
                    if room.game_state.game_started:
                        game_end = room.update_game()

                        # Broadcast game state
                        state_message = {
                            'type': 'game_state',
                            'state': room.get_state_dict()
                        }
                        room.broadcast(state_message)

                        # Handle game end
                        if game_end:
                            room.broadcast(game_end)

                time.sleep(1/60)  # 60 FPS
            except Exception as e:
                print(f"Error in game update loop: {e}")

    def stop(self):
        """Stop the WebSocket server"""
        self.running = False
        if hasattr(self, 'socket'):
            self.socket.close()

class PongServer:
    def __init__(self, host='0.0.0.0', http_port=8000, ws_port=8080):
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.ws_server = None
        self.http_server = None

    def get_html_content(self):
        """Generate the HTML content for the Pong game"""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cross-Device Pong - Advanced Python Server</title>
    <style>
        :root {
            --primary-bg: #1a1a1a;
            --secondary-bg: #2a2a2a;
            --accent-bg: #333;
            --primary-color: #fff;
            --accent-color: #0066cc;
            --accent-hover: #0080ff;
            --success-color: #2d5a27;
            --error-color: #5a2727;
            --warning-color: #5a5a27;
            --player1-color: #ff6b35;
            --player2-color: #4ecdc4;
            --ball-color: #fff;
            --border-color: #666;
        }

        body {
            margin: 0;
            padding: 10px;
            background: var(--primary-bg);
            color: var(--primary-color);
            font-family: 'Segoe UI', 'Roboto', 'Courier New', monospace;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            line-height: 1.4;
        }

        .container {
            max-width: 1200px;
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        #gameContainer {
            text-align: center;
            position: relative;
            order: 1;
        }

        canvas {
            border: 3px solid var(--border-color);
            background: #000;
            display: block;
            margin: 10px auto;
            border-radius: 8px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            max-width: 100%;
            height: auto;
        }

        .controls-panel {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 15px;
            order: 2;
        }

        .control-section {
            background: var(--secondary-bg);
            border-radius: 8px;
            padding: 15px;
            border: 1px solid var(--border-color);
        }

        .control-section h3 {
            margin: 0 0 12px 0;
            color: var(--accent-color);
            border-bottom: 2px solid var(--accent-color);
            padding-bottom: 4px;
            font-size: 16px;
        }

        button {
            background: var(--accent-color);
            color: white;
            border: none;
            padding: 8px 12px;
            margin: 3px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s ease;
            min-width: 80px;
        }

        button:hover {
            background: var(--accent-hover);
            transform: translateY(-1px);
            box-shadow: 0 2px 4px rgba(0,102,204,0.3);
        }

        button:disabled {
            background: #666;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .btn-primary { background: var(--accent-color); }
        .btn-success { background: #28a745; }
        .btn-danger { background: #dc3545; }
        .btn-warning { background: #ffc107; color: #000; }

        input, select {
            padding: 6px 10px;
            margin: 2px;
            border: 2px solid var(--border-color);
            background: var(--accent-bg);
            color: var(--primary-color);
            border-radius: 4px;
            font-size: 13px;
            transition: border-color 0.3s ease;
            width: 100%;
            max-width: 200px;
        }

        input:focus, select:focus {
            outline: none;
            border-color: var(--accent-color);
        }

        .score {
            font-size: 36px;
            font-weight: bold;
            margin: 8px 0;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        }

        .player1-score { color: var(--player1-color); }
        .player2-score { color: var(--player2-color); }

        .status {
            margin: 8px 0;
            padding: 8px 15px;
            border-radius: 6px;
            font-weight: bold;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            font-size: 14px;
        }

        .status-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }

        .connected { background: var(--success-color); }
        .disconnected { background: var(--error-color); }
        .waiting { background: var(--warning-color); }

        .room-code {
            background: var(--accent-bg);
            border: 2px solid var(--accent-color);
            padding: 10px;
            border-radius: 6px;
            font-size: 20px;
            text-align: center;
            margin: 8px 0;
            font-family: 'Courier New', monospace;
            letter-spacing: 3px;
            font-weight: bold;
        }

        .key-indicators {
            display: flex;
            gap: 8px;
            justify-content: center;
            margin: 8px 0;
            opacity: 0.7;
        }

        .key-indicator {
            width: 32px;
            height: 32px;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 12px;
            transition: all 0.15s ease;
            background: var(--accent-bg);
            color: #999;
        }

        .key-indicator.active {
            background: var(--accent-color);
            border-color: var(--accent-color);
            color: white;
            transform: scale(0.95);
        }

        .settings-panel {
            background: var(--secondary-bg);
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
            border: 1px solid var(--border-color);
        }

        .settings-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px;
            margin-top: 10px;
        }

        .setting-item {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .setting-item label {
            font-weight: 500;
            color: var(--accent-color);
            font-size: 12px;
        }

        .toggle-switch {
            position: relative;
            width: 40px;
            height: 20px;
            background: #666;
            border-radius: 10px;
            cursor: pointer;
            transition: background 0.3s ease;
        }

        .toggle-switch.active {
            background: var(--accent-color);
        }

        .toggle-switch::after {
            content: '';
            position: absolute;
            width: 16px;
            height: 16px;
            background: white;
            border-radius: 50%;
            top: 2px;
            left: 2px;
            transition: transform 0.3s ease;
        }

        .toggle-switch.active::after {
            transform: translateX(20px);
        }

        .instructions {
            background: var(--secondary-bg);
            border-radius: 8px;
            padding: 15px;
            line-height: 1.5;
            border: 1px solid var(--border-color);
            order: 4;
            font-size: 14px;
        }

        .instructions h3 {
            margin: 0 0 10px 0;
            color: var(--accent-color);
            font-size: 16px;
        }

        .instructions ul {
            margin: 8px 0;
            padding-left: 20px;
        }

        .instructions li {
            margin: 4px 0;
        }

        .troubleshooting {
            margin-top: 10px;
            font-size: 12px;
            color: #999;
        }

        .troubleshooting details {
            margin-top: 8px;
        }

        .troubleshooting summary {
            cursor: pointer;
            color: var(--accent-color);
            font-weight: bold;
            font-size: 13px;
        }

        .troubleshooting ul {
            margin: 6px 0;
        }

        .mobile-controls {
            display: none;
            flex-direction: column;
            gap: 8px;
            margin: 10px 0;
            order: 3;
        }

        .mobile-paddle-control {
            display: flex;
            gap: 8px;
            justify-content: center;
        }

        .paddle-btn {
            width: 60px;
            height: 40px;
            font-size: 14px;
            font-weight: bold;
        }

        .game-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
            gap: 8px;
            margin: 10px 0;
        }

        .stat-item {
            background: var(--accent-bg);
            padding: 8px;
            border-radius: 4px;
            text-align: center;
            border: 1px solid var(--border-color);
        }

        .stat-value {
            font-size: 16px;
            font-weight: bold;
            color: var(--accent-color);
        }

        .stat-label {
            font-size: 10px;
            color: #999;
        }

        @media (max-width: 768px) {
            .controls-panel {
                grid-template-columns: 1fr;
                gap: 10px;
            }

            .mobile-controls {
                display: flex;
            }

            .score {
                font-size: 28px;
            }

            canvas {
                max-width: 95vw;
            }

            .container {
                padding: 5px;
            }
        }

        @media (max-width: 1000px) {
            .controls-panel {
                grid-template-columns: 1fr 1fr;
            }
        }

        .theme-selector {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }

        .theme-option {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            border: 2px solid var(--border-color);
            cursor: pointer;
            transition: transform 0.2s ease;
        }

        .theme-option:hover {
            transform: scale(1.1);
        }

        .theme-option.active {
            border-color: var(--accent-color);
            box-shadow: 0 0 10px rgba(0,102,204,0.5);
        }

        .copy-btn {
            font-size: 10px;
            padding: 4px 8px;
            margin: 0 0 0 8px;
        }

        .toast {
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--success-color);
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            opacity: 0;
            transition: opacity 0.3s ease;
            z-index: 1000;
        }

        .toast.show {
            opacity: 1;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 style="text-align: center; margin: 10px 0; font-size: 24px;">🏓 Cross-Device Pong</h1>

        <div id="gameContainer">
            <div id="gameStatus" class="status disconnected">
                <span class="status-indicator"></span>
                <span>Not Connected</span>
            </div>

            <div class="score">
                <span id="player1Score" class="player1-score">0</span> - <span id="player2Score" class="player2-score">0</span>
            </div>

            <canvas id="gameCanvas" width="800" height="400"></canvas>

            <div class="key-indicators">
                <div class="key-indicator" id="keyW">W</div>
                <div class="key-indicator" id="keyS">S</div>
                <div style="width: 20px;"></div>
                <div class="key-indicator" id="keyUp">↑</div>
                <div class="key-indicator" id="keyDown">↓</div>
            </div>

            <div class="game-stats">
                <div class="stat-item">
                    <div class="stat-value" id="ballSpeed">5</div>
                    <div class="stat-label">Speed</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="gameTime">0:00</div>
                    <div class="stat-label">Time</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="hitCount">0</div>
                    <div class="stat-label">Hits</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="playerCount">0</div>
                    <div class="stat-label">Players</div>
                </div>
            </div>
        </div>

        <div class="controls-panel">
            <div class="control-section">
                <h3>🎮 Game Controls</h3>

                <div id="menuControls">
                    <div style="margin-bottom: 10px;">
                        <input type="text" id="roomInput" placeholder="Room code" maxlength="4" style="width: 100px; text-align: center; font-size: 14px; letter-spacing: 2px;">
                    </div>
                    <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                        <button id="joinBtn" class="btn-primary">🔗 Join</button>
                        <button id="createBtn" class="btn-success">🎯 Create</button>
                        <button id="localBtn" class="btn-warning">🏠 Local</button>
                    </div>
                </div>

                <div id="roomControls" style="display: none;">
                    <div>
                        <strong>Room:</strong>
                        <div class="room-code">
                            <span id="roomCode">----</span>
                            <button id="copyRoomBtn" class="copy-btn btn-success">📋</button>
                        </div>
                    </div>
                    <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                        <button id="leaveBtn" class="btn-danger">🚪 Leave</button>
                        <button id="newGameBtn" class="btn-primary" disabled>🆕 New</button>
                    </div>
                </div>
            </div>

            <div class="control-section">
                <h3>⚙️ Settings</h3>

                <div class="settings-grid">
                    <div class="setting-item">
                        <label>Win Score</label>
                        <select id="winningScore">
                            <option value="5">5</option>
                            <option value="10" selected>10</option>
                            <option value="15">15</option>
                            <option value="21">21</option>
                        </select>
                    </div>

                    <div class="setting-item">
                        <label>Paddle Speed</label>
                        <select id="paddleSpeed">
                            <option value="5">Slow</option>
                            <option value="7" selected>Normal</option>
                            <option value="10">Fast</option>
                            <option value="12">V.Fast</option>
                        </select>
                    </div>

                    <div class="setting-item">
                        <label>Ball Speed</label>
                        <select id="ballSpeedSetting">
                            <option value="3">Slow</option>
                            <option value="5" selected>Normal</option>
                            <option value="7">Fast</option>
                            <option value="9">V.Fast</option>
                        </select>
                    </div>

                    <div class="setting-item">
                        <label>Sound</label>
                        <div class="toggle-switch" id="soundToggle"></div>
                    </div>
                </div>

                <div class="setting-item" style="margin-top: 10px;">
                    <label>Theme</label>
                    <div class="theme-selector">
                        <div class="theme-option active" data-theme="default" style="background: linear-gradient(45deg, #0066cc, #fff);"></div>
                        <div class="theme-option" data-theme="neon" style="background: linear-gradient(45deg, #ff00ff, #00ffff);"></div>
                        <div class="theme-option" data-theme="retro" style="background: linear-gradient(45deg, #ff6b35, #4ecdc4);"></div>
                        <div class="theme-option" data-theme="dark" style="background: linear-gradient(45deg, #333, #666);"></div>
                    </div>
                </div>
            </div>

            <div class="control-section">
                <h3>📖 Info</h3>
                <p style="margin: 0 0 8px 0; font-size: 12px;"><strong>Controls:</strong></p>
                <p style="margin: 0 0 8px 0; font-size: 11px;">Player 1: W/S keys<br>Player 2: ↑/↓ keys</p>
                <p style="margin: 0 0 8px 0; font-size: 12px;"><strong>Share URL:</strong></p>
                <div style="display: flex; align-items: center; gap: 4px;">
                    <code id="shareUrl" style="font-size: 10px; flex: 1; word-break: break-all;">http://''' + self.host + ''':''' + str(self.http_port) + '''</code>
                    <button id="copyUrlBtn" class="copy-btn btn-success">📋</button>
                </div>

                <details class="troubleshooting">
                    <summary>🔧 Tips</summary>
                    <div style="margin-top: 6px;">
                        <p><strong>Issues?</strong></p>
                        <ul style="font-size: 11px; margin: 4px 0; padding-left: 16px;">
                            <li>Try "Local Game" first</li>
                            <li>Refresh page and create new room</li>
                            <li>Check internet connection</li>
                            <li>Close other browser tabs</li>
                        </ul>
                    </div>
                </details>
            </div>
        </div>

        <div class="mobile-controls">
            <h3 style="margin: 0 0 8px 0; font-size: 16px;">📱 Touch Controls</h3>
            <div class="mobile-paddle-control">
                <button class="paddle-btn btn-primary" id="player1Up">P1 ↑</button>
                <button class="paddle-btn btn-primary" id="player1Down">P1 ↓</button>
                <button class="paddle-btn btn-primary" id="player2Up">P2 ↑</button>
                <button class="paddle-btn btn-primary" id="player2Down">P2 ↓</button>
            </div>
        </div>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        // DOM Elements
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        const gameStatus = document.getElementById('gameStatus');
        const player1Score = document.getElementById('player1Score');
        const player2Score = document.getElementById('player2Score');
        const roomInput = document.getElementById('roomInput');
        const joinBtn = document.getElementById('joinBtn');
        const createBtn = document.getElementById('createBtn');
        const localBtn = document.getElementById('localBtn');
        const leaveBtn = document.getElementById('leaveBtn');
        const newGameBtn = document.getElementById('newGameBtn');
        const roomCode = document.getElementById('roomCode');
        const copyRoomBtn = document.getElementById('copyRoomBtn');
        const copyUrlBtn = document.getElementById('copyUrlBtn');
        const menuControls = document.getElementById('menuControls');
        const roomControls = document.getElementById('roomControls');
        const shareUrl = document.getElementById('shareUrl');
        const toast = document.getElementById('toast');

        // Settings elements
        const winningScore = document.getElementById('winningScore');
        const paddleSpeed = document.getElementById('paddleSpeed');
        const ballSpeedSetting = document.getElementById('ballSpeedSetting');
        const soundToggle = document.getElementById('soundToggle');
        const themeOptions = document.querySelectorAll('.theme-option');

        // Key indicators
        const keyIndicators = {
            w: document.getElementById('keyW'),
            s: document.getElementById('keyS'),
            up: document.getElementById('keyUp'),
            down: document.getElementById('keyDown')
        };

        // Stats elements
        const ballSpeedDisplay = document.getElementById('ballSpeed');
        const gameTimeDisplay = document.getElementById('gameTime');
        const hitCountDisplay = document.getElementById('hitCount');
        const playerCountDisplay = document.getElementById('playerCount');

        // Mobile controls
        const player1Up = document.getElementById('player1Up');
        const player1Down = document.getElementById('player1Down');
        const player2Up = document.getElementById('player2Up');
        const player2Down = document.getElementById('player2Down');

        // Game state
        let ws = null;
        let playerId = null;
        let gameState = null;
        let keys = {};
        let currentRoom = null;
        let gameMode = 'network'; // 'network' or 'local'
        let localGameState = null;
        let gameStartTime = 0;
        let gameTimeInterval = null;
        let hitCount = 0;
        let networkGameLoopStarted = false;
        let lastNetworkPaddleUpdate = 0;
        const NETWORK_PADDLE_UPDATE_INTERVAL = 16; // ~60fps (16ms)

        // Settings with localStorage integration
        let settings = {
            winningScore: 10,
            paddleSpeed: 7,
            ballSpeed: 5,
            soundEnabled: true,
            theme: 'default',
            playerName: '',
            lastRoomCode: '',
            keyboardLayout: 'default'
        };

        // Color themes
        const themes = {
            default: {
                paddle1: '#ff6b35',
                paddle2: '#4ecdc4',
                ball: '#fff',
                background: '#000'
            },
            neon: {
                paddle1: '#ff00ff',
                paddle2: '#00ffff',
                ball: '#ffff00',
                background: '#001122'
            },
            retro: {
                paddle1: '#ff6b35',
                paddle2: '#4ecdc4',
                ball: '#ffff00',
                background: '#2a1810'
            },
            dark: {
                paddle1: '#666',
                paddle2: '#999',
                ball: '#ccc',
                background: '#000'
            }
        };

        // Initialize
        function init() {
            loadSettings();
            applyTheme();
            setupEventListeners();
            connect();
            render();
            updateShareUrl();
        }

        function loadSettings() {
            const saved = localStorage.getItem('pongSettings');
            if (saved) {
                try {
                    const loadedSettings = JSON.parse(saved);
                    settings = { ...settings, ...loadedSettings };
                } catch (e) {
                    console.log('Error loading settings:', e);
                }
            }

            // Load game statistics
            const stats = localStorage.getItem('pongStats');
            if (stats) {
                try {
                    const gameStats = JSON.parse(stats);
                    // Could display lifetime stats here if desired
                } catch (e) {
                    console.log('Error loading stats:', e);
                }
            }

            updateSettingsUI();
        }

        function saveSettings() {
            try {
                localStorage.setItem('pongSettings', JSON.stringify(settings));
            } catch (e) {
                console.log('Error saving settings:', e);
            }
        }

        function saveGameStats() {
            try {
                const currentStats = localStorage.getItem('pongStats');
                let stats = { gamesPlayed: 0, totalHits: 0, totalTime: 0 };

                if (currentStats) {
                    stats = JSON.parse(currentStats);
                }

                stats.gamesPlayed++;
                stats.totalHits += hitCount;
                stats.totalTime += Math.floor((Date.now() - gameStartTime) / 1000);

                localStorage.setItem('pongStats', JSON.stringify(stats));
            } catch (e) {
                console.log('Error saving game stats:', e);
            }
        }

        function updateSettingsUI() {
            winningScore.value = settings.winningScore;
            paddleSpeed.value = settings.paddleSpeed;
            ballSpeedSetting.value = settings.ballSpeed;
            soundToggle.classList.toggle('active', settings.soundEnabled);

            themeOptions.forEach(option => {
                option.classList.toggle('active', option.dataset.theme === settings.theme);
            });
        }

        function applyTheme() {
            const theme = themes[settings.theme];
            document.documentElement.style.setProperty('--player1-color', theme.paddle1);
            document.documentElement.style.setProperty('--player2-color', theme.paddle2);
            document.documentElement.style.setProperty('--ball-color', theme.ball);
        }

        function setupEventListeners() {
            // Game controls
            joinBtn.addEventListener('click', joinRoom);
            createBtn.addEventListener('click', createRoom);
            localBtn.addEventListener('click', startLocalGame);
            leaveBtn.addEventListener('click', leaveRoom);
            newGameBtn.addEventListener('click', newGame);
            copyRoomBtn.addEventListener('click', () => copyToClipboard(roomCode.textContent));
            copyUrlBtn.addEventListener('click', () => copyToClipboard(shareUrl.textContent));

            // Settings
            winningScore.addEventListener('change', (e) => {
                settings.winningScore = parseInt(e.target.value);
                saveSettings();
            });

            paddleSpeed.addEventListener('change', (e) => {
                settings.paddleSpeed = parseInt(e.target.value);
                saveSettings();
            });

            ballSpeedSetting.addEventListener('change', (e) => {
                settings.ballSpeed = parseInt(e.target.value);
                saveSettings();
            });

            soundToggle.addEventListener('click', () => {
                settings.soundEnabled = !settings.soundEnabled;
                soundToggle.classList.toggle('active', settings.soundEnabled);
                saveSettings();
            });

            themeOptions.forEach(option => {
                option.addEventListener('click', () => {
                    settings.theme = option.dataset.theme;
                    themeOptions.forEach(opt => opt.classList.remove('active'));
                    option.classList.add('active');
                    applyTheme();
                    saveSettings();
                });
            });

            // Room input
            roomInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') joinRoom();
            });

            roomInput.addEventListener('input', (e) => {
                e.target.value = e.target.value.replace(/[^0-9]/g, '').slice(0, 4);
                settings.lastRoomCode = e.target.value;
                saveSettings();
            });

            // Load last room code
            if (settings.lastRoomCode) {
                roomInput.value = settings.lastRoomCode;
            }

            // Keyboard controls
            document.addEventListener('keydown', handleKeyDown);
            document.addEventListener('keyup', handleKeyUp);

            // Mobile controls
            player1Up.addEventListener('touchstart', () => simulateKey('w', true));
            player1Up.addEventListener('touchend', () => simulateKey('w', false));
            player1Down.addEventListener('touchstart', () => simulateKey('s', true));
            player1Down.addEventListener('touchend', () => simulateKey('s', false));
            player2Up.addEventListener('touchstart', () => simulateKey('ArrowUp', true));
            player2Up.addEventListener('touchend', () => simulateKey('ArrowUp', false));
            player2Down.addEventListener('touchstart', () => simulateKey('ArrowDown', true));
            player2Down.addEventListener('touchend', () => simulateKey('ArrowDown', false));

            // Prevent context menu on mobile controls
            [player1Up, player1Down, player2Up, player2Down].forEach(btn => {
                btn.addEventListener('contextmenu', e => e.preventDefault());
            });
        }

        function simulateKey(key, pressed) {
            keys[key] = pressed;
            updateKeyIndicators();
        }

        function handleKeyDown(e) {
            keys[e.key] = true;
            updateKeyIndicators();

            if (['w', 'W', 's', 'S', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
                e.preventDefault();
            }
        }

        function handleKeyUp(e) {
            keys[e.key] = false;
            updateKeyIndicators();

            if (['w', 'W', 's', 'S', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
                e.preventDefault();
            }
        }

        function updateKeyIndicators() {
            keyIndicators.w.classList.toggle('active', keys['w'] || keys['W']);
            keyIndicators.s.classList.toggle('active', keys['s'] || keys['S']);
            keyIndicators.up.classList.toggle('active', keys['ArrowUp']);
            keyIndicators.down.classList.toggle('active', keys['ArrowDown']);
        }

        function updateLocalPaddles() {
            // Player 1 (left paddle) controls
            if (keys['w'] || keys['W']) {
                localGameState.paddle1.y = Math.max(0, localGameState.paddle1.y - settings.paddleSpeed);
            }
            if (keys['s'] || keys['S']) {
                localGameState.paddle1.y = Math.min(300, localGameState.paddle1.y + settings.paddleSpeed);
            }

            // Player 2 (right paddle) controls
            if (keys['ArrowUp']) {
                localGameState.paddle2.y = Math.max(0, localGameState.paddle2.y - settings.paddleSpeed);
            }
            if (keys['ArrowDown']) {
                localGameState.paddle2.y = Math.min(300, localGameState.paddle2.y + settings.paddleSpeed);
            }
        }

        function updateNetworkPaddles() {
            if (!playerId || !currentRoom || !gameState) return;

            const now = Date.now();
            if (now - lastNetworkPaddleUpdate < NETWORK_PADDLE_UPDATE_INTERVAL) {
                return; // Throttle updates to ~60fps
            }

            let newY = null;
            let currentY = null;

            if (playerId === 1) {
                currentY = gameState.paddle1.y;
                if (keys['w'] || keys['W']) {
                    newY = Math.max(0, currentY - settings.paddleSpeed);
                } else if (keys['s'] || keys['S']) {
                    newY = Math.min(300, currentY + settings.paddleSpeed);
                }
            } else if (playerId === 2) {
                currentY = gameState.paddle2.y;
                if (keys['ArrowUp']) {
                    newY = Math.max(0, currentY - settings.paddleSpeed);
                } else if (keys['ArrowDown']) {
                    newY = Math.min(300, currentY + settings.paddleSpeed);
                }
            }

            // Send paddle update if position changed
            if (newY !== null && newY !== currentY) {
                // Update local state immediately for responsiveness
                if (playerId === 1) {
                    gameState.paddle1.y = newY;
                } else if (playerId === 2) {
                    gameState.paddle2.y = newY;
                }

                sendMessage({
                    type: 'paddle_update',
                    player_id: playerId,
                    y: newY
                });

                lastNetworkPaddleUpdate = now;
            }
        }

        function handlePaddleMovement(key) {
            // Paddle movement is now handled continuously in the game loops
            // This function can be kept for any special key press handling if needed
        }

        function handleLocalPaddleMovement(key) {
            // This function is now deprecated - movement handled in updateLocalPaddles
        }

        function handleNetworkPaddleMovement(key) {
            // This function is now deprecated - movement handled in updateNetworkPaddles
        }

        // WebSocket connection
        function connect() {
            if (gameMode !== 'network') return;

            const wsPort = ''' + str(self.ws_port) + ''';
            ws = new WebSocket(`ws://${window.location.hostname}:${wsPort}`);

            ws.onopen = () => {
                console.log('Connected to server');
                updateStatus('Connected to server', 'connected');
            };

            ws.onmessage = (event) => {
                const message = JSON.parse(event.data);
                handleMessage(message);
            };

            ws.onclose = () => {
                console.log('Disconnected from server');
                updateStatus('Disconnected from server', 'disconnected');
                playerId = null;
                currentRoom = null;
                gameState = null;
                networkGameLoopStarted = false;
                showMenuControls();
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                updateStatus('Connection error', 'disconnected');
            };
        }

        function handleMessage(message) {
            switch (message.type) {
                case 'room_joined':
                    currentRoom = message.room_id;
                    playerId = message.player_id;
                    roomCode.textContent = currentRoom;
                    showRoomControls();
                    updateStatus(`Connected as Player ${playerId}`, 'connected');
                    newGameBtn.disabled = false;
                    break;

                case 'room_created':
                    currentRoom = message.room_id;
                    playerId = message.player_id;
                    roomCode.textContent = currentRoom;
                    showRoomControls();
                    updateStatus(`Room created! You are Player ${playerId}`, 'waiting');
                    break;

                case 'game_state':
                    gameState = message.state;
                    updateScoreDisplay();
                    updateStats();

                    // Start network game loop when game state is received
                    if (gameState && gameState.game_started && !networkGameLoopStarted) {
                        startNetworkGameLoop();
                        networkGameLoopStarted = true;
                        startGameTimer();
                        updateStatus('Game Started!', 'connected');
                    }
                    break;

                case 'game_end':
                    updateStatus(`Game Over! Player ${message.winner} wins!`, 'connected');
                    playSound('gameEnd');
                    stopGameTimer();
                    break;

                case 'error':
                    showToast(`Error: ${message.message}`, 'error');
                    break;
            }
        }

        function updateStatus(text, className) {
            gameStatus.querySelector('span:last-child').textContent = text;
            gameStatus.className = `status ${className}`;
        }

        function updateScoreDisplay() {
            if (gameMode === 'local' && localGameState) {
                player1Score.textContent = localGameState.score1;
                player2Score.textContent = localGameState.score2;
            } else if (gameState) {
                player1Score.textContent = gameState.score1;
                player2Score.textContent = gameState.score2;
            }
        }

        function updateStats() {
            const state = gameMode === 'local' ? localGameState : gameState;
            if (state) {
                ballSpeedDisplay.textContent = Math.round(state.ball?.speed || 5);
                playerCountDisplay.textContent = gameMode === 'local' ? '2' : (state.players_count || 0);
            }
        }

        function updateGameTimer() {
            if (gameStartTime) {
                const elapsed = Math.floor((Date.now() - gameStartTime) / 1000);
                const minutes = Math.floor(elapsed / 60);
                const seconds = elapsed % 60;
                gameTimeDisplay.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        }

        function startGameTimer() {
            gameStartTime = Date.now();
            gameTimeInterval = setInterval(updateGameTimer, 1000);
        }

        function stopGameTimer() {
            if (gameTimeInterval) {
                clearInterval(gameTimeInterval);
                gameTimeInterval = null;
            }
        }

        function showMenuControls() {
            menuControls.style.display = 'block';
            roomControls.style.display = 'none';
        }

        function showRoomControls() {
            menuControls.style.display = 'none';
            roomControls.style.display = 'block';
        }

        function sendMessage(message) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(message));
            }
        }

        // Game controls
        function joinRoom() {
            const room = roomInput.value.trim();
            if (room.length === 4) {
                gameMode = 'network';
                sendMessage({type: 'join_room', room_id: room});
                roomInput.value = '';
            } else {
                showToast('Please enter a 4-digit room code', 'error');
            }
        }

        function createRoom() {
            gameMode = 'network';
            const room = Math.floor(1000 + Math.random() * 9000).toString();
            settings.lastRoomCode = room;
            saveSettings();
            sendMessage({type: 'create_room', room_id: room});
        }

        function startLocalGame() {
            gameMode = 'local';
            playerId = null;
            currentRoom = null;

            // Initialize local game state
            localGameState = {
                ball: { x: 400, y: 200, dx: settings.ballSpeed, dy: 3, radius: 8, speed: settings.ballSpeed },
                paddle1: { x: 20, y: 150, width: 10, height: 100 },
                paddle2: { x: 770, y: 150, width: 10, height: 100 },
                score1: 0,
                score2: 0,
                gameStarted: true
            };

            updateStatus('Local 2-Player Game', 'connected');
            showMenuControls();
            newGameBtn.disabled = false;
            startGameTimer();
            startLocalGameLoop();
        }

        function startLocalGameLoop() {
            function gameLoop() {
                if (gameMode === 'local' && localGameState && localGameState.gameStarted) {
                    updateLocalGame();
                    requestAnimationFrame(gameLoop);
                }
            }
            gameLoop();
        }

        function startNetworkGameLoop() {
            function networkGameLoop() {
                if (gameMode === 'network' && playerId && currentRoom && gameState) {
                    updateNetworkPaddles();
                    requestAnimationFrame(networkGameLoop);
                }
            }
            networkGameLoop();
        }

        function updateLocalGame() {
            if (!localGameState.gameStarted) return;

            // Update paddle positions based on held keys
            updateLocalPaddles();

            // Update ball position
            localGameState.ball.x += localGameState.ball.dx;
            localGameState.ball.y += localGameState.ball.dy;

            // Ball collision with top/bottom walls
            if (localGameState.ball.y <= localGameState.ball.radius ||
                localGameState.ball.y >= 400 - localGameState.ball.radius) {
                localGameState.ball.dy = -localGameState.ball.dy;
                playSound('wallHit');
            }

            // Ball collision with paddles
            const ball = localGameState.ball;
            const p1 = localGameState.paddle1;
            const p2 = localGameState.paddle2;

            // Left paddle collision
            if (ball.x - ball.radius <= p1.x + p1.width &&
                ball.y >= p1.y && ball.y <= p1.y + p1.height &&
                ball.dx < 0) {
                ball.dx = -ball.dx;
                ball.x = p1.x + p1.width + ball.radius;
                ball.speed += 0.3;
                ball.dx = ball.speed;
                hitCount++;
                playSound('paddleHit');
            }

            // Right paddle collision
            if (ball.x + ball.radius >= p2.x &&
                ball.y >= p2.y && ball.y <= p2.y + p2.height &&
                ball.dx > 0) {
                ball.dx = -ball.dx;
                ball.x = p2.x - ball.radius;
                ball.speed += 0.3;
                ball.dx = -ball.speed;
                hitCount++;
                playSound('paddleHit');
            }

            // Scoring
            if (ball.x < 0) {
                localGameState.score2++;
                resetLocalBall();
                playSound('score');
            } else if (ball.x > 800) {
                localGameState.score1++;
                resetLocalBall();
                playSound('score');
            }

            // Check for game end
            if (localGameState.score1 >= settings.winningScore || localGameState.score2 >= settings.winningScore) {
                const winner = localGameState.score1 >= settings.winningScore ? 1 : 2;
                updateStatus(`Game Over! Player ${winner} wins!`, 'connected');
                localGameState.gameStarted = false;
                playSound('gameEnd');
                stopGameTimer();
                saveGameStats();
            }

            updateScoreDisplay();
            updateStats();
        }

        function resetLocalBall() {
            localGameState.ball.x = 400;
            localGameState.ball.y = 200;
            localGameState.ball.speed = settings.ballSpeed;
            localGameState.ball.dx = Math.random() > 0.5 ? settings.ballSpeed : -settings.ballSpeed;
            localGameState.ball.dy = (Math.random() - 0.5) * 6;
        }

        function leaveRoom() {
            if (gameMode === 'local') {
                gameMode = 'network';
                localGameState = null;
                stopGameTimer();
                hitCount = 0;
                updateStatus('Choose game mode', 'disconnected');
            } else {
                sendMessage({type: 'leave_room'});
                showMenuControls();
                currentRoom = null;
                playerId = null;
                gameState = null;
                networkGameLoopStarted = false;
                updateStatus('Left room', 'connected');
                stopGameTimer();
                hitCount = 0;
            }
        }

        function newGame() {
            if (gameMode === 'local') {
                localGameState.score1 = 0;
                localGameState.score2 = 0;
                localGameState.gameStarted = true;
                resetLocalBall();
                hitCount = 0;
                startGameTimer();
                startLocalGameLoop();
            } else {
                networkGameLoopStarted = false;
                sendMessage({type: 'new_game'});
                hitCount = 0;
            }
        }

        // Utility functions
        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                showToast('Copied to clipboard!', 'success');
            }).catch(() => {
                showToast('Failed to copy', 'error');
            });
        }

        function showToast(message, type = 'success') {
            toast.textContent = message;
            toast.className = `toast show ${type}`;
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        function updateShareUrl() {
            shareUrl.textContent = `http://${window.location.hostname}:${window.location.port}`;
        }

        function playSound(type) {
            if (!settings.soundEnabled) return;

            // Create simple audio feedback using Web Audio API
            try {
                const audioContext = new (window.AudioContext || window.webkitAudioContext)();
                const oscillator = audioContext.createOscillator();
                const gainNode = audioContext.createGain();

                oscillator.connect(gainNode);
                gainNode.connect(audioContext.destination);

                switch (type) {
                    case 'paddleHit':
                        oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
                        gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
                        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);
                        break;
                    case 'wallHit':
                        oscillator.frequency.setValueAtTime(400, audioContext.currentTime);
                        gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
                        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);
                        break;
                    case 'score':
                        oscillator.frequency.setValueAtTime(600, audioContext.currentTime);
                        oscillator.frequency.setValueAtTime(400, audioContext.currentTime + 0.1);
                        gainNode.gain.setValueAtTime(0.2, audioContext.currentTime);
                        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.3);
                        break;
                    case 'gameEnd':
                        oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
                        oscillator.frequency.setValueAtTime(600, audioContext.currentTime + 0.2);
                        oscillator.frequency.setValueAtTime(400, audioContext.currentTime + 0.4);
                        gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
                        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.6);
                        break;
                }

                oscillator.start(audioContext.currentTime);
                oscillator.stop(audioContext.currentTime + 0.6);
            } catch (e) {
                console.log('Audio not supported');
            }
        }

        // Rendering
        function render() {
            const state = gameMode === 'local' ? localGameState : gameState;
            const theme = themes[settings.theme];

            // Clear canvas
            ctx.fillStyle = theme.background;
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            if (!state) {
                requestAnimationFrame(render);
                return;
            }

            // Draw center line
            ctx.strokeStyle = theme.ball;
            ctx.setLineDash([5, 5]);
            ctx.beginPath();
            ctx.moveTo(canvas.width / 2, 0);
            ctx.lineTo(canvas.width / 2, canvas.height);
            ctx.stroke();
            ctx.setLineDash([]);

            // Draw paddles
            if (state.paddle1) {
                ctx.fillStyle = theme.paddle1;
                ctx.fillRect(state.paddle1.x, state.paddle1.y,
                           state.paddle1.width, state.paddle1.height);
            }
            if (state.paddle2) {
                ctx.fillStyle = theme.paddle2;
                ctx.fillRect(state.paddle2.x, state.paddle2.y,
                           state.paddle2.width, state.paddle2.height);
            }

            // Draw ball
            if (state.ball) {
                ctx.fillStyle = theme.ball;
                ctx.beginPath();
                ctx.arc(state.ball.x, state.ball.y, state.ball.radius, 0, Math.PI * 2);
                ctx.fill();

                // Ball trail effect
                ctx.shadowColor = theme.ball;
                ctx.shadowBlur = 10;
                ctx.fill();
                ctx.shadowBlur = 0;
            }

            // Draw player indicators
            if (gameMode === 'local') {
                // Both players highlighted in local mode
                ctx.fillStyle = '#00ff00';
                if (state.paddle1) {
                    ctx.fillRect(state.paddle1.x - 5, state.paddle1.y - 5,
                               state.paddle1.width + 10, 5);
                }
                if (state.paddle2) {
                    ctx.fillRect(state.paddle2.x - 5, state.paddle2.y - 5,
                               state.paddle2.width + 10, 5);
                }
            } else if (playerId === 1 && state.paddle1) {
                ctx.fillStyle = '#00ff00';
                ctx.fillRect(state.paddle1.x - 5, state.paddle1.y - 5,
                           state.paddle1.width + 10, 5);
            } else if (playerId === 2 && state.paddle2) {
                ctx.fillStyle = '#00ff00';
                ctx.fillRect(state.paddle2.x - 5, state.paddle2.y - 5,
                           state.paddle2.width + 10, 5);
            }

            requestAnimationFrame(render);
        }

        // Initialize the game
        init();
    </script>
</body>
</html>'''

    def start(self):
        """Start both HTTP and WebSocket servers"""
        print("🏓 Starting Cross-Device Pong Server")
        print("=" * 40)

        # Start WebSocket server
        self.ws_server = WebSocketServer(self.host, self.ws_port)
        ws_thread = threading.Thread(target=self.ws_server.start, daemon=True)
        ws_thread.start()

        # Start HTTP server
        def make_handler(*args, **kwargs):
            return PongHTTPHandler(*args, server=self, **kwargs)

        with socketserver.TCPServer((self.host, self.http_port), make_handler) as self.http_server:
            self.http_server.allow_reuse_address = True

            print(f"🌐 HTTP server running on http://{self.host}:{self.http_port}")
            print(f"🎮 WebSocket server running on ws://{self.host}:{self.ws_port}")
            print(f"📱 Open in browser: http://localhost:{self.http_port}")
            print("🔗 Share the URL with friends for cross-device play!")
            print("⏹️  Press Ctrl+C to stop the server")

            # Auto-open browser
            try:
                webbrowser.open(f"http://localhost:{self.http_port}")
            except:
                pass

            try:
                self.http_server.serve_forever()
            except KeyboardInterrupt:
                print("\n🛑 Server stopped by user")
            finally:
                if self.ws_server:
                    self.ws_server.stop()

def main():
    parser = argparse.ArgumentParser(description='Cross-Device Multiplayer Pong Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host address (default: 0.0.0.0)')
    parser.add_argument('--http-port', type=int, default=8000, help='HTTP port (default: 8000)')
    parser.add_argument('--ws-port', type=int, default=8080, help='WebSocket port (default: 8080)')

    args = parser.parse_args()

    server = PongServer(args.host, args.http_port, args.ws_port)
    server.start()

if __name__ == "__main__":
    main()
