#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zenoh 主机端测试工具 — 图形化界面

Usage:
    python zenoh_gui.py

功能:
    - zenohd 路由器管理 (启动/停止/状态)
    - Pub / Sub / Get 测试按钮
    - 报文代理 + 协议解析
    - 参数可配置 (WiFi/主题/端口等)
"""
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import customtkinter as ctk

# 设置主题
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ZENOHD_DIR = os.path.join(SCRIPT_DIR, "zenoh")
ZENOHD_EXE = os.path.join(ZENOHD_DIR, "zenohd.exe")
PID_FILE = os.path.join(SCRIPT_DIR, ".zenohd.pid")


class ZenohdManager:
    """zenohd 路由器管理（后端逻辑）"""

    @staticmethod
    def _find_pid():
        try:
            if not os.path.exists(PID_FILE):
                return None
            with open(PID_FILE) as f:
                return int(f.read().strip())
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
    def is_running():
        pid = ZenohdManager._find_pid()
        if pid and ZenohdManager._check_process(pid):
            return True
        if pid:  # stale pid file
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
        return False

    @staticmethod
    def start(port, protocols=("tcp",)):
        """后台启动 zenohd，返回 (ok, message)
        protocols: 协议列表，如 ("tcp",) / ("udp",) / ("tcp", "udp")
        """
        if ZenohdManager.is_running():
            return True, f"zenohd 已在运行 (PID: {ZenohdManager._find_pid()})"
        if not os.path.exists(ZENOHD_EXE):
            return False, f"未找到 zenohd: {ZENOHD_EXE}"
        try:
            # 构建监听参数：每个协议一个 -l 参数
            listen_args = []
            for proto in protocols:
                listen_args += ["-l", f"{proto}/0.0.0.0:{port}"]

            proc = subprocess.Popen(
                [ZENOHD_EXE] + listen_args,
                cwd=ZENOHD_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(1)
            if ZenohdManager._check_process(proc.pid):
                with open(PID_FILE, "w") as f:
                    f.write(str(proc.pid))
                proto_str = "+".join(protocols)
                return True, f"zenohd 已启动 (PID: {proc.pid}, {proto_str}/0.0.0.0:{port})"
            return False, "zenohd 启动失败"
        except Exception as e:
            return False, f"启动异常: {e}"

    @staticmethod
    def start_trace(port, protocols=("tcp",)):
        """前台启动 zenohd (RUST_LOG=trace)，通过 stderr 输出报文日志。
        返回 (process, message) 或 (None, error_msg)。
        """
        if not os.path.exists(ZENOHD_EXE):
            return None, f"未找到 zenohd: {ZENOHD_EXE}"
        try:
            listen_args = []
            for proto in protocols:
                listen_args += ["-l", f"{proto}/0.0.0.0:{port}"]

            env = os.environ.copy()
            # trace 级别会输出 TransportMessage/NetworkMessage 报文内容
            env["RUST_LOG"] = "trace"

            proc = subprocess.Popen(
                [ZENOHD_EXE] + listen_args,
                cwd=ZENOHD_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # 等待片刻确认进程启动
            time.sleep(0.5)
            if proc.poll() is not None:
                return None, f"zenohd 启动失败，退出码: {proc.returncode}"

            proto_str = "+".join(protocols)
            return proc, f"zenohd 已启动 (trace 模式, PID: {proc.pid}, {proto_str}/0.0.0.0:{port})"
        except Exception as e:
            return None, f"启动异常: {e}"

    @staticmethod
    def stop_pid(pid):
        """按 PID 停止进程（通过 taskkill）"""
        if pid is None:
            return "未指定 PID"
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
            )
            return f"进程 (PID: {pid}) 已停止"
        except Exception as e:
            return f"停止失败: {e}"

    @staticmethod
    def stop():
        """停止 zenohd"""
        pid = ZenohdManager._find_pid()
        if pid is None:
            return "zenohd 未运行"
        if not ZenohdManager._check_process(pid):
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
            return f"zenohd 已停止 (PID: {pid})"
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
            )
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
            return f"zenohd 已停止 (PID: {pid})"
        except Exception as e:
            return f"停止失败: {e}"


class LogRedirector:
    """将子进程输出重定向到 GUI 文本框"""

    def __init__(self, append_func):
        self.append = append_func

    def write(self, text):
        if text.strip():
            self.append(text)


class ZenohGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Zenoh ESP32 测试工具")
        self.geometry("1080x720")
        self.minsize(960, 640)

        # 进程管理
        self.proxy_process = None
        self.trace_process = None
        self.trace_thread = None

        # ============ 布局：2行 × 2列 ============
        # Row 0: 顶部控制面板（水平4列，固定高度）
        # Row 1: 底部日志区（左右两列，expand）
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 顶部控制面板
        self.control_frame = ctk.CTkFrame(self, corner_radius=12)
        self.control_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        self._build_control_area()

        # 底部日志框架
        self.log_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
        self.log_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))
        self.log_frame.grid_rowconfigure(0, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_columnconfigure(1, weight=1)

        # 底部左侧：运行日志
        self.run_log_frame = ctk.CTkFrame(self.log_frame, corner_radius=12)
        self.run_log_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.run_log_frame.grid_rowconfigure(1, weight=1)
        self.run_log_frame.grid_columnconfigure(0, weight=1)
        self._build_run_log_area()

        # 底部右侧：抓包日志
        self.packet_frame = ctk.CTkFrame(self.log_frame, corner_radius=12)
        self.packet_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.packet_frame.grid_rowconfigure(1, weight=1)
        self.packet_frame.grid_columnconfigure(0, weight=1)
        self._build_packet_area()

        # 启动时刷新状态
        self.after(500, self.refresh_status)

    # ==================== 顶部控制面板（水平4列） ====================
    def _build_control_area(self):
        # 4列等宽
        self.control_frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="col")

        # ---------- ① 路由器管理 ----------
        router_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        router_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=8)

        ctk.CTkLabel(router_frame, text="① 路由器 (zenohd)",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#1565c0").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        # 协议
        ctk.CTkLabel(router_frame, text="协议:", anchor="w").grid(row=1, column=0, sticky="w")
        self.protocol_var = ctk.StringVar(value="tcp")
        ctk.CTkOptionMenu(router_frame, variable=self.protocol_var,
                          values=["tcp", "udp", "tcp+udp"], width=90).grid(row=1, column=1, sticky="w", padx=4)

        # 端口
        ctk.CTkLabel(router_frame, text="端口:", anchor="w").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.port_entry = ctk.CTkEntry(router_frame, width=70, placeholder_text="7447")
        self.port_entry.grid(row=2, column=1, sticky="w", padx=4, pady=(4, 0))
        self.port_entry.insert(0, "7447")

        self.trace_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(router_frame, text="抓包模式", variable=self.trace_var,
                        checkbox_height=18, checkbox_width=18).grid(row=2, column=2, columnspan=2, sticky="w", padx=4, pady=(4, 0))

        # 状态
        self.router_status = ctk.CTkLabel(router_frame, text="● 未知", text_color="orange")
        self.router_status.grid(row=1, column=2, columnspan=2, sticky="w", padx=4)

        # 按钮
        btn_frame = ctk.CTkFrame(router_frame, fg_color="transparent")
        btn_frame.grid(row=3, column=0, columnspan=4, pady=(6, 0), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(btn_frame, text="▶ 启动", command=self.on_start,
                      fg_color="#2e7d32", hover_color="#388e3c", height=26).grid(row=0, column=0, padx=2)
        ctk.CTkButton(btn_frame, text="■ 停止", command=self.on_stop,
                      fg_color="#c62828", hover_color="#d32f2f", height=26).grid(row=0, column=1, padx=2)
        ctk.CTkButton(btn_frame, text="⟳ 刷新", command=self.refresh_status,
                      fg_color="gray40", hover_color="gray50", height=26).grid(row=0, column=2, padx=2)

        # ---------- ② 主题配置 ----------
        ke_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        ke_frame.grid(row=0, column=1, sticky="nsew", padx=6, pady=8)

        ctk.CTkLabel(ke_frame, text="② 主题 (Key Expression)",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#1565c0").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        labels = ["发布主题:", "订阅主题:", "查询主题:", "Router端点:"]
        defaults = ["zenoh/esp32/test", "zenoh/esp32/**", "zenoh/esp32/query", "tcp/127.0.0.1:7447"]

        self.ke_vars = {}
        for i, (label, default) in enumerate(zip(labels, defaults)):
            ctk.CTkLabel(ke_frame, text=label, width=75, anchor="w").grid(
                row=i + 1, column=0, sticky="w", pady=1)
            var = ctk.StringVar(value=default)
            entry = ctk.CTkEntry(ke_frame, textvariable=var, height=24)
            entry.grid(row=i + 1, column=1, sticky="ew", pady=1, padx=4)
            self.ke_vars[label] = var

        ke_frame.grid_columnconfigure(1, weight=1)

        # ---------- ③ 通信测试 ----------
        test_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        test_frame.grid(row=0, column=2, sticky="nsew", padx=6, pady=8)

        ctk.CTkLabel(test_frame, text="③ 通信测试",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#1565c0").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        test_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(test_frame, text="📤 发布", command=self.on_pub,
                      fg_color="#1565c0", hover_color="#1976d2", height=26).grid(row=1, column=0, padx=2, pady=2, sticky="ew")
        ctk.CTkButton(test_frame, text="📥 订阅", command=self.on_sub,
                      fg_color="#00695c", hover_color="#00897b", height=26).grid(row=1, column=1, padx=2, pady=2, sticky="ew")
        ctk.CTkButton(test_frame, text="🔍 查询", command=self.on_get,
                      fg_color="#4527a0", hover_color="#5e35b1", height=26).grid(row=2, column=0, padx=2, pady=2, sticky="ew")
        ctk.CTkButton(test_frame, text="🧪 全部测试", command=self.on_all_test,
                      fg_color="#880e4f", hover_color="#ad1457", height=26).grid(row=2, column=1, padx=2, pady=2, sticky="ew")

        ctk.CTkLabel(test_frame, text="消息内容:", anchor="w").grid(
            row=3, column=0, sticky="w", pady=(4, 0))
        self.pub_msg_entry = ctk.CTkEntry(test_frame, placeholder_text="Hello from PC!", height=24)
        self.pub_msg_entry.grid(row=3, column=1, sticky="ew", padx=2, pady=(4, 0))
        self.pub_msg_entry.insert(0, "Hello ESP32 from GUI!")

        self.pub_status = ctk.CTkLabel(test_frame, text="", text_color="gray70", anchor="w")
        self.pub_status.grid(row=4, column=0, columnspan=2, sticky="ew", pady=2)

        # ---------- ④ 报文抓包 ----------
        proxy_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        proxy_frame.grid(row=0, column=3, sticky="nsew", padx=6, pady=8)

        ctk.CTkLabel(proxy_frame, text="④ 报文抓包 (Proxy)",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#1565c0").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        ctk.CTkLabel(proxy_frame, text="代理端口:", anchor="w").grid(row=1, column=0, sticky="w")
        self.proxy_port_entry = ctk.CTkEntry(proxy_frame, width=70, placeholder_text="7447", height=24)
        self.proxy_port_entry.grid(row=1, column=1, sticky="w", padx=4)
        self.proxy_port_entry.insert(0, "7447")

        self.hex_only_var = ctk.BooleanVar(value=False)
        self.full_hex_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(proxy_frame, text="仅HEX", variable=self.hex_only_var,
                        width=28, checkbox_height=18, checkbox_width=18).grid(row=1, column=2, sticky="e")
        ctk.CTkCheckBox(proxy_frame, text="完整HEX", variable=self.full_hex_var,
                        width=28, checkbox_height=18, checkbox_width=18).grid(row=1, column=3, sticky="e", padx=2)

        proxy_btns = ctk.CTkFrame(proxy_frame, fg_color="transparent")
        proxy_btns.grid(row=2, column=0, columnspan=4, pady=(6, 0), sticky="ew")
        proxy_btns.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(proxy_btns, text="🔍 启动代理", command=self.on_proxy_start,
                      fg_color="#e65100", hover_color="#ef6c00", height=26).grid(row=0, column=0, padx=2)
        ctk.CTkButton(proxy_btns, text="⏹ 停止代理", command=self.on_proxy_stop,
                      fg_color="gray50", hover_color="gray60", height=26).grid(row=0, column=1, padx=2)

        self.proxy_status = ctk.CTkLabel(proxy_frame, text="", text_color="gray70", anchor="w")
        self.proxy_status.grid(row=3, column=0, columnspan=4, sticky="ew", pady=2)

        self.proxy_hint = ctk.CTkLabel(
            proxy_frame, text="报文显示在右侧抓包区",
            font=ctk.CTkFont(size=11), text_color="gray50", anchor="w")
        self.proxy_hint.grid(row=4, column=0, columnspan=4, sticky="w")

    # ==================== 底部左侧：运行日志 ====================
    def _build_run_log_area(self):
        log_header = ctk.CTkFrame(self.run_log_frame, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        log_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(log_header, text="📋 运行日志",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(log_header, text="🗑 清空", width=60, command=self.clear_run_log,
                      fg_color="gray40", hover_color="gray55", height=24).grid(row=0, column=1, sticky="e")

        self.run_log_text = tk.Text(
            self.run_log_frame, bg="#fafafa", fg="#333333",
            font=("Consolas", 10), wrap="word", state="disabled",
            insertbackground="#333333", selectbackground="#add6ff"
        )
        self.run_log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(2, 8))

        scrollbar = ttk.Scrollbar(self.run_log_frame, command=self.run_log_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(2, 8))
        self.run_log_text.configure(yscrollcommand=scrollbar.set)

    # ==================== 底部右侧：抓包日志 ====================
    def _build_packet_area(self):
        pkt_header = ctk.CTkFrame(self.packet_frame, fg_color="transparent")
        pkt_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        pkt_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(pkt_header, text="📡 报文抓包",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(pkt_header, text="🗑 清空", width=60, command=self.clear_packet_log,
                      fg_color="gray40", hover_color="gray55", height=24).grid(row=0, column=1, sticky="e")

        self.packet_text = tk.Text(
            self.packet_frame, bg="#ffffff", fg="#333333",
            font=("Consolas", 10), wrap="word", state="disabled",
            insertbackground="#333333", selectbackground="#add6ff"
        )
        self.packet_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(2, 8))

        scrollbar = ttk.Scrollbar(self.packet_frame, command=self.packet_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(2, 8))
        self.packet_text.configure(yscrollcommand=scrollbar.set)

        # 状态栏
        self.status_bar = ctk.CTkLabel(
            self.packet_frame, text="就绪", text_color="gray60", anchor="w", height=20
        )
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))

    # ==================== 日志方法 ====================
    def _append_run_log(self, text, tag="info"):
        """向运行日志区追加一行（线程安全）"""
        def _do_append():
            self.run_log_text.configure(state="normal")
            self.run_log_text.insert("end", text + "\n", tag)
            self.run_log_text.tag_config("info", foreground="#333333")
            self.run_log_text.tag_config("success", foreground="#1a7f37")
            self.run_log_text.tag_config("error", foreground="#d1242f")
            self.run_log_text.tag_config("system", foreground="#0550ae")
            self.run_log_text.see("end")
            self.run_log_text.configure(state="disabled")
        self.after(0, _do_append)

    def _append_packet_log(self, text, tag="packet"):
        """向抓包日志区追加一行（线程安全）"""
        def _do_append():
            self.packet_text.configure(state="normal")
            self.packet_text.insert("end", text + "\n", tag)
            self.packet_text.tag_config("packet", foreground="#8250df")
            self.packet_text.tag_config("header", foreground="#953800")
            self.packet_text.tag_config("info", foreground="#333333")
            self.packet_text.tag_config("success", foreground="#1a7f37")
            self.packet_text.tag_config("error", foreground="#d1242f")
            self.packet_text.tag_config("system", foreground="#0550ae")
            self.packet_text.see("end")
            self.packet_text.configure(state="disabled")
        self.after(0, _do_append)

    def log_info(self, msg):
        self._append_run_log(f"[INFO] {msg}", "info")

    def log_success(self, msg):
        self._append_run_log(f"[✔] {msg}", "success")

    def log_error(self, msg):
        self._append_run_log(f"[✘] {msg}", "error")

    def log_system(self, msg):
        self._append_run_log(f"[SYS] {msg}", "system")

    def log_packet(self, msg):
        self._append_packet_log(f"  {msg}", "packet")

    def clear_run_log(self):
        self.run_log_text.configure(state="normal")
        self.run_log_text.delete("1.0", "end")
        self.run_log_text.configure(state="disabled")

    def clear_packet_log(self):
        self.packet_text.configure(state="normal")
        self.packet_text.delete("1.0", "end")
        self.packet_text.configure(state="disabled")

    # ==================== 后台任务 ====================
    def _run_in_thread(self, func, *args):
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()
        return t

    # ==================== 1. zenohd 管理 ====================
    def refresh_status(self):
        self._run_in_thread(self._refresh_status_worker)

    def _refresh_status_worker(self):
        if ZenohdManager.is_running():
            pid = ZenohdManager._find_pid()
            status = f"● 运行中 (PID {pid})"
            color = "#7ee787"
        else:
            status = "● 已停止"
            color = "#ff7b72"
        self.after(0, lambda: (
            self.router_status.configure(text=status, text_color=color),
            self.status_bar.configure(text=f"路由器状态: {status}")))

    def on_start(self):
        if self.trace_var.get():
            self.log_system("正在启动 zenohd (trace 模式)...")
            self._run_in_thread(self._start_trace_worker)
        else:
            self.log_system("正在启动 zenohd...")
            self._run_in_thread(self._start_worker)

    def _start_worker(self):
        port = self.port_entry.get().strip() or "7447"
        proto_raw = self.protocol_var.get()
        protocols = tuple(proto_raw.split("+"))
        ok, msg = ZenohdManager.start(port, protocols=protocols)
        self.after(0, lambda: self._handle_router_result(ok, msg))

    def _start_trace_worker(self):
        port = self.port_entry.get().strip() or "7447"
        proto_raw = self.protocol_var.get()
        protocols = tuple(proto_raw.split("+"))
        proc, msg = ZenohdManager.start_trace(port, protocols=protocols)
        if proc is None:
            self.after(0, lambda: self.log_error(msg))
            return

        self.trace_process = proc
        self.after(0, lambda: (
            self.log_success(msg),
            self.router_status.configure(text="● 运行中 (trace)", text_color="#7ee787"),
            self.status_bar.configure(text=f"路由器状态: trace 模式 (PID {proc.pid})")))

        # 启动 stderr 读取线程
        self.trace_thread = threading.Thread(
            target=self._read_trace_stderr, args=(proc,), daemon=True)
        self.trace_thread.start()

    def _read_trace_stderr(self, proc):
        """从 zenohd 的 stderr 读取日志，分流到运行日志和抓包区"""
        # 报文行的特征：zenohd RUST_LOG=trace 输出的传输层/网络层报文
        PACKET_PATTERNS = [
            b"TransportMessage",   # 传输层报文: InitSyn/InitAck/OpenSyn/KeepAlive...
            b"NetworkMessage",     # 网络层报文: N_PUSH/N_INTEREST/N_DECLARE...
            b"Received:",          # 收到报文
            b"Sending:",           # 发送报文
        ]
        try:
            for line in iter(proc.stderr.readline, b""):
                if not self.trace_process or self.trace_process.poll() is not None:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue

                # 判断是否报文行：包含任何报文特征
                is_packet = any(p in line.lower() for p in PACKET_PATTERNS)

                if is_packet:
                    # 报文行 → 抓包区
                    self.after(0, lambda t=text: self._append_packet_log(t, "packet"))
                else:
                    # 普通日志 → 运行日志
                    self.after(0, lambda t=text: self._append_run_log(t, "system"))
        except (OSError, ValueError):
            pass
        finally:
            # 进程退出后清理
            self.after(0, lambda: self._on_trace_stopped())

    def _on_trace_stopped(self):
        """trace 进程停止后的清理"""
        self.log_info("zenohd trace 进程已退出")
        if self.trace_process:
            try:
                self.trace_process.kill()
            except Exception:
                pass
            self.trace_process = None
        self.trace_thread = None
        self.refresh_status()

    def _handle_router_result(self, ok, msg):
        if ok:
            self.log_success(msg)
            self.refresh_status()
        else:
            self.log_error(msg)

    def on_stop(self):
        # 如果正在运行 trace 进程，先停掉它（不删除 PID 文件里的常规进程）
        if self.trace_process:
            self.log_system("正在停止 trace zenohd...")
            try:
                ZenohdManager.stop_pid(self.trace_process.pid)
                self.trace_process = None
            except Exception as e:
                self.log_error(f"停止 trace 进程异常: {e}")
        self.log_system("正在停止 zenohd...")
        self._run_in_thread(self._stop_worker)

    def _stop_worker(self):
        msg = ZenohdManager.stop()
        self.after(0, lambda: (
            self.log_info(msg),
            self.refresh_status()))

    # ==================== 2. 通信测试 ====================
    def _get_config(self):
        return {
            "pub_key": self.ke_vars["发布主题:"].get(),
            "sub_key": self.ke_vars["订阅主题:"].get(),
            "query_key": self.ke_vars["查询主题:"].get(),
            "router": self.ke_vars["Router端点:"].get(),
        }

    def on_pub(self):
        cfg = self._get_config()
        msg = self.pub_msg_entry.get() or "Hello from PC!"
        self.log_system(f"发布到 {cfg['pub_key']}: {msg}")
        self._run_in_thread(self._pub_worker, cfg, msg)

    def _pub_worker(self, cfg, msg):
        try:
            sys.path.insert(0, SCRIPT_DIR)
            import host_zenoh_test as hzt
            hzt.PUB_KEY = cfg["pub_key"]
            self._setup_session_config(hzt, cfg)
            hzt.run_pub(msg, router_endpoint=cfg["router"])
            self.after(0, lambda: (
                self.log_success(f"已发布: {msg}"),
                self.pub_status.configure(text="发布成功 ✅", text_color="#7ee787")))
        except Exception as e:
            self._thread_error(self.log_error, "发布失败", e)

    def on_sub(self):
        cfg = self._get_config()
        self.log_system(f"订阅 {cfg['sub_key']} (30秒超时)...")
        self._run_in_thread(self._sub_worker, cfg)

    def _sub_worker(self, cfg):
        try:
            sys.path.insert(0, SCRIPT_DIR)
            import host_zenoh_test as hzt
            hzt.SUB_KEY = cfg["sub_key"]
            self._setup_session_config(hzt, cfg)

            import zenoh as z
            session = hzt._open_session(cfg["router"])
            sub = session.declare_subscriber(
                cfg["sub_key"],
                lambda s: self.after(0, lambda: self.log_packet(
                    f"SUB << ({s.key_expr}, {s.payload.to_string()})")),
            )
            self.after(0, lambda: self.log_success(f"订阅成功: {cfg['sub_key']}"))

            # 订阅 30 秒后自动停止
            end = time.time() + 30
            while time.time() < end:
                time.sleep(0.5)
            sub.undeclare()
            session.close()
            self.after(0, lambda: self.log_info("订阅结束 (30秒超时)"))
        except Exception as e:
            self._thread_error(self.log_error, "订阅失败", e)

    def on_get(self):
        cfg = self._get_config()
        self.log_system(f"查询 {cfg['query_key']}...")
        self._run_in_thread(self._get_worker, cfg)

    def _get_worker(self, cfg):
        try:
            sys.path.insert(0, SCRIPT_DIR)
            import host_zenoh_test as hzt
            self._setup_session_config(hzt, cfg)
            hzt.run_get(router_endpoint=cfg["router"])
            self.after(0, lambda: self.log_success("查询完成"))
        except Exception as e:
            self._thread_error(self.log_error, "查询失败", e)

    def on_all_test(self):
        """顺序执行: 启动路由器 -> 订阅 -> 发布 -> 查询"""
        self.log_system("========== 开始全流程测试 ==========")
        self._run_in_thread(self._all_test_worker)

    def _all_test_worker(self):
        cfg = self._get_config()

        # 1. 确保 zenohd 运行
        if not ZenohdManager.is_running():
            port = self.port_entry.get().strip() or "7447"
            proto_raw = self.protocol_var.get()
            protocols = tuple(proto_raw.split("+"))
            ok, msg = ZenohdManager.start(port, protocols=protocols)
            if not ok:
                self.after(0, lambda: self.log_error(f"路由器启动失败: {msg}"))
                return
            self.after(0, lambda: self.log_success(f"路由器: {msg}"))

        time.sleep(1)

        # 2. 在当前线程里做 get 验证连通性
        try:
            sys.path.insert(0, SCRIPT_DIR)
            import host_zenoh_test as hzt
            self._setup_session_config(hzt, cfg)
            hzt.run_get(router_endpoint=cfg["router"])
            self.after(0, lambda: self.log_success("✅ GET 查询测试通过"))
        except Exception as e:
            self.after(0, lambda: self.log_error(f"GET 测试失败: {e}"))

        # 3. 发布测试
        try:
            msg = self.pub_msg_entry.get() or "Hello ESP32!"
            hzt.run_pub(msg, router_endpoint=cfg["router"])
            self.after(0, lambda: self.log_success("✅ PUB 发布测试通过"))
        except Exception as e:
            self.after(0, lambda: self.log_error(f"PUB 测试失败: {e}"))

        self.after(0, lambda: self.log_system("========== 全流程测试结束 =========="))

    def _setup_session_config(self, hzt, cfg):
        """同步会话配置"""
        hzt.SUB_KEY = cfg["sub_key"]

    def _thread_error(self, log_func, prefix, e):
        import traceback
        tb = traceback.format_exc()
        self.after(0, lambda: log_func(f"{prefix}: {e}\n{tb}"))

    # ==================== 3. 报文代理 ====================
    def on_proxy_start(self):
        if getattr(self, "proxy_thread", None) and self.proxy_thread.is_alive():
            messagebox.showwarning("提示", "代理已在运行")
            return

        # 清理旧的 zenohd 进程和 PID 文件（避免端口冲突）
        self.log_info("清理旧 zenohd 进程...")
        subprocess.run(["taskkill", "/F", "/IM", "zenohd.exe"],
                       capture_output=True)
        for f in [".zenohd.pid", ".proxy_zenohd.pid"]:
            fp = os.path.join(SCRIPT_DIR, f)
            if os.path.exists(fp):
                os.remove(fp)

        # 代理端口配置
        ext_port = int(self.proxy_port_entry.get().strip() or "7447")
        int_port = ext_port + 1

        # 导入 ProxyServer（从 host_zenoh_test.py）
        sys.path.insert(0, SCRIPT_DIR)
        try:
            from host_zenoh_test import ProxyServer
        except ImportError as e:
            messagebox.showerror("错误", f"导入失败: {e}")
            return

        self.log_system(f"启动代理: ESP32 → 127.0.0.1:{ext_port} → zenohd(:{int_port})...")
        self.proxy_status.configure(text=f"代理启动中 (端口 {ext_port})...",
                                    text_color="gray60")
        self.proxy_hint.configure(text="报文显示在右侧抓包区")

        # 在线程中运行代理（不回产生子进程）
        self.proxy_server = ProxyServer(
            ext_port=ext_port,
            int_port=int_port,
            hex_only=self.hex_only_var.get(),
            full_hex=self.full_hex_var.get(),
            log_callback=lambda m: self._append_packet_log(m, "packet"),
        )
        self.proxy_thread = threading.Thread(
            target=self._run_proxy, args=(ext_port, int_port), daemon=True)
        self.proxy_thread.start()
        self.after(500, lambda: self._check_proxy_started(ext_port))

    def _run_proxy(self, ext_port, int_port):
        """在线程中运行 ProxyServer，异常捕获后输出"""
        try:
            self.proxy_server.run()
        except Exception as e:
            import traceback
            self.after(0, lambda: self.log_error(
                f"代理异常: {e}\n{traceback.format_exc()}"))
            self.after(0, lambda: self.proxy_status.configure(
                text="代理已停止 (异常)", text_color="#d1242f"))

    def _check_proxy_started(self, ext_port):
        """轮询检查代理端口是否就绪"""
        # 线程可能已退出（异常）
        if not self.proxy_thread.is_alive():
            self.proxy_status.configure(text="代理已停止", text_color="#d1242f")
            self.proxy_thread = None
            return
        # 检查端口是否就绪
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            result = s.connect_ex(("127.0.0.1", int(ext_port)))
            s.close()
            if result == 0:
                self.log_success(f"代理运行中 (端口 {ext_port}) ✓")
                self.proxy_status.configure(text=f"代理运行中 (端口 {ext_port})",
                                            text_color="#1a7f37")
                return
        except OSError:
            pass
        # 继续轮询，最多 10 秒
        if not hasattr(self, "_proxy_retry"):
            self._proxy_retry = 0
        self._proxy_retry += 1
        if self._proxy_retry < 20:
            self.after(500, lambda: self._check_proxy_started(ext_port))
        else:
            self._proxy_retry = 0
            self.log_error("代理端口未就绪，请检查下方日志")
            self.proxy_status.configure(text="代理启动超时", text_color="#d1242f")

    def on_proxy_stop(self):
        if not getattr(self, "proxy_thread", None) or not self.proxy_thread.is_alive():
            self.log_info("代理未在运行")
            self.proxy_status.configure(text="代理已停止", text_color="gray60")
            return
        self.log_info("正在停止代理...")
        # 停止代理服务器（由回调在主线程安全触发）
        try:
            self.proxy_server.stop()
        except Exception as e:
            self.log_error(f"停止代理异常: {e}")
        self.proxy_status.configure(text="代理已停止", text_color="gray60")
        self.proxy_thread = None

    # ==================== 关闭处理 ====================
    def on_close(self):
        # 清理 trace 进程
        if self.trace_process:
            try:
                ZenohdManager.stop_pid(self.trace_process.pid)
            except Exception:
                pass
            self.trace_process = None
        # 清理代理
        try:
            self.on_proxy_stop()
        except Exception:
            pass
        self.destroy()


def main():
    app = ZenohGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()