// ============================================================
// Zenoh-Pico ESP32 综合测试程序
// ============================================================
// 功能 (通过串口菜单选择):
//   1. PUB  — 定时发布消息
//   2. SUB  — 订阅主题
//   3. QUERYABLE — 响应远程查询
//   4. GET  — 发送远程查询并等待回复
//   5. INFO — 查看当前连接状态
// ============================================================

#include <Arduino.h>
#include <WiFi.h>
#include <zenoh-pico.h>

#include "config.h"

// ============================================================
// 全局变量
// ============================================================
z_owned_session_t g_session;
bool g_session_open = false;
int g_pub_count = 0;

// ============================================================
// WiFi 连接
// ============================================================
static void wifi_connect() {
    Serial.print("Connecting to WiFi [");
    Serial.print(WIFI_SSID);
    Serial.print("] ...");

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int dots = 0;
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        dots++;
        if (dots % 40 == 0) {
            Serial.println();
            Serial.print("Still trying...");
        }
    }
    Serial.println(" OK");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
}

// ============================================================
// Zenoh 会话管理
// ============================================================
static bool zenoh_open() {
    if (g_session_open) {
        Serial.println("Session already open.");
        return true;
    }

    z_owned_config_t config;
    z_config_default(&config);

    // 设置模式
    zp_config_insert(z_config_loan_mut(&config), Z_CONFIG_MODE_KEY, ZENOH_MODE);

    // 设置连接端点
    if (strcmp(ZENOH_MODE, "client") == 0) {
        zp_config_insert(z_config_loan_mut(&config), Z_CONFIG_CONNECT_KEY, ZENOH_LOCATOR);
    } else {
        zp_config_insert(z_config_loan_mut(&config), Z_CONFIG_LISTEN_KEY, ZENOH_LOCATOR);
    }

    Serial.print("Opening Zenoh session (");
    Serial.print(ZENOH_MODE);
    Serial.print(" @ ");
    Serial.print(ZENOH_LOCATOR);
    Serial.print(") ...");

    if (z_open(&g_session, z_config_move(&config), NULL) < 0) {
        Serial.println(" FAILED!");
        Serial.println("Check router / network connectivity.");
        return false;
    }

    Serial.println(" OK");
    g_session_open = true;
    return true;
}

static void zenoh_close() {
    if (!g_session_open) return;
    Serial.print("Closing Zenoh session ...");
    z_session_drop(z_session_move(&g_session));
    g_session_open = false;
    Serial.println(" OK");
}

// ============================================================
// 1) PUB — 发布测试
// ============================================================
static void run_publisher() {
    if (!g_session_open && !zenoh_open()) return;

    Serial.println("\n=== PUBLISHER MODE ===");

    z_owned_publisher_t pub;
    z_view_keyexpr_t ke;

    z_view_keyexpr_from_str_unchecked(&ke, ZENOH_PUB_KEY);
    if (z_declare_publisher(z_session_loan(&g_session), &pub, z_view_keyexpr_loan(&ke), NULL) < 0) {
        Serial.println("ERROR: Failed to declare publisher!");
        return;
    }
    Serial.print("Publishing on: ");
    Serial.println(ZENOH_PUB_KEY);
    Serial.println("Publishing every 3 seconds. Press any key in serial to stop.\n");

    g_pub_count = 0;
    unsigned long last_pub = 0;

    // 非阻塞发布循环，按任意键退出
    while (true) {
        // 检查串口是否有输入（退出条件）
        if (Serial.available() > 0) {
            while (Serial.available()) Serial.read();  // 清空缓冲区
            Serial.println("\nStopping publisher...");
            break;
        }

        unsigned long now = millis();
        if (now - last_pub >= 3000) {
            last_pub = now;
            char buf[256];
            snprintf(buf, sizeof(buf),
                     "[ESP32] Hello from Zenoh-Pico! count=%d uptime=%lus",
                     g_pub_count++, millis() / 1000);

            z_owned_bytes_t payload;
            z_bytes_copy_from_str(&payload, buf);

            Serial.print("Put: ");
            Serial.println(buf);

            if (z_publisher_put(z_publisher_loan(&pub), z_bytes_move(&payload), NULL) < 0) {
                Serial.println("ERROR: publish failed!");
            }
        }
    }

    z_undeclare_publisher(z_publisher_move(&pub));
}

// ============================================================
// 2) SUB — 订阅回调
// ============================================================
static void sub_data_handler(z_loaned_sample_t *sample, void *arg) {
    (void)arg;

    z_view_string_t keystr;
    z_keyexpr_as_view_string(z_sample_keyexpr(sample), &keystr);

    z_owned_string_t value;
    z_bytes_to_string(z_sample_payload(sample), &value);

    Serial.print("<< [SUB] Received (");
    Serial.write(z_string_data(z_view_string_loan(&keystr)),
                 z_string_len(z_view_string_loan(&keystr)));
    Serial.print(", ");
    Serial.write(z_string_data(z_string_loan(&value)),
                 z_string_len(z_string_loan(&value)));
    Serial.println(")");

    z_string_drop(z_string_move(&value));
}

