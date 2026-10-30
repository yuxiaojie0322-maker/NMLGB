# VPS8 自动签到脚本

## 功能

- 登录 + hCaptcha 破解（LLM）
- 每日签到 + 积分兑换（≥100 自动换）
- 服务到期检查 + 自动续费（<5 天）
- Telegram 截图通知

## 公库环境变量

| 变量 | 说明 |
|---|---|
| `VPS8` | `邮箱-----密码` |
| `OPENAI_API_KEY` | LLM key |
| `OPENAI_BASE_URL` | 例：`https://ai.lezi-chat.bond/v1` |
| `OPENAI_MODEL` | 推荐 `deepseek-v4.1-flash` |
| `NOPECHA_KEY` | NopeCHA key（可多行） |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | Telegram Chat ID |