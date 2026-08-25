# Zenoh CANoe 式上位机 — 架构设计文档

> 日期：2026-08-25
> 项目：ESP32 + Zenoh-pico 测试平台
> 目标：开发一个 Zennoh-pico 上位机，实现上位机与 ESP32 的通信，
>       效果类似 CANoe，支持 ESP32 周期性报文接收和上位机手动/周期发送。

---

## 一、总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         PC 上位机（单进程自包含）                    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │               GUI 层（PyQt6）                                │  │
│  │  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  │  │
│  │  │ Trace    │  │ Send     │  │ Signal │  │ Graph/       │  │  │
│  │  │ 窗口     │  │ Panel    │  │ 面板   │  │ 波形图       │  │  │
│  │  └────┬─────┘  └────┬─────┘  └───┬────┘  └──────┬───────┘  │  │
│  └───────┼──────────────┼────────────┼───────────────┼──────────┘  │
│          │              │            │               │             │
│     ┌────▼──────────────▼────────────▼───────────────▼────────┐   │
│     │              内存队列 / 回调接口                          │   │
│     │  register_sub(topic, callback)  /  send(topic, data)    │   │
│     └────────────────────┬────────────────────────────────────┘   │
│                          │                                        │
│  ┌───────────────────────▼────────────────────────────────────┐   │
│  │          极简 Zenoh 路由层（~500 行）                       │   │
│  │                                                             │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │  UDP Server (监听 :7447)                             │   │   │
│  │  │  • 接受 ESP32 连接                                   │   │   │
│  │  │  • 会话管理 (INIT/OPEN/Keep-Alive)                   │   │   │
│  │  │  • Declare 解析 → 维护路由表                         │   │   │
│  │  │  • PUSH 转发 → 回调 GUI 层                           │   │   │
│  │  │  • 接收 GUI 层发送请求 → 构造报文 → UDP 发送         │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                          │ UDP
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  ESP32 (client 模式)                                             │
│                                                                  │
│  zenoh-pico v1.10.0                                              │
│  • client 模式，UDP 连接 PC 上位机 :7447                         │
│  • 周期发送 CAN 报文 (publisher → topic: can/0x100 等)          │
│  • 接收上位机指令 (subscriber → topic: can/0x200 等)            │
│  • 基本不变，仅调整 topic 与报文格式                             │
└──────────────────────────────────────────────────────────────────┘
```

### 核心设计原则

**上位机 = 路由层 + GUI 合体，不是 client，不是纯粹的 zenohd，而是两者的融合。**

- 路由层和 GUI 在同一个进程、同一个地址空间
- 路由层通过**内存队列/回调**直接与 GUI 通信，不走 TCP 环回
- 对 ESP32 来说，上位机看起来像一个 zenohd（接受 UDP 连接，完成协议握手）
- 对用户来说，上位机就是一个 GUI 应用

---

## 二、关键决策

| 决策项 | 结论 | 理由 |
|--------|------|------|
| GUI 框架 | **PyQt6** | 最成熟的 Python GUI，QTableView 做 Trace 窗口、pyqtgraph 做波形图都现成 |
| 路由层协议范围 | **仅 pub/sub** | 只实现会话管理 + Declare + PUSH 收发，~500 行，够用且稳妥 |
| ESP32 ↔ 上位机传输 | **UDP** | 低延迟，无连接状态，符合 CAN 总线语义 |
| ESP32 模式 | **client 不变** | 现有机 firmware 代码（config.h）基本不改 |
| 架构 | **自建 Zenoh 协议栈** | 上位机内嵌路由层，不需要额外启动 zenohd 进程 |
| Topic 策略 | **每个 CAN ID 独立 Topic** | 语义清晰，可按 ID 独立订阅/过滤 |

---

## 三、极简路由层 — 需要实现的 Zenoh 协议子集

### 3.1 协议层次

```
传输层 (Transport)     T_INIT / T_OPEN / T_CLOSE / T_KEEP_ALIVE / T_FRAME
网络层 (Network)       N_PUSH / N_DECLARE
会话层 (Session)       Z_PUT
```

### 3.2 各报文处理方式

| 报文 | 方向 | 处理方式 | 优先级 |
|------|------|----------|--------|
| **T_INIT** | ESP32 → 上位机 | 解析版本号，回 INIT-ACK | ✅ 必须 |
| **T_OPEN** | ESP32 → 上位机 | 解析 session 参数，回 OPEN-ACK | ✅ 必须 |
| **T_KEEP_ALIVE** | ESP32 → 上位机 | 回复 KEEP_ALIVE，维持会话 | ✅ 必须 |
| **T_CLOSE** | ESP32 → 上位机 | 关闭连接，清理路由表 | ✅ 可选 |
| **T_FRAME + N_DECLARE** | ESP32 → 上位机 | 解析 publisher/subscriber 声明，更新路由表 | ✅ 必须 |
| **T_FRAME + N_PUSH + Z_PUT** | ESP32 → 上位机 | 提取 topic + payload，回调 GUI 层 | ✅ 必须 |
| **T_FRAME + N_PUSH + Z_PUT** | 上位机 → ESP32 | 构造报文，通过 UDP 发送到 ESP32 | ✅ 必须 |
| **T_FRAGMENT** | — | 跳过（小数据不需要分片） | ❌ 跳过 |
| **N_INTEREST** | — | 跳过（优化用的，不实现也能工作） | ❌ 跳过 |
| **N_QUERY / N_REPLY** | — | 跳过（当前只用 pub/sub） | ❌ 跳过 |

### 3.3 路由层与 GUI 的接口定义

```python
# 路由层暴露给 GUI 的接口
class RouterInterface:
    # GUI 注册订阅回调（对应 CANoe 的 Trace 窗口接收）
    def register_subscriber(self, topic_filter: str, callback: Callable[[str, bytes], None])
    
    # GUI 发送数据（对应 CANoe 的 Send Panel / IG）
    def send(self, topic: str, data: bytes)
    
    # 连接管理
    def start(self, host: str = "0.0.0.0", port: int = 7447)
    def stop(self)
    def is_connected(self) -> bool