static void run_subscriber() {
    if (!g_session_open && !zenoh_open()) return;

    Serial.println("\n=== SUBSCRIBER MODE ===");

    z_owned_subscriber_t sub;
    z_owned_closure_sample_t callback;
    z_view_keyexpr_t ke;

    z_closure_sample(&callback, sub_data_handler, NULL, NULL);
    z_view_keyexpr_from_str_unchecked(&ke, ZENOH_SUB_KEY);

    if (z_declare_subscriber(z_session_loan(&g_session), &sub,
                             z_view_keyexpr_loan(&ke),
                             z_closure_sample_move(&callback), NULL) < 0) {
        Serial.println("ERROR: Failed to declare subscriber!");
        return;
    }
    Serial.print("Subscribing to: ");
    Serial.println(ZENOH_SUB_KEY);
    Serial.println("Waiting for messages. Press any key to stop.\n");

    // 等待，按任意键退出
    while (true) {
        if (Serial.available() > 0) {
            while (Serial.available()) Serial.read();
            Serial.println("\nStopping subscriber...");
            break;
        }
        delay(100);
    }

    z_undeclare_subscriber(z_subscriber_move(&sub));
}

// ============================================================
// 3) QUERYABLE — 响应远程查询
// ============================================================
static void queryable_handler(z_loaned_query_t *query, void *arg) {
    (void)arg;

    z_view_string_t keystr;
    z_keyexpr_as_view_string(z_query_keyexpr(query), &keystr);

    z_view_string_t params;
    z_query_parameters(query, &params);

    Serial.print("<< [Queryable] Received query on (");
    Serial.write(z_string_data(z_view_string_loan(&keystr)),
                 z_string_len(z_view_string_loan(&keystr)));
    if (z_string_len(z_view_string_loan(&params)) > 0) {
        Serial.print(") with params (");
        Serial.write(z_string_data(z_view_string_loan(&params)),
                     z_string_len(z_view_string_loan(&params)));
    }
    Serial.println(")");

    // 回复查询
    char buf[256];
    snprintf(buf, sizeof(buf),
             "[ESP32] Queryable reply! uptime=%lus free_heap=%u",
             millis() / 1000, esp_get_free_heap_size());

    z_owned_bytes_t payload;
    z_bytes_copy_from_str(&payload, buf);

    z_query_reply_options_t options;
    z_query_reply_options_default(&options);
    z_query_reply(query, z_query_keyexpr(query), z_bytes_move(&payload), &options);
}

static void run_queryable() {
    if (!g_session_open && !zenoh_open()) return;

    Serial.println("\n=== QUERYABLE MODE ===");

    z_owned_queryable_t qable;
    z_owned_closure_query_t callback;
    z_view_keyexpr_t ke;

    z_closure_query(&callback, queryable_handler, NULL, NULL);
    z_view_keyexpr_from_str_unchecked(&ke, ZENOH_QUERY_KEY);

    if (z_declare_queryable(z_session_loan(&g_session), &qable,
                            z_view_keyexpr_loan(&ke),
                            z_closure_query_move(&callback), NULL) < 0) {
        Serial.println("ERROR: Failed to declare queryable!");
        return;
    }
    Serial.print("Queryable on: ");
    Serial.println(ZENOH_QUERY_KEY);
    Serial.println("Waiting for queries. Press any key to stop.\n");

    while (true) {
        if (Serial.available() > 0) {
            while (Serial.available()) Serial.read();
            Serial.println("\nStopping queryable...");
            break;
        }
        delay(100);
    }

    z_undeclare_queryable(z_queryable_move(&qable));
}

// ============================================================
// 4) GET — 发送远程查询
// ============================================================
static void get_reply_handler(z_loaned_reply_t *reply, void *arg) {
    (void)arg;

    if (z_reply_is_ok(reply)) {
        const z_loaned_sample_t *sample = z_reply_ok(reply);

        z_view_string_t keystr;
        z_keyexpr_as_view_string(z_sample_keyexpr(sample), &keystr);
        z_owned_string_t value;
        z_bytes_to_string(z_sample_payload(sample), &value);

        Serial.print("<< [GET] Reply (");
        Serial.write(z_string_data(z_view_string_loan(&keystr)),
                     z_string_len(z_view_string_loan(&keystr)));
        Serial.print(", ");
        Serial.write(z_string_data(z_string_loan(&value)),
                     z_string_len(z_string_loan(&value)));
        Serial.println(")");

        z_string_drop(z_string_move(&value));
    } else {
        Serial.println("<< [GET] Reply: FAILED");
    }
}

