#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zenoh host test script (for ESP32 integration testing)

Usage:
    python host_zenoh_test.py sub              # subscribe to ESP32
    python host_zenoh_test.py pub "Hello PC"   # publish to ESP32
    python host_zenoh_test.py get              # query ESP32
"""
import sys
import time

import zenoh as z

SUB_KEY = "zenoh/esp32/**"
PUB_KEY = "zenoh/esp32/test"
QUERY_KEY = "zenoh/esp32/query"


def on_sample(sample):
    print(f"<< [SUB] Received ({sample.key_expr}, {sample.payload.to_string()})")


def run_sub():
    session = z.open(z.Config())
    print(f"[SUB] Subscribing to {SUB_KEY} ... (Ctrl+C to quit)")
    sub = session.declare_subscriber(SUB_KEY, on_sample)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sub.undeclare()
        session.close()


def run_pub(msg):
    session = z.open(z.Config())
    pub = session.declare_publisher(PUB_KEY)
    print(f"[PUB] Publishing to {PUB_KEY} : {msg}")
    pub.put(msg)
    print("Done! ESP32 should show the received message.")
    pub.undeclare()
    session.close()


def run_get():
    session = z.open(z.Config())
    print(f"[GET] Querying {QUERY_KEY} ...")
    replies = session.get(QUERY_KEY, timeout=3.0)
    for reply in replies:
        try:
            sample = reply.ok
            print(f"<< [GET] Reply ({sample.key_expr}, {sample.payload.to_string()})")
        except Exception:
            print(f"<< [GET] Error: {reply.err.payload.to_string()}")
    session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "sub":
        run_sub()
    elif cmd == "pub":
        msg = sys.argv[2] if len(sys.argv) > 2 else "Hello from PC!"
        run_pub(msg)
    elif cmd == "get":
        run_get()
    else:
        print(__doc__)
        sys.exit(1)
