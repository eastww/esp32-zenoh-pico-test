# Zenoh-Pico ESP32 测试项目

在 ESP32 上测试 [zenoh-pico](https://github.com/eclipse-zenoh/zenoh-pico) 协议的完整测试程序。

## 项目结构

```
zenoh/
├── platformio.ini      # PlatformIO 构建配置
├── include/
│   └── config.h        # WiFi / Zenoh 参数配置（需修改）
└── src/
    └── main.ino        # 主测试程序（带串口菜单）
```

## 功能（串口菜单）

| 按键 | 功能 |
|------|------|
| `1` | PUB — 每 3 秒发布一条测试消息 |
| `2` | SUB — 订阅 `zenoh/esp32/**` 主题 |
| `3` | QUERYABLE — 响应 `zenoh/esp32/query` 查询 |
| `4` | GET — 主动发送一条远程查询 |
| `5` | INFO — 查看系统与连接信息 |
| `O` | 打开 Zenoh 会话 |
| `C` | 关闭 Zenoh 会话 |
| `M` | 重新显示菜单 |

## 前置条件

1. 安装 [PlatformIO 扩展](https://marketplace.visualstudio.com/items?itemName=platformio.platformio-ide)（VS Code）
2. 安装 `espressif32` 平台（首次编译时自动下载，也可手动执行 `pio pkg install -p espressif32`）
3. 已连接 ESP32 开发板（USB）

## 使用方法

### 1. 修改配置

编辑 `include/config.h`：

```c
// WiFi 参数
#define WIFI_SSID       "你的WiFi名称"
#define WIFI_PASSWORD   "你的WiFi密码"

// 客户端模式：连接 Zenoh 路由器 (zenohd)
#define ZENOH_MODE      "client"
#define ZENOH_LOCATOR   "tcp/192.168.1.100:7447"   // 改为 zenohd 主机 IP
```

### 2. 编译并烧录

在 VS Code 中点击 PlatformIO 的 **Upload** 按钮（或运行）：

```bash
pio run -t upload
```

### 3. 监视串口

```bash
pio device monitor
```

看到菜单后，按 `1`~`5` 测试各功能。

## 与主机端联调

### 方式一：客户端模式（需要路由器）

在电脑上安装 [zenoh 路由器 zenohd](https://zenoh.io/docs/getting-started/installation/)（或使用 Docker）：

```bash
# Linux 主机（支持 UDP multicast 探测）
docker run --init --net host eclipse/zenoh:main
```

### 方式二：端到端测试示例

1. **ESP32 端 PUB**：按 `1` 开始发布 `zenoh/esp32/test`
2. **电脑端订阅**（从 zenoh-pico 源码编译，或安装 zenoh 工具链）：

```bash
# 订阅所有 zenoh/esp32/** 消息
./z_sub -e tcp/<esp32_所在网段主机IP>:7447 -k "zenoh/esp32/**"
# 或使用 zenoh 官方 CLI：
zenoh sub -e tcp/127.0.0.1:7447 -k "zenoh/esp32/**"
```

3. **电脑端发布 → ESP32 订阅**：ESP32 按 `2`，电脑端执行：

```bash
zenoh pub -e tcp/127.0.0.1:7447 -k "zenoh/esp32/test" -v "Hello from PC!"
```

ESP32 串口应显示：

```
<< [SUB] Received (zenoh/esp32/test, Hello from PC!)
```

4. **查询测试**：ESP32 按 `3` 开启 QUERYABLE，电脑端执行：

```bash
zenoh get -e tcp/127.0.0.1:7447 -k "zenoh/esp32/query"
```

ESP32 会回复包含运行时间和空闲堆内存的响应。

## 常见问题

### 1. 打开会话失败（`Unable to open session!` / `FAILED`）

- 确认 `zenohd` 已启动且防火墙放行 7447 端口
- 确认 `ZENOH_LOCATOR` 中 IP 正确（电脑端用 `ipconfig` 查看）
- ESP32 与电脑必须在同一局域网

### 2. 内存不足 / 编译报错

zenoh-pico 库自带的 `extra_script.py` 已使用嵌入式友好配置
（`FRAG_MAX_SIZE=4096`, `BATCH_UNICAST_SIZE=2048`）。如果仍内存不足，可在
`platformio.ini` 中进一步调低：

```ini
board_build.cmake_extra_args =
    -DFRAG_MAX_SIZE=2048
    -DBATCH_UNICAST_SIZE=1024
    -DBATCH_MULTICAST_SIZE=1024
```

### 3. 首次编译很慢

首次会下载 `espressif32` 平台与工具链，请耐心等待；之后编译会很快。

### 4. 板子型号不同

修改 `platformio.ini` 中的 `board` 字段：

| 板子 | board |
|------|-------|
| 通用 ESP32 DevKit | `esp32dev` |
| AZ-Delivery DevKit V4 | `az-delivery-devkit-v4` |
| ESP32-WROVER Kit | `esp32-wrover-kit` |
| ESP32-S3 | `esp32-s3-devkitc-1` |
| ESP32-C3 | `esp32-c3-devkitm-1` |

## 参考

- [zenoh-pico 官方仓库](https://github.com/eclipse-zenoh/zenoh-pico)
- [zenoh 官方文档](https://zenoh.io/docs/)
- [zenoh 安装指南](https://zenoh.io/docs/getting-started/installation/)