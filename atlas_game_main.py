import pygame
import serial
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum, auto

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 30
GAME_DURATION = 120
BLOCKS_TO_WIN = 10
WEIGHT_THRESHOLD = 5
COLLAPSE_THRESHOLD = 30

class GameState(Enum):
    PREGAME = auto()
    PLAYING = auto()
    POSTGAME = auto()

@dataclass
class Score:
    team_name: str
    blocks_removed: int
    time_remaining: float
    total_score: int
    timestamp: float = time.time()

class SerialHandler:
    def __init__(self, port='COM3', baudrate=57600):
        self.last_weight = 0
        try:
            self.serial = serial.Serial(port, baudrate)
            print(f"Successfully connected to {port}")
        except serial.SerialException as e:
            print(f"Serial connection failed: {e}")
            self.serial = None

    def read_weight(self) -> float:
        if not self.serial:
            return 0.0
        try:
            if self.serial.in_waiting:
                data = self.serial.readline().decode().strip()
                print(f"Raw data received: {data}")
                if "Load_cell output val:" in data:
                    value_part = data.split("Load_cell output val:")[1].strip()
                    return float(value_part)
                else:
                    return float(data)
        except (ValueError, serial.SerialException) as e:
            print(f"Error reading serial data: {e}")
        return self.last_weight

    def detect_block_removal(self) -> bool:
        current_weight = self.read_weight()
        print(f"Current weight: {current_weight}, Last weight: {self.last_weight}")
        if abs(current_weight - self.last_weight) > WEIGHT_THRESHOLD:
            print(f"Block removed! Weight change: {abs(current_weight - self.last_weight)}")
            self.last_weight = current_weight
            return True
        return False

    def detect_tower_collapse(self) -> bool:
        current_weight = self.read_weight()
        weight_change = current_weight - self.last_weight
        print(f"Weight change: {weight_change}, Threshold: {COLLAPSE_THRESHOLD}")
        if weight_change > COLLAPSE_THRESHOLD:
            print("TOWER COLLAPSE DETECTED!")
            self.last_weight = current_weight
            return True
        return False

    def cleanup(self):
        if self.serial:
            try:
                self.serial.close()
            except Exception as e:
                print(f"Error closing serial connection: {e}")

class LeaderboardManager:
    def __init__(self, db_path='leaderboard.db'):
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS leaderboard (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_name TEXT NOT NULL,
                    blocks_removed INTEGER NOT NULL,
                    time_remaining REAL NOT NULL,
                    total_score INTEGER NOT NULL,
                    timestamp REAL NOT NULL
                )
            ''')

    def add_score(self, score: Score):
        with self.conn:
            self.conn.execute('''
                INSERT INTO leaderboard 
                (team_name, blocks_removed, time_remaining, total_score, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (score.team_name, score.blocks_removed, score.time_remaining, 
                  score.total_score, score.timestamp))

    def get_top_scores(self, limit=5) -> List[Score]:
        cursor = self.conn.execute('''
            SELECT team_name, blocks_removed, time_remaining, total_score, timestamp
            FROM leaderboard
            ORDER BY total_score DESC
            LIMIT ?
        ''', (limit,))
        return [Score(*row) for row in cursor.fetchall()]

class AudioManager:
    def __init__(self):
        pygame.mixer.init()
        self.sounds = {
            'block_removed': pygame.mixer.Sound('block_removed.wav'),
            'success': pygame.mixer.Sound('success.mp3'),
            'failure': pygame.mixer.Sound('failure.mp3')
        }

    def play_sound(self, sound_name: str):
        if sound_name in self.sounds:
            self.sounds[sound_name].play()

class AtlasJengaGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Atlas Jenga Game")
        self.clock = pygame.time.Clock()
        self.serial_handler = SerialHandler()
        self.leaderboard = LeaderboardManager()
        self.audio = AudioManager()
        self.state = GameState.PREGAME
        self.reset_game()
        self.font_large = pygame.font.Font(None, 64)
        self.font_medium = pygame.font.Font(None, 48)
        self.font_small = pygame.font.Font(None, 32)

    def reset_game(self):
        self.blocks_removed = 0
        self.start_time = time.time()
        self.time_remaining = GAME_DURATION
        self.current_score = 0
        self.team_name = ""
        self.tower_collapsed = False
        if self.serial_handler:
            self.serial_handler.last_weight = self.serial_handler.read_weight()

    def calculate_score(self) -> int:
        speed_bonus = self.time_remaining * 10
        stability_bonus = 100 if self.blocks_removed >= BLOCKS_TO_WIN else 0
        return int(speed_bonus + stability_bonus + (self.blocks_removed * 100))

    def handle_events(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            elif event.type == pygame.KEYDOWN:
                if self.state == GameState.POSTGAME:
                    if event.key == pygame.K_RETURN:
                        score = Score(
                            team_name=self.team_name,
                            blocks_removed=self.blocks_removed,
                            time_remaining=self.time_remaining,
                            total_score=self.calculate_score()
                        )
                        self.leaderboard.add_score(score)
                        self.state = GameState.PREGAME
                    elif event.key == pygame.K_BACKSPACE:
                        self.team_name = self.team_name[:-1]
                    else:
                        self.team_name += event.unicode
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.state == GameState.PREGAME:
                    self.state = GameState.PLAYING
                    self.reset_game()
        return True

    def update(self):
        if self.state == GameState.PLAYING:
            self.time_remaining = GAME_DURATION - (time.time() - self.start_time)
            if self.serial_handler.detect_tower_collapse():
                print("Game over: Tower collapsed!")
                self.audio.play_sound('failure')
                self.state = GameState.POSTGAME
                return
            if self.serial_handler.detect_block_removal():
                self.blocks_removed += 1
                self.audio.play_sound('block_removed')
                if self.blocks_removed >= BLOCKS_TO_WIN:
                    self.audio.play_sound('success')
                    self.state = GameState.POSTGAME
            if self.time_remaining <= 0:
                self.audio.play_sound('failure')
                self.state = GameState.POSTGAME

    def draw(self):
        self.screen.fill((0, 0, 0))
        if self.state == GameState.PREGAME:
            self.draw_pregame()
        elif self.state == GameState.PLAYING:
            self.draw_game()
        elif self.state == GameState.POSTGAME:
            self.draw_postgame()
        pygame.display.flip()

    def draw_pregame(self):
        title = self.font_large.render("Atlas Jenga Game", True, (255, 255, 255))
        start_text = self.font_medium.render("Click to Start", True, (255, 255, 255))
        self.screen.blit(title, (WINDOW_WIDTH//2 - title.get_width()//2, WINDOW_HEIGHT//3))
        self.screen.blit(start_text, (WINDOW_WIDTH//2 - start_text.get_width()//2, 
                                    WINDOW_HEIGHT//2))
        leaderboard_title = self.font_medium.render("Leaderboard", True, (255, 255, 255))
        self.screen.blit(leaderboard_title, (WINDOW_WIDTH//2 - leaderboard_title.get_width()//2, 
                                        WINDOW_HEIGHT//2 + 80))
        top_scores = self.leaderboard.get_top_scores(5)
        for i, score in enumerate(top_scores):
            score_text = self.font_small.render(
                f"{i+1}. {score.team_name}: {score.total_score} pts ({score.blocks_removed} blocks, {int(score.time_remaining)}s left)",
                True, (200, 200, 200)
            )
            self.screen.blit(score_text, (WINDOW_WIDTH//2 - score_text.get_width()//2, 
                                    WINDOW_HEIGHT//2 + 130 + (i * 40)))

    def draw_game(self):
        time_text = self.font_medium.render(
            f"Time: {int(self.time_remaining)}s", True, 
            (255, 0, 0) if self.time_remaining <= 10 else (255, 255, 255)
        )
        self.screen.blit(time_text, (WINDOW_WIDTH//2 - time_text.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 60))
        blocks_text = self.font_medium.render(
            f"Blocks: {self.blocks_removed}/{BLOCKS_TO_WIN}", True, (255, 255, 255)
        )
        self.screen.blit(blocks_text, (WINDOW_WIDTH//2 - blocks_text.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 120))
        score_text = self.font_medium.render(
            f"Score: {self.calculate_score()}", True, (255, 255, 255)
        )
        self.screen.blit(score_text, (WINDOW_WIDTH//2 - score_text.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 170))

    def draw_postgame(self):
        if hasattr(self, 'tower_collapsed') and self.tower_collapsed:
            result_text = self.font_large.render(
                "Tower Collapsed!", True, (255, 0, 0)
            )
        else:
            result_text = self.font_large.render(
                "Victory!" if self.blocks_removed >= BLOCKS_TO_WIN else "Game Over",
                True, (0, 255, 0) if self.blocks_removed >= BLOCKS_TO_WIN else (255, 0, 0)
            )
        self.screen.blit(result_text, (WINDOW_WIDTH//2 - result_text.get_width()//2, 
                                    WINDOW_HEIGHT//4))
        final_score = self.font_medium.render(
            f"Final Score: {self.calculate_score()}", True, (255, 255, 255)
        )
        self.screen.blit(final_score, (WINDOW_WIDTH//2 - final_score.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 60))
        name_prompt = self.font_medium.render(
            f"Enter team name: {self.team_name}_", True, (255, 255, 255)
        )
        self.screen.blit(name_prompt, (WINDOW_WIDTH//2 - name_prompt.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 120))
        submit_text = self.font_small.render(
            "Press ENTER to submit score", True, (200, 200, 200)
        )
        self.screen.blit(submit_text, (WINDOW_WIDTH//2 - submit_text.get_width()//2, 
                                    WINDOW_HEIGHT//4 + 170))
        leaderboard_title = self.font_medium.render("Leaderboard", True, (255, 255, 255))
        self.screen.blit(leaderboard_title, (WINDOW_WIDTH//2 - leaderboard_title.get_width()//2, 
                                        WINDOW_HEIGHT//2 + 80))
        top_scores = self.leaderboard.get_top_scores(5)
        for i, score in enumerate(top_scores):
            score_text = self.font_small.render(
                f"{i+1}. {score.team_name}: {score.total_score} pts ({score.blocks_removed} blocks, {int(score.time_remaining)}s left)",
                True, (200, 200, 200)
            )
            self.screen.blit(score_text, (WINDOW_WIDTH//2 - score_text.get_width()//2, 
                                    WINDOW_HEIGHT//2 + 130 + (i * 40)))

    def run(self):
        running = True
        while running:
            running = self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        pygame.quit()

if __name__ == "__main__":
    game = AtlasJengaGame()
    game.run()