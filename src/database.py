"""
数据库操作模块

管理 SQLite 数据库，存储和查询游戏状态
"""

import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from collections import Counter
import logging

from .mjlog_parser import GameState, Discard

logger = logging.getLogger(__name__)


class Database:
    """数据库管理类"""
    
    def __init__(self, db_path: str):
        """
        初始化数据库连接
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self.cursor = None
    
    def connect(self):
        """建立数据库连接"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.cursor = self.conn.cursor()
        logger.info(f"已连接到数据库: {self.db_path}")
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")
    
    def create_tables(self):
        """创建数据库表"""
        
        # 游戏状态表
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id TEXT NOT NULL,
            round_num INTEGER NOT NULL,
            honba INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER NOT NULL,
            turn INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            is_tsumogiri INTEGER NOT NULL,
            hand_tiles TEXT NOT NULL,
            visible_tiles TEXT NOT NULL,
            dora_indicators TEXT NOT NULL,
            normalized_pattern TEXT,
            UNIQUE(log_id, round_num, honba, player_id, turn),
            FOREIGN KEY(log_id) REFERENCES logs(id)
        )
        """)
        
        # 创建索引
        self.cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_log_player 
        ON game_states(log_id, player_id)
        """)
        
        self.cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_normalized_pattern 
        ON game_states(normalized_pattern)
        """)
        
        self.cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_turn 
        ON game_states(turn)
        """)
        
        # 可见牌统计表
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS visible_tile_stats (
            state_id INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            visible_count INTEGER NOT NULL,
            FOREIGN KEY(state_id) REFERENCES game_states(id),
            PRIMARY KEY(state_id, tile)
        )
        """)
        
        self.cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tile_count 
        ON visible_tile_stats(tile, visible_count)
        """)
        
        self.conn.commit()
        logger.info("数据库表创建完成")
    
    def insert_game_state(
        self,
        log_id: str,
        round_num: int,
        honba: int,
        player_id: int,
        turn: int,
        tile: int,
        is_tsumogiri: bool,
        hand_tiles: set,
        visible_tiles: Counter,
        dora_indicators: List[int],
        normalized_pattern: Optional[str] = None
    ) -> int:
        """
        插入游戏状态记录
        
        Args:
            log_id: 对局ID
            round_num: 局数
            honba: 本场数（连庄次数）
            player_id: 玩家ID
            turn: 巡目
            tile: 打出的牌
            is_tsumogiri: 是否摸切
            hand_tiles: 手牌集合
            visible_tiles: 可见牌统计
            dora_indicators: 宝牌指示牌
            normalized_pattern: 标准化模式
            
        Returns:
            插入记录的ID
        """
        # 将集合和Counter转换为JSON
        hand_tiles_json = json.dumps(list(hand_tiles))
        visible_tiles_json = json.dumps(dict(visible_tiles))
        dora_indicators_json = json.dumps(dora_indicators)
        
        self.cursor.execute("""
        INSERT OR IGNORE INTO game_states (
            log_id, round_num, honba, player_id, turn, tile, is_tsumogiri,
            hand_tiles, visible_tiles, dora_indicators, normalized_pattern
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, round_num, honba, player_id, turn, tile, int(is_tsumogiri),
            hand_tiles_json, visible_tiles_json, dora_indicators_json, normalized_pattern
        ))
        
        state_id = self.cursor.lastrowid
        
        # 仅当成功插入新行时（state_id > 0），才插入可见牌统计
        if state_id > 0:
            for tile_code, count in visible_tiles.items():
                self.cursor.execute("""
                INSERT INTO visible_tile_stats (state_id, tile, visible_count)
                VALUES (?, ?, ?)
                """, (state_id, tile_code, count))
        
        return state_id
    
    def query_by_pattern(
        self,
        normalized_pattern: str,
        turn_range: Optional[Tuple[int, int]] = None
    ) -> List[Dict]:
        """
        根据标准化模式查询游戏状态
        
        Args:
            normalized_pattern: 标准化的舍牌模式
            turn_range: 巡目范围（最小, 最大）
            
        Returns:
            匹配的游戏状态列表
        """
        query = """
        SELECT id, log_id, round_num, player_id, turn, tile, is_tsumogiri,
               hand_tiles, visible_tiles, dora_indicators, normalized_pattern
        FROM game_states
        WHERE normalized_pattern = ?
        """
        
        params = [normalized_pattern]
        
        if turn_range:
            query += " AND turn >= ? AND turn <= ?"
            params.extend(turn_range)
        
        self.cursor.execute(query, params)
        
        results = []
        for row in self.cursor.fetchall():
            results.append({
                "id": row[0],
                "log_id": row[1],
                "round_num": row[2],
                "player_id": row[3],
                "turn": row[4],
                "tile": row[5],
                "is_tsumogiri": bool(row[6]),
                "hand_tiles": set(json.loads(row[7])),
                "visible_tiles": Counter(json.loads(row[8])),
                "dora_indicators": json.loads(row[9]),
                "normalized_pattern": row[10]
            })
        
        return results
    
    def query_with_visible_constraints(
        self,
        normalized_pattern: str,
        visible_constraints: Dict[int, Tuple[int, int]],
        turn_range: Optional[Tuple[int, int]] = None
    ) -> List[Dict]:
        """
        根据模式和可见枚数约束查询
        
        Args:
            normalized_pattern: 标准化模式
            visible_constraints: 可见枚数约束 {牌编码: (最小, 最大)}
            turn_range: 巡目范围
            
        Returns:
            匹配的游戏状态列表
        """
        # 先按模式查询
        base_results = self.query_by_pattern(normalized_pattern, turn_range)
        
        # 再按可见枚数过滤
        filtered_results = []
        for state in base_results:
            visible_tiles = state["visible_tiles"]
            match = True
            
            for tile, (min_count, max_count) in visible_constraints.items():
                count = visible_tiles.get(tile, 0)
                if not (min_count <= count <= max_count):
                    match = False
                    break
            
            if match:
                filtered_results.append(state)
        
        return filtered_results
    
    def get_database_stats(self) -> Dict:
        """
        获取数据库统计信息
        
        Returns:
            统计信息字典
        """
        stats = {}
        
        # 总游戏状态数
        self.cursor.execute("SELECT COUNT(*) FROM game_states")
        stats["total_states"] = self.cursor.fetchone()[0]
        
        # 唯一对局数
        self.cursor.execute("SELECT COUNT(DISTINCT log_id) FROM game_states")
        stats["unique_games"] = self.cursor.fetchone()[0]
        
        # 数据库文件大小
        if self.db_path.exists():
            stats["db_size_mb"] = self.db_path.stat().st_size / (1024 * 1024)
        else:
            stats["db_size_mb"] = 0
        
        return stats
    
    def batch_insert_game_states(self, game_states: List[GameState], log_id: str, round_num: int):
        """
        批量插入游戏状态
        
        Args:
            game_states: 游戏状态列表
            log_id: 对局ID
            round_num: 局数
        """
        for state in game_states:
            for i, discard in enumerate(state.discards):
                # 计算该巡的标准化模式（累计到该巡的所有手切）
                # 使用 simple_normalizer.normalize_discard_pattern
                normalized_pattern = None
                
                self.insert_game_state(
                    log_id=log_id,
                    round_num=round_num,
                    player_id=state.player_id,
                    turn=discard.turn,
                    tile=discard.tile,
                    is_tsumogiri=discard.is_tsumogiri,
                    hand_tiles=state.hand_tiles,
                    visible_tiles=state.visible_tiles,
                    dora_indicators=state.dora_indicators,
                    normalized_pattern=normalized_pattern
                )
        
        self.conn.commit()
    
    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()


def create_database(db_path: str):
    """
    便捷函数：创建数据库和表
    
    Args:
        db_path: 数据库路径
    """
    with Database(db_path) as db:
        db.create_tables()


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    db_path = "../data/test.db"
    
    # 创建数据库
    create_database(db_path)
    
    # 测试插入和查询
    with Database(db_path) as db:
        # 插入测试数据
        state_id = db.insert_game_state(
            log_id="test_log_001",
            round_num=1,
            player_id=0,
            turn=1,
            tile=20,  # 3s
            is_tsumogiri=False,
            hand_tiles={0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
            visible_tiles=Counter({20: 1}),
            dora_indicators=[23],
            normalized_pattern="1s-3s"
        )
        
        db.conn.commit()
        print(f"插入记录ID: {state_id}")
        
        # 查询统计信息
        stats = db.get_database_stats()
        print(f"数据库统计: {stats}")
