// Pure request/reply logic shared by the firmware and the native unit tests
// (test/test_web_logic). Nothing here touches hardware, the web server or
// globals, so it builds on the host against a small Arduino String shim.
// Everything here parses untrusted input (HTTP query strings, upload
// headers, settings files), so keep it bounds-checked and side-effect free.
#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <WString.h>

namespace weblogic {

// Escape a value for embedding in a JSON string literal: quotes and
// backslashes are escaped, control characters are dropped.
inline String jsonEscape(const String& s)
{
  String out;
  out.reserve(s.length() + 4);
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"' || c == '\\') { out += '\\'; out += c; }
    else if ((uint8_t)c >= 0x20) out += c;
  }
  return out;
}

// Index of the ']' closing the JSON array whose '[' is at `open` (or before
// it), skipping brackets inside strings. -1 if unterminated or `open` < 0.
inline int findArrayEnd(const String& json, int open)
{
  if (open < 0) return -1;
  bool inStr = false;
  int depth = 0;
  for (unsigned int i = open; i < json.length(); i++) {
    char c = json[i];
    if (inStr) {
      if (c == '\\') i++;          // skip escaped char
      else if (c == '"') inStr = false;
    } else if (c == '"') inStr = true;
    else if (c == '[') depth++;
    else if (c == ']' && --depth == 0) return i;
  }
  return -1;
}

// Value of `key` in the query string of an HTTP request line
// ("GET /stream?names=a,b&ms=100 HTTP/1.1"). Matches whole keys only, so
// "ms" never picks up "xms=". Empty if absent.
inline String queryParam(const String& reqLine, const char* key)
{
  int q = reqLine.indexOf('?');
  if (q < 0) return "";
  int end = reqLine.indexOf(' ', q);
  if (end < 0) end = reqLine.length();
  String pat = String(key) + "=";
  int i = q + 1;
  while (i < end) {
    if (reqLine.substring(i, i + pat.length()) == pat) {
      int s = i + pat.length();
      int e = s;
      while (e < end && reqLine[e] != '&') e++;
      return reqLine.substring(s, e);
    }
    int amp = reqLine.indexOf('&', i);
    if (amp < 0 || amp >= end) break;
    i = amp + 1;
  }
  return "";
}

// The SSE stream turns its names into a "get" terminal command, so only
// spot-value-name characters are allowed: anything else could smuggle a
// second command or framing onto the UART.
static const unsigned int SSE_NAMES_MAX = 120;
inline bool sseNamesValid(const String& names)
{
  if (names.length() == 0 || names.length() > SSE_NAMES_MAX) return false;
  for (unsigned int i = 0; i < names.length(); i++) {
    unsigned char ch = (unsigned char)names[i];
    bool ok = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') ||
              ch == '_' || ch == ',' || ch == '.' || ch == '-';
    if (!ok) return false;
  }
  return true;
}

// SSE refresh interval: <50 ms (or junk) falls back to 100, capped at 5 s
inline int sseIntervalMs(int ms)
{
  if (ms < 50) return 100;
  if (ms > 5000) return 5000;
  return ms;
}

// SPIFFS paths are rooted; the UI sends bare names
inline String normalizeFsPath(const String& path)
{
  if (path.startsWith("/")) return path;
  return "/" + path;
}

// /cmd repeat count: unclamped values could block the single-threaded
// server for minutes and grow the reply until the heap dies
static const int CMD_REPEAT_MAX = 100;
inline int clampRepeat(int repeat)
{
  if (repeat < 0) return 0;
  if (repeat > CMD_REPEAT_MAX) return CMD_REPEAT_MAX;
  return repeat;
}

// GPIO numbers from settings: garbage would persist and break the link at
// every boot. Required pins fall back to the build default; optional pins
// (allowUnused) accept -1 for "not used" and fall back to it.
static const int GPIO_MAX = 48;
inline int sanitizePin(int pin, int fallback, bool allowUnused = false)
{
  if (allowUnused) return (pin < -1 || pin > GPIO_MAX) ? -1 : pin;
  return (pin < 0 || pin > GPIO_MAX) ? fallback : pin;
}

// Combined OTA image header: "OIWEBOTA" + uint32 LE firmware length +
// uint32 LE filesystem length
static const size_t OTA_HEADER_LEN = 16;
enum OtaHeaderResult { OTA_HDR_OK = 0, OTA_HDR_BAD_MAGIC, OTA_HDR_BAD_LENGTH };
inline OtaHeaderResult parseOtaHeader(const uint8_t* hdr, uint32_t& fwLen, uint32_t& fsLen)
{
  static const char magic[8] = {'O','I','W','E','B','O','T','A'};
  fwLen = fsLen = 0;
  if (memcmp(hdr, magic, 8) != 0) return OTA_HDR_BAD_MAGIC;
  fwLen = (uint32_t)hdr[8] | ((uint32_t)hdr[9] << 8) | ((uint32_t)hdr[10] << 16) | ((uint32_t)hdr[11] << 24);
  fsLen = (uint32_t)hdr[12] | ((uint32_t)hdr[13] << 8) | ((uint32_t)hdr[14] << 16) | ((uint32_t)hdr[15] << 24);
  if (fwLen == 0 || fsLen == 0) return OTA_HDR_BAD_LENGTH;
  return OTA_HDR_OK;
}

// Collect one terminal reply (plus `repeat` repetitions triggered with "!")
// from the inverter UART. Port provides:
//   int  readChunk(char* buf, size_t size, Tick firstByteTimeout)
//        — a chunk that starts within the timeout and ends on line silence;
//          <= 0 if nothing arrived
//   void triggerRepeat()  — send "!" and consume its echo
// The long timeout applies only while waiting for a reply (or repetition)
// to start; once bytes flow, the idle timeout ends it.
template <typename Port, typename Tick>
inline String readReply(Port& port, int repeat, Tick replyTimeout, Tick idleTimeout)
{
  String output;
  char buffer[255];
  int rpt = repeat;
  int len;
  Tick wait = replyTimeout;
  do {
    len = port.readChunk(buffer, sizeof(buffer), wait);
    if (len > 0) output.concat(buffer, len);
    wait = idleTimeout;
    if (rpt) {
      rpt--;
      port.triggerRepeat();
      wait = replyTimeout;
    }
  } while (len > 0);
  return output;
}

} // namespace weblogic
