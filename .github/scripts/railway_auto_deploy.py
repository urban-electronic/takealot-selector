"""Railway 构建恢复后自动部署 main 最新提交；已上线则什么都不做。

由 .github/workflows/railway-auto-deploy.yml 每 10 分钟调用一次。
Railway 平台故障期间 serviceInstanceDeploy 会返回
"Deploys have been paused temporarily"，此时静默退出，等下一轮。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://backboard.railway.app/graphql/v2"
LIVE = "https://takealot-selector-production.up.railway.app/"
OLD_BUILD = "3ab844f4079d"  # 2026-10-20 18:41 的旧版本，没有多市场参考价
SERVICE_ID = "a4a7215e-5eeb-4f6f-ad3d-8408df66f2d1"
ENVIRONMENT_ID = "01353cb9-9318-4c1e-b1b2-7c4fe9030f50"
TOKEN = os.environ.get("RAILWAY_TOKEN", "")

MUT = """
mutation($s: String!, $e: String!) {
  serviceInstanceDeploy(serviceId: $s, environmentId: $e, latestCommit: true)
}
"""


def live_build():
    req = urllib.request.Request(LIVE, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("build", "")


def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "takealot-selector-ci",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:300]}


def main():
    if not TOKEN:
        print("缺少 RAILWAY_TOKEN secret")
        return 1
    cur = live_build()
    print("线上 build =", cur)
    if cur and not cur.startswith(OLD_BUILD):
        print("线上已不是旧版本，无需部署")
        return 0
    res = gql(MUT, {"s": SERVICE_ID, "e": ENVIRONMENT_ID})
    print("触发结果:", json.dumps(res, ensure_ascii=False)[:400])
    errs = res.get("errors") or res.get("http")
    if errs:
        if "paused" in json.dumps(errs, ensure_ascii=False).lower():
            print("Railway 仍在暂停构建，等下一轮")
            return 0
        print("触发失败（非暂停原因）")
        return 1
    for _ in range(60):
        time.sleep(20)
        cur = live_build()
        print("  build =", cur)
        if cur and not cur.startswith(OLD_BUILD):
            print("部署成功:", cur)
            return 0
    print("等待超时，构建可能仍在进行")
    return 1


if __name__ == "__main__":
    sys.exit(main())
