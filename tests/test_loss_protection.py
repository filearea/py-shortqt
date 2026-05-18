# -*- coding: utf-8 -*-
"""
持仓超时保护（原浮亏保护）单元测试

测试场景：
1. 浮亏 → 保本止盈（撤原TP，挂开仓价限价单）
2. 浮盈 → 保本止损单（algo条件单，触发价=开仓价）
3. 浮盈+网格1~2之间 → 额外创建网格1止损单
4. 浮盈+超过网格2 → 只创建保本单
5. 浮盈+移动止损已有订单 → 只创建保本单
6. SHORT方向：浮盈保本止损
7. 未超时不触发
8. 平仓清理撤销所有止损单
9. 重复调用不重复触发
"""

import asyncio
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading.loss_protection import LossProtectionManager


def make_mock_trader(position=None, last_price=None, has_trailing_stop=False):
    """创建模拟的 LiveTrader 对象"""
    trader = MagicMock()
    trader.symbol = 'ETHUSDC'
    trader.position = position
    trader.last_price = last_price
    trader.log_manager = MagicMock()
    trader.log_manager.system.info = MagicMock()
    trader._add_action = MagicMock()
    trader.config_manager = None

    # 模拟 API
    trader.api = MagicMock()
    trader.api.place_order = MagicMock(return_value={'orderId': 100})
    trader.api.place_algo_order = MagicMock(return_value={'algoId': 200})
    trader.api.cancel_order = MagicMock()
    trader.api.cancel_algo_order = MagicMock()

    # 可选的 trailing stop manager
    if has_trailing_stop:
        ts_mgr = MagicMock()
        ts_mgr.enabled = True
        ts_mgr.active_orders = {}
        ts_mgr.grid_prices = [Decimal('202.000'), Decimal('204.000'), Decimal('206.000')]
        trader.trailing_stop_manager = ts_mgr

    return trader


def make_position(side='LONG', entry_price=None, size=None):
    return {
        'side': side,
        'entry_price': entry_price or Decimal('200.000'),
        'size': size or Decimal('0.1000'),
    }