```

---

## 四、报文格式设计

### 4.1 Topic 命名规则

```
can/{can_id}
```

示例：

| Topic | 含义 |
|-------|------|
| `can/0x100` | CAN ID 0x100 的报文 |
| `can/0x200` | CAN ID 0x200 的报文 |
| `can/#` | 通配符，匹配所有 CAN 报文（上位机订阅用） |

### 4.2 Payload 格式（二进制）

```
┌────────┬──────────┬────────┬──────────────┬───────────┐
│ CAN ID │  DLC     │  Data  │  Timestamp   │  Flags    │
│ (4 B)  │  (1 B)   │ (0-8 B)│  (4 B, ms)   │  (1 B)    │
│ BE     │          │        │              │  RTR/TX   │
├────────┼──────────┼────────┼──────────────┼───────────┤
│ 0x100  │  0x08    │ xx...  │  0x0000A3E1  │  0x00     │
└────────┴──────────┴────────┴──────────────┴───────────┘
```

- **CAN ID**: 4 字节，大端
- **DLC**: 1 字节，数据长度 (0-8)
- **Data**: 0-8 字节，报文数据
- **Timestamp**: 4 字节，毫秒时间戳（大端）
- **Flags**: 1 字节，位 0 = RTR, 位 1 = TX（上位机发送标志）

### 4.3 信号定义文件（JSON）

```json
{
  "frames": [
    {
      "id": "0x100",
      "name": "VehicleStatus",
      "cycle_time_ms": 100,
      "signals": [
        {
          "name": "VehicleSpeed",
          "start_bit": 0,
          "length": 16,
          "scale": 0.01,
          "offset": 0,
          "unit": "km/h",
          "min": 0,
          "max": 300
        },
        {
          "name": "EngineRPM",
          "start_bit": 16,
          "length": 16,
          "scale": 0.125,
          "offset": 0,
          "unit": "rpm",
          "min": 0,
          "max": 8000
        }
      ]
    }
  ]
}
```

---

## 五、GUI 功能规划

### 5.1 主窗口布局

```
┌──────────────────────────────────────────────────────────┐
│ 菜单栏: 文件 | 连接 | 视图 | 工具 | 帮助                   │
├──────────────────────────────────────────────────────────┤
│ 工具栏: 连接/断开 | 开始/停止记录 | 清除 Trace | 滤波     │
├────────────────────┬─────────────────────────────────────┤
│                    │                                     │
│  左侧:             │  中央: Trace 窗口 (消息列表)          │
│  - 报文树          │  ┌─────────────────────────────────┐│
│  - 已定义报文列表   │  │ Time │ ID  │ DLC│ Data    │ Dir││
│  - 信号树          │  │ 0.100│ 0x100│ 8  │ 00 01..│ RX ││
│  - 启用/禁用发送   │  │ 0.200│ 0x200│ 4  │ AA BB..│ RX ││
│                    │  │ 0.250│ 0x100│ 8  │ 00 02..│ RX ││
│                    │  │ 0.300│ 0x100│ 8  │  ←手动发送│TX ││
│                    │  └─────────────────────────────────┘│
│                    │                                     │
│  底部: Send Panel (发送面板)                             │
│  ┌─────────────────────────────────────────────────────┐│
│  │ ID: 0x100  │ DLC: 8  │ Data: 00 01 02 03 ...  │[发送]││
│  │ 周期: 100ms │ ☑ 启用  │ 计数: 123  │ [停止]  │     ││
│  └─────────────────────────────────────────────────────┘│
│                                                          │
│  底部: Signal Panel (信号解析面板)                        │
│  ┌─────────────────────────────────────────────────────┐│
│  │ VehicleSpeed: 65 km/h  │ EngineRPM: 3200 rpm       ││
│  │ CoolantTemp: 90 °C     │ BatteryVolt: 12.8 V       ││
│  └─────────────────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────┤
│ 状态栏: 已连接 | 192.168.1.100:7447 | 100 msg/s         │
└──────────────────────────────────────────────────────────┘
```

