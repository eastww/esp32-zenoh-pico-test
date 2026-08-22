# extra_script.py (pre)
# 确保 VS Code 的 PlatformIO 扩展能找到 CMake、Ninja 和 LLVM-MinGW
# 这些工具在系统 PATH 中可能不存在，但终端里手动配置了

import os

Import('env')

# 添加工具路径到环境变量
tool_paths = [
    r'C:\Program Files\CMake\bin',
    os.path.join(os.environ['LOCALAPPDATA'], r'Microsoft\WinGet\Links'),
    r'C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Packages\MartinStorsjo.LLVM-MinGW.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\llvm-mingw-20260616-ucrt-x86_64\bin',
]

for p in tool_paths:
    if p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = p + os.pathsep + os.environ.get('PATH', '')

env.PrependENVPath('PATH', os.environ['PATH'])