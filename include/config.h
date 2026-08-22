// ============================================================
// WiFi & Zenoh Configuration
// ============================================================
// Edit the values below to match your network environment.
// Then build and upload to the ESP32.
// ============================================================

#ifndef CONFIG_H
#define CONFIG_H

// --------------- WiFi Settings ---------------
#define WIFI_SSID       "AORANGE"
#define WIFI_PASSWORD   "15651111878"

// --------------- Zenoh Mode ---------------
// Choose ONE of the two modes below (comment/uncomment accordingly):

// Mode 1: Client mode — connect to a Zenoh router (zenohd)
// #define ZENOH_MODE      "client"
// #define ZENOH_LOCATOR   "tcp/192.168.31.239:7447"   // TCP (reliable)

// Mode 2: Client mode — UDP unicast to Zenoh router
// Note: zenohd must also listen on UDP: -l udp/0.0.0.0:7447
#define ZENOH_MODE      "client"
#define ZENOH_LOCATOR   "udp/192.168.31.239:7447"   // UDP unicast (lower latency, may lose packets)

// Mode 3: Peer mode — use UDP multicast, no router needed
// #define ZENOH_MODE      "peer"
// #define ZENOH_LOCATOR   "udp/224.0.0.225:7447#iface=eth0"  // adjust multicast addr + iface

// --------------- Key Expressions ---------------
// Topic used for publishing
#define ZENOH_PUB_KEY    "zenoh/esp32/test"
// Topic pattern for subscribing
#define ZENOH_SUB_KEY    "zenoh/esp32/**"
// Queryable key
#define ZENOH_QUERY_KEY  "zenoh/esp32/query"

#endif  // CONFIG_H