import sys, time, http.client, json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

conn = http.client.HTTPConnection('127.0.0.1', 18792, timeout=120)
body = json.dumps({
    'model': 'zai/glm-5.2',
    'messages': [{'role': 'user', 'content': 'reply Hello in 2 words'}],
    'stream': True,
    'max_tokens': 30,
})
conn.request('POST', '/v1/chat/completions', body=body.encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer any'})

resp = conn.getresponse()
print(f"Status: {resp.status}")
print(f"Headers: {dict(resp.getheaders())}")

raw_chunks = []
start = time.time()
while time.time() - start < 120:
    chunk = resp.read(4096)
    if chunk:
        raw_chunks.append(chunk)
        decoded = chunk.decode('utf-8', errors='replace')
        print(f"CHUNK[{len(chunk)}]: {repr(decoded[:200])}")
    else:
        break
    time.sleep(0.1)

full = b''.join(raw_chunks)
print(f"\nTotal: {len(full)} bytes")
if full:
    print(f"Text: {full.decode('utf-8','replace')[:500]}")
