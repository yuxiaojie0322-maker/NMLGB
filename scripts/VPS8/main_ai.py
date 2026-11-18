import os
import re
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import asyncio
import time
import random
import requests
from datetime import datetime, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ══════════════════════════════════════════════════════════════════
#  ── 常量 ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
LOGIN_URL    = "https://vps8.zz.cd/login"
SIGNIN_URL   = "https://vps8.zz.cd/points/signin"
REDEEM_URL   = "https://vps8.zz.cd/points/redeem"
SERVICE_URL  = "https://vps8.zz.cd/order/service"
SS_DIR       = "screenshots"
NOPECHA_PATH = "scripts/extensions/nopecha/unpacked"
HC_REPO_DIR  = "hcaptcha-challenger"
HC_SRC_DIR   = os.path.join(HC_REPO_DIR, "src")

BLOCKED_PROMPTS = [
    "move the correct vial into the empty slot",
    "click the flower the bee never lands on",
    "please drag the screw to the empty joint",
    "please click on the two icons that break the pattern",
    "please click on the shape that grows",
    "please click on the two lines that are shorter",
    "please click on the icon that is different",
]
BLOCKED_MAX_SKIP = 4
RENEW_DAYS_THRESHOLD = 5

RECAPTCHA_SOLVE_TIMEOUT = 90
RECAPTCHA_IDLE_GIVEUP = 45
RECAPTCHA_IDLE_GIVEUP_FIRST = 35
SIGNIN_PAGE_SETTLE = 12

LOGIN_URL_NORM          = LOGIN_URL.rstrip("/")
LOGIN_API_ENDPOINT_NORM = "https://vps8.zz.cd/api/guest/client/login"

_NOPECHA_WARM = False
_LAST_CAPTCHA_TOKEN = ""


# ══════════════════════════════════════════════════════════════════
#  ── 通用工具 ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
def _by(sel):
    if sel.startswith("xpath="):
        return By.XPATH, sel[6:]
    return By.CSS_SELECTOR, sel


def log(level, msg):
    print(f"[{time.strftime('%H:%M:%S')}][{level}] {msg}", flush=True)


_NOISE_FILTER_PREFIXES = (b"Unable to revert mtime",)


def _install_noise_filter():
    import threading
    def _pump(fd_in, fd_out):
        buf = b""
        while True:
            try:
                chunk = os.read(fd_in, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                if any(line.lstrip().startswith(d) for d in _NOISE_FILTER_PREFIXES):
                    continue
                try:
                    os.write(fd_out, line + b"\n")
                except OSError:
                    pass
    try:
        for fd in (1, 2):
            read_fd, write_fd = os.pipe()
            saved_fd = os.dup(fd)
            os.dup2(write_fd, fd)
            os.close(write_fd)
            threading.Thread(target=_pump, args=(read_fd, saved_fd), daemon=True).start()
    except Exception:
        pass


def screenshot(driver, name):
    os.makedirs(SS_DIR, exist_ok=True)
    path = f"{SS_DIR}/{name}.png"
    try:
        driver.save_screenshot(path)
        log("INFO", f"截图: {name}.png")
        return path
    except Exception as e:
        log("WARN", f"截图失败: {e}")
        return None


def send_tg(token, chat_id, path, caption=""):
    if not (token and chat_id and path and os.path.exists(path)):
        return
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                files={"photo": f},
                data={"chat_id": chat_id, "caption": caption},
                timeout=30,
            )
        log("INFO", f"Telegram: {r.status_code}")
    except Exception as e:
        log("WARN", f"Telegram 失败: {e}")


def send_tg_text(token, chat_id, text):
    if not (token and chat_id and text):
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        log("INFO", f"Telegram 消息发送: {r.status_code}")
    except Exception as e:
        log("WARN", f"Telegram 消息失败: {e}")


def finish(driver, tg_token, tg_chat_id, name, caption):
    path = screenshot(driver, name)
    send_tg(tg_token, tg_chat_id, path, caption)


def parse_account(raw):
    if "-----" not in (raw or ""):
        raise ValueError("格式错误，应为 邮箱-----密码")
    email, password = raw.strip().split("-----", 1)
    return email.strip(), password.strip()


def build_caption(email, info=None, fail_reason=None, redeem_result=None,
                  services_info=None, already_signed=False,
                  points_before=None, points_gained=None):
    lines = [f"👤 账号：{email}"]
    if fail_reason:
        lines += ["📊 签到状态：❌ 失败", f"⚠️ 原因：{fail_reason}"]
        if points_before not in (None, "未知"):
            lines.append(f"💰 当前积分：{points_before} 分")
    elif info:
        cur_pt = info.get("points", "未知")
        if already_signed:
            lines.append("📊 签到状态：ℹ️ 今日已签到 (保持不变)")
            lines.append(f"💰 当前积分：{cur_pt} 分")
        else:
            lines.append("📊 签到状态：✅ 签到成功")
            if points_before not in (None, "未知"):
                try:
                    pt_b = int(points_before)
                    if points_gained is not None and points_gained > 0:
                        lines.append(f"💰 签到前积分：{pt_b} 分")
                        lines.append(f"🎁 本次获得：+{points_gained} 积分")
                        if not redeem_result:
                            lines.append(f"📈 签到后积分：{cur_pt} 分")
                    elif cur_pt != "未知":
                        pt_a = int(cur_pt)
                        diff = pt_a - pt_b
                        if diff > 0:
                            lines.append(f"💰 签到前积分：{pt_b} 分")
                            lines.append(f"🎁 本次获得：+{diff} 积分")
                            lines.append(f"📈 签到后积分：{pt_a} 分")
                        else:
                            lines.append(f"💰 当前积分：{cur_pt} 分")
                    else:
                        lines.append(f"💰 当前积分：{cur_pt} 分")
                except Exception:
                    lines.append(f"💰 当前积分：{cur_pt} 分")
            else:
                lines.append(f"💰 当前积分：{cur_pt} 分")

        if info.get("streak", "未知") != "未知":
            lines.append(f"🔥 连续签到：{info['streak']} 天")
    else:
        lines.append("📊 签到状态：未知")

    if redeem_result:
        lines.append(f"💎 兑换结果：{redeem_result}")
        if info and info.get("points", "未知") != "未知":
            lines.append(f"💰 兑换后当前剩余积分：{info.get('points')} 分")

    if services_info:
        for s in services_info:
            prefix = "🖥️ VPS" if s["type"] == "vps" else "🌐 域名"
            line = f"{prefix}：{s['name']} → 到期 {s['expiry_display']}"
            if s.get("renew_result"):
                line = f"{prefix}：{s['name']} → {s['renew_result']}"
            lines.append(line)
    else:
        lines.append("🖥️ VPS：名下暂无活跃订单")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines += ["", f"⏰ 执行时间：{now_str}", "#VPS8每日签到"]
    return "\n".join(lines)


