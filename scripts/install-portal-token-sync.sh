#!/usr/bin/env bash
# 装一个 launchd 定时任务：每 30 分钟把 Chrome 里最新的卖家门户令牌同步到后端。
#
# 为什么用 launchd 而不是 cron：macOS 上 cron 需要额外的完全磁盘访问授权、且休眠
# 唤醒后不补跑；launchd 的 StartInterval 会在唤醒后补一次，更接近"机器在用就一直有效"。
#
# 用法：
#   bash scripts/install-portal-token-sync.sh                    # 安装并立即跑一次
#   bash scripts/install-portal-token-sync.sh --open-browser      # 令牌快过期时自动开 Chrome 续期
#   bash scripts/install-portal-token-sync.sh --uninstall         # 卸载
set -euo pipefail

LABEL="com.zenty.portal-token-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/zenty-portal-token-sync.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/portal-token-sync.py"
OPEN_BROWSER=0

for arg in "$@"; do
  case "$arg" in
    --uninstall)
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$PLIST"
      echo "已卸载 $LABEL"
      exit 0
      ;;
    --open-browser) OPEN_BROWSER=1 ;;
    *) echo "未知参数：$arg" >&2; exit 1 ;;
  esac
done

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "找不到 python3，先装一个再回来（brew install python）" >&2
  exit 1
fi
[[ -f "$SYNC_SCRIPT" ]] || { echo "找不到 $SYNC_SCRIPT" >&2; exit 1; }

EXTRA=()
if [[ "$OPEN_BROWSER" == "1" ]]; then
  EXTRA+=("--open-browser")
fi

# API Key 只需存一次，之后脚本自己读 ~/.zenty/portal-token-sync.json。
# 目标后端默认是 Railway；桌面版用的本机后端可以在配置里加进 api_bases。
mkdir -p "$HOME/.zenty"
if [[ ! -s "$HOME/.zenty/portal-token-sync.json" ]]; then
  echo "填一次后端 API Key（Railway 上的 API_KEY 环境变量）。留空则跳过，之后再补也行。"
  read -r -p "API Key: " -s API_KEY
  echo
  if [[ -n "${API_KEY:-}" ]]; then
    "$PYTHON" "$SYNC_SCRIPT" --save-key "$API_KEY"
  fi
fi

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SYNC_SCRIPT</string>
    <string>--notify</string>
${EXTRA[@]/#/    <string>}
  </array>
  <!-- 每 30 分钟一次：令牌 24 小时有效，余量足够；脚本自身判重，多数时候空转。 -->
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
</dict>
</plist>
PLIST_EOF

# EXTRA 为空时上面会留一个只含空白的行，plutil 会报错——用 plutil 先校验，出错就重写干净版本。
if ! plutil -lint "$PLIST" >/dev/null 2>&1; then
  sed -i '' -e '/^[[:space:]]*$/d' "$PLIST"
  plutil -lint "$PLIST"
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "已安装 $LABEL（每 30 分钟运行一次）"
echo "日志：$LOG_FILE"
if [[ "$OPEN_BROWSER" == "1" ]]; then
  echo "已开启浏览器自动续期：令牌剩余不足 1 小时时会自动打开 Chrome 刷一次门户页。"
fi
echo
echo "立刻跑一次看结果："
"$PYTHON" "$SYNC_SCRIPT" --notify "${EXTRA[@]}" || true
