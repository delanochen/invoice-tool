#!/bin/sh
# 确保测试站的公网入口可用：共享的 cloudflared 隧道容器必须挂在测试容器的网络上。
#
# 背景：test-invoice.prasinospower.com 的 ingress 指向 http://invoice-tool-test:8000，
# 由生产 compose 项目里的 invoice-tool-cloudflared 提供隧道。隧道容器与测试容器不在
# 同一个 Docker 网络时，公网访问会得到 502/503，日志里是
# "dial tcp: lookup invoice-tool-test ... no such host"。
#
# 这个脚本是幂等的，可以反复执行：已经挂接就什么都不做；隧道容器被重建后
# （例如生产 deploy 里 compose up 重建了 cloudflared），网络成员会丢失，再跑一次即可恢复。
#
# 用法：sh scripts/ensure_test_tunnel_network.sh
# 可选环境变量：
#   INVOICE_TEST_TUNNEL_CONTAINER  隧道容器名，默认 invoice-tool-cloudflared
#   INVOICE_TEST_NETWORK           测试网络名，默认 invoice-tool-test_default
#   INVOICE_TEST_APP_CONTAINER     测试应用容器名，默认 invoice-tool-test
#
# 注意：本脚本不会重启任何容器，也不会修改 compose 配置；失败时只记录 WARN，不阻断部署。
set -eu

TUNNEL_CONTAINER="${INVOICE_TEST_TUNNEL_CONTAINER:-invoice-tool-cloudflared}"
# 可选的第二个 connector（同一隧道冗余），存在时要一起挂到测试网络，
# 否则边缘把测试站流量分给它时仍会 502（lookup invoice-tool-test）。
TUNNEL_CONTAINER_2="${INVOICE_TEST_TUNNEL_CONTAINER_2:-invoice-tool-cloudflared-2}"
NETWORK="${INVOICE_TEST_NETWORK:-invoice-tool-test_default}"
APP_CONTAINER="${INVOICE_TEST_APP_CONTAINER:-invoice-tool-test}"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

attach() {
  tunnel="$1"
  if ! docker inspect "$tunnel" >/dev/null 2>&1; then
    log "WARN tunnel container $tunnel not found; skip network attach"
    return 0
  fi
  networks="$(docker inspect "$tunnel" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')"
  case " $networks " in
    *" $NETWORK "*)
      log "tunnel $tunnel already attached to $NETWORK"
      ;;
    *)
      docker network connect "$NETWORK" "$tunnel"
      log "attached $tunnel to $NETWORK"
      ;;
  esac
}

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  log "WARN test network $NETWORK not found; skip network attach"
  exit 0
fi

attach "$TUNNEL_CONTAINER"
attach "$TUNNEL_CONTAINER_2"

for tunnel in "$TUNNEL_CONTAINER" "$TUNNEL_CONTAINER_2"; do
  case "$(docker inspect "$tunnel" >/dev/null 2>&1 && echo present || echo absent)" in
    present)
      members="$(docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}')"
      case " $members " in
        *" $tunnel "*) log "network $NETWORK has $tunnel" ;;
        *) log "WARN network $NETWORK is missing $tunnel" ;;
      esac
      ;;
  esac
done

members="$(docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}')"
case " $members " in
  *" $APP_CONTAINER "*) log "network $NETWORK has $APP_CONTAINER" ;;
  *) log "WARN network $NETWORK is missing $APP_CONTAINER" ;;
esac