class TestLossProtectionScenarios:
    """场景1-3 核心逻辑测试"""

    async def test_scenario1_loss_protection(self):
        """场景1：浮亏 → 撤销原止盈单，创建开仓价限价止盈单"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        # 开仓，5分钟前
        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'
        mgr.tp_order_id = 50

        # 当前价格低于开仓价 → 浮亏
        await mgr.check_and_protect(
            current_price=Decimal('199.500'),
            unrealized_pnl=Decimal('-0.500')
        )

        assert mgr.protected is True
        # 原止盈单被撤销
        trader.api.cancel_order.assert_called()
        # 创建了新的限价单
        trader.api.place_order.assert_called_once()
        call_args = trader.api.place_order.call_args
        assert call_args.kwargs['type'] == 'LIMIT'
        assert call_args.kwargs['price'] == '200.000'
        # 更新止盈单 ID
        assert mgr.tp_order_id == 100
        print('  [OK] 场景1：浮亏保本止盈')

    async def test_scenario2_profit_breakeven_stop(self):
        """场景2：浮盈 → 创建保本止损单（algo条件单）"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'
        mgr.tp_order_id = 50

        # 当前价格高于开仓价 → 浮盈
        await mgr.check_and_protect(
            current_price=Decimal('201.500'),
            unrealized_pnl=Decimal('1.500')
        )

        assert mgr.protected is True
        # 不撤销原止盈单
        trader.api.cancel_order.assert_not_called()
        # 创建 algo 止损单
        trader.api.place_algo_order.assert_called_once()
        call_kwargs = trader.api.place_algo_order.call_args.kwargs
        assert call_kwargs['type'] == 'STOP'
        assert call_kwargs['triggerPrice'] == '200.00'
        assert call_kwargs['priceMatch'] == 'QUEUE'
        assert call_kwargs['side'] == 'SELL'
        # 记录止损单 ID
        assert mgr._breakeven_stop_id == 200
        print('  [OK] 场景2：浮盈保本止损单')

    async def test_scenario3_extra_grid1_stop(self):
        """场景3：浮盈 + 移动止损启用 + 无活动订单 + 价格网格1~2之间 → 额外创建网格1止损单"""
        trader = make_mock_trader(
            position=make_position(),
            has_trailing_stop=True
        )
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        # 设置移动止损
        ts_mgr = trader.trailing_stop_manager
        ts_mgr.enabled = True
        ts_mgr.active_orders = {}
        ts_mgr.grid_prices = [Decimal('202.000'), Decimal('204.000'), Decimal('206.000')]

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        # 价格在网格1（202.00）和网格2（204.00）之间
        await mgr.check_and_protect(
            current_price=Decimal('203.000'),
            unrealized_pnl=Decimal('3.000')
        )

        assert mgr.protected is True
        # 应该调用了 2 次 place_algo_order：场景2 + 场景3
        assert trader.api.place_algo_order.call_count == 2

        # 第1次：保本止损单 @ 200.00
        call1 = trader.api.place_algo_order.call_args_list[0].kwargs
        assert call1['triggerPrice'] == '200.00'

        # 第2次：网格1止损单 @ 202.00
        call2 = trader.api.place_algo_order.call_args_list[1].kwargs
        assert call2['triggerPrice'] == '202.00'

        assert mgr._breakeven_stop_id == 200
        assert mgr._grid1_stop_id == 200
        print('  [OK] 场景3：网格1额外止损单')

    async def test_scenario3_not_in_range_above_grid2(self):
        """场景3不触发：价格超过网格2 → 只创建场景2保本单"""
        trader = make_mock_trader(
            position=make_position(),
            has_trailing_stop=True
        )
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        ts_mgr = trader.trailing_stop_manager
        ts_mgr.enabled = True
        ts_mgr.active_orders = {}
        ts_mgr.grid_prices = [Decimal('202.000'), Decimal('204.000')]

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        # 价格超过网格2
        await mgr.check_and_protect(
            current_price=Decimal('205.000'),
            unrealized_pnl=Decimal('5.000')
        )

        # 只调用一次（只有场景2）
        assert trader.api.place_algo_order.call_count == 1
        call = trader.api.place_algo_order.call_args.kwargs
        assert call['triggerPrice'] == '200.00'
        print('  [OK] 超过网格2：只创建保本单')

    async def test_scenario3_not_in_range_has_active_orders(self):
        """场景3不触发：移动止损已有活动订单 → 只创建场景2保本单"""
        trader = make_mock_trader(
            position=make_position(),
            has_trailing_stop=True
        )
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        ts_mgr = trader.trailing_stop_manager
        ts_mgr.enabled = True
        ts_mgr.active_orders = {1: 12345}  # 已有活动订单
        ts_mgr.grid_prices = [Decimal('202.000'), Decimal('204.000')]

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        await mgr.check_and_protect(
            current_price=Decimal('203.000'),
            unrealized_pnl=Decimal('3.000')
        )

        # 只调用一次（只有场景2）
        assert trader.api.place_algo_order.call_count == 1
        print('  [OK] 移动止损已有订单：只创建保本单')


