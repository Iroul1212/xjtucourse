# -*- coding: utf-8 -*-
"""
西安交通大学选课系统（xkfw）登录模块。

说明：
    学校已将统一身份认证从 org.xjtu.edu.cn/openplatform 迁移到
    login.xjtu.edu.cn/cas（2025-07 后），因此本模块改为对接新 CAS：
    1. 访问选课入口，自动 302 到 CAS 登录页；
    2. 从服务端获取 RSA 公钥，对密码做 PKCS#1 v1.5 加密（带 __RSA__ 前缀）；
    3. 可选进行登录保护(MFA)探测；
    4. 提交登录表单，跟随重定向回到选课系统并保存 ticket；
    5. 调用 register.do 换取选课系统 token。
"""
import base64
import hashlib
import platform
import re
import time
import uuid
from typing import Callable, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

# 真实桌面浏览器 UA（requests 默认 UA 可能被统一认证拒绝）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0"
)

XKFW_INDEX_URL = "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
CAS_PUBLIC_KEY_URL = "https://login.xjtu.edu.cn/cas/jwt/publicKey"
CAS_MFA_DETECT_URL = "https://login.xjtu.edu.cn/cas/mfa/detect"
REGISTER_URL = "https://xkfw.xjtu.edu.cn/xsxkapp/sys/xsxkapp/student/register.do"
CAS_LOGIN_PATH = "/cas/login"
XKFW_HOST = "xkfw.xjtu.edu.cn"

# 选课系统会把会话 token 放在跳转地址里，例如 grablessons.do?token=xxxx
TOKEN_URL_RE = re.compile(r"[?&]token=([0-9A-Za-z._-]{8,})")
TOKEN_BODY_RE = re.compile(r"grablessons\.do\?token=([0-9A-Za-z._-]{8,})")
TOKEN_JSON_RE = re.compile(r"[\"']token[\"']\s*[:=]\s*[\"']([0-9A-Za-z._-]{8,})[\"']")


def _make_fp_visitor_id() -> str:
    """生成一个较为稳定的 32 位十六进制设备指纹（风控按它区分客户端，保持稳定即可）。"""
    info = "|".join([
        platform.system(),
        platform.machine(),
        platform.node(),
        str(uuid.getnode()),
    ])
    return hashlib.sha256(info.encode("utf-8")).hexdigest()[:32]


def build_browser_driver():
    """
    启动一个真实浏览器窗口（优先 Chrome，不可用时回退 Edge）。

    真实浏览器可以正常完成两步验证（短信/扫码），程序随后直接读取其 Cookie。
    """
    try:
        from selenium import webdriver
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 selenium 依赖，无法使用浏览器登录（请先 pip install selenium）"
        ) from exc

    errors = []

    try:
        from selenium.webdriver.chrome.options import Options as ChromeOptions

        options = ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        return webdriver.Chrome(options=options)
    except Exception as exc:  # noqa: BLE001 - 需要尝试下一个浏览器
        errors.append(f"Chrome 启动失败：{exc}")

    try:
        from selenium.webdriver.edge.options import Options as EdgeOptions

        options = EdgeOptions()
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        return webdriver.Edge(options=options)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Edge 启动失败：{exc}")

    raise RuntimeError("无法启动浏览器（请确认已安装 Chrome 或 Edge）：" + "；".join(errors))


