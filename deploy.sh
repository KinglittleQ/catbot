#!/bin/bash
# catbot 一键部署脚本
# 用法: bash deploy.sh

set -e

echo "=== catbot 部署脚本 ==="

# 1. 安装依赖
echo "[1/4] 安装 Python 依赖..."
pip install -e ".[all]"

# 2. 创建 workspace 目录
echo "[2/4] 初始化 workspace..."
mkdir -p ~/.catbot/workspace/memory
mkdir -p ~/.catbot/sessions

# 3. 创建默认 SOUL.md（如果不存在）
if [ ! -f ~/.catbot/workspace/SOUL.md ]; then
cat > ~/.catbot/workspace/SOUL.md << 'EOF'
You are a helpful AI assistant. Be concise, accurate, and friendly.
Reply in the user's language.
EOF
echo "  Created ~/.catbot/workspace/SOUL.md"
fi

# 4. 检查 .env 文件
if [ ! -f .env ]; then
cat > .env << 'EOF'
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=your_secret_here
ANTHROPIC_API_KEY=sk-ant-xxx
# or use OpenAI:
# OPENAI_API_KEY=sk-xxx
EOF
echo ""
echo "[!] 请编辑 .env 文件填入你的凭证："
echo "    nano .env"
echo ""
fi

echo "[4/4] 完成！运行方式："
echo "    python examples/feishu_bot.py"
echo "    # 或使用 systemd 服务："
echo "    sudo cp deploy/catbot.service /etc/systemd/system/"
echo "    sudo systemctl enable --now catbot"
