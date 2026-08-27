from tam.research.data.sec.store import SecStore

store = SecStore()
client = store._client
paginator = client.get_paginator("list_objects_v2")
total_objects, total_bytes = 0, 0
by_top = {}
for page in paginator.paginate(Bucket=store._credentials.bucket, Prefix="sec/"):
    for obj in page.get("Contents", []):
        total_objects += 1
        total_bytes += obj["Size"]
        top = obj["Key"].split("/")[1]
        count, size = by_top.get(top, (0, 0))
        by_top[top] = (count + 1, size + obj["Size"])

print(f"{total_objects} object(s), {total_bytes/1024/1024:.2f} MB under sec/")
for top, (count, size) in sorted(by_top.items()):
    print(f"  sec/{top}/: {count} object(s), {size/1024:.1f} KB")
