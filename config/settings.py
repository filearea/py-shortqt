# 交易配置参数

# 币安配置
BINANCE_WS_URL = "wss://fstream.binance.com/ws"
BINANCE_API_URL = "https://fapi.binance.com"
SYMBOL = "ETHUSDC"

# 交易参数
LEVERAGE_LIMIT = 100  # API 设置的杠杆上限
ACTUAL_LEVERAGE = 25  # 实际仓位计算用的杠杆（测试环境）
INITIAL_BALANCE = 35  # 初始保证金 (USDT) - 实盘从账户读取，此参数用于模拟
TAKE_PROFIT_POINTS = 1  # 止盈点数
STOP_LOSS_POINTS = 3  # 止损点数

# 实盘配置
USE_LIVE_TRADING = True  # True=实盘，False=模拟
TESTNET = False  # 是否使用测试网

# 订单超时配置
ORDER_TIMEOUT_SECONDS = 2.0  # 挂单超时时间（秒）

# 日志配置
LOG_BASE_DIR = "logs"
LOG_DEBUG_MODE = False  # True=记录详细调试日志（订单簿、WS 消息等）
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

# 显示配置
PRICE_PRECISION = 2  # 价格精度
QTY_PRECISION = 3  # 数量精度
ORDERBOOK_LEVELS = 10  # 订单簿显示档数

# TUI 配置
TUI_REFRESH_RATE = 10  # 界面刷新率 (Hz)
