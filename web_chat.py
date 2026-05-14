import os
import re
import time
import json
import glob
import hashlib
import hmac
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 创建历史对话存储目录
CHATS_DIR = "chats"
os.makedirs(CHATS_DIR, exist_ok=True)

# ==================== Cookie Token 持久化认证 ====================
# 方案：HMAC 签名 Token 存入浏览器 Cookie，支持"记住我"免密登录
# 安全性：
#   1. Token = 创建时间:过期时间:HMAC-SHA256(密码哈希+时间戳+密钥)
#   2. 修改 ACCESS_PASSWORD 会使所有旧 Token 立即失效
#   3. hmac.compare_digest 防止时序攻击
#   4. Token 最长有效期由 TOKEN_MAX_AGE_DAYS 控制（默认 30 天）

COOKIE_NAME = "deepseek_auth"
TOKEN_MAX_AGE_DAYS = int(os.environ.get('TOKEN_MAX_AGE_DAYS', '30'))
# COOKIE_SECRET 用于 HMAC 签名，默认由 ACCESS_PASSWORD 派生
COOKIE_SECRET = os.environ.get(
    'COOKIE_SECRET',
    os.environ.get('ACCESS_PASSWORD', 'default-secret') + ':cookie-salt'
)


def _hash_password(password: str) -> str:
    """对密码做 SHA-256 单向哈希，嵌入 Token 用于验证密码是否变更"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def generate_auth_token() -> str:
    """生成签名认证 Token，格式: 创建时间戳:过期时间戳:HMAC签名"""
    now = int(time.time())
    expiry = now + TOKEN_MAX_AGE_DAYS * 86400
    password_hash = _hash_password(os.environ.get('ACCESS_PASSWORD', '123456'))
    message = f"{password_hash}:{expiry}:{now}"
    signature = hmac.new(
        COOKIE_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{now}:{expiry}:{signature}"


# Token 黑名单（服务端内存缓存，重启清空；用于实现可靠的"退出登录"）
@st.cache_resource
def _get_token_blacklist() -> set:
    """返回全局 Token 黑名单集合。退出登录时加入，validate 时优先拒绝。"""
    return set()


def _invalidate_token(token: str):
    """将指定 Token 加入黑名单（退出登录时调用）"""
    if token:
        _get_token_blacklist().add(token)


def validate_auth_token(token: str) -> bool:
    """验证 Token：检查黑名单 → 过期时间 → HMAC 签名完整性"""
    # ★ 优先检查黑名单（退出登录的 Token 立即失效）
    if token in _get_token_blacklist():
        return False
    try:
        parts = token.split(':')
        if len(parts) != 3:
            return False
        created, expiry, signature = parts
        # 检查是否过期
        if int(expiry) < time.time():
            return False
        # 重新计算签名并比对（hmac.compare_digest 防时序攻击）
        password_hash = _hash_password(os.environ.get('ACCESS_PASSWORD', '123456'))
        message = f"{password_hash}:{expiry}:{created}"
        expected_sig = hmac.new(
            COOKIE_SECRET.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


def _set_persistent_cookie(token: str, max_age_days: int = None):
    """双重写入持久化 Cookie：
    1. st.context.cookies → HTTP 响应头 Set-Cookie（立即可用）
    2. st.markdown <script> → 主页面 JS document.cookie + max-age（跨浏览器会话，移动端兼容）
    
    注意：使用 st.markdown 注入 <script> 而非 components.html（iframe），
    因为移动端浏览器对 iframe 内 document.cookie 限制严格（ITP 等隐私策略）。
    """
    if max_age_days is None:
        max_age_days = TOKEN_MAX_AGE_DAYS
    max_age_secs = max_age_days * 86400
    try:
        # HTTP 响应头设 Cookie（立即可用，确保 st.rerun() 后能读到）
        st.context.cookies[COOKIE_NAME] = token
    except Exception:
        pass
    # 主页面 JS 设持久化 Cookie（max-age 跨浏览器会话，移动端可靠）
    st.markdown(
        f'<script>document.cookie="{COOKIE_NAME}={token};max-age={max_age_secs};path=/";</script>',
        unsafe_allow_html=True
    )


def _delete_persistent_cookie():
    """双重清除 Cookie：HTTP 响应头 + 主页面 JS（max-age=0 立即过期）"""
    try:
        st.context.cookies[COOKIE_NAME] = ""
    except Exception:
        pass
    st.markdown(
        f'<script>document.cookie="{COOKIE_NAME}=;max-age=0;path=/";</script>',
        unsafe_allow_html=True
    )


def clear_auth_cookie():
    """清除认证 Cookie 并将当前 Token 加入黑名单（彻底阻止自动登录）"""
    # 先读取当前 Token 加入黑名单（关键：防止 Cookie 清除不彻底导致自动登回）
    try:
        current_token = st.context.cookies.get(COOKIE_NAME)
        _invalidate_token(current_token or "")
    except Exception:
        pass
    _delete_persistent_cookie()

# ==================== Cookie Token 认证 END ====================

# 设置页面标题
st.set_page_config(page_title="DeepSeek", page_icon="🤖")
st.title("DeepSeek Web 聊天助手")

# 初始化 OpenAI 客户端
@st.cache_resource # 缓存客户端，避免每次刷新页面重新实例化
def get_client():
    return OpenAI(
        api_key=os.environ.get('DEEPSEEK_API_KEY'),
        base_url="https://api.deepseek.com"
    )

client = get_client()

# 定义系统提示词角色
ROLES = {
    "均衡默认": "你是一个乐于助人的AI智能助手。请根据用户的输入自然、准确、友善地作答。\n- 请保持回答清晰、简洁、逻辑分明。\n- 遇到不知道或不确定的知识，请客观坦诚地告知，不编造虚假信息。",
    "编码大师": "你是一名世界顶级的首席软件工程师和架构师。你的目标是输出最高质量、符合生产环境标准的代码。请严格遵循以下原则：\n1. 优先提供优雅、高效、可维护且符合该语言最佳实践的代码。\n2. 提供的代码尽量完整且可以直接运行，避免使用含糊的伪代码。\n3. 在代码中添加精练的中文注释以解释复杂的核心逻辑。\n4. 主动思考并指出潜在的边界条件（Edge Cases）、异常处理和性能优化建议。\n5. 减少过多不必要的寒暄，直奔技术要点和解决方案。",
    "数学大师": "你是一位极其严谨的理论数学家与受人尊敬的教授。请以极致的逻辑性和专业性回答问题。请严格遵循以下要求：\n1. 必须使用准确的 LaTeX 表达数学概念，行内公式严格使用 `$` 包裹，独立块级公式严格使用 `$$` 包裹。\n2. 对于计算或证明题，必须采取分步解析（Step-by-Step）的方式，写出清晰的演算过程。\n3. 在得出结论后，尽可能简要总结其背后的核心定理或数学直觉。\n4. 保持语言的学术性与严谨性。"
}

# 定义一个初始化或重置对话的函数
def init_or_reset_chat():
    role_name = st.session_state.get("selected_role", "均衡默认")
    st.session_state.messages = [
        {"role": "system", "content": ROLES.get(role_name, ROLES["均衡默认"])}
    ]
    # 生成一个新的时间戳作为对话ID
    st.session_state.current_chat_id = str(int(time.time() * 1000))
    st.session_state.chat_title = "新对话"

# 将当前对话保存到本地 JSON 文件
def save_current_chat():
    if "current_chat_id" not in st.session_state:
        return
    
    # 尝试自动从第一句用户输入生成标题
    if st.session_state.chat_title == "新对话":
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.session_state.chat_title = msg["content"][:15] + ("..." if len(msg["content"])>15 else "")
                break

    file_path = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")
    data = {
        "id": st.session_state.current_chat_id,
        "title": st.session_state.chat_title,
        "messages": st.session_state.messages
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 从本地 JSON 文件加载对话
def load_chat(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        st.session_state.messages = data.get("messages", [])
        st.session_state.current_chat_id = data.get("id")
        st.session_state.chat_title = data.get("title", "未命名对话")

# 导出当前对话为 Markdown 格式
def export_chat_as_markdown():
    """将当前对话（不含系统提示词）导出为格式化的 Markdown 字符串"""
    title = st.session_state.get("chat_title", "新对话")
    lines = [f"# {title}", "", f"*导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}*", "", "---", ""]
    for msg in st.session_state.messages:
        if msg["role"] == "system":
            continue
        if msg["role"] == "user":
            lines.append(f"## 👤 用户")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")
        elif msg["role"] == "assistant":
            lines.append(f"## 🤖 助手")
            lines.append("")
            if msg.get("thinking"):
                lines.append("<details>")
                lines.append("<summary>🤔 思考过程</summary>")
                lines.append("")
                lines.append(msg["thinking"])
                lines.append("")
                lines.append("</details>")
                lines.append("")
            lines.append(msg["content"])
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)

# 导出当前对话为 JSON 格式
def export_chat_as_json():
    """将当前对话导出为格式化的 JSON 字符串"""
    data = {
        "id": st.session_state.get("current_chat_id", ""),
        "title": st.session_state.get("chat_title", "新对话"),
        "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": st.session_state.messages
    }
    return json.dumps(data, ensure_ascii=False, indent=2)

# 缓存历史对话列表，避免每次刷新重复读取全部文件
@st.cache_data
def get_history_chats():
    if not os.path.exists(CHATS_DIR):
        return []
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    files.sort(reverse=True)
    res = []
    # 最多只读取最近 5 条，大幅提升侧边栏加载速度
    for f in files[:5]:
        try:
            with open(f, "r", encoding="utf-8") as file:
                data = json.load(file)
                res.append({"id": data.get("id"), "title": data.get("title", "未命名对话"), "path": f})
        except Exception as e:
            print(f"解析历史记录出错 {f}: {e}")
            continue
    return res

# 密码验证逻辑（支持 Cookie Token 自动登录 + "记住我"）
def check_password():
    CORRECT_PASSWORD = os.environ.get('ACCESS_PASSWORD', '123456')
    
    # 初始化 session state
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "login_attempts" not in st.session_state:
        st.session_state.login_attempts = 0

    # ★ 步骤 1：Cookie Token 自动登录（仅在未认证时尝试）
    if not st.session_state.authenticated:
        try:
            cookie_token = st.context.cookies.get(COOKIE_NAME)
        except Exception:
            cookie_token = None
        
        if cookie_token and validate_auth_token(cookie_token):
            st.session_state.authenticated = True
            st.session_state.login_attempts = 0
            # 自动刷新 Token（每次访问延长有效期，活跃用户永不过期）
            _set_persistent_cookie(generate_auth_token())
            st.rerun()
    
    # ★ 步骤 2：未认证 → 显示登录表单
    if not st.session_state.authenticated:
        # 暴力破解保护：5 次错误后锁定
        if st.session_state.login_attempts >= 5:
            st.error("尝试次数过多，为了安全起见，已锁定登录。请重启服务或稍后再试。")
            return False

        st.warning("请输入访问密码以继续。")
        
        # 密码输入框 + "记住我"复选框同行布局
        col1, col2 = st.columns([3, 1])
        with col1:
            pwd = st.text_input("密码", type="password", key="pwd_input")
        with col2:
            st.write("")  # 垂直对齐占位
            remember = st.checkbox("记住我", value=False, key="remember_checkbox",
                                   help="勾选后 30 天内无需重复输入密码")
        
        if st.button("登录", use_container_width=True):
            if pwd == CORRECT_PASSWORD:
                st.session_state.authenticated = True
                st.session_state.login_attempts = 0
                # 勾选"记住我" → 双重写入持久 Cookie（HTTP 响应头 + JS max-age）
                if remember:
                    _set_persistent_cookie(generate_auth_token())
                st.rerun()
            else:
                st.session_state.login_attempts += 1
                # 时间惩罚，抵御脚本暴力破解
                time.sleep(2)
                st.error(f"密码错误！剩余尝试次数: {5 - st.session_state.login_attempts}")
        return False
    return True

# 如果未通过验证，则停止向下执行后续的聊天代码
if not check_password():
    st.stop()

# 初始化对话历史，存放在 Streamlit 的 session_state 中
if "selected_role" not in st.session_state:
    st.session_state.selected_role = "均衡默认"
if "messages" not in st.session_state:
    init_or_reset_chat()

# 在侧边栏添加新建对话按钮
with st.sidebar:
    st.title("功能菜单")
    
    # 添加角色选择下拉框
    new_role = st.selectbox(
        "选择助手角色",
        list(ROLES.keys()),
        index=list(ROLES.keys()).index(st.session_state.selected_role)
    )
    if new_role != st.session_state.selected_role:
        st.session_state.selected_role = new_role
        init_or_reset_chat()
        st.rerun()
    
    # 添加模型选择下拉框
    selected_model = st.selectbox(
        "选择模型",
        ("deepseek-v4-pro", "deepseek-v4-flash"),
        index=0
    )
    
    # 添加思考模式开关和强度选择
    enable_thinking = st.toggle("开启思考模式", value=True)
    reasoning_effort = "high"
    if enable_thinking:
        reasoning_effort = st.selectbox(
            "选择思考强度",
            ("high", "max"),
            index=0
        )
    
    if st.button("➕ 新建对话", use_container_width=True, type="primary"):
        init_or_reset_chat()
        st.rerun()
    
    # 退出登录按钮（黑名单 + Cookie 清除 + 延迟确保 JS 执行）
    if st.button("🚪 退出登录", use_container_width=True):
        clear_auth_cookie()
        st.session_state.authenticated = False
        st.session_state.login_attempts = 0
        time.sleep(0.3)  # 给浏览器 300ms 执行 JS 清除持久化 Cookie
        st.rerun()

    st.divider()
    st.subheader("历史记录")
    
    # 获取被缓存的历史记录列表
    history_list = get_history_chats()
    for item in history_list:
        chat_id = item["id"]
        title = item["title"]
        is_current = (chat_id == st.session_state.get("current_chat_id"))
        label = f"🔵 {title}" if is_current else f"💬 {title}"
        
        # 单击历史文件将其加载到屏幕中央
        if st.button(label, key=f"btn_{chat_id}", use_container_width=True):
            load_chat(item["path"])
            st.rerun()

    st.divider()
    st.subheader("导出对话")
    
    # 生成安全的文件名（移除非法字符）
    safe_title = st.session_state.get("chat_title", "新对话")
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", safe_title)
    
    # Markdown 下载按钮
    md_content = export_chat_as_markdown()
    st.download_button(
        label="📥 导出 Markdown",
        data=md_content,
        file_name=f"{safe_title}.md",
        mime="text/markdown",
        use_container_width=True
    )
    
    # JSON 下载按钮
    json_content = export_chat_as_json()
    st.download_button(
        label="📥 导出 JSON",
        data=json_content,
        file_name=f"{safe_title}.json",
        mime="application/json",
        use_container_width=True
    )

# === format_latex 性能优化：预编译正则表达式（避免每次调用重复编译） ===
_RE_BEGIN_ENV = re.compile(r'\\begin\{(?:aligned|array|matrix|pmatrix|bmatrix|cases|align|gather|split)\}')
_RE_END_ALIGNED = re.compile(r'\\end\{aligned\}')
_RE_DOLLAR_BLOCK = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)

@st.cache_data(show_spinner=False, max_entries=300, ttl=3600)
def format_latex(text):
    r"""将模型输出中的 LaTeX 标识符标准化为 Streamlit (KaTeX) 可渲染的格式。
    
    处理三个主要问题：
    1. 将 \(...\) 转换为 $...$（行内公式）
    2. 将 \[...\] 转换为 $$...$$（块级公式）
    3. 自动修复缺失的 \begin{aligned} 环境——当 $$...$$ 块内使用了 & 对齐符
       但没有对齐环境包裹时，自动补全 \begin{aligned}...\end{aligned}
    
    性能优化：
    - 预编译正则表达式（模块级常量），消除每次调用重复编译开销
    - @st.cache_data 持久化缓存：页面刷新时相同历史消息直接命中缓存，跳过全部处理
    - 快速路径：不含 & 符号时跳过昂贵的正则替换（覆盖 >95% 纯文本消息）
    - 安全限制：跳过超长公式块（>50000 字符），防止极端输入导致灾难性回溯
    """
    if not text:
        return text
    
    # 临时保护 \\[ 和 \\] (通常用于矩阵或多行公式的换行)，避免被错误替换
    text = text.replace(r'\\[', '___TEMP_LBRACKET___')
    text = text.replace(r'\\]', '___TEMP_RBRACKET___')
    
    # 替换常规的数学公式首尾标识符
    text = text.replace(r'\[', '$$')
    text = text.replace(r'\]', '$$')
    text = text.replace(r'\(', '$')
    text = text.replace(r'\)', '$')
    
    # 恢复被保护的换行符
    text = text.replace('___TEMP_LBRACKET___', r'\\[')
    text = text.replace('___TEMP_RBRACKET___', r'\\]')
    
    # ★ 自动修复：检测 $$...$$ 块内缺失对齐环境的 & 符号
    # 快速路径：不含 & 的文本无需对齐修复，跳过昂贵的 re.sub 扫描
    # 绝大多数聊天消息为纯文本，此优化可覆盖 95%+ 的渲染调用
    if '&' in text:
        def fix_missing_aligned(match):
            r"""若 $$ 块内含 & 且缺少对齐环境，则自动包裹 \begin{aligned}...\end{aligned}"""
            content = match.group(1)
            # 安全限制：跳过超长公式块，防止极端输入导致灾难性回溯
            if len(content) > 50000:
                return f'$${content}$$'
            # 已有对齐环境（aligned, array, matrix, cases 等）则不处理
            if _RE_BEGIN_ENV.search(content):
                return f'$${content}$$'
            # 包含 & 对齐符但缺少环境 → 自动补全 aligned
            # 若已有残留的 \end{aligned} 则移除（防止重复闭合）
            content = _RE_END_ALIGNED.sub('', content)
            return f'$$\\begin{{aligned}}\n{content}\n\\end{{aligned}}$$'
        
        text = _RE_DOLLAR_BLOCK.sub(fix_missing_aligned, text)
    
    return text

# 显示历史对话记录 (跳过系统提示词)
for msg in st.session_state.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            # 如果消息包含思考过程，一并渲染
            if msg.get("thinking"):
                thinking_html = (
                    f'<div style="background:rgba(128,128,128,0.1);border-left:4px solid rgba(128,128,128,0.35);'
                    f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                    f'font-size:0.9em;line-height:1.6;">'
                    f'<details><summary>🤔 <strong>思考过程</strong></summary>'
                    f'{msg["thinking"]}</details></div>'
                )
                st.markdown(thinking_html, unsafe_allow_html=True)
            st.markdown(format_latex(msg["content"]))

# 接收用户输入
if prompt := st.chat_input("请输入文本"):
    
    # 1. 把用户的问题展示在网页上
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 2. 把用户的问题追加到历史记录中
    st.session_state.messages.append({"role": "user", "content": prompt})
    # 这里不需要立刻存硬盘，忍住！
    
    # 3. 请求大模型并展示回答（性能优化：分离思考与回复的渲染容器）
    with st.chat_message("assistant"):
        # ★ 性能优化核心：使用两个独立容器，彻底解耦思考过程和回复内容的渲染
        # 优化前：思考 + 回复拼在一个 HTML 字符串中，每次全量重绘（O(n²)）
        # 优化后：
        #   - 思考容器：仅在思考阶段更新，完成后立即"冻结"，不再参与后续重绘
        #   - 回复容器：仅渲染回复文本，虽然仍为全量替换，但文本量大幅缩小
        #   - 消除"思考 HTML 反复重建"产生的额外序列化 / Markdown 解析开销
        thinking_placeholder = st.empty()
        response_placeholder = st.empty()
        
        full_response = ""
        full_thinking = ""
        saved_thinking = ""  # 保存思考内容副本，避免被清空标记覆盖
        last_update_time = 0
        
        # 准备 API 请求参数
        api_kwargs = {
            "model": selected_model,
            "messages": st.session_state.messages,
            "stream": True,
        }
        if enable_thinking:
            api_kwargs["reasoning_effort"] = reasoning_effort
            api_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            api_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            
        try:
            response = client.chat.completions.create(**api_kwargs)
            
            for chunk in response:
                delta = chunk.choices[0].delta
                current_time = time.time()
                
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    full_thinking += reasoning
                    # 思考阶段：单独更新思考容器（内容较短，重绘开销极小）
                    # 此阶段不触发回复容器更新，互不干扰
                    thinking_html = (
                        f'<div style="background:rgba(128,128,128,0.1);'
                        f'border-left:4px solid rgba(128,128,128,0.35);'
                        f'padding:10px 14px;border-radius:4px;margin-bottom:8px;'
                        f'font-size:0.92em;line-height:1.6;">'
                        f'🤔 <strong>思考中...</strong><br>{full_thinking}▌</div>'
                    )
                    thinking_placeholder.markdown(thinking_html, unsafe_allow_html=True)
                
                if delta.content is not None:
                    full_response += delta.content
                    
                    # 思考完成，冻结思考容器为可折叠块（此后不再更新此容器）
                    if full_thinking:
                        saved_thinking = full_thinking
                        thinking_html = (
                            f'<div style="background:rgba(128,128,128,0.1);'
                            f'border-left:4px solid rgba(128,128,128,0.35);'
                            f'padding:8px 14px;border-radius:4px;margin-bottom:10px;'
                            f'font-size:0.9em;line-height:1.6;">'
                            f'<details open><summary>🤔 <strong>思考过程</strong></summary>'
                            f'{saved_thinking}</details></div>'
                        )
                        thinking_placeholder.markdown(thinking_html, unsafe_allow_html=True)
                        full_thinking = ""  # 清空标记，确保不再进入思考更新分支
                    
                    # ★ 增量渲染优化：流式过程中展示原始文本（不调用 format_latex），
                    # 彻底消除流式循环中每 60ms 对全文跑正则处理的 O(n²) 性能瓶颈。
                    # LaTeX 源码中的 $ 和 $$ 会以纯文本形式展示（类似 st.write_stream），
                    # 流式结束后统一调用 format_latex 一次性渲染完整公式，确保 KaTeX 不会因截断而失败。
                    if current_time - last_update_time > 0.06:
                        response_placeholder.markdown(
                            full_response + "▌",
                            unsafe_allow_html=False
                        )
                        last_update_time = current_time
            
            # 流式结束，一次性调用 format_latex 渲染完整 LaTeX（去掉光标）
            response_placeholder.markdown(format_latex(full_response), unsafe_allow_html=False)
            
        except Exception as e:
            st.error(f"API 请求失败: {e}")
            # 剔除刚才写入历史记录的用户提问，保证状态回退允许用户重试
            st.session_state.messages.pop()
            st.stop()
        
    # 4. 把 AI 的回答（含思考过程）追加到历史记录中
    st.session_state.messages.append({"role": "assistant", "content": full_response, "thinking": saved_thinking})
    
    # 【性能优化关键】：AI 答复完之后，才合并进行一次“集中的保存”，绝不打断打字流
    save_current_chat() 
    get_history_chats.clear() # 清空侧边栏列表缓存，告知最新的一条有更新