def extract_info(html):
    info = {"points": "未知", "is_signed": False, "streak": "未知"}
    m = re.search(r'当前积分[：:]\s*(?:<strong>)?(\d+)(?:</strong>)?', html)
    if not m:
        m = re.search(r'积分[：:]\s*(?:<strong>)?(\d+)(?:</strong>)?', html)
    if m:
        info["points"] = m.group(1)
    m = re.search(r'今日签到状态[：:]\s*([^\n<]+)', html)
    if m:
        info["is_signed"] = "已签到" in m.group(1).strip()
    elif "签到成功" in html or "今日已签到" in html:
        info["is_signed"] = True
    m = re.search(r'当前连续签到[：:]\s*(?:<strong>)?(\d+)(?:</strong>)?\s*天', html)
    if not m:
        m = re.search(r'连续签到[：:]\s*(?:<strong>)?(\d+)(?:</strong>)?\s*天', html)
    if m:
        info["streak"] = m.group(1)
    return info


def is_signed(html):
    return extract_info(html)["is_signed"]


def points_to_int(points):
    try:
        return int(points)
    except (TypeError, ValueError):
        return None


def human_type(driver, selector, text):
    el = driver.find_element(By.CSS_SELECTOR, selector)
    el.clear()
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(0.08, 0.2))


def is_element_visible(driver, sel):
    els = driver.find_elements(*_by(sel))
    return len(els) > 0 and els[0].is_displayed()


def is_element_present(driver, sel):
    return len(driver.find_elements(*_by(sel))) > 0

# ── 验证码检测与处理 ──────────────────────────────────────────
def has_hcaptcha_on_page(driver) -> bool:
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="hcaptcha"]'):
            if el.is_displayed():
                return True
        for el in driver.find_elements(By.CSS_SELECTOR, '.h-captcha'):
            if el.is_displayed():
                return True
        return False
    except Exception:
        return False