def _token_from_driver(driver, timeout: float = 15.0) -> str:
    """从浏览器地址栏 / 本地存储中尽量取到会话 token。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            current = driver.current_url or ""
        except Exception:  # noqa: BLE001 - 窗口已被关闭
            break
        match = TOKEN_URL_RE.search(current)
        if match:
            return match.group(1)
        time.sleep(0.5)

    for script in (
        "return window.location.href;",
        "return window.sessionStorage.getItem('token');",
        "return window.localStorage.getItem('token');",
    ):
        try:
            value = driver.execute_script(script)
        except Exception:  # noqa: BLE001 - 某些页面不允许执行脚本
            value = None
        if value:
            text = str(value).strip().strip("\"'")
            match = TOKEN_URL_RE.search(text)
            if match:
                return match.group(1)
            plain = re.fullmatch(r"[0-9A-Za-z._-]{16,}", text)
            if plain:
                return plain.group(0)
    return ""


class Login:
    def __init__(self):
        self.cookies = {}
        self.ticket = ""
        self.name = ""
        self.token = ""
        self._session: Optional[requests.Session] = None
        self._post_url = ""
        self._execution = ""
        self._mfa_state = ""
        self._fp_visitor_id = _make_fp_visitor_id()
        self._rsa_public_key: Optional[str] = None

    # ---------------- 静态工具 ----------------
    @staticmethod
    def _extract_execution(html: str) -> Optional[str]:
        """从登录页 HTML 中提取账号密码登录表单(fm1)的 execution 字段。"""
        m = re.search(
            r'<form[^>]*\bid="fm1".*?name="execution"[^>]*value="([^"]*)"',
            html,
            re.S,
        )
        if m:
            return m.group(1)
        m2 = re.search(r'name="execution"[^>]*value="([^"]*)"', html)
        return m2.group(1) if m2 else None

    @staticmethod
    def _extract_alert_title(html: str) -> Optional[str]:
        m = re.search(r'<el-alert[^>]*title="([^"]*)"', html)
        if m:
            return m.group(1)
        return None

    # ---------------- 密码加密 ----------------
    def _encrypt_password(self, password: str) -> str:
        """RSA(PKCS#1 v1.5) 加密密码，加 __RSA__ 前缀，与前端 JSEncrypt 一致。"""
        if self._rsa_public_key is None:
            r = self._session.get(
                CAS_PUBLIC_KEY_URL,
                headers={"Referer": self._post_url},
                timeout=15,
            )
            r.raise_for_status()
            self._rsa_public_key = r.text
        key = RSA.import_key(self._rsa_public_key.encode("utf-8"))
        cipher = PKCS1_v1_5.new(key)
        encrypted = cipher.encrypt(password.encode("utf-8"))
        return "__RSA__" + base64.b64encode(encrypted).decode("ascii")

    # ---------------- 登录保护(MFA)探测 ----------------
    def _mfa_detect(self, username: str, enc_password: str) -> Tuple[bool, str]:
        """探测该账号是否需要两步验证。返回 (是否必须 MFA, 提示信息)。"""
        data = {
            "username": username,
            "password": enc_password,
            "fpVisitorId": self._fp_visitor_id,
            "loginType": "passwordLogin",
        }
        r = self._session.post(
            CAS_MFA_DETECT_URL,
            data=data,
            headers={"Referer": self._post_url},
            timeout=15,
        )
        r.raise_for_status()
        try:
            j = r.json()
        except Exception:
            return True, "登录保护状态检测失败（服务器返回异常）"
        if j.get("code") != 0:
            return True, f"登录保护状态检测失败：{j.get('message') or j.get('msg')}"
        data = j.get("data") or {}
        self._mfa_state = data.get("state") or ""
        if bool(data.get("need")):
            return True, "该账号开启了登录保护（两步验证/短信/扫码），工具无法自动完成，请先在浏览器中登录一次"
        return False, ""

    # ---------------- 提交登录 ----------------
    def _submit_login(self, username: str, enc_password: str):
        data = {
            "username": username,
            "password": enc_password,
            "execution": self._execution,
            "_eventId": "submit",
            "submit1": "Login1",
            "fpVisitorId": self._fp_visitor_id,
            "captcha": "",
            "currentMenu": "1",
            "failN": "0",
            "mfaState": self._mfa_state,
            "geolocation": "",
            "trustAgent": "",
        }
        resp = self._session.post(
            self._post_url,
            data=data,
            allow_redirects=False,
            timeout=20,
        )
        return self._follow_redirects(resp)

    def _follow_redirects(self, resp: requests.Response) -> requests.Response:
        """手工跟随重定向链，捕捉 CAS ticket 并最终落到选课系统。"""
        url = self._post_url
        current = resp
        for _ in range(15):
            if current.status_code in (301, 302, 303, 307, 308) and "Location" in current.headers:
                loc = urljoin(url, current.headers["Location"])
                m = re.search(r"[?&]ticket=([^&#]+)", loc)
                if m:
                    self.ticket = m.group(1)
                current = self._session.get(loc, allow_redirects=False, timeout=20)
                url = current.url
                continue
            break
        return current

    # ---------------- 结果分类 ----------------
    def _classify_failure(self, resp: requests.Response) -> str:
        text = resp.text
        if resp.status_code == 401:
            return "用户名或密码错误"
        if "/cas/sec/" in text or "二次认证" in text or "选择安全认证" in text:
            return "需要进行二次安全认证，无法自动完成，请先在浏览器中完成一次登录"
        if "请输入验证码" in text and "验证码" in text:
            return "需要输入图形验证码（登录环境异常或失败次数过多触发），无法自动完成"
        if "account-wrap" in text or ("请选择" in text and "身份" in text):
            return "账号关联了多种身份，请使用学号直接登录"
        if "锁定" in text or "冻结" in text or "失败次数过多" in text:
            return "账号可能被临时锁定，请稍后再试"
        alert = self._extract_alert_title(text)
        if alert:
            return f"登录失败：{alert}"
        return "登录失败：用户名或密码错误，或账号存在安全限制"

    # ---------------- 换取选课 token ----------------
    def _register(self, username: str = "") -> Tuple[str, str, str]:
        """尝试调用 register.do，返回 (token, name, 失败信息)；失败时 token 为空。"""
        referer = XKFW_INDEX_URL + (f"?ticket={self.ticket}" if self.ticket else "")
        headers = {"Referer": referer}
        attempts = []
        if username:
            attempts.append({"number": username})
            if self.ticket:
                attempts.append({"number": username, "ticket": self.ticket})
        attempts.extend([None, {}])

        last_msg = ""
        for params in attempts:
            try:
                resp = self._session.get(
                    REGISTER_URL,
                    params=params,
                    headers=headers,
                    timeout=20,
                )
                j = resp.json()
            except Exception:  # noqa: BLE001 - 换下一种参数继续尝试
                last_msg = last_msg or "解析用户会话信息失败"
                continue
            data = j.get("data")
            if isinstance(data, dict) and data.get("token"):
                return data["token"], (data.get("name") or ""), ""
            message = (j.get("msg") or j.get("message") or "").strip()
            if message:
                last_msg = message
        return "", "", last_msg

    def _token_from_entry(self) -> str:
        """访问选课系统入口，从跳转地址或页面脚本中解析会话 token。"""
        try:
            resp = self._session.get(XKFW_INDEX_URL, allow_redirects=True, timeout=20)
        except requests.RequestException:
            return ""
        match = TOKEN_URL_RE.search(resp.url or "")
        if match:
            return match.group(1)
        text = resp.text or ""
        for pattern in (TOKEN_BODY_RE, TOKEN_JSON_RE):
            match = pattern.search(text)
            if match:
                return match.group(1)
        return ""

    def _finalize(self, username: str = "", token: str = "") -> Tuple[bool, str]:
        """确定会话 token（优先使用浏览器带回来的 token），并导出会话 cookies。"""
        self.token = token or ""
        self.name = ""
        reg_msg = ""

        # 1) 浏览器已带回 token 时无需再调 register.do（避免多余请求与误报）
        if not self.token:
            reg_token, reg_name, reg_msg = self._register(username)
            self.token = reg_token
            self.name = reg_name

        # 2) 兜底：从选课系统入口的跳转地址中取 token
        if not self.token:
            self.token = self._token_from_entry()

        if not self.token:
            suffix = f"：{reg_msg}" if reg_msg else "，请重试"
            if not username:
                suffix = (f"：{reg_msg}" if reg_msg else "") + "（请在“学号”栏填写学号后重试）"
            return False, f"未能获取用户会话 token{suffix}"

        self.name = self.name or username or "同学"
        if self._session is not None:
            self.cookies = self._session.cookies.get_dict()
        return True, "登录成功"

    # ---------------- 浏览器会话导入（应对两步验证） ----------------
    @staticmethod
    def _parse_cookie_text(text: str) -> dict:
        """从 document.cookie / Cookie 请求头 / cURL 命令中解析出 cookie 字典。"""
        if not text:
            return {}
        raw = text.strip()
        # 兼容直接粘贴 cURL 命令的情况
        m = re.search(r"-H\s+['\"]Cookie:\s*([^'\"]+)['\"]", raw, re.I)
        if not m:
            m = re.search(r"-b\s+['\"]([^'\"]+)['\"]", raw, re.I)
        if m:
            raw = m.group(1)
        raw = re.sub(r"^Cookie:\s*", "", raw.strip(), flags=re.I)
        raw = raw.replace("\r", ";").replace("\n", ";")
        skip = {
            "path", "domain", "expires", "max-age", "httponly",
            "secure", "samesite", "priority", "version",
        }
        cookies = {}
        for part in raw.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"')
            if not key or key.lower() in skip:
                continue
            cookies[key] = value
        return cookies

    def login_with_cookies(self, cookie_text: str, username: str = "") -> Tuple[bool, str]:
        """
        使用浏览器中已登录的 Cookie 建立会话（可绕过两步验证）。

        参数:
            cookie_text: 浏览器中复制的 Cookie 内容（document.cookie / 请求头 / cURL 均可）
            username:    学号，用于向 register.do 换取 token
        返回: (是否成功, 提示信息)
        """
        cookies = self._parse_cookie_text(cookie_text)
        if not cookies:
            return False, "未能从粘贴内容中解析出 Cookie，请复制完整的 Cookie 内容后重试"

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        for key, value in cookies.items():
            self._session.cookies.set(key, value, domain=XKFW_HOST, path="/")
        self.ticket = ""

        try:
            # 校验会话是否仍然有效（失效会被跳回 CAS 登录页）
            resp = self._session.get(XKFW_INDEX_URL, allow_redirects=True, timeout=20)
        except requests.Timeout:
            return False, "网络请求超时，请检查网络连接"
        except requests.ConnectionError:
            return False, "无法连接选课系统，请检查网络或稍后重试"
        if CAS_LOGIN_PATH in (urlparse(resp.url).path or ""):
            return False, "该会话已失效或无效，请重新在浏览器中登录选课系统后再复制 Cookie"

        return self._finalize(username)

    # ---------------- 真实浏览器登录（推荐，支持两步验证） ----------------
    def login_with_browser(
        self,
        username: str = "",
        timeout: int = 600,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        打开真实浏览器让用户自己登录（支持两步验证），登录成功后自动接管会话。

        参数:
            username:  学号，用于向 register.do 换取 token
            timeout:   等待用户完成登录的秒数
            on_status: 进度回调，用于在界面上展示当前状态
        返回: (是否成功, 提示信息)
        """
        def notify(message: str) -> None:
            if on_status:
                try:
                    on_status(message)
                except Exception:
                    pass

        notify("正在启动浏览器…")
        try:
            driver = build_browser_driver()
        except Exception as exc:
            return False, str(exc)

        try:
            try:
                from selenium.common.exceptions import WebDriverException
            except ImportError:  # pragma: no cover
                WebDriverException = Exception  # type: ignore[assignment,misc]

            driver.get(XKFW_INDEX_URL)
            notify("请在浏览器窗口中完成登录（含两步验证），完成后本程序会自动继续…")

            deadline = time.time() + timeout
            logged_in = False
            browser_ticket = ""
            while time.time() < deadline:
                try:
                    current = driver.current_url or ""
                except Exception:
                    return False, "浏览器窗口已被关闭，登录已取消"
                if (urlparse(current).hostname or "").lower() == XKFW_HOST:
                    logged_in = True
                    ticket_match = re.search(r"[?&]ticket=([^&#]+)", current)
                    if ticket_match:
                        browser_ticket = ticket_match.group(1)
                    break
                time.sleep(1)
            if not logged_in:
                return False, "等待浏览器登录超时，请重试"

            notify("已检测到登录成功，正在获取会话…")
            browser_token = _token_from_driver(driver)
            try:
                last_url = driver.current_url or ""
            except Exception:
                last_url = ""
            cookies = {
                item["name"]: item.get("value", "")
                for item in (driver.get_cookies() or [])
                if item.get("name")
            }
        finally:
            try:
                driver.quit()
            except Exception:
                pass

        if not cookies:
            return False, "未能从浏览器获取 Cookie，请重试"

        # 用捕获到的 Cookie / token 建立会话
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        for key, value in cookies.items():
            self._session.cookies.set(key, value, domain=XKFW_HOST, path="/")
        self.ticket = browser_ticket
        notify("正在换取选课 token…")
        success, message = self._finalize(username, token=browser_token)
        if not success and last_url:
            message = f"{message}（浏览器最后停留在：{last_url[:120]}）"
        return success, message

    # ---------------- 入口 ----------------
    def login_process(self, username: str, password: str) -> Tuple[bool, str]:
        """
        完整登录流程（新版 CAS）。

        返回: (是否成功, 提示信息)
        """
        if not username:
            return False, "请输入账号"
        if not password:
            return False, "请输入密码"

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

        try:
            # 1) 访问选课入口（会 302 到新统一认证 CAS 登录页）
            resp = self._session.get(XKFW_INDEX_URL, allow_redirects=True, timeout=20)
            self._post_url = resp.url

            # 若直接落在选课系统内（单点登录仍有效），直接换取 token
            if CAS_LOGIN_PATH not in self._post_url:
                return self._finalize(username)

            # 2) 解析 execution 并加密密码
            self._execution = self._extract_execution(resp.text) or ""
            if not self._execution:
                return False, "无法解析登录页面(execution)，登录页可能已改版"
            enc_password = self._encrypt_password(password)

            # 3) 登录保护(MFA)探测
            need_mfa, msg = self._mfa_detect(username, enc_password)
            if need_mfa:
                return False, msg

            # 4) 提交账号密码登录
            final = self._submit_login(username, enc_password)

            # 5) 校验结果：应已跳回选课系统（用 hostname 判断，勿用子串，
            #    否则会被 CAS 登录页 service 参数中的 xkfw 域名误判）
            host = (urlparse(final.url).hostname or "").lower()
            if host == XKFW_HOST:
                return self._finalize(username)
            return False, self._classify_failure(final)

        except requests.Timeout:
            return False, "网络请求超时，请检查网络连接（登录需在校园网/可访问内网的环境进行）"
        except requests.ConnectionError:
            return False, "无法连接登录服务器，请检查网络或稍后重试"
        except Exception as e:
            return False, f"登录过程出错：{e}"