static void run_get() {
    if (!g_session_open && !zenoh_open()) return;

    Serial.println("\n=== GET MODE ===");

    z_owned_closure_reply_t callback;
    z_closure_reply(&callback, get_reply_handler, NULL, NULL);

    z_view_keyexpr_t ke;
    z_view_keyexpr_from_str_unchecked(&ke, ZENOH_QUERY_KEY);

    Serial.print("Querying: ");
    Serial.println(ZENOH_QUERY_KEY);
    Serial.println("Waiting for replies (3s timeout). Press any key to stop.\n");

    z_get_options_t opts;
    z_get_options_default(&opts);
    opts.timeout_ms = 3000;

    if (z_get(z_session_loan(&g_session), z_view_keyexpr_loan(&ke),
              "", z_closure_reply_move(&callback), &opts) < 0) {
        Serial.println("ERROR: Failed to send get!");
        return;
    }

    // 等待回复（最多 4 秒，因为设置了 3 秒超时）
    unsigned long start = millis();
    while (millis() - start < 4000) {
        if (Serial.available() > 0) {
            while (Serial.available()) Serial.read();
            break;
        }
        delay(50);
    }

    Serial.println("\nGet finished.");
}

// ============================================================
// 5) INFO — 显示状态信息
// ============================================================
static void show_info() {
    Serial.println("\n=== SYSTEM INFO ===");
    Serial.print("WiFi SSID:       "); Serial.println(WIFI_SSID);
    Serial.print("WiFi status:     ");
    Serial.println(WiFi.status() == WL_CONNECTED ? "Connected" : "Disconnected");
    Serial.print("IP address:      ");
    Serial.println(WiFi.localIP());
    Serial.print("Zenoh mode:      "); Serial.println(ZENOH_MODE);
    Serial.print("Zenoh locator:   "); Serial.println(ZENOH_LOCATOR);
    Serial.print("Zenoh session:   ");
    Serial.println(g_session_open ? "Open" : "Closed");
    Serial.print("Free heap:       ");
    Serial.print(esp_get_free_heap_size());
    Serial.println(" bytes");
    Serial.print("Uptime:          ");
    Serial.print(millis() / 1000);
    Serial.println(" s");
    Serial.print("ESP32 chip:      ");
    Serial.println(ESP.getChipModel());
    Serial.print("Chip revision:   ");
    Serial.println(ESP.getChipRevision());
    Serial.print("Flash size:      ");
    Serial.print(ESP.getFlashChipSize() / (1024 * 1024));
    Serial.println(" MB");
    Serial.println();
}

// ============================================================
// 串口菜单
// ============================================================
static void print_menu() {
    Serial.println("\n==============================================");
    Serial.println("  Zenoh-Pico ESP32 Test Program");
    Serial.println("==============================================");
    Serial.println("  [1] PUB  — 发布测试消息 (每 3 秒)");
    Serial.println("  [2] SUB  — 订阅消息");
    Serial.println("  [3] QUERYABLE — 响应远程查询");
    Serial.println("  [4] GET  — 发送远程查询");
    Serial.println("  [5] INFO — 查看状态信息");
    Serial.println("  [O] OPEN — 打开 Zenoh 会话");
    Serial.println("  [C] CLOSE — 关闭 Zenoh 会话");
    Serial.println("  [M] MENU — 重新显示菜单");
    Serial.println("==============================================");
    Serial.print("  Choice: ");
}

// ============================================================
// 主程序
// ============================================================
void setup() {
    Serial.begin(115200);
    // 等待串口连接（对 USB 原生串口有用）
    delay(500);
    Serial.println();
    Serial.println();
    Serial.println("==============================================");
    Serial.println("  Zenoh-Pico ESP32  Test Program");
    Serial.println("  Version 1.0");
    Serial.println("==============================================");

    // 连接 WiFi
    wifi_connect();

    // 显示菜单
    print_menu();
}

void loop() {
    if (Serial.available() > 0) {
        char c = Serial.read();

        // 清除换行符
        if (c == '\r' || c == '\n') return;

        Serial.println(c);
        Serial.println();

        switch (c) {
            case '1':
                run_publisher();
                break;
            case '2':
                run_subscriber();
                break;
            case '3':
                run_queryable();
                break;
            case '4':
                run_get();
                break;
            case '5':
                show_info();
                break;
            case 'O':
            case 'o':
                zenoh_open();
                break;
            case 'C':
            case 'c':
                zenoh_close();
                break;
            case 'M':
            case 'm':
                print_menu();
                return;  // 不打印额外的换行
            default:
                Serial.print("Unknown command: ");
                Serial.println(c);
                break;
        }

        // 重新显示菜单
        print_menu();
    }

    // 让 ESP32 低功耗空闲
    delay(50);
}