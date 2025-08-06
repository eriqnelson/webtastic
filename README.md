# Webtastic v0.1

**Project Title:** Webtastic v0.1  
**Objective:** Build a proof-of-concept offline webserver that delivers HTML content over a LoRa mesh network using the Meshtastic protocol. Pages are requested by client nodes and rendered using a text-based browser, simulating HTTP-style behavior without TCP/IP or internet access.

---

# Functional Overview:

* Use Meshtastic firmware and protocol to create a mesh of LoRa-connected nodes  
* Implement a lightweight, HTTP-like application-layer protocol over Meshtastic JSON messages  
* Serve static HTML files stored locally on a Raspberry Pi to client ESP32 LoRa nodes  
* Clients reassemble HTML files and render them using a real HTML interpreter (Lynx or w3m)

---

# Hardware Specification (First Article Build):

**Server Node**

* Raspberry Pi Zero 2 W or Raspberry Pi 4  
* RAK4631 or similar LoRa dev board (connected via UART/SPI/USB)  
* 16GB MicroSD card

**Client Node**

* Heltec WiFi LoRa 32 V3 (ESP32 \+ OLED display)  
* Optional: button input, microSD card (if local file storage is used)

**Shared Components**

* 915 MHz antennas (US region)  
* USB power banks or LiPo batteries (minimum 2000 mAh)  
* Optional: waterproof enclosures, USB-to-serial adapter for debugging

---

# Document & Protocol Specification

**Transport Layer**: Raw LoRa over Meshtastic mesh protocol  
**Application Protocol**: Custom JSON message format that mimics HTTP 1.0  
**HTML Format**: HTML 4.01 Transitional subset, text only, UTF-8  
**Allowed Tags**: `<html>`, `<head>`, `<title>`, `<body>`, `<h1>`, `<p>`, `<a>`, `<pre>`, `<br>`  
**Browser Requirement**: Lynx or w3m running on client-side terminal, shell, or serial interface

**Message Example (Request)**

{  
  "dest": "server\_node\_id",  
  "type": "custom:webreq",  
  "payload": {  
    "method": "GET",  
    "path": "/index.html",  
    "accept": "text/html",  
    "httpVersion": "1.0"  
  }  
}

**Message Example (Response Chunk)**

{  
  "dest": "client\_node\_id",  
  "type": "custom:webchunk",  
  "payload": {  
    "status": 200,  
    "contentType": "text/html",  
    "part": 1,  
    "total": 3,  
    "httpVersion": "1.0",  
    "body": "\<html\>\<head\>\<title\>Hello\</title\>\</head\>"  
  }  
}

---

# Project Phases

## Phase 1: Environment Setup

1. Flash Meshtastic firmware to RAK4631 and Heltec ESP32  
2. Set up Meshtastic CLI tools on development machines  
3. Establish working mesh communication between devices  
4. Install Python 3, Meshtastic Python API, and dependencies on Raspberry Pi  
5. Create initial `pages/index.html` file on server

**Deliverables:**

* Working Meshtastic mesh with serial or MQTT control  
* Confirmed device addressing and peer visibility

## Phase 2: Core Messaging Protocol

1. Define JSON request/response schema  
2. Implement GET message structure on client  
3. Build server-side listener that parses GET requests  
4. Chunk HTML files into 200-byte payloads  
5. Transmit chunks using ordered Meshtastic messages  
6. On client: receive, store, and reassemble chunks in correct order

**Deliverables:**

* Successful round-trip message flow (GET → CHUNK\[\])  
* HTML file fully reassembled in buffer

## Phase 3: Basic Web Simulation

1. Save reconstructed HTML to file on client device  
2. Render using text-based browser (Lynx or w3m)  
3. Validate page displays without HTML syntax errors  
4. Implement a second page (e.g., help.html) and add hyperlink navigation

**Deliverables:**

* Text-rendered HTML file with header, paragraph, and links  
* Navigation to secondary page confirmed

## 

## Phase 4: POST Simulation and Logging

