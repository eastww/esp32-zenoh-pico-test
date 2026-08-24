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
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import customtkinter as ctk

# 设置主题
ctk.set_appearance_mode("dark")
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
    def start(port):
        """后台启动 zenohd，返回 (ok, message)"""
        if ZenohdManager.is_running():
            return True, f"zenohd 已在运行 (PID: {ZenohdManager._find_pid()})"
        if not os.path.exists(ZENOHD_EXE):
            return False, f"未找到 zenohd: {ZENOHD_EXE}"
        try:
            proc = subprocess.Popen(
                [ZENOHD_EXE, "-l", f"tcp/0.0.0.0:{port}"],
                cwd=ZENOHD_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(1)
            if ZenohdManager._check_process(proc.pid):
                with open(PID_FILE, "w") as f:
                    f.write(str(proc.pid))
                return True, f"zenohd 已启动 (PID: {proc.pid}, 端口 {port})"
            return False, "zenohd 启动失败"
        except Exception as e:
            return False, f"启动异常: {e}"

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

        # ============ 布局 ============
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # 左: 控制区
        self.control_frame = ctk.CTkFrame(self, corner_radius=12)
        self.control_frame.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        self.control_frame.grid_columnconfigure(0, weight=1)
        self._build_control_area()

        # 右: 日志区
        self.log_frame = ctk.CTkFrame(self, corner_radius=12)
        self.log_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        self.log_frame.grid_rowconfigure(1, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)
        self._build_log_area()

        # 启动时刷新状态
        self.after(500, self.refresh_status)

    # ==================== 控制区 ====================
    def _build_control_area(self):
        # ---- 标题 ----
        title = ctk.CTkLabel(
            self.control_frame, text="Zenoh ESP32 测试工具",
            font=ctk.CTkFont(size=22, weight="bold")
        )
        title.grid(row=0, column=0, pady=(16, 4))

        subtitle = ctk.CTkLabel(
            self.control_frame, text="图形化界面 · v1.0",
            font=ctk.CTkFont(size=12),
            text_color="gray60"
        )
        subtitle.grid(row=1, column=0, pady=(0, 12))

        # ---- 1. 路由器管理 ----
        self._section_label("① 路由器管理 (zenohd)").grid(row=2, column=0, sticky="w", padx=16, pady=(12, 4))

        router_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        router_frame.grid(row=3, column=0, sticky="ew", padx=16)
        router_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(router_frame, text="端口:").grid(row=0, column=0, sticky="w")
        self.port_entry = ctk.CTkEntry(router_frame, width=80, placeholder_text="7447")
        self.port_entry.grid(row=0, column=1, sticky="w", padx=6)
        self.port_entry.insert(0, "7447")

        self.router_status = ctk.CTkLabel(router_frame, text="● 未知", text_color="orange")
        self.router_status.grid(row=0, column=2, sticky="e", padx=6)

        btns = ctk.CTkFrame(router_frame, fg_color="transparent")
        btns.grid(row=1, column=0, columnspan=3, pady=(8, 2), sticky="ew")
        btns.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(btns, text="▶ 启动", command=self.on_start,
                      fg_color="#2e7d32", hover_color="#388e3c").grid(row=0, column=0, padx=3)
        ctk.CTkButton(btns, text="■ 停止", command=self.on_stop,
                      fg_color="#c62828", hover_color="#d32f2f").grid(row=0, column=1, padx=3)
        ctk.CTkButton(btns, text="⟳ 刷新", command=self.refresh_status,
                      fg_color="gray40", hover_color="gray50").grid(row=0, column=2, padx=3)

        # ---- 2. 主题配置 ----
        self._section_label("② 主题 (Key Expression)").grid(row=4, column=0, sticky="w", padx=16, pady=(16, 4))

        ke_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        ke_frame.grid(row=5, column=0, sticky="ew", padx=16)
        ke_frame.grid_columnconfigure(1, weight=1)

        labels = ["发布主题:", "订阅主题:", "查询主题:", "Router端点:"]
        defaults = ["zenoh/esp32/test", "zenoh/esp32/**", "zenoh/esp32/query", "tcp/127.0.0.1:7447"]

        self.ke_vars = {}
        for i, (label, default) in enumerate(zip(labels, defaults)):
            ctk.CTkLabel(ke_frame, text=label, width=80, anchor="w").grid(
                row=i, column=0, sticky="w", pady=3)
            var = ctk.StringVar(value=default)
            entry = ctk.CTkEntry(ke_frame, textvariable=var)
            entry.grid(row=i, column=1, sticky="ew", pady=3, padx=6)
            self.ke_vars[label] = var

        # ---- 3. 测试操作 ----
        self._section_label("③ 通信测试").grid(row=6, column=0, sticky="w", padx=16, pady=(16, 4))

        test_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        test_frame.grid(row=7, column=0, sticky="ew", padx=16)
        test_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(test_frame, text="📤 发布", command=self.on_pub,
                      fg_color="#1565c0", hover_color="#1976d2").grid(row=0, column=0, padx=3, pady=3, sticky="ew")
        ctk.CTkButton(test_frame, text="📥 订阅", command=self.on_sub,
                      fg_color="#00695c", hover_color="#00897b").grid(row=0, column=1, padx=3, pady=3, sticky="ew")
        ctk.CTkButton(test_frame, text="🔍 查询", command=self.on_get,
                      fg_color="#4527a0", hover_color="#5e35b1").grid(row=1, column=0, padx=3, pady=3, sticky="ew")
        ctk.CTkButton(test_frame, text="🧪 全部测试", command=self.on_all_test,
                      fg_color="#880e4f", hover_color="#ad1457").grid(row=1, column=1, padx=3, pady=3, sticky="ew")

        # 发布消息内容
        ctk.CTkLabel(test_frame, text="消息内容:", anchor="w").grid(
            row=2, column=0, sticky="w", pady=(8, 0))
        self.pub_msg_entry = ctk.CTkEntry(
            test_frame, placeholder_text="Hello from PC!")
        self.pub_msg_entry.grid(row=2, column=1, sticky="ew", padx=3, pady=(8, 0))
        self.pub_msg_entry.insert(0, "Hello ESP32 from GUI!")

        self.pub_status = ctk.CTkLabel(
            test_frame, text="", text_color="gray70", anchor="w", wraplength=380)
        self.pub_status.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)

        # ---- 4. 报文抓包 ----
        self._section_label("④ 报文抓包 (Proxy)").grid(row=8, column=0, sticky="w", padx=16, pady=(16, 4))

        proxy_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        proxy_frame.grid(row=9, column=0, sticky="ew", padx=16)
        proxy_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(proxy_frame, text="代理端口:", anchor="w").grid(row=0, column=0, sticky="w")
        self.proxy_port_entry = ctk.CTkEntry(proxy_frame, width=80, placeholder_text="7447")
        self.proxy_port_entry.grid(row=0, column=1, sticky="w", padx=6)
        self.proxy_port_entry.insert(0, "7447")

        self.hex_only_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(proxy_frame, text="仅 HEX", variable=self.hex_only_var,
                        width=28).grid(row=0, column=2, sticky="e")

        proxy_btns = ctk.CTkFrame(proxy_frame, fg_color="transparent")
        proxy_btns.grid(row=1, column=0, columnspan=3, pady=(8, 0), sticky="ew")
        proxy_btns.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(proxy_btns, text="🔍 启动代理", command=self.on_proxy_start,
                      fg_color="#e65100", hover_color="#ef6c00").grid(row=0, column=0, padx=3)
        ctk.CTkButton(proxy_btns, text="⏹ 停止代理", command=self.on_proxy_stop,
                      fg_color="gray50", hover_color="gray60").grid(row=0, column=1, padx=3)

        self.proxy_status = ctk.CTkLabel(
            proxy_frame, text="", text_color="gray70", anchor="w", wraplength=380)
        self.proxy_status.grid(row=2, column=0, columnspan=3, sticky="ew", pady=4)

        # ---- 底部提示 ----
        footer = ctk.CTkLabel(
            self.control_frame,
            text="提示: 启动代理前请先确保 zenohd 已启动\n"
                 "代理会将 ESP32 的报文透明转发并显示在右侧日志区",
            font=ctk.CTkFont(size=11), text_color="gray50", justify="left"
        )
        footer.grid(row=10, column=0, sticky="w", padx=16, pady=(16, 8))

    def _section_label(self, text):
        return ctk.CTkLabel(
            self.control_frame, text=text,
            font=ctk.CTkFont(size=14, weight="bold"), text_color="#64b5f6"
        )

    # ==================== 日志区 ====================
    def _build_log_area(self):
        log_header = ctk.CTkFrame(self.log_frame, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        log_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(log_header, text="📋 运行日志",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(log_header, text="🗑 清空", width=60, command=self.clear_log,
                      fg_color="gray40", hover_color="gray55").grid(row=0, column=1, sticky="e")

        # 使用 Text 控件 + 自定义配色
        self.log_text = tk.Text(
            self.log_frame, bg="#1e1e1e", fg="#d4d4d4",
            font=("Consolas", 10), wrap="word", state="disabled",
            insertbackground="#d4d4d4", selectbackground="#264f78"
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 8))

        # 滚动条
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(4, 8))
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # 状态栏
        self.status_bar = ctk.CTkLabel(
            self.log_frame, text="就绪", text_color="gray60", anchor="w", height=22
        )
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))

    # ==================== 日志方法 ====================
    def _append_log(self, text, tag="info"):
        """向日志区追加一行（线程安全）"""
        def _do_append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n", tag)
            self.log_text.tag_config("info", foreground="#d4d4d4")
            self.log_text.tag_config("success", foreground="#7ee787")
            self.log_text.tag_config("error", foreground="#ff7b72")
            self.log_text.tag_config("system", foreground="#79c0ff")
            self.log_text.tag_config("packet", foreground="#d2a8ff")
            self.log_text.tag_config("header", foreground="#ffa657")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.after(0, _do_append)

    def log_info(self, msg):
        self._append_log(f"[INFO] {msg}", "info")

    def log_success(self, msg):
        self._append_log(f"[✔] {msg}", "success")

    def log_error(self, msg):
        self._append_log(f"[✘] {msg}", "error")

    def log_system(self, msg):
        self._append_log(f"[SYS] {msg}", "system")

    def log_packet(self, msg):
        self._append_log(f"  {msg}", "packet")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

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
        self.log_system("正在启动 zenohd...")
        self._run_in_thread(self._start_worker)

    def _start_worker(self):
        port = self.port_entry.get().strip() or "7447"
        ok, msg = ZenohdManager.start(port)
        self.after(0, lambda: self._handle_router_result(ok, msg))

    def _handle_router_result(self, ok, msg):
        if ok:
            self.log_success(msg)
            self.refresh_status()
        else:
            self.log_error(msg)

    def on_stop(self):
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
            ok, msg = ZenohdManager.start(port)
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
        if self.proxy_process and self.proxy_process.poll() is None:
            messagebox.showwarning("提示", "代理已在运行")
            return

        # 确保 zenohd 运行
        if not ZenohdManager.is_running():
            port = self.port_entry.get().strip() or "7447"
            ok, msg = ZenohdManager.start(port)
            if not ok:
                messagebox.showerror("错误", f"无法启动 zenohd: {msg}")
                return
            self.log_success(f"路由器: {msg}")

        # 启动代理
        ext_port = self.proxy_port_entry.get().strip() or "7447"
        int_port = str(int(ext_port) + 1)  # 内部端口

        proxy_script = os.path.join(SCRIPT_DIR, "host_zenoh_test.py")
        cmd = [
            sys.executable, proxy_script, "proxy",
            "--port", ext_port,
        ]
        if self.hex_only_var.get():
            cmd.append("--hex-only")

        try:
            self.proxy_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.log_success(f"代理已启动: ESP32 → 127.0.0.1:{ext_port} → zenohd")
            self.proxy_status.configure(text=f"代理运行中 (端口 {ext_port}) ✅",
                                        text_color="#7ee787")
            self._read_proxy_output()
        except Exception as e:
            messagebox.showerror("错误", f"代理启动失败: {e}")

    def _read_proxy_output(self):
        if self.proxy_process is None:
            return
        try:
            line = self.proxy_process.stdout.readline()
            if line:
                self.log_packet(line.rstrip())
        except Exception:
            pass
        if self.proxy_process and self.proxy_process.poll() is None:
            self.after(50, self._read_proxy_output)
        else:
            self.log_info("代理已停止")
            self.proxy_status.configure(text="代理已停止", text_color="#ff7b72")
            self.proxy_process = None

    def on_proxy_stop(self):
        if self.proxy_process and self.proxy_process.poll() is None:
            self.proxy_process.terminate()
            self.log_info("正在停止代理...")
        else:
            self.log_info("代理未在运行")

    # ==================== 关闭处理 ====================
    def on_close(self):
        # 清理子进程
        if self.proxy_process and self.proxy_process.poll() is None:
            self.proxy_process.terminate()
        self.destroy()


def main():
    app = ZenohGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()