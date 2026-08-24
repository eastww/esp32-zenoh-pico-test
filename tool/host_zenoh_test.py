#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zenoh Host Test Tool — ESP32 联调 + 报文抓取 + 自动管理 zenohd

Usage:
    # --- 管理 zenohd ---
    python host_zenoh_test.py start              # 后台启动 zenohd
    python host_zenoh_test.py start --trace      # 前台启动 + 打印报文日志
    python host_zenoh_test.py stop               # 停止 zenohd
    python host_zenoh_test.py status             # 查看状态

    # --- 报文抓取（TCP 代理模式） ---
    python host_zenoh_test.py proxy              # 启动代理解析报文
    python host_zenoh_test.py proxy --hex-only   # 仅十六进制 dump

    # --- 普通 Zenoh 通信 ---
    python host_zenoh_test.py sub                # 订阅 ESP32
    python host_zenoh_test.py pub "Hello"        # 向 ESP32 发布
    python host_zenoh_test.py get                # 查询 ESP32
"""
import argparse
import atexit
import os
import socket
import struct
import subprocess
import sys
import threading
import time

# =============================================================================
# 配置
# =============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ZENOHD_DIR = os.path.join(SCRIPT_DIR, "zenoh")
ZENOHD_EXE = os.path.join(ZENOHD_DIR, "zenohd.exe")
PID_FILE = os.path.join(SCRIPT_DIR, ".zenohd.pid")

# 端口配置
DEFAULT_ROUTER_PORT = 7447  # start 命令：zenohd 直接监听（Python 客户端直连）
ZENOHD_INT_PORT = 7448      # proxy 模式：zenohd 内部端口
PROXY_EXT_PORT = 7447       # proxy 模式：代理对外端口（ESP32 连接）

# =============================================================================
# Zenoh 协议消息类型定义（基于 zenoh-pico 1.10 源码）
# =============================================================================
# 传输层消息 (Transport) — 低 4 位 mid
TRANSPORT_MSG = {
    0x00: "T_OAM",
    0x01: "T_INIT",
    0x02: "T_OPEN",
    0x03: "T_CLOSE",
    0x04: "T_KEEP_ALIVE",
    0x05: "T_FRAME",
    0x06: "T_FRAGMENT",
    0x07: "T_JOIN",
}
# 网络层消息 (Network) — 在 FRAME 内部，低 4 位 mid
NETWORK_MSG = {
    0x19: "N_INTEREST",
    0x1a: "N_RESPONSE_FINAL",
    0x1b: "N_RESPONSE",
    0x1c: "N_REQUEST",
    0x1d: "N_PUSH",
    0x1e: "N_DECLARE",
    0x1f: "N_OAM",
}
# 会话层消息 (Zenoh) — 在 PUSH/RESPONSE 内部（低 4 位 mid）
ZENOH_MSG = {
    0x00: "Z_OAM",
    0x01: "Z_PUT",
    0x02: "Z_DEL",
    0x03: "Z_QUERY",
    0x04: "Z_REPLY",
    0x05: "Z_ERR",
}

# 报文方向
DIR_ESP2PC = ">>> ESP32 -> PC"
DIR_PC2ESP = "<<< PC -> ESP32"


# =============================================================================
# Zenohd 管理器
# =============================================================================
class ZenohdManager:
    @staticmethod
    def _find_pid():
        if not os.path.exists(PID_FILE):
            return None
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            return pid
        except (ValueError, OSError):
            return None

    @staticmethod
    def _check_process(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def status():
        pid = ZenohdManager._find_pid()
        if pid is None:
            print("[STATUS] zenohd 未运行 (PID 文件不存在)")
            return False
        if ZenohdManager._check_process(pid):
            print(f"[STATUS] zenohd 正在运行 (PID: {pid})")
            return True
        else:
            print(f"[STATUS] zenohd 已停止 (PID 文件过期: {pid})")
            ZenohdManager._cleanup_pid()
            return False

    @staticmethod
    def _cleanup_pid():
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)

    @staticmethod
    def start(trace=False, int_port=DEFAULT_ROUTER_PORT, protocols=("tcp",)):
        """启动 zenohd。trace=True 前台运行 + 日志输出；False 后台运行。
        protocols: 协议列表，如 ("tcp",) / ("udp",) / ("tcp", "udp")
        """
        if ZenohdManager.status():
            print("[START] zenohd 已在运行，跳过")
            return True

        if not os.path.exists(ZENOHD_EXE):
            print(f"[ERROR] zenohd 未找到: {ZENOHD_EXE}")
            return False

        # 构建监听参数：每个协议一个 -l 参数
        cmd = [ZENOHD_EXE]
        for proto in protocols:
            cmd += ["-l", f"{proto}/0.0.0.0:{int_port}"]
        listen_desc = " ".join(f"{p}/0.0.0.0:{int_port}" for p in protocols)

        if trace:
            # 前台运行 + RUST_LOG=debug 输出报文日志
            env = os.environ.copy()
            env["RUST_LOG"] = "debug"
            print(f"[START] 启动 zenohd (trace 模式, 监听 {listen_desc})")
            print(f"[START] RUST_LOG=debug 将打印详细报文日志")
            print(f"[START] 按 Ctrl+C 停止...\n")
            try:
                subprocess.run(cmd, env=env, cwd=ZENOHD_DIR)
            except KeyboardInterrupt:
                print("\n[STOP] zenohd 已停止")
            return True
        else:
            # 后台运行
            print(f"[START] 启动 zenohd (后台, 监听 {listen_desc}) ...")
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=ZENOHD_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                # 等待片刻确认启动
                time.sleep(1)
                if ZenohdManager._check_process(proc.pid):
                    with open(PID_FILE, "w") as f:
                        f.write(str(proc.pid))
                    print(f"[START] zenohd 已启动 (PID: {proc.pid})")
                    print(f"[START] 监听 {listen_desc}")
                    print(f"[START] 使用 'python host_zenoh_test.py stop' 停止")
                    return True
                else:
                    print(f"[ERROR] zenohd 启动失败")
                    return False
            except Exception as e:
                print(f"[ERROR] 启动失败: {e}")
                return False

    @staticmethod
    def stop():
        pid = ZenohdManager._find_pid()
        if pid is None:
            print("[STOP] zenohd 未运行")
            return
        if not ZenohdManager._check_process(pid):
            print("[STOP] zenohd 已停止")
            ZenohdManager._cleanup_pid()
            return
        try:
            # Windows 下用 taskkill 停止子进程
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
            ZenohdManager._cleanup_pid()
            print(f"[STOP] zenohd (PID: {pid}) 已停止")
        except Exception as e:
            print(f"[STOP] 停止失败: {e}")


# =============================================================================
# Zenoh 报文解析器
# =============================================================================
class ZenohPacketParser:
    """解析 zenoh 协议报文（尽力而为，用于抓包显示）"""

    @staticmethod
    def describe_transport_header(header_byte):
        """解析传输层报文头"""
        mid = header_byte & 0x0F
        flags = header_byte >> 4
        name = TRANSPORT_MSG.get(mid, f"T_UNKNOWN({mid:#x})")
        flag_desc = []
        if mid == 0x01:  # INIT
            if flags & 0x04: flag_desc.append("A(ACK)")
            if flags & 0x02: flag_desc.append("S(SIZE)")
        elif mid == 0x02:  # OPEN
            if flags & 0x04: flag_desc.append("A(ACK)")
            if flags & 0x02: flag_desc.append("T(LEASE)")
        elif mid == 0x05:  # FRAME
            if flags & 0x04: flag_desc.append("R(RELIABLE)")
        elif mid == 0x07:  # JOIN
            if flags & 0x04: flag_desc.append("S(SIZE)")
            if flags & 0x02: flag_desc.append("T(LEASE)")
        if flags & 0x08: flag_desc.append("Z(EXT)")
        return name, flag_desc

    @staticmethod
    def describe_network_header(header_byte):
        """解析网络层报文头（在 FRAME payload 内，mid 占低 5 位）"""
        mid = header_byte & 0x1F
        flags = header_byte >> 5
        name = NETWORK_MSG.get(mid, f"N_UNKNOWN({mid:#x})")
        flag_desc = []
        if mid == 0x1d:  # PUSH
            if flags & 0x04: flag_desc.append("N(NAMED)")
            if flags & 0x02: flag_desc.append("M(MAPPING)")
        elif mid == 0x1c:  # REQUEST
            if flags & 0x04: flag_desc.append("N(NAMED)")
            if flags & 0x02: flag_desc.append("M(MAPPING)")
        elif mid == 0x1b:  # RESPONSE
            if flags & 0x04: flag_desc.append("N(NAMED)")
            if flags & 0x02: flag_desc.append("M(MAPPING)")
        if flags & 0x01: flag_desc.append("Z(EXT)")
        return name, flag_desc

    @staticmethod
    def scan_network_messages(data):
        """在二进制数据中扫描网络报文类型（启发式，mid 占低 5 位）"""
        if not data:
            return []
        found = []
        scan_len = min(len(data), 1024)
        for i in range(scan_len):
            b = data[i]
            mid = b & 0x1F  # 网络层 mid 是 5 位
            if mid in NETWORK_MSG:
                name = NETWORK_MSG[mid]
                if found and found[-1][1] == name and found[-1][0] == i - 1:
                    continue
                found.append((i, name))
        return found

    @staticmethod
    def hex_dump(data, max_bytes=64, label=""):
        """生成 hex dump 文本"""
        if not data:
            return ""
        show = data[:max_bytes]
        hex_str = " ".join(f"{b:02x}" for b in show)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in show)
        if len(data) > max_bytes:
            return f"{label} [{len(data)} bytes, showing {max_bytes}]: {hex_str}  |{ascii_str}| ..."
        return f"{label} [{len(data)} bytes]: {hex_str}  |{ascii_str}|"


# =============================================================================
# TCP 代理（报文抓取）
# =============================================================================
class ProxyServer:
    def __init__(self, ext_port=PROXY_EXT_PORT, int_port=ZENOHD_INT_PORT,
                 hex_only=False, full_hex=False, log_callback=None):
        self.ext_port = ext_port
        self.int_port = int_port
        self.hex_only = hex_only
        self.full_hex = full_hex
        self.log_callback = log_callback
        self._running = False
        self._zenohd_proc = None

    def _log(self, msg, *args, **kwargs):
        """输出日志：有回调用回调，否则 print"""
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg, *args, **kwargs)

    def _log_packet(self, direction, data):
        """打印一条报文记录（完整的逐层解析）"""
        timestamp = time.strftime("%H:%M:%S")
        if not data:
            return

        hex_limit = 999999 if self.full_hex else (256 if self.hex_only else 128)
        first_byte = data[0]
        mid = first_byte & 0x0F

        # ---- 第一行：传输层报文类型 ----
        t_name, t_flags = ZenohPacketParser.describe_transport_header(first_byte)
        flag_str = f"[{','.join(t_flags)}]" if t_flags else ""
        direction_tag = "IN" if direction == DIR_ESP2PC else "OUT"
        self._log(f"{timestamp} {direction} {t_name} {flag_str}")

        # ---- hex dump ----
        show = data[:hex_limit]
        hex_str = " ".join(f"{b:02x}" for b in show)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in show)
        if len(data) > hex_limit:
            prefix = f"  HEX [{len(data)}b, show {hex_limit}]: "
        elif len(data) > 0:
            prefix = f"  HEX [{len(data)}b]: "
        else:
            prefix = "  HEX: "
        self._log(f"{prefix}{hex_str}  |{ascii_str}|")
        if len(data) > hex_limit:
            self._log(f"  ... 剩余 {len(data) - hex_limit} 字节省略")

        # ---- 深度解析：FRAME → 内部网络报文 ----
        if mid == 0x05:  # T_FRAME
            payload = data[1:]
            # 跳过 zint SN
            sn_len = 0
            for b in payload:
                sn_len += 1
                if b < 0x80:
                    break
            net_payload = payload[sn_len:]
            if net_payload:
                nmsgs = ZenohPacketParser.scan_network_messages(net_payload)
                if nmsgs:
                    names = "  ├─ 网络层: " + " → ".join(n[1] for n in nmsgs)
                    self._log(names)

                    # 尝试进一步解析 N_PUSH 内的 Z_PUT/Z_DEL/Z_QUERY
                    for offset, name in nmsgs:
                        if name == "N_PUSH" and offset + 1 < len(net_payload):
                            inner = net_payload[offset + 1:]
                            z_msgs = []
                            for j, b in enumerate(inner):
                                if b in ZENOH_MSG:
                                    z_msgs.append((j, ZENOH_MSG[b]))
                                    break
                            if z_msgs:
                                z_names = " → ".join(n[1] for n in z_msgs)
                                self._log(f"  └─ 会话层: {z_names}")

        # ---- 握手消息额外信息 ----
        if mid == 0x01:  # T_INIT
            if len(data) > 1:
                # 尝试解析 ZID 长度
                is_ack = (first_byte >> 5) & 1  # flag A
                self._log(f"    类型: {'InitAck' if is_ack else 'InitSyn'}")
        elif mid == 0x02:  # T_OPEN
            is_ack = (first_byte >> 5) & 1
            self._log(f"    类型: {'OpenAck' if is_ack else 'OpenSyn'}")
        elif mid == 0x03:  # T_CLOSE
            if len(data) > 1:
                reason = data[1]
                self._log(f"    原因码: {reason:#04x}")
        elif mid == 0x07:  # T_JOIN
            if len(data) > 1:
                zid_len = ((data[1] & 0xF0) >> 4) + 1
                self._log(f"    ZID 长度: {zid_len} 字节")

    def _forward(self, src_name, src_sock, dst_sock, log_cb):
        """双向转发：从 src 读，写入 dst，同时记录日志"""
        try:
            while self._running:
                data = src_sock.recv(65536)
                if not data:
                    break
                log_cb(data)
                dst_sock.sendall(data)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                src_sock.close()
            except OSError:
                pass

    def stop(self):
        """停止代理服务器"""
        self._running = False
        # 关闭监听 socket（如果有）
        if hasattr(self, '_server_sock') and self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        # 关闭内部 zenohd
        if self._zenohd_proc and self._zenohd_proc.poll() is None:
            self._zenohd_proc.terminate()
            try:
                self._zenohd_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._zenohd_proc.kill()
        # 清理 PID 文件
        if hasattr(self, '_proxy_pid_file') and os.path.exists(self._proxy_pid_file):
            try:
                os.remove(self._proxy_pid_file)
            except OSError:
                pass

    def run(self):
        """启动代理服务器 + 内部 zenohd"""
        ext_addr = f"0.0.0.0:{self.ext_port}"
        int_addr = f"127.0.0.1:{self.int_port}"

        # 直接启动内部 zenohd（使用独立 PID 文件避免冲突）
        self._proxy_pid_file = os.path.join(SCRIPT_DIR, f".proxy_zenohd_{self.int_port}.pid")
        try:
            self._zenohd_proc = subprocess.Popen(
                [ZENOHD_EXE, "-l", f"tcp/127.0.0.1:{self.int_port}"],
                cwd=ZENOHD_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(1.5)
            if self._zenohd_proc.poll() is not None:
                self._log(f"[ERROR] 内部 zenohd 启动失败，退出码: {self._zenohd_proc.returncode}")
                return
            # 写入唯一 PID 文件
            with open(self._proxy_pid_file, "w") as f:
                f.write(str(self._zenohd_proc.pid))
        except Exception as e:
            self._log(f"[ERROR] 内部 zenohd 启动异常: {e}")
            return

        self._log(f"\n{'=' * 60}")
        if self.hex_only:
            self._log(f"  Zenoh 报文代理 (HEX ONLY)")
        elif self.full_hex:
            self._log(f"  Zenoh 报文代理 (完整 HEX)")
        else:
            self._log(f"  Zenoh 报文代理 + 协议解析")
        self._log(f"  ESP32 连接: {ext_addr}  ──►  zenohd: {int_addr}")
        self._log(f"  按 Ctrl+C 停止")
        self._log(f"{'=' * 60}\n")

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("0.0.0.0", self.ext_port))
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)
        self._running = True

        try:
            while self._running:
                try:
                    client_sock, addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                self._log(f"\n[CONNECT] ESP32 已连接: {addr[0]}:{addr[1]}")

                try:
                    zenohd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    zenohd_sock.connect(("127.0.0.1", self.int_port))
                except ConnectionRefusedError:
                    self._log(f"[ERROR] 无法连接内部 zenohd (127.0.0.1:{self.int_port})")
                    client_sock.close()
                    continue

                t1 = threading.Thread(
                    target=self._forward,
                    args=("ESP32->PC", client_sock, zenohd_sock,
                          lambda d: self._log_packet(DIR_ESP2PC, d)),
                    daemon=True,
                )
                t2 = threading.Thread(
                    target=self._forward,
                    args=("PC->ESP32", zenohd_sock, client_sock,
                          lambda d: self._log_packet(DIR_PC2ESP, d)),
                    daemon=True,
                )
                t1.start()
                t2.start()

                t1.join()
                t2.join()
                self._log(f"[DISCONNECT] ESP32 已断开 ({addr[0]}:{addr[1]})\n")

        except KeyboardInterrupt:
            self._log("\n[PROXY] 代理已停止")
        finally:
            self._running = False
            if hasattr(self, '_server_sock') and self._server_sock:
                try:
                    self._server_sock.close()
                except OSError:
                    pass
            if self._zenohd_proc and self._zenohd_proc.poll() is None:
                self._zenohd_proc.terminate()
                self._zenohd_proc.wait(timeout=5)
            # 清理 PID 文件
            if hasattr(self, '_proxy_pid_file') and os.path.exists(self._proxy_pid_file):
                os.remove(self._proxy_pid_file)


# =============================================================================
# 原始 Zenoh 通信功能（sub/pub/get）
# =============================================================================
SUB_KEY = "zenoh/esp32/**"
PUB_KEY = "zenoh/esp32/test"
QUERY_KEY = "zenoh/esp32/query"


def _open_session(router_endpoint):
    """打开 zenoh 会话（支持显式 router 连接）"""
    import zenoh as z
    conf = z.Config()
    if router_endpoint:
        conf.insert_json5("connect/endpoints", f"['{router_endpoint}']")
    return z.open(conf)


def run_sub(router_endpoint=None):
    session = _open_session(router_endpoint)
    print(f"[SUB] Subscribing to {SUB_KEY} ... (Ctrl+C to quit)")
    sub = session.declare_subscriber(SUB_KEY, lambda s: print(
        f"<< [SUB] Received ({s.key_expr}, {s.payload.to_string()})"))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sub.undeclare()
        session.close()


def run_pub(msg, router_endpoint=None):
    session = _open_session(router_endpoint)
    pub = session.declare_publisher(PUB_KEY)
    print(f"[PUB] Publishing to {PUB_KEY} : {msg}")
    pub.put(msg)
    print("Done! ESP32 should show the received message.")
    pub.undeclare()
    session.close()


def run_get(router_endpoint=None):
    session = _open_session(router_endpoint)
    print(f"[GET] Querying {QUERY_KEY} ...")
    replies = session.get(QUERY_KEY, timeout=3.0)
    for reply in replies:
        try:
            sample = reply.ok
            print(f"<< [GET] Reply ({sample.key_expr}, {sample.payload.to_string()})")
        except Exception:
            print(f"<< [GET] Error: {reply.err.payload.to_string()}")
    session.close()


# =============================================================================
# 主入口
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Zenoh Host Test Tool — ESP32 联调 + 报文抓取 + 自动管理 zenohd",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  start             后台启动 zenohd
  start --trace     前台启动 zenohd + 打印报文日志
  stop              停止 zenohd
  status            查看 zenohd 状态
  proxy             启动 TCP 代理 + 报文解析
  proxy --hex-only  仅 hex dump（不解析）
  sub               订阅 ESP32 消息
  pub <msg>         向 ESP32 发布消息
  get               查询 ESP32

示例:
  python host_zenoh_test.py start
  python host_zenoh_test.py proxy
  python host_zenoh_test.py sub
        """)
    parser.add_argument("cmd", nargs="?", default=None,
                        help="子命令: start|stop|status|proxy|sub|pub|get")
    parser.add_argument("arg", nargs="?", default=None,
                        help="pub 消息内容")
    parser.add_argument("--trace", action="store_true",
                        help="start 命令: 前台运行 + 打印 zenohd 报文日志")
    parser.add_argument("--hex-only", action="store_true",
                        help="proxy 命令: 仅 hex dump，不解析协议")
    parser.add_argument("--port", type=int, default=PROXY_EXT_PORT,
                        help="代理对外端口 (默认 7447)")
    parser.add_argument("--router", type=str, default=None,
                        help="sub/pub/get 命令: 显式指定 router 端点，"
                             "如 tcp/127.0.0.1:7447 (默认使用 scouting)")
    parser.add_argument("--proto", type=str, default="tcp",
                        choices=["tcp", "udp", "tcp+udp"],
                        help="start 命令: zenohd 监听协议 (默认 tcp)")
    parser.add_argument("--full-hex", action="store_true",
                        help="proxy 命令: 输出完整 HEX (不截断)")
    parser.add_argument("--int-port", type=int, default=ZENOHD_INT_PORT,
                        help="proxy 命令: 内部 zenohd 端口 (默认 7448)")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return

    cmd = args.cmd.lower()

    if cmd == "start":
        protocols = tuple(args.proto.split("+"))
        ZenohdManager.start(trace=args.trace, protocols=protocols)
    elif cmd == "stop":
        ZenohdManager.stop()
    elif cmd == "status":
        ZenohdManager.status()
    elif cmd == "proxy":
        proxy = ProxyServer(
            ext_port=args.port,
            int_port=args.int_port,
            hex_only=args.hex_only,
            full_hex=args.full_hex,
        )
        proxy.run()
    elif cmd == "sub":
        run_sub(router_endpoint=args.router)
    elif cmd == "pub":
        msg = args.arg if args.arg else "Hello from PC!"
        run_pub(msg, router_endpoint=args.router)
    elif cmd == "get":
        run_get(router_endpoint=args.router)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()