1. Define `POST` message structure for form data  
2. Implement input mechanism (button, serial prompt) to send POST  
3. Log received POST payloads on server in JSON format  
4. Send confirmation page as HTML response to POST

**Deliverables:**

* Form data received and logged  
* Confirmation page rendered via Lynx

## Phase 5: Mesh Validation and Field Test

1. Add third node (optional repeater) to test mesh routing  
2. Place client at increasing distance to test delivery range and delay  
3. Test multiple clients requesting content concurrently  
4. Validate multi-hop success and throughput limits
5. Run tests for differing LoRa modem presets

| Preset | Bandwidth (kHz) | SF | Data Rate (kbps) | Link Budget | Best For |
| :---- | :---- | :---- | ----: | :---- | :---- |
| LongFast | 250 | 11 | 1.07 | 153dB | Default |
| MediumSlow | 250 | 10 | 1.95 | 150.5dB | Better speed |
| MediumFast | 250 | 9 | 3.52 | 148dB | Fast with good range |
| ShortSlow | 250 | 8 | 6.25 | 145.5dB | Fast with moderate range |
| ShortFast | 250 | 7 | 10.94 | 143dB | Very fast, shorter range |
| ShortTurbo | 500 | 7 | 21.88 | 140dB | Maximum speed, minimum range |

**Deliverables:**

* Measured transmission times and success rates per preset 
* Field test logs for each test condition

## Phase 6: Packaging and Deployment

1. Mount server and client devices in portable cases  
2. Install power supply for 4–6 hour runtime\\  
3. Design field demonstration including:  
   1. Request from client  
   2. Chunked delivery  
   3. Reassembly  
   4. Page rendering

**Deliverables:**

* Complete, portable mesh webserver prototype  
* Demonstration script and evaluation checklist

## Phase 7: Encrypted Content Storage (Encryption-at-Rest)

1. Evaluate lightweight encryption libraries (e.g., AES-CTR, XSalsa20)  
2. Implement file encryption for stored HTML on server and client  
3. Implement secure key distribution or key pre-sharing mechanism  
4. Encrypt/decrypt chunked payloads before transmit/after receive  
5. Ensure compatibility with real-time reassembly and HTML rendering  
6. Simulate compromised/malicious node behavior (e.g., injection, replay)  
7. Implement signature validation or HMAC for message integrity

**Deliverables:**

* Encrypted storage of HTML content on server and client  
* Message integrity checks with HMAC or public-key signature  
* Demonstrated protection against poisoned or malicious nodes

## Phase 8: Protocol-Level Caching and Delta Optimization

1. Define hash-based identifier (e.g., SHA-256) for each HTML file  
2. Add `If-None-Match`\-like field to GET request with known hash or last modified timestamp  
3. On server, compare request hash with current file hash  
4. If unchanged, return `304 Not Modified`\-style response with zero payload  
5. On change, send full content with updated hash metadata  
6. Implement server-side file timestamp tracking and optional hash index  
7. Evaluate impact on network bandwidth, latency, and power consumption

**Deliverables:**

* Delta-aware GET request capability  
* Reduced traffic during repeat queries  
* Full hash tracking for cache validation

---

**Success Criteria**

* HTML content is delivered via LoRa-only channel, without Wi-Fi or internet  
* Client device renders reassembled HTML in Lynx or w3m with valid structure  
* Project simulates basic HTTP-style GET and POST interactions using structured JSON  
* Multi-hop routing is supported and validated within Meshtastic mesh network  
* HTML content is stored and transmitted using encryption-at-rest mechanisms  
* Poison-pill or compromised node does not compromise content integrity or confidentiality  
* Server-side content is validated by hash and cache-aware queries reduce traffic

---

# Next Steps

* Build Python server script for Meshtastic message listening and file chunking  
* Create minimal ESP32 firmware to send GET, receive CHUNK, and save to file  
* Select HTML test content and prepare pages  
* Build demonstration script for field test  
* Implement symmetric and/or asymmetric encryption support for stored and transmitted content  
* Add client-side support for caching metadata and server-side content hash indexing

