# -*- coding: utf-8 -*-
"""
启动测试脚本 - 简化版
测试程序能否正常启动并进入主界面
"""

import sys
import ast
from pathlib import Path

project_root = Path(__file__).parent.parent

CHECK = '[OK]'
CROSS = '[FAIL]'


def test_syntax():
    """测试 1: 语法检查"""
    print(f"{CHECK} 测试 1: 语法检查...")
    
    files_to_check = [
        project_root / "src" / "main_live.py",
        project_root / "src" / "trading" / "live.py",
        project_root / "src" / "config" / "manager.py",
        project_root / "src" / "config" / "validator.py",
        project_root / "src" / "ui" / "live_ui.py",
        project_root / "src" / "ui" / "settings_ui.py",
    ]
    
    all_ok = True
    for file_path in files_to_check:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source)
            print(f"  {CHECK} {file_path.name}")
        except SyntaxError as e:
            print(f"  {CROSS} {file_path.name}: {e}")
            all_ok = False
        except Exception as e:
            print(f"  {CROSS} {file_path.name}: {e}")
            all_ok = False
    
    return all_ok


def test_config_manager_params():
    """测试 2: get_stop_loss_params 参数签名"""
    print(f"{CHECK} 测试 2: get_stop_loss_params 参数签名...")
    
    file_path = project_root / "src" / "config" / "manager.py"
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    # 检查方法签名
    if "def get_stop_loss_params(self, symbol:" in source:
        print(f"  {CHECK} 参数签名正确 (包含 symbol 参数)")
        return True
    else:
        print(f"  {CROSS} 参数签名错误 (缺少 symbol 参数)")
        return False


def test_api_params_complete():
    """测试 3: API 参数完整性"""
    print(f"{CHECK} 测试 3: API 参数完整性...")
    
    file_path = project_root / "src" / "config" / "manager.py"
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    # 检查必需参数
    required = ["'symbol': symbol", "'side':", "'type':"]
    all_ok = True
    
    for param in required:
        if param in source:
            print(f"  {CHECK} 包含参数：{param.split(':')[0]}")
        else:
            print(f"  {CROSS} 缺少参数：{param.split(':')[0]}")
            all_ok = False
    
    return all_ok


def test_ui_settings_call():
    """测试 4: 设置界面调用参数"""
    print(f"{CHECK} 测试 4: 设置界面调用参数...")
    
    file_path = project_root / "src" / "ui" / "settings_ui.py"
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    # 检查调用是否包含 symbol 参数
    if "get_stop_loss_params('ETHUSDC'" in source or 'get_stop_loss_params("ETHUSDC"' in source:
        print(f"  {CHECK} 设置界面调用正确 (包含 symbol 参数)")
        return True
    else:
        print(f"  {CROSS} 设置界面调用错误 (缺少 symbol 参数)")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("py-shortqt v1.2.0 启动测试")
    print("=" * 60)
    print()
    
    tests = [
        ("语法检查", test_syntax),
        ("get_stop_loss_params 参数", test_config_manager_params),
        ("API 参数完整性", test_api_params_complete),
        ("设置界面调用", test_ui_settings_call),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"  {CROSS} 测试异常：{e}")
            results.append((name, False))
        print()
    
    # 汇总结果
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = f"{CHECK} 通过" if result else f"{CROSS} 失败"
        print(f"  {status}: {name}")
    
    print()
    print(f"总计：{passed}/{total} 通过")
    
    if passed == total:
        print("\n[OK] 所有测试通过！")
        return 0
    else:
        print(f"\n[CROSS] {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
