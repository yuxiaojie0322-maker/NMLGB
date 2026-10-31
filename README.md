# VPS8 自动签到与续期系统 (VPS8-Auto-Checkin)

专为 VPS8 (`vps8.zz.cd`) 设计的自动化每日签到、积分兑换及到期服务自动续期系统。支持多账号并发、自包含免外链脚本、严格密码模拟登录防封、多协议节点代理及 GitHub Actions 自动维护。

---

## ✨ 核心特性

1. **严格原生密码模拟登录（防封策略）**
   - 绝不使用持久化 Cookie/Session 缓存直登（VPS8 频繁复用持久 Cookie 极易触发风控封号）。
   - 每次签到完整模拟真人逐字输入密码与表单交互，配合随机延时与时区伪装，安全性高。
2. **双重验证码全自动破解**
   - **hCaptcha**：集成 `hcaptcha-challenger` + 视觉大模型（Vision LLM），遇到无法识别的生僻题型自动刷新换题重试。
   - **reCAPTCHA**：集成 `NopeCHA` 扩展自动求解。
   - 破解后直接调用内部 RESTful 接口异步提交，彻底解决传统网页前端卡死或点击无响应问题。
3. **多协议代理环境自适应（sing-box 驱动）**
   - 深度支持 `VLESS` (含 Reality / xtls-rprx-vision)、`VMess`、`Trojan`、`Hysteria2`、`TUIC`、`SOCKS5` 等常见节点链接。
   - 启动后自动进行多轮连通性验证与真实出口 IP 检测，代理异常时即时推送到 Telegram 并中断任务。
4. **积分自动兑换 & VPS 到期自动续期**
   - 签到成功后，若积分达到 100 分以上，自动触发兑换余额流程。
   - 自动扫描名下活跃 VPS 与域名服务，到期时间小于 5 天时自动使用账户余额进行续费。
5. **多账号独立并行支持**
   - 原生支持 F1、F2、F3 三组账号及对应独立代理节点并行执行。
   - 自动加入错峰延迟（Stagger Wait），避免多账号同一时间请求造成 API 频控。
   - 智能空密匙检测：未配置的副账号自动跳过，无需手动修改工作流代码。
6. **仓库自动维护机制**
   - **防休眠保活**：每日自动在 `time.txt` 中写入最新时间戳并提交，防止 GitHub 自动禁用超过 60 天无提交的定时 Actions。
   - **历史运行清理**：采用 GitHub Script 自动清理旧工作流日志，严格保留最新 2 次记录，保持 Actions 页面干净清爽。

---

## 🔐 GitHub Secrets 配置

在 GitHub 仓库中进入 **Settings -> Secrets and variables -> Actions**，配置以下变量：

### 1. 账号与节点（支持最多 3 组）

| Secret 名称 | 必须 | 说明 | 示例 / 格式 |
|---|---|---|---|
| `VPS8` | **是** (F1) | F1 主账号邮箱与密码 | `your_email@gmail.com-----your_password` |
| `F1_NODE_LINK` | 否 | F1 专属代理节点链接 | `vless://...` 或 `tuic://...` 或 `hy2://...` |
| `VPS82` | 否 (F2) | F2 账号邮箱与密码 | `email2@gmail.com-----pass2` |
| `F2_NODE_LINK` | 否 | F2 专属代理节点链接 | 同上 |
| `VPS83` | 否 (F3) | F3 账号邮箱与密码 | `email3@gmail.com-----pass3` |
| `F3_NODE_LINK` | 否 | F3 专属代理节点链接 | 同上 |

### 2. 验证码与 AI 服务配置

| Secret 名称 | 必须 | 说明 | 示例 |
|---|---|---|---|
| `OPENAI_API_KEY` | **是** | 视觉大模型 API Key（用于解答 hCaptcha） | `sk-...` |
| `OPENAI_BASE_URL` | **是** | 兼容 OpenAI 格式的 API 端点 | `https://api.openai.com/v1` 或第三方代理端点 |
| `OPENAI_MODEL` | **是** | 视觉模型名称 | `deepseek-v4.1-flash` 或 `gpt-4o-mini` |
| `NOPECHA_KEY` | 否 | NopeCHA 密钥（支持多行填入轮换，留空使用免费试用配额） | `key1` |

### 3. Telegram 消息推送

| Secret 名称 | 必须 | 说明 |
|---|---|---|
| `TG_BOT_TOKEN` | 否 | Telegram Bot Token，签到成败及截图均推送至此 |
| `TG_CHAT_ID` | 否 | 接收推送消息的 Telegram 聊天 ID |

---

## ⏰ 运行周期与手动触发

- **定时触发**：每天 UTC 时间 `00:30`（即北京时间 `08:30`）全自动执行。
- **手动触发**：在 GitHub 仓库进入 **Actions -> VPS8 自动签到与续期 -> Run workflow**，可勾选要测试/运行的账号（F1 / F2 / F3）。