### 5.2 功能清单

| 功能 | 说明 | 优先级 |
|------|------|--------|
| **Trace 窗口** | 实时显示所有收发报文，支持着色、过滤、暂停、导出 CSV | P0 |
| **Send Panel** | 类似 CANoe 的 IG (Interactive Generator)，手动单次发送 + 周期发送 | P0 |
| **周期发送器** | 可配置多个周期报文，设置 ID/DLC/Data/周期，可启用/禁用 | P0 |
| **连接管理** | 启动/停止路由层，显示连接状态 | P0 |
| **信号解析面板** | 根据 JSON 定义实时解析并显示信号值 | P1 |
| **波形图** | 实时绘制信号变化曲线（pyqtgraph），可缩放、拖拽 | P1 |
| **报文记录/回放** | 保存到 CSV/ASC 格式，可回放 | P2 |
| **滤波** | 按 CAN ID 范围、信号值、方向过滤显示 | P2 |
| **DBC 文件导入** | 支持标准 DBC 格式解析（使用 cantools 库） | P2 |
| **脚本自动化** | 内嵌 Python 脚本引擎，模拟复杂时序（类似 CAPL） | P3 |

---

## 六、数据流

### 6.1 ESP32 → 上位机（接收）

```
ESP32 周期发送 PUSH (topic="can/0x100", payload=二进制CAN帧)
  │
  ▼ UDP 收包
路由层解析 T_FRAME → N_PUSH → Z_PUT
提取 topic + payload (CAN ID + DLC + Data + Timestamp + Flags)
  │
  ▼ 匹配本地订阅表
路由层找到 GUI 注册了 "can/#"
  │
  ▼ 直接回调
GUI 的 Trace 窗口收到数据 → 显示
Signal Panel 根据 JSON 定义解析信号值 → 显示
Graph 窗口更新波形
```

### 6.2 上位机 → ESP32（发送）

```
用户在 Send Panel 填写 ID=0x100, Data=..., 点击发送
  │
  ▼ 直接调用
GUI 调用 routing_layer.send("can/0x100", payload)
  │
  ▼ 路由层
组装 Z_PUT → N_PUSH → T_FRAME 报文
  │
  ▼ UDP 发送
写入 ESP32 的 UDP socket
```

---

## 七、ESP32 端需要的变化

| 项 | 当前代码 | 新代码 |
|----|----------|--------|
| 模式 | client | client（不变） |
| 传输 | UDP | UDP（不变） |
| Topic | `zenoh/esp32/test` | `can/0x100`, `can/0x200` 等 |
| Payload 格式 | 文本字符串 | 二进制 CAN 帧 |
| 周期发送 | 一个 publisher，3s 一次 | 每个 CAN ID 一个 publisher，独立周期 |
| 接收 | 订阅 `zenoh/esp32/**` | 订阅 `can/0x200` 等需要接收的 topic |

---

## 八、开发路线图

```
Phase 1: 基础通信框架
  ├── 极简路由层核心（UDP Server + 会话管理 + 报文解析/组装）
  ├── ESP32 firmware 调整（topic 与 payload 格式）
  └── 端到端收发测试

Phase 2: GUI 核心
  ├── PyQt6 主窗口框架
  ├── Trace 窗口（QTableView + 实时更新）
  ├── Send Panel（手动 + 周期发送）
  └── 连接管理（启动/停止路由层）

Phase 3: 信号解析
  ├── JSON 信号定义文件格式
  ├── 信号解析引擎
  └── Signal Panel（信号值显示）

Phase 4: 进阶功能
  ├── 波形图（pyqtgraph）
  ├── 报文记录/回放
  ├── 滤波
  └── DBC 文件导入
```

---

## 九、参考

- [Zenoh 协议规范](https://zenoh.io/docs)
- [zenoh-pico GitHub](https://github.com/eclipse-zenoh/zenoh-pico)
- [zenoh-python PyPI](https://pypi.org/project/eclipse-zenoh/)
- [PyQt6 文档](https://www.riverbankcomputing.com/static/Docs/PyQt6/)
- [pyqtgraph 文档](https://pyqtgraph.readthedocs.io/)
- [cantools (DBC 解析)](https://github.com/eerimoq/cantools)