class TestEdgeCases:
    """边界情况测试"""

    async def test_short_direction(self):
        """SHORT方向：超时浮盈 → 创建BUY方向止损单"""
        trader = make_mock_trader(position=make_position(side='SHORT'))
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'SHORT'

        # SHORT浮盈：当前价格低于开仓价
        await mgr.check_and_protect(
            current_price=Decimal('198.000'),
            unrealized_pnl=Decimal('2.000')
        )

        assert mgr.protected is True
        call = trader.api.place_algo_order.call_args.kwargs
        assert call['side'] == 'BUY'
        assert call['triggerPrice'] == '200.00'
        print('  [OK] SHORT方向止损单')

    async def test_short_scenario3(self):
        """SHORT方向场景3：价格在网格1~2之间 → 额外网格1止损单"""
        trader = make_mock_trader(
            position=make_position(side='SHORT'),
            has_trailing_stop=True
        )
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        ts_mgr = trader.trailing_stop_manager
        ts_mgr.enabled = True
        ts_mgr.active_orders = {}
        # SHORT网格：从上到下 [198.00, 196.00, 194.00]
        ts_mgr.grid_prices = [Decimal('198.000'), Decimal('196.000'), Decimal('194.000')]

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'SHORT'

        # 价格在网格1(198.00)~网格2(196.00)之间
        await mgr.check_and_protect(
            current_price=Decimal('197.000'),
            unrealized_pnl=Decimal('3.000')
        )

        assert trader.api.place_algo_order.call_count == 2
        call1 = trader.api.place_algo_order.call_args_list[0].kwargs
        assert call1['triggerPrice'] == '200.00'  # 保本价

        call2 = trader.api.place_algo_order.call_args_list[1].kwargs
        assert call2['triggerPrice'] == '198.00'  # 网格1价
        print('  [OK] SHORT方向场景3')

    async def test_not_triggered_before_timeout(self):
        """未超时：不触发任何操作"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        # 开仓后1分钟（未达5分钟）
        mgr.entry_time = datetime.now() - timedelta(minutes=1)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        await mgr.check_and_protect(
            current_price=Decimal('199.000'),
            unrealized_pnl=Decimal('-1.000')
        )

        assert mgr.protected is False
        trader.api.place_order.assert_not_called()
        trader.api.place_algo_order.assert_not_called()
        print('  [OK] 未超时不触发')

    async def test_no_double_trigger(self):
        """重复调用：protected=True 后不再触发"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        # 第一次触发
        await mgr.check_and_protect(
            current_price=Decimal('201.500'),
            unrealized_pnl=Decimal('1.500')
        )

        # 第二次调用（即使价格更低变成浮亏）
        await mgr.check_and_protect(
            current_price=Decimal('199.000'),
            unrealized_pnl=Decimal('-1.000')
        )

        # 只调用一次
        assert trader.api.place_algo_order.call_count == 1
        print('  [OK] 重复调用不重复触发')

    async def test_on_position_closed_cleanup(self):
        """平仓清理：撤销所有止损单，清空状态"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': True, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        # 先执行保护创建止损单
        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'
        mgr._breakeven_stop_id = 300
        mgr._grid1_stop_id = 400

        # 模拟平仓清理
        mgr.on_position_closed()

        # 两个止损单都被撤销
        assert trader.api.cancel_algo_order.call_count == 2
        assert mgr._breakeven_stop_id is None
        assert mgr._grid1_stop_id is None
        assert mgr.entry_price is None
        assert mgr.protected is False
        print('  [OK] 平仓清理撤销所有止损单')

    async def test_disabled_no_action(self):
        """未启用：不执行任何操作"""
        trader = make_mock_trader(position=make_position())
        config = {'enabled': False, 'trigger_minutes': 5}
        mgr = LossProtectionManager(trader, MagicMock(), config)

        mgr.entry_time = datetime.now() - timedelta(minutes=6)
        mgr.entry_price = Decimal('200.000')
        mgr.side = 'LONG'

        await mgr.check_and_protect(
            current_price=Decimal('199.000'),
            unrealized_pnl=Decimal('-1.000')
        )

        assert mgr.protected is False
        trader.api.place_order.assert_not_called()
        trader.api.place_algo_order.assert_not_called()
        print('  [OK] 未启用不触发')


async def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("持仓超时保护（原浮亏保护）单元测试")
    print("=" * 60)

    tests = [
        ("场景1：浮亏保本止盈", TestLossProtectionScenarios().test_scenario1_loss_protection),
        ("场景2：浮盈保本止损单", TestLossProtectionScenarios().test_scenario2_profit_breakeven_stop),
        ("场景3：网格1额外止损单", TestLossProtectionScenarios().test_scenario3_extra_grid1_stop),
        ("场景3不触发：超过网格2", TestLossProtectionScenarios().test_scenario3_not_in_range_above_grid2),
        ("场景3不触发：已有活动订单", TestLossProtectionScenarios().test_scenario3_not_in_range_has_active_orders),
        ("SHORT方向止损单", TestEdgeCases().test_short_direction),
        ("SHORT方向场景3", TestEdgeCases().test_short_scenario3),
        ("未超时不触发", TestEdgeCases().test_not_triggered_before_timeout),
        ("重复调用不重复触发", TestEdgeCases().test_no_double_trigger),
        ("平仓清理", TestEdgeCases().test_on_position_closed_cleanup),
        ("未启用不触发", TestEdgeCases().test_disabled_no_action),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f'  X {name}: {e}')
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"结果：{passed} 通过，{failed} 失败，共 {len(tests)} 个测试")
    print("=" * 60)


if __name__ == '__main__':
    asyncio.run(run_all_tests())