def _hide_hcaptcha_ui(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll('iframe[src*="hcaptcha"]').forEach(el => {
                if (el.src && el.src.includes('frame=challenge')) {
                    el.style.setProperty('display', 'none', 'important');
                    el.style.setProperty('visibility', 'hidden', 'important');
                    if (el.parentElement && el.parentElement.tagName === 'DIV') {
                        el.parentElement.style.setProperty('display', 'none', 'important');
                    }
                }
            });
            document.querySelectorAll('.h-captcha').forEach(el => {
                el.style.setProperty('display', 'none', 'important');
            });
            document.querySelectorAll('div[style*="position: fixed"], div[style*="position:fixed"]').forEach(el => {
                const s = el.getAttribute('style') || '';
                if (s.includes('z-index') || s.includes('opacity') || s.includes('cursor: pointer')) {
                    el.style.setProperty('display', 'none', 'important');
                    el.style.setProperty('pointer-events', 'none', 'important');
                }
            });
        """)
        log("INFO", "✅ 已隐藏 hCaptcha UI 及遮罩")
    except Exception:
        pass


def has_recaptcha_on_page(driver) -> bool:
    try:
        return driver.execute_script("""
            const els = document.querySelectorAll('iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha/api2"], .g-recaptcha');
            for (const el of els) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width > 20 && rect.height > 20 && 
                    style.display !== 'none' && 
                    style.visibility !== 'hidden' && 
                    parseFloat(style.opacity) > 0.1) {
                    return true;
                }
            }
            return false;
        """)
    except Exception:
        return False


def get_recaptcha_token(driver):
    try:
        val = driver.execute_script("""
            const el = document.getElementById('g-recaptcha-response') || document.querySelector('textarea[name="g-recaptcha-response"]');
            return el ? el.value : '';
        """)
        return val if len((val or "").strip()) > 10 else None
    except Exception:
        return None


def get_recaptcha_state(driver) -> str:
    if get_recaptcha_token(driver):
        return "solved"
    try:
        if driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="bframe"]'):
            return "working"
    except Exception:
        pass
    return "idle"


def wait_recaptcha_solved(driver, timeout=RECAPTCHA_SOLVE_TIMEOUT, idle_giveup=None):
    global _NOPECHA_WARM
    if not has_recaptcha_on_page(driver):
        return True
    if get_recaptcha_token(driver):
        _NOPECHA_WARM = True
        return True

    if idle_giveup is None:
        idle_giveup = RECAPTCHA_IDLE_GIVEUP if _NOPECHA_WARM else RECAPTCHA_IDLE_GIVEUP_FIRST

    warm_tag = "已热身" if _NOPECHA_WARM else "未热身"
    log("INFO", f"🔍 检测到可见 reCAPTCHA，等待 NopeCHA 自动求解（{warm_tag}, idle 上限 {idle_giveup}s）...")
    start = time.time()
    deadline = start + timeout
    saw_working = False

    while time.time() < deadline:
        state = get_recaptcha_state(driver)
        if state == "solved":
            elapsed = time.time() - start
            _NOPECHA_WARM = True
            log("INFO", f"✅ reCAPTCHA 已自动求解（用时 {elapsed:.0f}s）")
            return True
        if state == "working":
            saw_working = True

        elapsed = time.time() - start
        if (not saw_working) and elapsed >= idle_giveup:
            log("WARN", f"⚠️ {idle_giveup}s 内 NopeCHA 未触发挑战（一直 idle），提前放弃")
            return False
        time.sleep(2)

    log("WARN", f"⚠️ reCAPTCHA 等待超时（{timeout}s），未获取到 token")
    return False


def dismiss_cookie(driver):
    selectors = [
        "#close-cookie-consent-btn", 
        "button:has-text('Accept')", 
        "button:has-text('I accept')",
        ".cookie-consent button", 
        "button[aria-label='Close']",
    ]
    for sel in selectors:
        try:
            for b in driver.find_elements(By.CSS_SELECTOR, sel):
                if b.is_displayed():
                    b.click()
                    log("INFO", "关闭 cookie")
                    time.sleep(1)
                    return
        except Exception:
            continue

    try:
        hidden = driver.execute_script("""
            let hid = false;
            document.querySelectorAll('div[style*="fixed"], div[style*="absolute"], .cookie-banner, #cookie-banner').forEach(el => {
                const text = (el.textContent || '').toLowerCase();
                if (text.includes('cookie') || text.includes('privacy')) {
                    el.style.setProperty('transform', 'translateY(200%)', 'important');
                    el.style.setProperty('pointer-events', 'none', 'important');
                    hid = true;
                }
            });
            return hid;
        """)
        if hidden:
            log("INFO", "通过 JS 移开 cookie 提示条")
    except Exception:
        pass


def wait_ready(driver, timeout=15):
    for _ in range(timeout * 2):
        try:
            if driver.execute_script("return document.readyState") == "complete":
                return
        except Exception:
            pass
        time.sleep(0.5)


def hide_social_login(driver):
    try:
        driver.execute_script("""() => {
            const OAUTH_RE = /github|google|nodeloc|microsoft|apple|twitter|facebook|oauth/i;
            const isOAuth = (el) => {
                if (!el) return false;
                const href = (el.getAttribute('href') || '').toLowerCase();
                const onclick = (el.getAttribute('onclick') || '').toLowerCase();
                const txt = (el.textContent || '').toLowerCase().trim();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const title = (el.getAttribute('title') || '').toLowerCase();
                return OAUTH_RE.test(href + ' ' + onclick + ' ' + txt + ' ' + aria + ' ' + title);
            };
            const kill = (el) => {
                if (!el) return;
                el.style.setProperty('display', 'none', 'important');
                el.style.setProperty('pointer-events', 'none', 'important');
                if (el.tagName === 'A' || el.tagName === 'BUTTON') {
                    el.removeAttribute('href');
                    el.removeAttribute('onclick');
                    el.setAttribute('disabled', 'disabled');
                }
            };
            document.querySelectorAll('a, button').forEach(el => {
                if (isOAuth(el)) kill(el);
            });
        }""")
        log("INFO", "✅ 已禁用 OAuth 按钮")
    except Exception as e:
        log("WARN", f"禁用 OAuth 按钮失败: {e}")

# ══════════════════════════════════════════════════════════════════
#  ── hCaptcha 破解 (AI) ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
def _ensure_hc_src():
    if os.path.isdir(HC_SRC_DIR) and HC_SRC_DIR not in sys.path:
        sys.path.insert(0, HC_SRC_DIR)

_SENSITIVE_TOKENS = (
    "hcaptcha_challenger", "hcaptcha-challenger", "playwright",
    "nopecha", "openai", "gemini", "agnes",
    "image_label", "image_drag", "challengeexception",
)

def _sanitize(text):
    out = str(text)
    for token in _SENSITIVE_TOKENS:
        out = re.sub(re.escape(token), "***", out, flags=re.I)
    return out

_LOGS_QUIETED = False

def _quiet_library_logs(respect_env=False):
    global _LOGS_QUIETED
    if _LOGS_QUIETED or respect_env:
        return
    _LOGS_QUIETED = True
    try:
        from loguru import logger
        logger.remove()
        logger.add(sys.stdout, level="INFO")
    except Exception:
        pass

def _patch_robotic_arm_locator():
    from hcaptcha_challenger.agent.challenger import RoboticArm
    if getattr(RoboticArm, "_vps8_patched", False):
        return
    orig = RoboticArm.get_challenge_frame_locator

    async def robust(self):
        for _ in range(15):
            fr = await orig(self)
            if fr is not None:
                return fr
            await asyncio.sleep(1)
        return None

    RoboticArm.get_challenge_frame_locator = robust
    RoboticArm._vps8_patched = True

def _patch_robotic_arm_skip():
    from hcaptcha_challenger.agent.challenger import RoboticArm
    if getattr(RoboticArm, "_vps8_skip_patched", False):
        return
    orig = RoboticArm.check_challenge_type

    async def _dom_prompt(self):
        try:
            frame = await self.get_challenge_frame_locator()
            if frame is None:
                return ""
            for sel in ("//div[@class='prompt-text']", "//div[contains(@class,'prompt')]"):
                el = frame.locator(sel)
                if await el.count():
                    return (await el.first.inner_text(timeout=2000)).strip()
        except Exception:
            return ""
        return ""

    async def patched(self):
        self._challenge_prompt = None
        result = await orig(self)
        for _ in range(BLOCKED_MAX_SKIP):
            prompt = (getattr(self, "_challenge_prompt", None) or "").strip()
            shown = prompt or await self._dom_prompt()
            log("INFO", f"🎯 挑战题型: {shown or '(未识别)'}")
            if not prompt or not any(bp in prompt.lower() for bp in BLOCKED_PROMPTS):
                return result
            log("INFO", "🎯 遇到困难题型，已换题")
            await self.refresh_challenge()
            await self.page.wait_for_timeout(2000)
            self._challenge_prompt = None
            result = await orig(self)
        return result

    RoboticArm.check_challenge_type = patched
    RoboticArm._dom_prompt = _dom_prompt
    RoboticArm._vps8_skip_patched = True
    if BLOCKED_PROMPTS:
        log("INFO", f"🎯 困难题型跳过规则已加载（{len(BLOCKED_PROMPTS)} 项）")

def _hc_token_ready(driver):
    try:
        val = driver.execute_script(
            "return (document.querySelector('textarea[name=\"h-captcha-response\"]')?.value || "
            "document.querySelector('iframe[data-hcaptcha-response]')?.getAttribute('data-hcaptcha-response') || "
            "(window.hcaptcha && typeof window.hcaptcha.getResponse === 'function' ? window.hcaptcha.getResponse() : '') || "
            "'');"
        )
        return val if len((val or "").strip()) > 20 else None
    except Exception:
        return None

async def _hc_agent_solve(driver, rounds):
    from playwright.async_api import async_playwright
    from hcaptcha_challenger import AgentV, AgentConfig

    dbg = (driver.capabilities.get("goog:chromeOptions") or {}).get("debuggerAddress")
    if not dbg:
        raise RuntimeError("拿不到 Chrome 调试端口")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://{dbg}")
        try:
            ctx = browser.contexts[0]
            page = next((pg for pg in ctx.pages if "vps8.zz.cd" in (pg.url or "")), None)
            if page is None and ctx.pages:
                page = ctx.pages[0]
            if page is None:
                raise RuntimeError("CDP 接管失败：找不到页面")

            try:
                already = await page.evaluate(
                    "() => document.querySelector('iframe[src*=\"hcaptcha\"]') "
                    "|| document.querySelector('.h-captcha')")
            except Exception:
                already = False
                
            if not already:
                await page.reload()
                await page.wait_for_load_state("load")
                await asyncio.sleep(6)

            for rnd in range(1, rounds + 1):
                if _hc_token_ready(driver):
                    log("INFO", f"轮次 {rnd}: 已存在 token")
                    return True
                
                log("INFO", f"验证码处理轮次 {rnd}/{rounds}")
                agent = AgentV(page=page, agent_config=AgentConfig())
                
                clicked = False
                for attempt in range(3):
                    try:
                        await agent.robotic_arm.click_checkbox()
                        clicked = True
                        break
                    except Exception as e:
                        err_msg = str(e)
                        if "'NoneType' object has no attribute" in err_msg or "not visible" in err_msg:
                            log("WARN", f"轮次 {rnd} 元素丢失/未就绪，等待重试 ({attempt+1}/3)...")
                            await asyncio.sleep(3)
                        else:
                            log("WARN", f"轮次 {rnd} 点击异常: {_sanitize(err_msg[:120])}")
                            break
                            
                if not clicked:
                    continue

                ready = False
                poll_deadline = time.time() + 30
                while time.time() < poll_deadline:
                    await asyncio.sleep(2)
                    try:
                        if await agent.robotic_arm.get_challenge_frame_locator() is not None:
                            ready = True
                            break
                    except Exception:
                        pass
                        
                if not ready:
                    log("WARN", f"轮次 {rnd}: 验证码未出现")
                    continue

                try:
                    await agent.wait_for_challenge()
                except Exception as e:
                    log("WARN", f"轮次 {rnd} 异常: {_sanitize(str(e)[:120])}")

                if _hc_token_ready(driver):
                    log("INFO", f"✅ 轮次 {rnd} token 已就绪")
                    return True
                await asyncio.sleep(2)
            return False
        finally:
            try:
                await browser.close()
            except Exception:
                pass

def solve_hcaptcha_agent(driver, rounds=2) -> bool:
    missing = [v for v in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL") if not os.getenv(v)]
    if missing:
        log("ERROR", "必要的凭据未配置")
        return False
    explicit_log_level = os.getenv("LOG_LEVEL")
    if not explicit_log_level:
        os.environ["LOG_LEVEL"] = "SUCCESS"
    _ensure_hc_src()
    try:
        _patch_robotic_arm_locator()
        _patch_robotic_arm_skip()
    except Exception as e:
        log("ERROR", f"组件准备失败: {_sanitize(str(e)[:200])}")
        return False
    _quiet_library_logs(respect_env=bool(explicit_log_level))
    try:
        return asyncio.run(_hc_agent_solve(driver, rounds))
    except Exception as e:
        log("ERROR", f"流程失败: {_sanitize(str(e)[:200])}")
        return False

def hide_recaptcha_iframes(driver):
    try:
        driver.execute_script("""() => {
            for (const iframe of document.querySelectorAll('iframe')) {
                if (iframe.src && iframe.src.includes('recaptcha')) {
                    iframe.style.setProperty('display', 'none', 'important');
                    iframe.style.setProperty('visibility', 'hidden', 'important');
                }
            }
        }""")
    except Exception:
        pass

def trigger_hcaptcha_checkbox(driver):
    try:
        if _hc_token_ready(driver):
            return True
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha.com']")
        for frame in frames:
            src = frame.get_attribute("src") or ""
            if "sitekey" in src or "checkbox" in src:
                driver.switch_to.frame(frame)
                try:
                    box = driver.find_element(By.ID, "checkbox") or driver.find_element(By.ID, "anchor")
                    if box and box.is_displayed():
                        box.click()
                        log("INFO", "🖱️ 已触发点击 hCaptcha 选框以激活求解")
                except Exception:
                    pass
                driver.switch_to.default_content()
                break
    except Exception:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

def wait_hcaptcha_nopecha_solved(driver, timeout=35):
    if not has_hcaptcha_on_page(driver):
        return True
    if _hc_token_ready(driver):
        log("INFO", "✅ hCaptcha 已存在有效 token")
        return True
    log("INFO", f"🔍 等待 NopeCHA 自动求解 hCaptcha（上限 {timeout}s）...")
    time.sleep(2)
    trigger_hcaptcha_checkbox(driver)
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        if _hc_token_ready(driver):
            elapsed = time.time() - start
            log("INFO", f"✅ NopeCHA 已成功求解 hCaptcha（用时 {elapsed:.0f}s）")
            return True
        time.sleep(1.5)
    return False

def get_sitekey(driver):
    try:
        sk = driver.execute_script("""
            const el = document.querySelector('[data-sitekey]');
            if (el) return el.getAttribute('data-sitekey');
            for (const f of document.querySelectorAll('iframe[src*="sitekey="]')) {
                const m = f.src.match(/sitekey=([a-f0-9-]+)/i);
                if (m) return m[1];
            }
            return '';
        """)
        if sk:
            return sk
    except Exception:
        pass
    return "7f30f3d4-581c-4a3c-a8a2-4f6ed25006e4"

def solve_hcaptcha_nonecap(driver) -> bool:
    global _LAST_CAPTCHA_TOKEN
    key = os.getenv("NONECAP_KEY", "").strip() or os.getenv("NONECAP_API_KEY", "").strip()
    if not key:
        return False
    try:
        from nonecap import NoneCap
        sitekey = get_sitekey(driver)
        current_url = driver.current_url or "https://vps8.zz.cd/login"
        log("INFO", f"🚀 正在调用 NoneCap 极速求解 hCaptcha (sitekey={sitekey[:8]}...)...")
        nc = NoneCap(api_key=key)
        res = nc.solve(
            type="hcaptcha",
            sitekey=sitekey,
            url=current_url,
            timeout=45
        )
        if res.status == "solved" and res.token:
            token = res.token
            _LAST_CAPTCHA_TOKEN = token
            log("INFO", "✅ NoneCap 成功获取 hCaptcha token！")
            driver.execute_script("""
                const token = arguments[0];
                window.__nonecap_token = token;
                window.__captcha_token = token;
                document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]').forEach(el => {
                    el.value = token;
                });
                if (window.hcaptcha && typeof window.hcaptcha.setData === 'function') {
                    try { window.hcaptcha.setData(token); } catch(e){}
                }
                // 隐藏遮挡的 iframe 与遮罩，防止遮挡按钮点击
                document.querySelectorAll('iframe').forEach(f => {
                    const src = f.getAttribute('src') || '';
                    if (src.includes('hcaptcha.com')) {
                        f.style.setProperty('display', 'none', 'important');
                        f.style.setProperty('visibility', 'hidden', 'important');
                        if (f.parentElement && f.parentElement.tagName === 'DIV') {
                            f.parentElement.style.setProperty('display', 'none', 'important');
                        }
                    }
                });
                document.querySelectorAll('div[style*="position: fixed"], div[style*="position:fixed"]').forEach(el => {
                    const s = el.getAttribute('style') || '';
                    if (s.includes('z-index') || s.includes('opacity') || s.includes('cursor: pointer')) {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('pointer-events', 'none', 'important');
                    }
                });
            """, token)
            time.sleep(1)
            return True
        else:
            log("WARN", f"NoneCap 响应状态: {res.status}")
    except Exception as e:
        log("WARN", f"NoneCap 求解异常: {e}")
    return False

def solve_captcha(driver, timeout=40) -> bool:
    time.sleep(2)
    has_hc = has_hcaptcha_on_page(driver)
    if not has_hc:
        for f in driver.find_elements(By.CSS_SELECTOR, "iframe"):
            src = f.get_attribute("src") or ""
            if "hcaptcha" in src:
                has_hc = True
                break

    if not has_hc:
        log("INFO", "未检测到 hCaptcha 验证码")
        return True

    # 1. 检查是否已有可用 token
    if _hc_token_ready(driver):
        log("INFO", "✅ hCaptcha 已存在有效 token")
        return True

    # 2. 首选：使用 NoneCap 极速自动求解
    if solve_hcaptcha_nonecap(driver):
        return True

    # 3. 备选：使用 NopeCHA 自动求解
    log("INFO", "🔍 尝试使用 NopeCHA 自动求解...")
    if wait_hcaptcha_nopecha_solved(driver, timeout=25):
        return True

    # 4. 备选：尝试使用 AI 模型求解
    log("INFO", "🔄 NopeCHA 未就绪，尝试使用 AI 模型求解 hCaptcha...")
    try:
        if solve_hcaptcha_agent(driver, rounds=1):
            if _hc_token_ready(driver):
                log("INFO", "✅ AI 模型成功求解 hCaptcha")
                return True
    except Exception as e:
        log("WARN", f"AI 模型求解过程异常: {e}")

    if _hc_token_ready(driver):
        log("INFO", "✅ hCaptcha token 已就绪")
        return True

    log("WARN", "⚠️ hCaptcha 未能在时限内获取到 token，尝试继续流程...")
    return False

# ══════════════════════════════════════════════════════════════════
#  ── IP 地理探测 & NopeCHA patch ────────────────────────────────
# ══════════════════════════════════════════════════════════════════
CONFIG = {"fallback_timezone": "America/Los_Angeles", "fallback_locale": "en-US"}

def detect_proxy_geo(proxy=None, timeout=10):
    proxies = None
    if proxy:
        proxy_url = proxy.replace("socks5://", "socks5h://").replace("socks://", "socks5h://")
        proxies = {"http": proxy_url, "https": proxy_url}
    url = "http://ip-api.com/json/?fields=status,message,country,countryCode,regionName,city,timezone,query"
    try:
        r = requests.get(url, proxies=proxies, timeout=timeout)
        data = r.json()
        if data.get("status") != "success":
            return None
        cc = data.get("countryCode", "")
        lang_map = {"US": "en-US", "GB": "en-GB", "AU": "en-AU", "CA": "en-CA", "DE": "de-DE", "FR": "fr-FR", "JP": "ja-JP", "KR": "ko-KR"}
        lang = lang_map.get(cc, "en-US")
        result = {
            "country": data.get("country", ""), "regionName": data.get("regionName", ""),
            "city": data.get("city", ""), "timezone": data.get("timezone", CONFIG["fallback_timezone"]), "lang": lang
        }
        log("INFO", f"🌍 代理出口地理: [{result['country']} {result['regionName']} {result['city']}] 时区={result['timezone']} 语言={result['lang']}")
        return result
    except Exception as e:
        log("WARN", f"IP 地理查询失败: {e}")
        return None

def check_nopecha_availability(proxy=None):
    raw_key = os.getenv("NOPECHA_KEY", "").strip()
    keys = [k.strip() for k in raw_key.splitlines() if k.strip()] if raw_key else []
    proxies = None
    if proxy:
        proxy_url = proxy.replace("socks5://", "socks5h://").replace("socks://", "socks5h://")
        proxies = {"http": proxy_url, "https": proxy_url}

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    for idx, key in enumerate(keys, start=1):
        try:
            resp = session.get(f"https://api.nopecha.com/v1/status?key={key}", timeout=15)
            data = resp.json()
            if "error" not in data and data.get("credit", 0) > 0:
                log("INFO", f"✅ NopeCHA 已选择有效 KEY (额度: {data['credit']})")
                return key, None
        except Exception:
            pass
    if keys:
        log("INFO", "所有 NopeCHA Key 均无可用额度，回退到试用模式")
    try:
        resp = session.get("https://api.nopecha.com/v1/status", timeout=15)
        data = resp.json()
        if "error" not in data and data.get("credit", 0) > 0:
            log("INFO", "✅ NopeCHA 当前 IP 具备试用资格")
            return "", None
    except Exception:
        pass
    log("WARN", "NopeCHA 无可用额度，仍继续使用试用模式")
    return "", None

def patch_nopecha(nopecha_path: str, api_key: str) -> bool:
    candidates = [os.path.join(nopecha_path, "assets", "qrmm9f.js"), os.path.join(nopecha_path, "background.js")]
    bg = next((c for c in candidates if os.path.exists(c)), None)
    if not bg:
        return False
    try:
        content = open(bg, encoding="utf-8").read()
        if "NopeCHA-Inject" in content:
            log("INFO", "NopeCHA 已注入")
            return True
        key_str = api_key or ""
        inject = f"""// NopeCHA-Inject
