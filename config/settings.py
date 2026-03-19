# 交易配置参数

# 币安配置
BINANCE_WS_URL = "wss://fstream.binance.com/ws"
SYMBOL = "ethusdc"

# 交易参数
LEVERAGE = 75  # 杠杆倍数
INITIAL_BALANCE = 10  # 初始保证金 (USDT)
TAKE_PROFIT_POINTS = 1  # 止盈点数
STOP_LOSS_POINTS = 3  # 止损点数

# 日志配置
LOG_BASE_DIR = "logs"

# 显示配置
PRICE_PRECISION = 2  # 价格精度
QTY_PRECISION = 3  # 数量精度
ORDERBOOK_LEVELS = 10  # 订单簿显示档数

# TUI 配置
TUI_REFRESH_RATE = 10  # 界面刷新率 (Hz)
