#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
点淘1024数字合成游戏 - ADB自动化脚本
规则: 4x4棋盘, 相同数字合并后+1 (1+1=2, 2+2=3, n+n=n+1)
用法:
  python diantao_1024_auto.py --fast --games 3   # 快速测试
  python diantao_1024_auto.py --depth 4            # 完整AI模式
  python diantao_1024_auto.py --calibrate          # 校准棋盘位置
"""
import os
import sys
import time
import io
import subprocess
import numpy as np
import cv2
from cloud_ocr import ocr_server_url

# ============================================================
# 云端 PaddleOCR 配置
# ============================================================
OCR_SERVER_URL = ocr_server_url()

try:
    import requests as _requests
except ImportError:
    print("[错误] 需要 requests: pip install requests")
    sys.exit(1)

from PIL import Image

# ============================================================
# 配置 - 根据手机分辨率和游戏位置调整
# ============================================================
class Config:
    ADB_PATH = "adb"
    DEVICE_SERIAL = None  # None=自动选择, 或填序列号

    # 棋盘区域 (left, top, right, bottom) - 1264x2780分辨率
    # 运行 calibrate.py 获取精确值
    BOARD_AREA = (188, 1421, 1036, 2299)

    GRID_SIZE = 4
    CELL_MARGIN = 10

    @classmethod
    def load_calibration(cls, path="calibration_config.txt"):
        """从校准配置文件读取 BOARD_AREA 和 CELL_MARGIN"""
        if not os.path.exists(path):
            return  # 文件不存在则使用默认值
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            for line in content.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("BOARD_AREA"):
                    # 解析 (left, top, right, bottom)
                    nums = line.split("=", 1)[1].strip().strip("()")
                    parts = [int(x.strip()) for x in nums.split(",")]
                    if len(parts) == 4:
                        cls.BOARD_AREA = tuple(parts)
                elif line.startswith("CELL_MARGIN"):
                    parts = line.split("=", 1)[1].strip()
                    cls.CELL_MARGIN = int(parts)
            print(f"[校准] 读取 {path}: BOARD_AREA={cls.BOARD_AREA}, CELL_MARGIN={cls.CELL_MARGIN}")
        except Exception as e:
            print(f"[校准] 读取 {path} 失败: {e}，使用默认值")
    SWIPE_DURATION = 300   # ms
    ACTION_INTERVAL = 0.5   # s
    SCREENSHOT_INTERVAL = 0.8  # s
    SEARCH_DEPTH = 3
    MAX_GAMES = -1   # -1=无限


# ============================================================
# ADB控制
# ============================================================
class ADBController:
    def __init__(self, adb_path=None, device_serial=None):
        self.adb_path = adb_path or Config.ADB_PATH
        self.device_serial = device_serial or Config.DEVICE_SERIAL

    def _cmd(self, args):
        cmd = [self.adb_path]
        if self.device_serial:
            cmd += ["-s", self.device_serial]
        cmd += args
        return cmd

    def run(self, args, timeout=10):
        cmd = self._cmd(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError("ADB命令超时: " + " ".join(cmd))
        except FileNotFoundError:
            raise RuntimeError("找不到adb: " + self.adb_path)

    def get_screen_size(self):
        out, _ = self.run(["shell", "wm", "size"])
        for part in out.split(":"):
            part = part.strip()
            if "x" in part and part[0].isdigit():
                w, h = part.split("x")
                return int(w), int(h)
        raise RuntimeError("无法解析屏幕尺寸: " + out)

    def screenshot(self, remote="/sdcard/screen.png"):
        self.run(["shell", "screencap", "-p", remote])
        local = "screen_temp.png"
        self.run(["pull", remote, local])
        img = cv2.imread(local)
        if img is None:
            raise RuntimeError("截图读取失败")
        if os.path.exists(local):
            os.remove(local)
        return img

    def swipe(self, direction, duration=None):
        duration = duration or Config.SWIPE_DURATION
        l, t, r, b = Config.BOARD_AREA
        cx, cy = (l + r) // 2, (t + b) // 2   # 棋盘中心
        off = min(r - l, b - t) // 3           # 以棋盘尺寸算偏移
        moves = {
            "up":    (cx, cy + off, cx, cy - off),
            "down":  (cx, cy - off, cx, cy + off),
            "left":  (cx + off, cy, cx - off, cy),
            "right": (cx - off, cy, cx + off, cy),
        }
        if direction not in moves:
            raise ValueError("无效方向: " + direction)
        x1, y1, x2, y2 = moves[direction]
        self.run(["shell", "input", "swipe",
                  str(x1), str(y1), str(x2), str(y2), str(duration)])
        print("  [操作] " + direction.upper() + " (" + str(x1) + "," + str(y1) +
              " -> " + str(x2) + "," + str(y2) + ")")

    def tap(self, x, y):
        self.run(["shell", "input", "tap", str(x), str(y)])

    def get_device_info(self):
        out, _ = self.run(["shell", "getprop", "ro.product.model"])
        model = out or "Unknown"
        w, h = self.get_screen_size()
        return {"model": model, "resolution": str(w) + "x" + str(h)}


# ============================================================
# 棋盘识别 (云端 PaddleOCR)
# ============================================================
class BoardRecognizer:
    def __init__(self, board_area=None, grid_size=4):
        self.board_area = board_area or Config.BOARD_AREA
        self.grid_size = grid_size
        self.cell_positions = []
        self._calc_positions()

    def _calc_positions(self):
        l, t, r, b = self.board_area
        cw = (r - l) // self.grid_size
        ch = (b - t) // self.grid_size
        m = Config.CELL_MARGIN
        for row in range(self.grid_size):
            row_cells = []
            for col in range(self.grid_size):
                cx = l + col * cw + cw // 2
                cy = t + row * ch + ch // 2
                rect = (l + col * cw + m, t + row * ch + m,
                        l + (col + 1) * cw - m, t + (row + 1) * ch - m)
                row_cells.append({"center": (cx, cy), "rect": rect})
            self.cell_positions.append(row_cells)

    def crop_board(self, img):
        l, t, r, b = self.board_area
        return img[t:b, l:r].copy()

    def crop_cell(self, img, row, col):
        x1, y1, x2, y2 = self.cell_positions[row][col]["rect"]
        return img[y1:y2, x1:x2].copy()

    def _cloud_ocr(self, board_img_bgr):
        """将棋盘裁剪图发往云端 PaddleOCR, 返回 [(text, cx, cy, conf), ...]
        
        限制上传尺寸: 最大宽高 800px, 超过则等比缩小后上传,
        返回的坐标自动映射回原始坐标系.
        """
        h, w = board_img_bgr.shape[:2]
        MAX_UPLOAD_SIZE = 800
        MIN_UPLOAD_SIZE = 50
        scale = 1.0

        # 限制: 尺寸太小则直接返回空
        if w < MIN_UPLOAD_SIZE or h < MIN_UPLOAD_SIZE:
            print(f"  [OCR跳过] 裁剪图太小 ({w}x{h})")
            return []

        # 限制: 超过最大尺寸则等比缩小
        if w > MAX_UPLOAD_SIZE or h > MAX_UPLOAD_SIZE:
            scale = min(MAX_UPLOAD_SIZE / w, MAX_UPLOAD_SIZE / h)
            new_w, new_h = int(w * scale), int(h * scale)
            board_img_bgr = cv2.resize(board_img_bgr, (new_w, new_h),
                                       interpolation=cv2.INTER_AREA)
            h, w = new_h, new_w
            print(f"  [OCR缩放] {board_img_bgr.shape[1]}x{board_img_bgr.shape[0]} (scale={scale:.3f})")

        # 动态 quality: 大图压缩更多
        quality = 60 if max(w, h) > 600 else 70
        pil_img = Image.fromarray(cv2.cvtColor(board_img_bgr, cv2.COLOR_BGR2RGB))
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=quality)

        try:
            resp = _requests.post(
                OCR_SERVER_URL,
                files={"image": ("board.jpg", buf.getvalue(), "image/jpeg")},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [OCR错误] {e}")
            return []

        blocks = []
        for item in data.get("data", []):
            text = item.get("text", "").strip()
            score = item.get("score", 0.0)
            center = item.get("center", {})
            cx = center.get("x", 0)
            cy = center.get("y", 0)
            # 坐标映射回原始棋盘图坐标系
            if scale != 1.0:
                cx = int(cx / scale)
                cy = int(cy / scale)
            if text and score > 0.3:
                blocks.append((text, cx, cy, score))
        return blocks

    def recognize_board(self, screenshot):
        """截图→裁剪棋盘→云端OCR→填4x4矩阵"""
        board = np.zeros((self.grid_size, self.grid_size), dtype=int)
        board_img = self.crop_board(screenshot)
        bw, bh = board_img.shape[1], board_img.shape[0]
        cw, ch = bw // self.grid_size, bh // self.grid_size

        blocks = self._cloud_ocr(board_img)

        for text, cx, cy, conf in blocks:
            # 只取数字
            digits = ''.join(c for c in text if c.isdigit())
            if not digits:
                continue
            val = int(digits)

            # 坐标映射到格子
            col = int(cx // cw)
            row = int(cy // ch)
            if 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                board[row][col] = val

        return board

    def visualize(self, screenshot, board):
        """调试用: 在截图上标记识别结果"""
        vis = screenshot.copy()
        l, t, r, b = self.board_area
        cv2.rectangle(vis, (l, t), (r, b), (0, 255, 0), 2)
        for row in range(self.grid_size):
            for col in range(self.grid_size):
                cx, cy = self.cell_positions[row][col]["center"]
                num = board[row][col]
                color = (0, 0, 255) if num > 0 else (128, 128, 128)
                cv2.circle(vis, (cx, cy), 5, color, -1)
                if num > 0:
                    label = str(num)
                    cv2.putText(vis, label, (cx - 15, cy + 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                                (0, 0, 0), 3)
                    cv2.putText(vis, label, (cx - 15, cy + 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                                (255, 255, 255), 2)
        return vis


# ============================================================
# 游戏逻辑 (递进合并 n+n=n+1)
# ============================================================
class GameEngine:
    DIRECTIONS = ["up", "down", "left", "right"]

    @staticmethod
    def move_left(board):
        new = np.zeros_like(board)
        score = 0
        moved = False
        for row in range(board.shape[0]):
            tiles = [board[row][c] for c in range(board.shape[1]) if board[row][c] > 0]
            merged = []
            i = 0
            while i < len(tiles):
                if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
                    v = tiles[i] + 1
                    merged.append(v)
                    score += v
                    i += 2
                    moved = True
                else:
                    merged.append(tiles[i])
                    i += 1
            for c, v in enumerate(merged):
                new[row][c] = v
            if not np.array_equal(board[row], new[row]):
                moved = True
        return new, score, moved

    @staticmethod
    def move_right(board):
        f = np.fliplr(board)
        n, s, m = GameEngine.move_left(f)
        return np.fliplr(n), s, m

    @staticmethod
    def move_up(board):
        t = board.T.copy()
        n, s, m = GameEngine.move_left(t)
        return n.T, s, m

    @staticmethod
    def move_down(board):
        n, s, m = GameEngine.move_right(board.T)
        return n.T, s, m

    @classmethod
    def move(cls, board, direction):
        fn = {
            "left":  cls.move_left,
            "right": cls.move_right,
            "up":    cls.move_up,
            "down":  cls.move_down,
        }.get(direction)
        if fn is None:
            raise ValueError("无效方向: " + direction)
        return fn(board)

    @staticmethod
    def is_game_over(board):
        if np.any(board == 0):
            return False
        rows, cols = board.shape
        for r in range(rows):
            for c in range(cols):
                v = board[r][c]
                if c + 1 < cols and board[r][c + 1] == v:
                    return False
                if r + 1 < rows and board[r + 1][c] == v:
                    return False
        return True

    @staticmethod
    def empty_cells(board):
        return [(r, c) for r in range(board.shape[0])
                for c in range(board.shape[1]) if board[r][c] == 0]

    @staticmethod
    def max_value(board):
        return int(np.max(board))


# ============================================================
# AI决策 (启发式评估 + Expectimax搜索)
# ============================================================
class GameAI:
    def __init__(self, depth=3):
        self.depth = depth

    def evaluate(self, board):
        """启发式评分: 空格/最大值/单调性/平滑性/角落策略"""
        if np.all(board == 0):
            return 0.0

        score = 0.0
        empty = len(GameEngine.empty_cells(board))
        score += empty * 100.0

        mx = GameEngine.max_value(board)
        if mx > 0:
            score += np.log2(mx + 1) * 50.0

        # 单调性 (行+列)
        mono = 0.0
        rows, cols = board.shape
        for r in range(rows):
            for c in range(cols - 1):
                a, b = board[r][c], board[r][c + 1]
                if a > 0 and b > 0:
                    la, lb = np.log2(a + 1), np.log2(b + 1)
                    if la >= lb:
                        mono += la - lb
                    else:
                        mono -= (lb - la) * 0.5
        for c in range(cols):
            for r in range(rows - 1):
                a, b = board[r][c], board[r + 1][c]
                if a > 0 and b > 0:
                    la, lb = np.log2(a + 1), np.log2(b + 1)
                    if la >= lb:
                        mono += la - lb
                    else:
                        mono -= (lb - la) * 0.5
        score += mono * 30.0

        # 平滑性
        smooth = 0.0
        for r in range(rows):
            for c in range(cols):
                if board[r][c] == 0:
                    continue
                lv = np.log2(board[r][c] + 1)
                if c + 1 < cols and board[r][c + 1] > 0:
                    smooth -= abs(lv - np.log2(board[r][c + 1] + 1))
                if r + 1 < rows and board[r + 1][c] > 0:
                    smooth -= abs(lv - np.log2(board[r + 1][c] + 1))
        score += smooth * 20.0

        # 角落策略
        corners = [board[0][0], board[0][-1], board[-1][0], board[-1][-1]]
        if mx in corners:
            score += mx * 5.0

        return score

    def expectimax(self, board, depth, is_max):
        if depth == 0 or GameEngine.is_game_over(board):
            return self.evaluate(board)

        if is_max:
            best = float("-inf")
            for d in GameEngine.DIRECTIONS:
                nb, _, moved = GameEngine.move(board, d)
                if not moved:
                    continue
                best = max(best, self.expectimax(nb, depth - 1, False))
            return best if best != float("-inf") else self.evaluate(board)
        else:
            empty = GameEngine.empty_cells(board)
            if not empty:
                return self.expectimax(board, depth - 1, True)
            avg = 0.0
            for r, c in empty:
                for val, prob in [(1, 0.9), (2, 0.1)]:
                    tb = board.copy()
                    tb[r][c] = val
                    avg += prob * self.expectimax(tb, depth - 1, True)
            return avg / len(empty)

    def decide(self, board):
        best_dir, best_score = "up", float("-inf")
        for d in GameEngine.DIRECTIONS:
            nb, _, moved = GameEngine.move(board, d)
            if not moved:
                continue
            s = self.expectimax(nb, self.depth - 1, False)
            print("    [AI] " + d.upper() + " -> " + str(round(s, 1)))
            if s > best_score:
                best_score = s
                best_dir = d
        return best_dir

    def decide_fast(self, board):
        """仅用启发式评估, 不搜索"""
        best_dir, best_score = "up", float("-inf")
        for d in GameEngine.DIRECTIONS:
            nb, _, moved = GameEngine.move(board, d)
            if not moved:
                continue
            s = self.evaluate(nb)
            if s > best_score:
                best_score = s
                best_dir = d
        return best_dir


# ============================================================
# 主控
# ============================================================
class AutoPlayer:
    def __init__(self, debug=True):
        self.debug = debug
        self.adb = ADBController()
        self.recognizer = BoardRecognizer()
        self.ai = GameAI(depth=Config.SEARCH_DEPTH)
        self.game_count = 0
        self.total_moves = 0
        info = self.adb.get_device_info()
        print("[设备] " + info["model"] + " (" + info["resolution"] + ")")

    def calibrate(self):
        print("\n[校准] 截图...")
        shot = self.adb.screenshot()
        cv2.imwrite("calibration_original.png", shot)
        print("[校准] 已保存 calibration_original.png")
        board_img = self.recognizer.crop_board(shot)
        cv2.imwrite("calibration_board.png", board_img)
        print("[校准] 已保存 calibration_board.png (" +
              str(board_img.shape[1]) + "x" + str(board_img.shape[0]) + ")")
        print("\n请修改 Config.BOARD_AREA = " + str(Config.BOARD_AREA))

    def run_single_game(self, fast=False):
        moves, no_progress, last_max = 0, 0, 0
        print("\n" + "=" * 50)
        print("[游戏#" + str(self.game_count + 1) + "] 开始")
        print("=" * 50)

        while True:
            try:
                time.sleep(Config.SCREENSHOT_INTERVAL)
                shot = self.adb.screenshot()

                board = self.recognizer.recognize_board(shot)

                if self.debug and moves % 5 == 0:
                    vis = self.recognizer.visualize(shot, board)
                    cv2.imwrite("debug_g" + str(self.game_count) +
                                "_m" + str(moves) + ".png", vis)

                mx = GameEngine.max_value(board)
                empty = len(GameEngine.empty_cells(board))
                print("\n[步#" + str(moves) + "] 最大=" + str(mx) +
                      " 空格=" + str(empty))
                print(str(board))

                if GameEngine.is_game_over(board):
                    print("\n[结束] 最大值=" + str(mx) + " 步数=" + str(moves))
                    break

                if mx == last_max:
                    no_progress += 1
                    if no_progress > 15:
                        print("[警告] 无进展, 尝试换方向...")
                        self.adb.swipe(GameEngine.DIRECTIONS[moves % 4])
                        moves += 1
                        continue
                else:
                    last_max = mx
                    no_progress = 0

                direction = self.ai.decide_fast(board) if fast else self.ai.decide(board)
                self.adb.swipe(direction)
                moves += 1
                self.total_moves += 1
                time.sleep(Config.ACTION_INTERVAL)

            except KeyboardInterrupt:
                print("\n[中断] 用户停止")
                raise  # 向上传递, 让外层 run 直接退出, 跳过等待
            except Exception as e:
                print("[错误] " + str(e))
                time.sleep(1)

        self.game_count += 1
        return {"game_id": self.game_count, "moves": moves,
                "max_value": mx, "finished": True}

    def run(self, max_games=None, fast=False):
        max_games = max_games or Config.MAX_GAMES
        results = []
        try:
            while True:
                if max_games > 0 and self.game_count >= max_games:
                    break
                r = self.run_single_game(fast=fast)
                results.append(r)
                if r["finished"]:
                    print("\n[等待] 5秒后下一局...")
                    time.sleep(5)
        except KeyboardInterrupt:
            print("\n" + "=" * 50 + "\n[总结] 停止")
            self._summary(results)

    def _summary(self, results):
        if not results:
            print("无记录")
            return
        print("\n局数: " + str(len(results)))
        print("总步数: " + str(self.total_moves))
        max_vals = [r["max_value"] for r in results]
        print("平均步数: " + str(round(sum(r["moves"] for r in results) / len(results), 1)))
        print("历史最大: " + str(max(max_vals)))
        print("平均最大: " + str(round(sum(max_vals) / len(max_vals), 1)))


# ============================================================
# 入口
# ============================================================
def main():
    import argparse
    p = argparse.ArgumentParser(
        description="点淘1024 ADB自动化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例: python diantao_1024_auto.py --fast --games 3"
    )
    p.add_argument("--calibrate", "-c", action="store_true", help="校准棋盘")
    p.add_argument("--fast", "-f", action="store_true", help="快速模式")
    p.add_argument("--games", "-g", type=int, default=-1, help="最大局数")
    p.add_argument("--depth", "-d", type=int, default=3, help="AI搜索深度")
    p.add_argument("--no-debug", action="store_true", help="关闭调试")
    p.add_argument("--device", type=str, default=None, help="ADB设备序列号")
    args = p.parse_args()

    # 启动时加载校准配置
    Config.load_calibration()

    if args.depth:
        Config.SEARCH_DEPTH = args.depth
    if args.games:
        Config.MAX_GAMES = args.games
    if args.device:
        Config.DEVICE_SERIAL = args.device

    player = AutoPlayer(debug=not args.no_debug)
    if args.calibrate:
        player.calibrate()
    else:
        player.run(max_games=args.games, fast=args.fast)


if __name__ == "__main__":
    main()
