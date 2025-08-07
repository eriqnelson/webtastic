# **MiniHTTP v0.1 — Simplified Mesh Web Transfer Protocol**

A method for requesting a single HTML file from a server over the Meshtastic application interface. Note: JSON is too bloated for ultra low bandwidth like this system. This will need to be refactored using protobufs for anything but this first round of proofs.

---

## **Transport Layer**

* **Protocol**: Meshtastic

* **Transmission Field**: text (Meshtastic JSON field)

* **Encoding**: UTF-8 JSON string

* **Max Payload**: 200 bytes per message (including envelope and data)

---

## **1\. Message Envelope Specification**

All MiniHTTP messages follow a consistent JSON envelope structure.

### **Envelope Format**

{  
  "type": "GET" | "RESP",  
  "path": "/filename.html",   // required for both types  
  "frag": 1,                  // required for RESP only  
  "of\_frag": 3,               // required for RESP only  
  "data": "\<html chunk\>"      // required for RESP only  
}

### **GET Request Example**

{  
  "type": "GET",  
  "path": "/about.html"  
}

* The client sends this as a single message.

* No fragment information is included.

### **RESP Fragment Example**

{  
  "type": "RESP",  
  "path": "/about.html",  
  "frag": 3,  
  "of\_frag": 4,  
  "data": "\<footer\>Contact us offline.\</footer\>\\n  \</body\>\\n\</html\>"  
}

* Fragment 3 of 4

* data contains exactly 122 bytes (or less) of UTF-8 encoded HTML content

---

## **2\. Payload and Field Size Limits**

To ensure reliable and consistent fragmentation, field lengths are bounded and the JSON envelope is treated as having a fixed overhead of 78 bytes.

| Field | Max Length (Bytes) | Notes |
| ----- | ----- | ----- |
| type | 4 | “GET” or “RESP” |
| path | 12 | Example: “/about.html” |
| frag | 2 | Supports up to 99 fragments |
| of\_frag | 2 | Supports up to 99 fragments |
| JSON framing | 58 | Quotes, colons, braces, commas |
| data | 122 | Max content bytes per RESP |

*   
  Always limit the data field to 122 bytes, regardless of actual envelope field lengths

* Do not rely on dynamic measurement of envelope size during fragmentation

* All messages must be UTF-8 encoded

---

## **3\. Server Response Behavior**

Upon receiving a GET request, the server performs the following steps:

### **Server Procedure**

1. Parse and validate the GET request

2. Look up and read the contents of /filename.html

3. Split the file into 122-byte UTF-8 chunks

4. For each chunk:

   * Construct a RESP message using the fixed envelope format

   * Set:

     * type: “RESP”

     * path: same as request

     * frag: current fragment number (1-based)

     * of\_frag: total number of fragments

     * data: 122-byte chunk

5. Send each fragment as a separate Meshtastic message

---

## **4\. Full Example Response**

Given a file /about.html with 480 bytes of content, the server will return the following fragments:

### **Fragment 1 of 4**

{  
  "type": "RESP",  
  "path": "/about.html",  
  "frag": 1,  
  "of\_frag": 4,  
  "data": "\<html\>\\n  \<head\>\<title\>About\</title\>\</head\>\\n  \<body\>\\n    \<h1\>Welcome"  
}

### **Fragment 2 of 4**

{  
  "type": "RESP",  
  "path": "/about.html",  
  "frag": 2,  
  "of\_frag": 4,  
  "data": " to the Mesh\!\</h1\>\\n    \<p\>This is the offline about page served over"  
}

### **Fragment 3 of 4**

{  
  "type": "RESP",  
  "path": "/about.html",  
  "frag": 3,  
  "of\_frag": 4,  
  "data": " LoRa. It is designed to demonstrate MiniHTTP running on Meshtastic.\</p\>"  
}

### **Fragment 4 of 4**

{  
  "type": "RESP",  
  "path": "/about.html",  
  "frag": 4,  
  "of\_frag": 4,  
  "data": "\<footer\>Contact us offline.\</footer\>\\n  \</body\>\\n\</html\>"  
}