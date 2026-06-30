#!/usr/bin/env bash
# 一键重出 docs/pic/v2_7.png —— "v2 可观测性"健康仪表盘截图
#
# ============================================================
# 这个脚本干什么
# ============================================================
# 不做任何人为干预(没有 SIGSTOP / 没有 spike),只跑 30s 预热 + 90s 满载
# 混合负载,让 Grafana 四面板自然呈现稳态:
#
#   左上 QPS by cmd               GET ~1.6K + SET ~400 ops/s
#   右上 GET 命中率                稳定 ~75%(0.6 / (0.6 + 0.2))
#   左下 P99 延迟 by cmd           ~10-25µs,健康
#   右下 复制 offsets (master vs slave)  两线同步上涨且重合 = slave 已追上
#
# 截到的图用于:
#   - README 的 "v2 可观测性"章节
#   - 汇报里"v2 把运营可见性从 0 做到 8 个指标家族"的主图
#
# 想看"复制 lag 的逐秒时序"?另跑:
#   python3 scripts/demo_replication.py --total 1000000
# 截那个终端,作为 v2_7.png 的配图(见 docs/learn/项目串讲与汇报指南.md)
#
# ============================================================
# 用法
# ============================================================
#   bash scripts/repro_v2_7_stable.sh
#
# 流程(~3 分钟,截图时间不计):
#   1. 清场 + 起监控栈
#   2. 起 master(7001/9101) + slave(7002/9102) 对齐 dashboard 写死的 instance
#   3. 提示用户先在 Cursor 转发 3000/9090,在浏览器打开 Grafana
#   4. 跑 30s 预热(填 hit 池) + 90s 满载混合负载
#   5. 提示截图;按 Enter 后 trap 自动清场
#
# Ctrl+C 中断也会 trap 清理,绝不污染环境。
# ============================================================

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cleanup() {
  echo ""
  echo "==> 清理中 …"
  pkill -9 -f 'serve\.py'         2>/dev/null || true
  pkill -9 -f 'scripts/load\.py'  2>/dev/null || true
  (cd deploy && docker compose down -v >/dev/null 2>&1) || true
  rm -f /tmp/n700[1-2].log /tmp/load.log 2>/dev/null || true
  echo "==> 清理完成"
}
trap cleanup EXIT INT TERM

banner() {
  echo ""
  echo "------------------------------------------------------------"
  echo "$*"
  echo "------------------------------------------------------------"
}

banner "[1/5] 清场"
pkill -9 -f 'serve\.py'        2>/dev/null || true
pkill -9 -f 'scripts/load\.py' 2>/dev/null || true
(cd deploy && docker compose down -v >/dev/null 2>&1) || true
sleep 1

banner "[2/5] 起 docker 监控栈"
(cd deploy && docker compose up -d) >/dev/null
for _ in $(seq 1 30); do
  curl -sf --max-time 1 http://127.0.0.1:9090/-/ready    >/dev/null 2>&1 && break
  sleep 1
done
echo "    ✓ Prometheus ready"
for _ in $(seq 1 30); do
  curl -sf --max-time 1 http://127.0.0.1:3000/api/health >/dev/null 2>&1 && break
  sleep 1
done
echo "    ✓ Grafana ready"

banner "[3/5] 起 master(7001/9101) + slave(7002/9102)"
nohup python3 serve.py --port 7001 --metrics-port 9101 --maxsize 1000000 \
    > /tmp/n7001.log 2>&1 &
disown
sleep 1
nohup python3 serve.py --port 7002 --metrics-port 9102 \
    --role slave --master 127.0.0.1:7001 --maxsize 1000000 \
    > /tmp/n7002.log 2>&1 &
disown
sleep 3
for p in 9101 9102; do
  role=$(curl -s --max-time 1 http://127.0.0.1:$p/metrics | awk '/^distcache_role / {print $NF}')
  echo "    metrics:$p  role=$role  $([ "$p" = "9101" ] && echo '(应为 1)' || echo '(应为 0)')"
done

echo ""
echo "    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "    ⏸  请先确认本地浏览器能打开 Grafana,再按 Enter 继续"
echo "    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "    1) Cursor 端口转发(只用做一次,后续不用重复):"
echo "       Ctrl+\` → PORTS 标签 → + 添加 3000、9090"
echo "    2) 本地浏览器打开: http://127.0.0.1:3000 (admin / admin)"
echo "       Dashboards → distcache(此刻应看到空白面板)"
echo "    3) 右上角时间窗设为: Last 5 minutes、刷新 5s"
read -r -p "    > "

banner "[4/5] 跑负载: 30s 预热 + 90s 满载"
echo "    [t=0]   预热 30s,1000 QPS,把 hit 池铺满 …"
python3 scripts/load.py --duration 30 --qps 1000 \
    --hit 0.6 --miss 0.2 --set 0.2 --keyspace 1000 \
    --nodes 127.0.0.1:7001 >/dev/null 2>&1

echo "    [t=30s] 满载 90s,2000 QPS,6:2:2 混合(GET ~1.6K + SET ~400 ops/s) …"
python3 scripts/load.py --duration 90 --qps 2000 \
    --hit 0.6 --miss 0.2 --set 0.2 --keyspace 1000 \
    --nodes 127.0.0.1:7001 2>&1 | grep -E '^\[load\]'

banner "[5/5] 截图时间"
cat <<'EOF'
  📸 Grafana → Dashboards → distcache

  时间窗:  Last 5 minutes  (确保 4 面板能装下整个负载段)
  刷新:    5s

  预期画面:
    左上 QPS    : GET 绿线 ~1.6K + SET 黄线 ~400(or 视脚本而定)
    右上 命中率 : 稳态 ~75%,平直
    左下 P99    : GET ~10µs,SET 10-25µs 之间,健康微秒级
    右下 复制   : master.offset + slave.applied 同步上涨且基本重合
                  ← 本机 slave 几乎实时追上,两线像素级重合

  保存为:  docs/pic/v2_7.png(覆盖旧文件)

  汇报右下面板时这样讲:
    "master.offset 是 master 已应用的 batch 数,slave.applied 是 slave
     已回放的 batch 数。本机两线基本重合,说明 slave 已追上;若 slave
     卡顿或网络抖动,黄线会低于绿线,纵向间距就是复制 lag。要看 lag
     的逐秒时序,见 demo_replication.py 终端(配图 v2_7_repl.png)。"

  截好图后按 Enter 自动清理 …
EOF
read -r -p "  > "
echo ""
echo "==> 用户确认完成,开始清理"
