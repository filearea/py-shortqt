# -*- coding: utf-8 -*-
"""
自动化测试脚本 - 模拟完整交易流程
"""

import asyncio
import sys
from pathlib import Path
from decimal import Decimal

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.trader import TradeState
from src.logger import TradeLogger

print("=" * 70)
print("自动化测试 - 完整交易流程")
print("=" * 70)

# 初始化日志
log_dir = project_root / "test_logs"
logger = TradeLogger(log_dir)

# 初始化交易状态
state = TradeState(
    logger, 75,
    Decimal("10.0"),
    Decimal("1.0"),
    Decimal("3.0")
)

# 模拟订单簿
def set_orderbook(bid: float, ask: float):
    """设置模拟订单簿"""
    state.orderbook = {
        'bids': [[Decimal(str(bid)), Decimal("10.0")]],
        'asks': [[Decimal(str(ask)), Decimal("10.0")]]
    }

async def test_1_place_long_order():
    """测试 1: 挂多单"""
    print("\n[测试 1] 挂多单...")
    set_orderbook(2180.00, 2180.10)
    state.last_price = Decimal("2180.05")
    
    # 模拟开多单
    position, size = state.can_open_position('LONG', Decimal("2180.00"))
    if position:
        state.place_pending_order(position)
        print(f"  ✓ 挂多单成功 @ 2180.00, size={size}")
        assert state.pending_order is not None
        assert state.pending_order['side'] == 'LONG'
        assert state.pending_order['price'] == Decimal("2180.00")
    else:
        print("  ✗ 挂单失败")
        return False
    return True

async def test_2_cancel_order():
    """测试 2: 撤单"""
    print("\n[测试 2] 撤单...")
    result = state.cancel_pending_order()
    if result:
        print("  ✓ 撤单成功")
        assert state.pending_order is None
    else:
        print("  ✗ 撤单失败")
        return False
    return True

async def test_3_place_short_order():
    """测试 3: 挂空单"""
    print("\n[测试 3] 挂空单...")
    set_orderbook(2180.00, 2180.10)
    state.last_price = Decimal("2180.05")
    
    position, size = state.can_open_position('SHORT', Decimal("2180.10"))
    if position:
        state.place_pending_order(position)
        print(f"  ✓ 挂空单成功 @ 2180.10, size={size}")
        assert state.pending_order is not None
        assert state.pending_order['side'] == 'SHORT'
    else:
        print("  ✗ 挂单失败")
        return False
    return True

async def test_4_order_fill():
    """测试 4: 空单成交"""
    print("\n[测试 4] 空单成交...")
    # 价格上涨，空单成交
    filled = state.check_pending_order_filled(Decimal("2180.10"))
    if filled:
        print("  ✓ 空单成交")
        assert state.position is not None
        assert state.position['side'] == 'SHORT'
        assert state.pending_order is None
    else:
        print("  ✗ 成交失败")
        return False
    return True

async def test_5_take_profit():
    """测试 5: 止盈"""
    print("\n[测试 5] 止盈...")
    # 价格下跌到止盈价
    result = state.check_tp_sl(Decimal("2179.10"))  # 开仓价 2180.10 - 1 = 2179.10
    if result and result['type'] == 'TP':
        print(f"  ✓ 止盈成功 PnL={result['pnl']:.2f}")
        assert state.position is None
        assert state.balance > Decimal("10.0")  # 应该盈利
    else:
        print("  ✗ 止盈失败")
        return False
    return True

async def test_6_place_long_and_early_close():
    """测试 6: 多单 + 提前平仓"""
    print("\n[测试 6] 多单 + 提前平仓...")
    
    # 挂多单
    set_orderbook(2170.00, 2170.10)
    state.last_price = Decimal("2170.05")
    position, size = state.can_open_position('LONG', Decimal("2170.00"))
    if position:
        state.place_pending_order(position)
        print(f"  ✓ 挂多单 @ 2170.00")
    
    # 成交
    filled = state.check_pending_order_filled(Decimal("2170.00"))
    if filled:
        print("  ✓ 多单成交")
    else:
        print("  ✗ 成交失败")
        return False
    
    # 提前平仓挂单
    state.close_position_early('LONG', Decimal("2170.10"))  # 挂卖单 @ 卖一价
    print(f"  ✓ 提前平仓挂单 @ 2170.10")
    
    # 价格上涨，平仓成交
    filled = state.check_pending_order_filled(Decimal("2170.10"))
    if filled:
        print(f"  ✓ 提前平仓成交")
        assert state.position is None
    else:
        print("  ✗ 平仓成交失败")
        return False
    
    return True

async def test_7_stop_loss():
    """测试 7: 止损"""
    print("\n[测试 7] 止损...")
    
    # 挂多单
    set_orderbook(2160.00, 2160.10)
    state.last_price = Decimal("2160.05")
    position, size = state.can_open_position('LONG', Decimal("2160.00"))
    if position:
        state.place_pending_order(position)
    
    # 成交
    state.check_pending_order_filled(Decimal("2160.00"))
    print(f"  ✓ 多单成交 @ 2160.00")
    
    # 价格下跌到止损价
    result = state.check_tp_sl(Decimal("2157.00"))  # 开仓价 2160.00 - 3 = 2157.00
    if result and result['type'] == 'SL':
        print(f"  ✓ 止损成功 PnL={result['pnl']:.2f}")
        assert state.position is None
        assert result['pnl'] < 0  # 应该亏损
    else:
        print("  ✗ 止损失败")
        return False
    
    return True

async def run_all_tests():
    """运行所有测试"""
    tests = [
        test_1_place_long_order,
        test_2_cancel_order,
        test_3_place_short_order,
        test_4_order_fill,
        test_5_take_profit,
        test_6_place_long_and_early_close,
        test_7_stop_loss,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            result = await test()
            if result:
                passed += 1
            else:
                failed += 1
                print(f"  ✗ 测试失败")
        except Exception as e:
            failed += 1
            print(f"  ✗ 测试异常：{e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print(f"测试结果：{passed} 通过，{failed} 失败")
    print("=" * 70)
    
    logger.close()
    
    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
