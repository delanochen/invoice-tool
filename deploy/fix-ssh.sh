#!/bin/bash
# fix-ssh.sh: 修复 invoice-tool 服务器 SSH 密钥登录（幂等，可重复执行）
# 用法: bash deploy/fix-ssh.sh

# 内置公钥常量（不依赖外部文件，免疫 BOM/乱码/CRLF 污染）
NEWKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHEP58jDvpydTYFbLcg4Smub/81rpyzRzB7dADdRKpKi invoice-deploy-2026'
KEYPREFIX=$(echo "$NEWKEY" | cut -c1-40)

echo "[1/5] 配置 /root/.ssh 目录与权限..."
mkdir -p /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys

echo "[2/5] 清理垃圾行并幂等写入公钥（内置常量，只保留有效 ssh-* 行）..."
# 只保留以 ssh-ed25519/ssh-rsa/ecdsa- 开头的有效公钥行（剔除 BOM/乱码/粘贴带入的垃圾）
grep -E "^ssh-(ed25519|rsa|ecdsa)" /root/.ssh/authorized_keys > /tmp/ak.valid || true
# 删除旧版本公钥（避免重复）
grep -vF "$KEYPREFIX" /tmp/ak.valid > /tmp/ak.new || true
cat /tmp/ak.new > /root/.ssh/authorized_keys
printf '\n' >> /root/.ssh/authorized_keys
echo "$NEWKEY" >> /root/.ssh/authorized_keys
printf '\n' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

echo "[3/5] 设置 sshd: PermitRootLogin prohibit-password + PubkeyAuthentication yes..."
SSHD=/etc/ssh/sshd_config
for opt in "PermitRootLogin prohibit-password" "PubkeyAuthentication yes"; do
  key="${opt%% *}"
  if grep -q "^$key" "$SSHD"; then
    sed -i "s/^$key.*/$opt/" "$SSHD"
  elif grep -q "^#$key" "$SSHD"; then
    sed -i "s/^#$key.*/$opt/" "$SSHD"
  else
    echo "$opt" >> "$SSHD"
  fi
done

echo "[4/5] 检查 sshd_config.d 子目录冲突配置..."
if [ -d /etc/ssh/sshd_config.d ]; then
  grep -Rl "PermitRootLogin no" /etc/ssh/sshd_config.d/ 2>/dev/null | while read -r f; do
    sed -i 's/^PermitRootLogin no/PermitRootLogin prohibit-password/' "$f"
    echo "  已修正: $f"
  done
fi

echo "[5/5] 校验配置并重启 sshd..."
sshd -t && systemctl restart ssh && echo "=== FIX_SSH_DONE ==="

echo "--- 公钥确认 ---"
grep invoice-deploy /root/.ssh/authorized_keys
echo "--- 生效配置 ---"
grep -hE "^PermitRootLogin|^PubkeyAuthentication" "$SSHD" /etc/ssh/sshd_config.d/*.conf 2>/dev/null
echo "--- 最近 sshd 日志 ---"
journalctl -u ssh -n 8 --no-pager 2>/dev/null | tail -8
