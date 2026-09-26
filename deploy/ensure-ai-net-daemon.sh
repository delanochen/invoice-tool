#!/bin/sh
# 常驻守护：周期性确保 invoice-tool-cloudflared 挂在 ai-net 上。
#
# 取代原先「ensure-ai-net.timer 每 70 秒 fork 一次 oneshot」的做法：
# oneshot 跑完即 dead，状态在 activating/dead 之间反复，看起来像卡住。
# 这里改成常驻服务后状态恒为 active (running)，且每轮循环都真正执行检查。
#
# 注意：不要给 oneshot 版本加 RemainAfterExit=yes 来「让状态好看」——
# 实测 systemd 对已 active 的 oneshot 再 start 是 no-op，timer 触发会全部
# 空转，cloudflared 一旦脱离 ai-net 就再也不会被接回。
#
# 真正的检查逻辑在 /usr/local/sbin/ensure-ai-net.sh，本脚本只负责循环调用。
# 安装：
#   install -m 0755 ensure-ai-net-daemon.sh /usr/local/sbin/
#   install -m 0644 ensure-ai-net.service    /etc/systemd/system/
#   systemctl daemon-reload
#   systemctl disable --now ensure-ai-net.timer
#   systemctl enable --now ensure-ai-net.service
INTERVAL="${ENSURE_AI_NET_INTERVAL:-60}"
while :; do
  /usr/local/sbin/ensure-ai-net.sh || true
  sleep "$INTERVAL"
done