(function(){{
    const s={{
        enabled: true,
        key: "{key_str}",
        auto_solve_hcaptcha: true,
        auto_solve_recaptcha: true,
        auto_solve_turnstile: true,
        hcaptcha_auto_open: true,
        hcaptcha_auto_solve: true,
        recaptcha_auto_open: true,
        recaptcha_auto_solve: true
    }};
    function w(){{
        chrome.storage.local.set({{settings:s,key:"{key_str}"}});
        chrome.storage.sync&&chrome.storage.sync.set({{settings:s,key:"{key_str}"}});
    }}
    w();setTimeout(w,500);setTimeout(w,1500);setTimeout(w,3000);
}})();\n"""
        open(bg, "w", encoding="utf-8").write(inject + content)
        if api_key:
            log("INFO", "✅ NopeCHA patch 成功 (已开启 hCaptcha 与 reCAPTCHA 自动求解)")
        else:
            log("INFO", "✅ NopeCHA patch 成功 (已开启 hCaptcha 与 reCAPTCHA 试用模式自动求解)")
        return True
    except Exception as e:
        log("ERROR", f"NopeCHA patch 失败: {e}")
        return False

# ══════════════════════════════════════════════════════════════════
#  ── 签到流程 ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
def fail(driver, tg_token, tg_chat_id, email, name, reason):
    finish(driver, tg_token, tg_chat_id, name, build_caption(email, fail_reason=reason))
    return False, f"❌ {reason}"

def wait_signin_button(driver, timeout=20):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#points-signin-submit"))
        )
    except Exception:
        return None

# ★ 核心优化：确保验证码就绪与遮罩消除
def ensure_signin_page_captcha_ready(driver, label=""):
    if has_hcaptcha_on_page(driver):
        log("INFO", f"{label}检测到 hCaptcha，自动处理中...")
        if not solve_captcha(driver):
            return False
        _hide_hcaptcha_ui(driver)
        time.sleep(1)

    if has_recaptcha_on_page(driver):
        log("INFO", f"{label}检测到 reCAPTCHA，正在等待求解...")
        if not wait_recaptcha_solved(driver):
            tok = get_recaptcha_token(driver)
            if tok:
                log("INFO", f"✅ {label} reCAPTCHA 已存在有效 token")
            else:
                log("ERROR", f"❌ {label} reCAPTCHA 未能成功求解，放弃本次盲目提交以防 9999 报错")
                return False
            
    return True

def do_signin_flow(driver, tg_token, tg_chat_id, email, attempt_num):
    global _LAST_CAPTCHA_TOKEN
    log("INFO", f"=== 签到流程第 {attempt_num} 次 ===")
    dismiss_cookie(driver)

    if not ensure_signin_page_captcha_ready(driver, label="进入时"):
        log("ERROR", f"第 {attempt_num} 次进入时验证码未就绪")
        screenshot(driver, f"err_captcha_{attempt_num}")
        return False, "验证码未就绪"

    log("INFO", "✅ 进入时验证码就绪")

    btn = wait_signin_button(driver, 10)
    if btn is None:
        return False, "找不到签到按钮"

    log("INFO", ">>> 直接通过内部 API 提交签到")
    time.sleep(random.uniform(1.5, 3.0))

    tok = _LAST_CAPTCHA_TOKEN or get_recaptcha_token(driver) or _hc_token_ready(driver) or ""
    if tok:
        _LAST_CAPTCHA_TOKEN = tok

    try:
        driver.set_script_timeout(30)
        # 使用 JS Fetch 发起请求，确保同时携带 g-recaptcha-response 与 h-captcha-response，并优先采用 urlencoded 编码提交
        res_json = driver.execute_async_script("""
            const passedToken = arguments[0] || '';
            const done = arguments[arguments.length - 1];
            const timeoutId = setTimeout(() => done({ error: "请求超时" }), 25000);
            
            try {
                const form = document.querySelector('#points-signin-submit')?.closest('form') || document.querySelector('form[action*="points"]');
                if (!form) { clearTimeout(timeoutId); done({ error: "找不到表单" }); return; }
                
                const rawAction = form.getAttribute('action') || 'client/points/signin';
                const url = (window.bb && typeof window.bb.restUrl === 'function')
                    ? window.bb.restUrl(rawAction)
                    : (rawAction.startsWith('http') ? rawAction : '/api/' + rawAction.replace(/^\\//, ''));
                const method = form.getAttribute('method') || 'POST';
                const formData = new FormData(form);
                
                if (!formData.get('CSRFToken')) {
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    if (meta) formData.append('CSRFToken', meta.getAttribute('content'));
                }
                
                const data = {};
                for (const [key, value] of formData.entries()) { data[key] = value; }
                
                // 关键点：将解出来的 token 填入 g-recaptcha-response 与 h-captcha-response
                const grEl = document.querySelector('textarea[name="g-recaptcha-response"]') || document.getElementById('g-recaptcha-response');
                const hcEl = document.querySelector('textarea[name="h-captcha-response"]');
                const activeToken = passedToken || window.__nonecap_token || window.__captcha_token || (grEl ? grEl.value : '') || (hcEl ? hcEl.value : '');
                
                if (activeToken) {
                    data['g-recaptcha-response'] = activeToken;
                    data['h-captcha-response'] = activeToken;
                    if (grEl) grEl.value = activeToken;
                    if (hcEl) hcEl.value = activeToken;
                }
                
                const searchParams = new URLSearchParams();
                for (const k in data) { searchParams.append(k, data[k]); }

                // 优先以 application/x-www-form-urlencoded 发送，使 PHP 原生解析入 $_POST
                fetch(url, {
                    method: method,
                    headers: { 
                        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 
                        'Accept': 'application/json, text/javascript, */*; q=0.01',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: searchParams.toString()
                })
                .then(res => res.json())
                .then(res => {
                    // 若接口报错且和验证码相关，尝试用 JSON 格式兜底再次请求
                    if (res && res.error && String(res.error.message || res.error).toLowerCase().includes('recaptcha')) {
                        fetch(url, {
                            method: method,
                            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                            body: JSON.stringify(data)
                        })
                        .then(r => r.json())
                        .then(r => { clearTimeout(timeoutId); done(r); })
                        .catch(e => { clearTimeout(timeoutId); done(res); });
                    } else {
                        clearTimeout(timeoutId);
                        done(res);
                    }
                })
                .catch(err => {
                    fetch(url, {
                        method: method,
                        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                        body: JSON.stringify(data)
                    })
                    .then(r => r.json())
                    .then(r => { clearTimeout(timeoutId); done(r); })
                    .catch(e => { clearTimeout(timeoutId); done({ error: e.toString() }); });
                });
            } catch(e) {
                clearTimeout(timeoutId);
                done({ error: "JS 异常: " + e.toString() });
            }
        """, tok)

        if not res_json:
            log("WARN", "API 无响应")
            return False, "无响应"
        
        if res_json.get("error"):
            err = res_json["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            if any(k in msg.lower() for k in ["already", "已签到", "重复", "已领取"]):
                log("INFO", f"ℹ️ 今日已签到: {msg}")
                driver.get(SIGNIN_URL)
                wait_ready(driver)
                time.sleep(2)
                return True, extract_info(driver.page_source)
            log("WARN", f"签到接口报错: {msg}")
            if "9999" in str(err) or "captcha" in str(msg).lower():
                return False, "9999 (验证码校验失败)"
            return False, msg[:50]

        log("INFO", "✅ 签到接口返回成功")
        time.sleep(2)
        
        # 成功后刷新页面，获取最新的 HTML 状态（积分/连续天数）
        driver.get(SIGNIN_URL)
        wait_ready(driver)
        time.sleep(3)
        
        html = driver.page_source
        info = extract_info(html)
        return True, info

    except Exception as e:
        log("WARN", f"API 提交异常: {e}")
        return False, "API 异常"

# ══════════════════════════════════════════════════════════════════
#  ── 自动兑换积分 & 续期 ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
def auto_redeem_points(driver, current_points: str):
    pt = points_to_int(current_points)
    if pt is None:
        return False, "积分无法识别"
    if pt < 100:
        return False, f"未满100分(当前{pt}分)，无需兑换"
    log("INFO", "积分足够，开始兑换")
    try:
        try:
            driver.find_element(By.CSS_SELECTOR, f'a[href="{REDEEM_URL}"]').click()
        except Exception:
            driver.get(REDEEM_URL)
        time.sleep(5)
        screenshot(driver, "05_redeem_loaded")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="points"]')))
        points_input = driver.find_element(By.CSS_SELECTOR, 'input[name="points"]')
        points_input.click()
        points_input.clear()
        points_input.send_keys("100")
        time.sleep(1)
        screenshot(driver, "06_redeem_filled")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'button[type="submit"]')))
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        log("INFO", "已点击立即兑换")
        time.sleep(5)
        screenshot(driver, "07_redeem_result")
        return True, "✅ 已提交100积分兑换"
    except Exception as e:
        log("ERROR", f"兑换异常: {e}")
        return False, f"❌ 兑换失败"

def parse_expiry_date(text):
    if not text:
        return None
    clean = re.sub(r'[\s\u202f\u00A0]+', ' ', text.strip())
    cn_match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})', clean)
    if cn_match:
        y, m, d, h, minute = map(int, cn_match.groups())
        return datetime(y, m, d, h, minute)
    for fmt in ["%b %d, %Y, %I:%M %p", "%b %d, %Y, %I:%M%p", "%b %d %Y %I:%M %p"]:
        try:
            return datetime.strptime(clean, fmt)
        except Exception:
            continue
    return None

def extract_services_from_page(driver):
    services = []
    try:
        for row in driver.find_elements(By.CSS_SELECTOR, "table.table tbody tr"):
            try:
                tds = row.find_elements(By.CSS_SELECTOR, "td")
                product_text = (tds[1].find_element(By.CSS_SELECTOR, "span").text or "").strip()
                expiry_text  = tds[3].text.strip()
                manage_link  = tds[5].find_elements(By.CSS_SELECTOR, "a")
                manage_url   = manage_link[0].get_attribute("href") if manage_link else None
                if not product_text or not expiry_text:
                    continue
                stype = "domain" if re.search(r'(?i)domain|域名', product_text) else "vps"
                name = (re.sub(r'(?i)(域名|domain|注册|registration)', '', product_text).strip() if stype == "domain" else product_text.strip())
                services.append({"name": name or product_text, "type": stype, "expiry_text": expiry_text, "manage_url": manage_url})
            except Exception:
                pass
    except Exception:
        pass
    return services

def renew_single_vps(driver, display_name, manage_url):
    log("INFO", f">>> 续费 {display_name}")
    safe_name = display_name.replace(' ', '_').replace('#', '')
    try:
        driver.get(manage_url)
        time.sleep(5)
        try:
            renew_btn = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#renewal-button")))
        except Exception:
            return "❌ 找不到续费按钮"
        
        driver.execute_script("window.confirm = function(){ return true; };")
        driver.execute_script("window.alert = function(){};")
        driver.execute_script("arguments[0].click();", renew_btn)
        log("INFO", "已点击立即续费")
        
        for _ in range(3):
            time.sleep(1)
            try:
                alert = driver.switch_to.alert
                alert.accept()
                break
            except Exception:
                pass
                
        deadline = time.time() + 60
        invoice_url = None
        while time.time() < deadline:
            time.sleep(2)
            if "invoice" in driver.current_url.lower():
                invoice_url = driver.current_url
                break
        if not invoice_url:
            return "❌ 未跳转到发票页"
            
        time.sleep(3)
        balance_found = False
        for sel in ['label[onclick*="ClientBalance"]', 'input[type="radio"][value*="balance"]', '.payment-method label']:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        balance_found = True
                        break
            except Exception:
                continue
            if balance_found:
                break
                
        if not balance_found:
            return "❌ 找不到余额支付选项"
            
        time.sleep(2)
        payment_found = False
        for sel in ["#single", 'a[href*="allow_subscription=0"]', 'a.btn-success']:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        payment_found = True
                        break
            except Exception:
                continue
            if payment_found:
                break
                
        if not payment_found:
            return "❌ 找不到支付按钮"
            
        time.sleep(5)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if any(kw in driver.page_source for kw in ["已支付", "支付成功", "Paid", "Payment successful"]):
                    return "✅ 续期成功"
            except Exception:
                pass
            time.sleep(3)
        return "⚠️ 支付结果未确认"
    except Exception as e:
        return f"❌ 续费失败: {str(e)[:80]}"

def check_and_renew_all(driver):
    log("INFO", ">>> 查询服务列表")
    def _load_services():
        driver.get(SERVICE_URL)
        wait_ready(driver)
        time.sleep(5)
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table")))
            time.sleep(2)
            return True
        except Exception:
            return False

    loaded = _load_services()
    if not loaded:
        log("WARN", "首次加载产品表格失败，刷新重试...")
        time.sleep(5)
        _load_services()

    screenshot(driver, "08_service_loaded")
    services = extract_services_from_page(driver)
    if not services:
        return []

    services_info = []
    vps_counter = 1
    domain_counter = 1
    for s in services:
        expiry_dt = parse_expiry_date(s["expiry_text"])
        cn_date = expiry_dt.strftime("%Y年%m月%d日") if expiry_dt else "未知"
        info = {"name": s["name"], "type": s["type"], "expiry_display": cn_date}
        if s["type"] == "vps":
            display_index = f"VPS #{vps_counter}"
            vps_counter += 1
            if expiry_dt and (expiry_dt - datetime.now()).total_seconds() / 86400 < RENEW_DAYS_THRESHOLD:
                info["renew_result"] = renew_single_vps(driver, display_index, s["manage_url"])
        else:
            domain_counter += 1
        services_info.append(info)
    return services_info

# ══════════════════════════════════════════════════════════════════
#  ── 主流程 ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════
def vps8_checkin():
    tg_token     = os.getenv("TG_BOT_TOKEN")
    tg_chat_id   = os.getenv("TG_CHAT_ID")
    account_raw  = os.getenv("VPS8")
    proxy_server = os.getenv("PROXY_SERVER", "").strip() or os.getenv("PROXY_HTTP", "").strip()

    if not account_raw:
        return False, "❌ 缺少 VPS8"

    missing = [v for v in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL") if not os.getenv(v)]
    if missing:
        msg = f"缺少环境变量: {', '.join(missing)}"
        send_tg_text(tg_token, tg_chat_id, build_caption(account_raw, fail_reason=msg))
        return False, f"❌ {msg}"

    email, password = parse_account(account_raw)
    log("INFO", f"账号: {email[:1]}***")

    geo = detect_proxy_geo(proxy_server)
    browser_timezone = geo["timezone"] if geo else CONFIG["fallback_timezone"]
    browser_locale   = geo["lang"] if geo else CONFIG["fallback_locale"]

    selected_key, _ = check_nopecha_availability(proxy_server)
    patch_nopecha(os.path.abspath(NOPECHA_PATH), selected_key)

    driver = None
    try:
        kwargs = {
            "uc": False, "headless": False, "no_sandbox": True, "disable_gpu": True,
            "extension_dir": os.path.abspath(NOPECHA_PATH), "remote_debug": True,
            "chromium_arg": "--disable-http2,--disable-quic",
        }
        if proxy_server:
            kwargs["proxy"] = proxy_server
        driver = Driver(**kwargs)
        log("INFO", "✅ 浏览器已启动")

        try:
            driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": browser_timezone})
            driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": browser_locale})
            driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"Accept-Language": f"{browser_locale},en;q=0.9"}})
        except Exception:
            pass

        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        time.sleep(5)

        login_ok = False
        for login_retry in range(3):
            log("INFO", f">>> 打开登录页 (第 {login_retry+1}/3 次尝试)")
            driver.get(LOGIN_URL)
            wait_ready(driver)
            time.sleep(4)
            dismiss_cookie(driver)
            hide_social_login(driver)
            screenshot(driver, f"01_login_page_{login_retry+1}")

            # 1. 检查并输入账号密码表单
            if not is_element_present(driver, "#email"):
                log("WARN", "未找到 #email 输入框，等待 5s 重试...")
                time.sleep(5)
                if not is_element_present(driver, "#email"):
                    log("ERROR", "页面未包含登录表单")
                    continue

            log("INFO", "✍️ 正在输入账号与密码...")
            human_type(driver, "#email", email)
            time.sleep(random.uniform(0.5, 1.0))
            human_type(driver, "#password", password)
            time.sleep(random.uniform(0.5, 1.0))

            # 2. 求解验证码（NopeCHA 优先，自动获取 hCaptcha/reCAPTCHA token）
            log("INFO", "🔍 正在检测并求解验证码...")
            solve_captcha(driver, timeout=30)
            hide_recaptcha_iframes(driver)
            time.sleep(1)

            # 3. 提交登录表单
            log("INFO", "🚀 提交登录表单...")
            try:
                driver.execute_script("""
                    document.querySelectorAll('iframe[src*="hcaptcha.com"]').forEach(f => {
                        if (f.src.includes('frame=challenge')) {
                            f.style.setProperty('display', 'none', 'important');
                            f.style.setProperty('visibility', 'hidden', 'important');
                        }
                    });
                """)
                submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
                driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
                time.sleep(0.5)
                submit_btn.click()
            except Exception as e:
                log("WARN", f"点击登录按钮异常: {e}，尝试 JS click")
                driver.execute_script("document.querySelector('button[type=\"submit\"]').click()")

            # 4. 等待登录成功跳转或 API 结果
            deadline = time.time() + 45
            api_login_detected = False
            while time.time() < deadline:
                try:
                    cur = (driver.current_url or "").rstrip("/")
                    # 检测 FOSSBilling API 响应
                    if cur.endswith("/api/guest/client/login"):
                        body = driver.execute_script("return (document.body?.innerText || '').slice(0, 500)").replace(" ", "")
                        if '"error":null' in body or '"role":"client"' in body:
                            api_login_detected = True
                            login_ok = True
                            break
                    # 检测已离开登录页（如跳到 /dashboard 或 /points/signin）
                    if "dashboard" in cur or "points" in cur or (cur != LOGIN_URL_NORM and not is_element_visible(driver, "#email")):
                        login_ok = True
                        break
                except Exception:
                    pass
                time.sleep(1)

            if login_ok:
                log("INFO", "✅ 成功登录账号！")
                if api_login_detected:
                    driver.get(SIGNIN_URL)
                    wait_ready(driver)
                    time.sleep(3)
                break
            else:
                log("WARN", f"第 {login_retry+1} 次登录未跳转，截屏保存...")
                screenshot(driver, f"err_login_attempt_{login_retry+1}")
                time.sleep(5)

        if not login_ok:
            return fail(driver, tg_token, tg_chat_id, email, "err_login", "登录失败")

        log("INFO", "✅ 登录成功")
        screenshot(driver, "02_login_ok")

        if "points/signin" not in (driver.current_url or ""):
            driver.get(SIGNIN_URL)
            wait_ready(driver)
            time.sleep(SIGNIN_PAGE_SETTLE)
        else:
            time.sleep(SIGNIN_PAGE_SETTLE)
        screenshot(driver, "03_signin_page")

        html = driver.page_source
        before_info = extract_info(html)
        points_before = before_info.get("points")
        already_signed = is_signed(html)
        signed = already_signed
        points_gained = None

        if already_signed:
            info = before_info
            dismiss_cookie(driver)
        else:
            info = None

        last_fail_reason = None
        if not signed:
            for retry in range(3):
                if retry > 0:
                    log("INFO", f"外层重试第 {retry+1} 次，重新加载签到页...")
                    time.sleep(10)
                    driver.get(SIGNIN_URL)
                    wait_ready(driver)
                    time.sleep(SIGNIN_PAGE_SETTLE)
                    if is_signed(driver.page_source):
                        info = extract_info(driver.page_source)
                        signed = True
                        already_signed = True
                        break

                ok, result = do_signin_flow(driver, tg_token, tg_chat_id, email, retry + 1)
                if ok:
                    signed = True
                    info = result
                    if points_before not in (None, "未知") and info.get("points") not in (None, "未知"):
                        try:
                            diff = int(info["points"]) - int(points_before)
                            if diff > 0:
                                points_gained = diff
                        except Exception:
                            pass
                    break
                else:
                    last_fail_reason = result
                    log("WARN", f"第 {retry+1} 次签到失败: {result}")
                    screenshot(driver, f"signin_retry_{retry+1}")

        if signed and info:
            redeemed, redeem_result = auto_redeem_points(driver, info.get("points", "未知"))
            if redeemed:
                driver.get(SIGNIN_URL)
                wait_ready(driver)
                time.sleep(3)
                info = extract_info(driver.page_source)
            services_info = check_and_renew_all(driver)
            finish(driver, tg_token, tg_chat_id, "success",
                   build_caption(email, info=info, redeem_result=redeem_result,
                                 services_info=services_info, already_signed=already_signed,
                                 points_before=points_before, points_gained=points_gained))
            return True, "✅ 签到成功" if not already_signed else "ℹ️ 今日已签到"

        finish(driver, tg_token, tg_chat_id, "unknown", build_caption(email, fail_reason=last_fail_reason or "状态未更新", points_before=points_before))
        return False, "⚠️ 状态未确认"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"💥 {str(e)[:200]}"
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    _install_noise_filter()
    ok, msg = vps8_checkin()
    log("RESULT", msg)
    sys.exit(0 if ok else 1)
