"""Submits every lead in leads.json to the live n8n form, then reads each
execution's "Lead row" output from the n8n Postgres DB and scores the category.

Runs on the n8n host: python3 run_tests.py leads.json [base_url]
"""
import json
import subprocess
import sys
import time
import urllib.request
import uuid

leads = json.load(open(sys.argv[1], encoding="utf-8"))
base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5678"


def post_form(lead):
    boundary = uuid.uuid4().hex
    values = [lead["name"], lead["email"], lead["company"], lead["cards"], lead["message"]]
    body = b""
    for i, v in enumerate(values):
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"field-{i}\"\r\n\r\n{v}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{base}/form/discard-lead",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return urllib.request.urlopen(req, timeout=60).status


def psql(sql):
    out = subprocess.run(
        ["docker", "compose", "-f", "/opt/n8n/docker-compose.yml", "exec", "-T", "db",
         "psql", "-U", "n8n", "-d", "n8n", "-At", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout
    return out


def unflatten(text):
    # n8n stores execution data in "flatted" format: a list where string
    # values that are numeric indices point to other list entries.
    arr = json.loads(text)
    cache = {}

    def rev(v):
        if isinstance(v, str) and v.isdigit():
            i = int(v)
            if i in cache:
                return cache[i]
            return resolve(i)
        return v

    def resolve(i):
        v = arr[i]
        if isinstance(v, list):
            out = []
            cache[i] = out
            out.extend(rev(x) for x in v)
            return out
        if isinstance(v, dict):
            out = {}
            cache[i] = out
            for k, x in v.items():
                out[k] = rev(x)
            return out
        cache[i] = v
        return v

    return resolve(0)


start_id = int(psql("select coalesce(max(id),0) from execution_entity;").strip() or 0)
t0 = time.time()
for lead in leads:
    post_form(lead)
    time.sleep(1)

# wait for all executions to finish
for _ in range(90):
    done = psql(
        f"select count(*) from execution_entity where id > {start_id} and finished = true;"
    ).strip()
    if int(done) >= len(leads):
        break
    time.sleep(2)

rows = psql(
    f"select e.id, e.status, d.data from execution_entity e join execution_data d on d.\"executionId\" = e.id "
    f"where e.id > {start_id} order by e.id;"
).strip().splitlines()

results = []
for line, lead in zip(rows, leads):
    exec_id, status, data = line.split("|", 2)
    run = unflatten(data)["resultData"]["runData"]
    row = run["Lead row"][0]["data"]["main"][0][0]["json"] if "Lead row" in run else {}
    results.append({
        "execution": int(exec_id),
        "status": status,
        "name": lead["name"],
        "expected": lead["expected"],
        "got": row.get("category"),
        "priority": row.get("priority"),
        "language": row.get("language"),
        "estimated_cards": row.get("estimated_cards"),
        "summary": row.get("summary"),
        "reply_subject": row.get("reply_subject"),
        "reply_draft": row.get("reply_draft"),
    })

ok = sum(r["expected"] == r["got"] for r in results)
print(json.dumps({"total": len(results), "correct": ok, "seconds": round(time.time() - t0), "results": results},
                 ensure_ascii=False, indent=2))
