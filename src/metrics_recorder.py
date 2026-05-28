# -*- coding: utf-8 -*-
"""指标数据记录器 - v1.4.2"""
import json
import time
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from typing import List, Dict

DATA_DIR = Path(__file__).parent.parent / "data"
METRICS_DIR = DATA_DIR / "metrics"

class MetricsRecorder:
    def __init__(self, symbol: str = "ETHUSDC", save_interval: int = 30):
        self.symbol = symbol
        self.save_interval = save_interval
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.metrics_file = METRICS_DIR / f"{symbol}_{self.today}.jsonl"
        self._metrics_cache: List[Dict] = []
        self._last_save = 0.0
        self._records_saved = 0
    
    def save_snapshot(self, indicators_manager, trader=None):
        current_time = time.time()
        if current_time - self._last_save < self.save_interval:
            return
        try:
            # v1.5.0 修复：使用 get_snapshot() 替代 get_metrics()
            snapshot_data = indicators_manager.get_snapshot()
            
            # snapshot_data 格式：{'volatility': {...}, 'liquidity': {...}, 'score': {...}}
            # 提取 score 数据
            score_data = snapshot_data.get('score', {})
            
            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'symbol': self.symbol,
                'metrics': {
                    'volatility': snapshot_data.get('volatility', {}),
                    'liquidity': snapshot_data.get('liquidity', {})
                },
                'score': score_data.get('quality_score', 0),
                'signal': score_data.get('recommendation', 'unknown'),
                'signal_emoji': score_data.get('signal_emoji', '')
            }
            
            if trader:
                position = trader.position
                if position:
                    snapshot['position'] = {
                        'side': position.get('side', 'NONE'),
                        'size': str(position.get('size', 0)),
                        'entry_price': str(position.get('entry_price', 0)),
                        'unrealized_pnl': str(position.get('unrealized_pnl', 0))
                    }
            
            self._metrics_cache.append(snapshot)
            self._last_save = current_time
            self._records_saved += 1
            if len(self._metrics_cache) >= 50:
                self.flush()
        except Exception as e:
            pass
    
    def flush(self):
        if not self._metrics_cache:
            return
        with open(self.metrics_file, 'a', encoding='utf-8') as f:
            for snapshot in self._metrics_cache:
                f.write(json.dumps(snapshot, ensure_ascii=False, default=str) + '\n')
        self._records_saved += len(self._metrics_cache)
        self._metrics_cache = []
    
    def get_stats(self) -> Dict[str, int]:
        return {'records_saved': self._records_saved + len(self._metrics_cache)}
    
    def __del__(self):
        try:
            self.flush()
        except:
            pass
