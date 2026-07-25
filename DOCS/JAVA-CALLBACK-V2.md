# Java Action Layer — callback signature v2 (integration spec)

Audience: the Java Action Layer team. Status: **Python side shipped
(`a54944b`); Java side outstanding.**

> **Replay protection is not active until Java implements this.** The Python
> service already sends everything needed, but v1 (body-only) remains
> authoritative on your side, and a captured v1 request stays valid forever.
> This document is the whole contract — you should not need to read Python.

---

## 1. Why

v1 signs the **body only**:

```
X-VoiceIQ-Signature = HMAC-SHA256(secret, body)
```

Nothing in the signed material expires, so anyone who captures one request
can resend it indefinitely and it verifies. v2 binds the signature to a
timestamp you can range-check.

---

## 2. Headers sent on every callback

| Header | Example | Notes |
|---|---|---|
| `Content-Type` | `application/json` | body is UTF-8 JSON |
| `X-VoiceIQ-Service` | `python-agent-brain` | constant |
| `X-VoiceIQ-Signature` | `9f86d0…` | **v1, legacy** — body only |
| `X-VoiceIQ-Signature-V2` | `4c8a2b…` | **v2** — timestamp-bound |
| `X-VoiceIQ-Timestamp` | `1769342461` | **Unix seconds**, decimal string, UTC |
| `X-VoiceIQ-Trace-Id` | `9b1f…` | per-send id, use for de-duplication |

Both signatures are lowercase hex SHA-256 (64 chars).

---

## 3. The exact signed material

**This is the part to get byte-exact.**

```
v1:  HMAC-SHA256(secret, body)
v2:  HMAC-SHA256(secret, timestamp || "." || body)
```

where:

- `secret` — the shared secret, UTF-8 encoded (Python: `callback_secret`)
- `timestamp` — the **exact ASCII string** from `X-VoiceIQ-Timestamp`. Do not
  re-serialise it, do not parse-and-reformat it, do not zero-pad.
- `"."` — a single literal ASCII period (0x2E)
- `body` — the **raw request bytes as received**. Do **not** pretty-print,
  re-serialise, reorder keys, or parse-then-re-encode the JSON. Any of those
  changes the bytes and the HMAC will not match.

Reference (Python, authoritative — mirrors `java_callback_client.py`):

```python
signed_v2 = timestamp.encode("utf-8") + b"." + body
signature_v2 = hmac.new(secret.encode("utf-8"), signed_v2, hashlib.sha256).hexdigest()
```

---

## 4. Verification algorithm (Java)

1. Read the **raw body bytes** before any JSON binding.
2. Read `X-VoiceIQ-Timestamp`; reject if absent or not an integer.
3. **Freshness:** reject if `abs(now - timestamp) > 300` seconds (±5 min —
   tolerates modest clock skew; tighten once clocks are known-good).
4. Recompute v2 and compare with `X-VoiceIQ-Signature-V2` using a
   **constant-time** comparison (`MessageDigest.isEqual`). Never `String.equals`.
5. **Replay:** reject if `X-VoiceIQ-Trace-Id` was already processed. Cache
   seen ids for at least the freshness window (Redis/Caffeine with a 10-min
   TTL is sufficient).
6. Only then bind the JSON and act.

### Reference implementation

```java
private static final long MAX_SKEW_SECONDS = 300;

public boolean verify(byte[] rawBody, String timestamp, String signatureV2) {
    if (timestamp == null || signatureV2 == null) return false;

    final long ts;
    try {
        ts = Long.parseLong(timestamp);
    } catch (NumberFormatException e) {
        return false;
    }

    long now = Instant.now().getEpochSecond();
    if (Math.abs(now - ts) > MAX_SKEW_SECONDS) return false;   // stale or future

    byte[] tsBytes = timestamp.getBytes(StandardCharsets.UTF_8);
    byte[] signed = new byte[tsBytes.length + 1 + rawBody.length];
    System.arraycopy(tsBytes, 0, signed, 0, tsBytes.length);
    signed[tsBytes.length] = (byte) '.';
    System.arraycopy(rawBody, 0, signed, tsBytes.length + 1, rawBody.length);

    Mac mac = Mac.getInstance("HmacSHA256");
    mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
    byte[] expected = mac.doFinal(signed);

    // Constant-time compare against the hex header.
    return MessageDigest.isEqual(expected, hexToBytes(signatureV2));
}
```

> **Spring note:** `@RequestBody MyDto` consumes and re-parses the stream —
> the re-encoded bytes will differ. Take the raw body via
> `ContentCachingRequestWrapper`, a filter, or `@RequestBody byte[]`, verify,
> *then* deserialize.

---

## 5. Migration sequence

Both signatures ship today, so this is zero-downtime and each step is
independently revertible.

| Step | Owner | Action | Rollback |
|---|---|---|---|
| 1 | Java | Verify **v2**; on mismatch fall back to v1 and **log a warning**. Freshness + trace-id checks active. | drop to v1-only |
| 2 | Java | Watch the warning count. Zero over a full traffic cycle ⇒ v2 verifies universally. | — |
| 3 | Java | Remove the v1 fallback. **Replay protection is now live.** | re-enable fallback |
| 4 | Python | Delete `X-VoiceIQ-Signature` from `java_callback_client.py` + its test. | revert commit |

Do not start step 4 before Java confirms step 3 in production.

---

## 6. Test vector

Fixed values so both sides can assert identical output.

```
secret    : s3cr3t-shared-key
timestamp : 1769342461
body      : {"sessionId":"sess-1","schemaVersion":"1.0"}
```

Signed material for v2 (exact bytes):

```
1769342461.{"sessionId":"sess-1","schemaVersion":"1.0"}
```

Generate the expected digests locally (avoids a stale hardcoded value):

```bash
python -c "import hmac,hashlib; s=b's3cr3t-shared-key'; b=b'{\"sessionId\":\"sess-1\",\"schemaVersion\":\"1.0\"}'; print('v1', hmac.new(s,b,hashlib.sha256).hexdigest()); print('v2', hmac.new(s,b'1769342461.'+b,hashlib.sha256).hexdigest())"
```

A Java unit test hardcoding these two digests, fed the same inputs, is the
cheapest proof the implementations agree.

---

## 7. Transport

The Python client refuses to send unless `callback_url` is `https://`
(plaintext `http://` is allowed only to `localhost`, `127.0.0.1`, `::1`,
`host.docker.internal` for local development). Configure an https URL in
every deployed environment — the payload carries conversation-derived
recommendations and is authenticated by a shared secret.

---

## 8. Definition of done

- [ ] v2 verified with constant-time comparison
- [ ] Freshness window enforced (±5 min)
- [ ] Trace-id replay cache active
- [ ] Raw-body capture confirmed (not re-serialised JSON)
- [ ] Cross-language test vector passes
- [ ] v1 fallback removed after a clean traffic cycle
- [ ] Python v1 header deleted (step 4)
